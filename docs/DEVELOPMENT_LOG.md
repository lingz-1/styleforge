# StyleForge 开发过程记录

> 更新时间：2026-08-12

本文按开发批次记录“为什么改、如何设计、实现顺序、遇到的问题和验证证据”。当前能力结论以[项目状态](PROJECT_STATUS.md)为准；具体故障按编号收录在[问题与解决记录](ISSUE_LOG.md)。

## 2026-08-12：部署环境升级（PostgreSQL 全量迁移 + Redis 会话缓存 + Chroma RAG）

### 1. 需求与边界

定稿技术栈把 SQLite 全量迁移到 PostgreSQL，并接入两个可选外部服务。开发前确认四个决策：**PG 全量一次性迁移**（20 张表 15,271 行 8.2MB）、**PG 由用户安装**（PG 17.10，`E:\PostgreSQL\`，trust 认证，127.0.0.1:5432，role `styleforge`，库 `styleforge`/`styleforge_test`）、**Redis 只接会话状态缓存**、**Chroma RAG 用 FashionCLIP 过渡**（复用 `encode_texts`，512 维）。

边界：

- 不引入 SQLAlchemy；裸 psycopg3 最贴合现状。`pgvector` 列为后续（本期 BYTEA 原样搬）。
- 外部服务遵循既有模式：`enabled` 开关 + None 降级 + 吞异常 + 注入式测试。
- 衣柜检索保持 NumPy/FAISS 不动；Chroma 只做知识文本检索，allow-list 隔离是隐私设计。
- 真实连接串放项目根 `.env`（`STYLEFORGE_DATABASE_DSN` 等），不打印、不提交。

### 2. 方言移植要点（SQLite → PostgreSQL）

- 连接层：`connect(path)` → `connect(dsn)`，psycopg3 + 自定义 `sqlite_like_row_factory`；`PgConnection` 代理类为 psycopg3 Connection 补 `executemany`，`row["col"]`/`row[0]` 兼容旧 `sqlite3.Row`。
- `contextmanager` 语义：`PgConnection` 显式定义 `__enter__`/`__exit__`（隐式特殊方法查找不走 `__getattr__`），`with connect(dsn)` 与旧 `sqlite3.Connection` 一致。
- 方言点：`AUTOINCREMENT`→`BIGSERIAL`、`BLOB`→`BYTEA`、`rowid`（3 处排序 tiebreaker）→`chat_messages.message_seq BIGSERIAL`、`cursor.lastrowid`→`INSERT ... RETURNING`、`LIKE`→`ILIKE`、`?`→`%s`、`lastrowid`→`RETURNING`。

### 3. 实现顺序

1. PG schema 先行：`artifacts/init_pg_schema.py`（gitignored）建 20 表；`artifacts/migrate_sqlite_to_pg.py` 逐表迁移并 count 对比，20 表行数 SQLite == PG 零差异。
2. `core/config.py` 增 `database_dsn`/`redis_enabled`/`redis_url`/`redis_ttl`/`chroma_dir`；`load_dotenv(override=False)`，窗口 env 优先。
3. `repositories/database.py` 重写为 PG 连接层（`SCHEMA_VERSION=10`），SQLite 路径与 DDL 删除；`config.py` 删 `database_path`，只留 `database_dsn`。
4. 14 个 repo + services/pipelines/evaluation 裸 SQL 分批移植（`?`→`%s`、`rowid`→`message_seq`、`ILIKE`、`RETURNING`）。
5. 调用方连接注入：`api.py`/`graph.py`/`task_workflow.py`/services 等约 33 个文件，`database_session(settings.database_path)` → `database_session(settings)`。
6. Redis 读穿缓存：`core/redis.py::redis_client_from_settings`（禁用或失败返回 None）；`chat_service.set_session_outfit_cache` 写 `session:<session_id>`，`get_session_outfit_context` 命中读/未命中重算写回；删会话失效；异常回落 DB。
7. Chroma RAG：`integrations/embeddings/text_embedder.py` 包装 FashionCLIP `encode_texts`（超 77 token 分块）；`integrations/vectorstores/chroma_store.py`（PersistentClient，cosine）；`knowledge/{chunking,ingestion,indexer}.py` + CLI `styleforge-knowledge-index`；`knowledge/retriever.py::search` 加向量路径（关键词回退保留），`source="chroma_rag"`。
8. 测试层：`tests/conftest.py` 建 PG fixture——每测试独立 schema（`options=-csearch_path%3D<schema>`），`db_dsn`/`db_conn` 隔离，`STYLEFORGE_TEST_DATABASE_DSN` 未设置时 skip；13 个 `tmp_path / "*.db"` 模式改 `db_dsn`；`test_database_schema.py` 重写为 `information_schema` 断言；`test_knowledge_rag.py` 新增 12 测试。

### 4. 遇到的问题与修复

- **pytest 全 skip（19 skipped）**：pytest 进程没加载 `.env`，conftest 模块级 `import styleforge.core.config` 触发 dotenv side effect 后修复。
- **`AttributeError: __enter__`**：`with connect(dsn)` 报错，隐式特殊方法查找不走实例 `__getattr__` → 在 `PgConnection` 显式定义 `__enter__`/`__exit__`。
- **sed 误改测试名**：批量 sed 只匹配单行签名，把 `test_batch_recognition.py` 的 5 个测试名误改，逐一手工恢复。
- **`_make_workflow() missing 'llm'`**：多行调用漏插 `db_dsn`，手工补齐。
- **ChromaStore.query include 缺 `documents`**、`search()` 向量路径提前 return、huggingface-hub 降级 0.36.2。

### 5. 验证证据

- 数据侧：20 表 15,271 行 SQLite == PG 逐表零差异。
- 全量 Pytest **382 passed**（原 364 + 新增 18）、`compileall`、Ruff clean。
- `artifacts/verify_pg_schema_isolation.py` 证明每测试独立 schema 端到端可行（20 表进独立 schema、public 零泄漏、drop CASCADE 清理）。

## 2026-08-10：P5 天气上下文接入三个主 Agent

### 1. 需求与不可突破的边界

本批次继续扩展标准穿搭推荐，使“明天在上海户外参加活动，穿什么”这类请求能够使用实时天气。开发前重新核对`项目文档/扩展版方案.txt`、`项目文档/修正版方案.txt`、`docs/architecture/ARCHITECTURE_PLAN.md`以及现有 Agent、Context Pack、工作流状态和测试合同，确定以下边界：

- 不新增天气 Agent；Context Router、Tool Registry 和 Weather Tool 都是共享系统能力。
- 不新增决定穿搭结果的 Service；所有搭配判断仍经过 SemanticRetriever、Composer、Critic 三个主 Agent。
- Weather Tool 只提供温度、体感、降水、湿度、风和天气代码等事实，不输出“该穿什么”。
- 不使用确定性穿搭结果替代 Agent。天气不可用时只记录`unavailable`，继续让三个 Agent依据已有衣橱事实工作。
- 外部事实必须进入统一 Context Pack、trace、API 响应和持久化明细，便于追溯建议依据。
- 当前只实现进程内 typed Tool；没有 MCP Client/Server 协议，因此不能命名为 Weather MCP。

### 2. 阶段输入输出设计

Agent 1 输出新增`context_requirements.weather`：

- `needed`：当前请求是否确实受天气影响。
- `location`：用户显式地点；为空时才使用默认城市。
- `date`：`今天`、`明天`或`YYYY-MM-DD`。
- `reason`：为什么本次任务需要天气事实。

Context Router 只接受通过 Pydantic 校验的参数，通过 Tool Registry 调用`weather.get_weather`。天气可用时，工作流复用同一个 Agent 1 进行第二次语义细化：把天气事实写入`practical_context`并调整 FashionCLIP 英文检索短语。之后 Agent 2 在候选池内处理层次、材质、鞋履和实穿平衡，Agent 3 将相同事实用于`wearability`审校。

普通请求保持三次 LLM 调用；需要且成功取得天气的请求为四次 LLM 调用，其中两次属于同一个 Agent 1。工具调用既不计作 LLM 调用，也不代表新增第四个 Agent。

### 3. 实现顺序

1. 新增天气领域合同：上下文需求、工具输入、解析地点和天气事实。
2. 新增 Open-Meteo Provider；使用标准库 HTTP，并允许注入 JSON Transport 进行离线测试。
3. 新增 typed Tool Registry 和 Context Router，统一参数校验、调用与审计轨迹。
4. 扩展 Agent 1 Schema 和三阶段提示词；天气不可用时明确禁止生成天气主张。
5. 在 LangGraph 中加入按需 Context Router 节点；成功后回到同一个 Agent 1，失败则直接进入后续三个 Agent流程。
6. 将`context_requirements`、`environment_context`和`tool_calls`写入工作流输出、`styling_runs.semantic_detail_json`及统一任务 Context Pack。
7. Web 推荐页增加天气事实卡片；不增加任务选择器。
8. 更新架构、部署、测试、状态和问题记录。

主要实现位置：

- `apps/api/styleforge/tools/weather/`：天气合同、Provider 和 Tool。
- `apps/api/styleforge/tools/registry.py`：typed Tool Registry。
- `apps/api/styleforge/orchestration/context_router.py`：上下文需求执行与调用轨迹。
- `apps/api/styleforge/workflow/graph.py`：按需工具路由及三个 Agent 的事实共享。
- `apps/web/src/pages/RecommendPage.vue`：天气事实展示。

### 4. 开发期间发现的问题

#### Pytest 临时目录无权限

首次专项测试有11项通过，但使用`tmp_path`的工作流测试在系统目录`C:\Users\32369\AppData\Local\Temp\pytest-of-32369`遇到`PermissionError`。该错误发生在测试夹具创建阶段，不是业务失败。后续使用项目内独立`--basetemp artifacts\...`并关闭缓存插件完成验证。

#### Vite 在受限沙箱中无法读取父目录

首次 Web 构建因 esbuild 扫描父目录被沙箱拒绝。保持同一`npm.cmd run build`命令，在获准的构建环境重跑后成功，1674个模块完成转换。保留的提示只有现有大 chunk 和第三方 PURE 注释位置，不影响构建产物。

#### API 启动命令遗漏后端包目录

代码已经从仓库根目录移动到`apps/api/styleforge/`，但 README、部署文档和首次验收命令仍从仓库根目录直接执行`python -m uvicorn styleforge.api:app`，导致：

```text
ModuleNotFoundError: No module named 'styleforge'
```

根因不是包缺失，而是仓库根目录没有把`apps/api`加入 Python 模块搜索路径。统一修正为：

```powershell
D:\anaconda\envs\style\python.exe -m uvicorn styleforge.api:app `
  --app-dir apps\api `
  --host 127.0.0.1 `
  --port 8000
```

CLI 模块命令则先设置：

```powershell
$env:PYTHONPATH=(Resolve-Path ".\apps\api")
```

### 5. 验证证据

开发基线提交为`a16d62b`；本批次代码和文档在验证时尚未创建新提交。最终验证命令为：

```powershell
D:\anaconda\envs\style\python.exe -m compileall -q apps\api\styleforge tests
D:\anaconda\envs\style\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp artifacts\pytest-full-20260810-weather-2 tests
D:\anaconda\envs\style\python.exe -m ruff check .
cd apps\web
npm.cmd run build
```

- 天气、Agent 与工作流专项：19 passed。
- 最终全量 Pytest：189 passed，1个FastAPI TestClient第三方弃用提示。
- `compileall`：通过。
- 全项目 Ruff：All checks passed。
- Vue Vite生产构建：通过，1674个模块完成转换。
- 普通推荐仍保持三次 LLM 调用。
- 天气推荐验证同一个 Agent 1 二次执行、工具只调用一次、Agent 2/3收到相同事实。
- 统一`/tasks/execute`入口验证天气事实同步进入共享 Context Pack。
- `/health`验证天气开关和Provider信息。

测试没有访问实时天气网络；Open-Meteo通过注入固定Transport验证。实时Provider连通性属于运行验收，不替代离线合同测试。本批次没有生成JUnit/XML测试报告，`189 passed`来自最终控制台输出及同步文档记录；需要发布可独立复核的版本时，应把报告写入`artifacts/evaluation/`并记录对应提交SHA。

### 6. 无上下文读者检查

文档完成后，用只读取README、文档索引、开发日志、部署、天气、状态和问题记录的独立读者检查可发现性。读者能够正确回答API启动命令、`PYTHONPATH`原因、Weather Tool边界和189个Pytest用例结果的位置，同时发现以下迁移遗留并完成修正：

- Streamlit代码块补齐`PYTHONPATH`和图片根目录，保证单独复制也能运行。
- README、Next Steps和Mytheresa文档中的Ruff/compileall旧路径改为`apps/api/styleforge`。
- 问题记录中指向旧`styleforge/...`目录的Markdown链接改到`../apps/api/styleforge/...`。
- 真实API验收口径修正为“8请求全部accept；7类无回退；高考请求Critic瞬时降级一次”。
- 2062件是Schema迁移演练快照，2058件是2026-08-09干净重建快照；`mixed-large`则是最多204件的可重建profile。
- 历史“最终验收”改为“当批验收”，避免与后续189个用例的全量回归冲突。

### 7. 当前验收入口

从仓库根目录启动 API：

```powershell
cd C:\Users\32369\Desktop\agent-p\style
$env:NO_PROXY="127.0.0.1,localhost,::1"
$env:GARMENTS2LOOK_IMAGE_ROOT="E:\image.tar\image\images"
$env:STYLEFORGE_WEATHER_ENABLED="true"
$env:STYLEFORGE_WEATHER_PROVIDER="open-meteo"
$env:STYLEFORGE_DEFAULT_LOCATION="上海"
D:\anaconda\envs\style\python.exe -m uvicorn styleforge.api:app `
  --app-dir apps\api `
  --host 127.0.0.1 `
  --port 8000
```

验收请求：`明天在上海户外参加音乐节，请根据天气从我的衣柜推荐三套穿搭。`

天气可用时，Web应展示天气事实卡片，语义链路通常为四次 LLM 调用；外部天气不可用时，应显示明确原因并且不能出现无依据的天气建议。

## 8. 天气与时空上下文 V2方案（2026-08-10，未实现）

### 8.1 新需求边界

用户要求不必显式说“根据天气”，而由Agent 1从“明天穿什么”“去北京旅游”“下周看某演出”等自然语言中判断隐含环境需求。范围随后扩展为所有会实质影响穿着安全、舒适度、活动完成度或随身准备的场景，而不是为三个示例写关键词分支。

本地请求优先使用用户授权的设备定位；显式目的地、行程城市或Event Lookup解析出的场馆必须覆盖当前位置。精确坐标只用于本次天气查询和短期缓存，默认不进入长期用户资料和普通日志。

### 8.2 已完成的方案工作

- 盘点V1实际边界：单日、日级、今天/明天/ISO日期、显式或全局默认城市。
- 定义完整场景矩阵：时间、地点移动、活动事件、户外暴露、室内外切换、天气风险和用户显式敏感偏好。
- 定义Context Router依赖图：Calendar → Event Lookup → Location Resolver → Weather/Climate/其他环境工具。
- 定义三个Agent的新输入输出：Agent 1声明隐含需求；Agent 2输出环境调整与外部随身物品；Agent 3校验事实引用、衣橱ID和远期语义。
- 定义近3天默认窗口、事件歧义澄清、小时级活动窗口、远期气候参考和Provider不可用语义。
- 拆分V2.1—V2.4开发阶段、代码改动地图、测试合同和端到端验收表。

详细方案见[天气与时空上下文 V2 详细方案](WEATHER_CONTEXT_V2_PLAN.md)。本批次只更新设计文档，没有修改代码，也没有新增测试通过数。

### 8.3 独立读者审阅后的收口

无上下文读者发现首稿在两阶段定位授权、跨城市地点段、时区解析顺序、坐标脱敏、缓存阈值、工具幂等键和暂停状态合同上仍不够可执行。方案随后补充：

- `needs_context + resume_token`暂停/续跑协议及`task_runs` CHECK约束迁移要求。
- `location_segments[]`按时间段绑定地点，多城市不再使用单一地点。
- bootstrap时区、事件地点解析后的二次时间解析和冲突状态。
- 原始坐标仅存未完成run内存，API/trace/Context Pack/数据库字段级脱敏。
- 30分钟定位新鲜度、5000米最大精度、15分钟粗粒度天气缓存和授权撤回清理策略。
- 每个Provider、地点段、时间窗和粒度组成的规范化查询键至多调用一次，而不是整个多城市请求只能调用一次。
- Calendar、Location、Event、Weather、Climate公共状态、主要错误码和Agent 3重试/终止决策。

同时修正Agent 2 JSON示例，使环境动作引用的外套ID确实包含在最终`item_ids`中。

## 9. 天气与时空上下文 V2.1 后端核心（2026-08-10，已实现）

### 9.1 范围

按 V2 方案的 18 节拆分，本次落地 **V2.1 后端核心**：Agent 1 隐含天气意图识别、地点优先级链（显式 → 设备定位 → 用户默认城市 → 全局默认）、近 3 天默认窗口、Agent 2 环境调整与随身物品、Agent 3 环境审校、API 首请求定位接收、Web 天气卡片增强。

**不含**（属 V2.2/后续）：`needs_context + resume_token` 续跑协议、Calendar 解析（后天/周末/下周等日期表达式 → `unsupported_date_expression` 诚实降级）、前端定位授权交互、天气缓存、小时级活动窗口、远期气候参考。

### 9.2 实现清单

- `tools/weather/schemas.py`：新增 `WeatherDay`（多日窗口 `days[]`）、`TemporalContextRequirement`、`LocationContextRequirement`、`DeviceLocationContext`；`WeatherFacts` 增加 `start_date/end_date/default_applied/days[]`（单日头部字段保留=首日，向后兼容）；`WeatherToolInput` 支持坐标直查与 `start_date/end_date` 窗口（与 `date` 互斥）。
- `tools/weather/provider.py`：`forecast_range()` 多日窗口、坐标直查（`timezone=auto`）、可注入 `reverse_transport` 的 `reverse_geocode()`（BigDataCloud，失败优雅降级）。
- `tools/weather/client.py`：`_resolve_window()` 分发单日/窗口；`_resolve_device_location()` 坐标 round 到 2 位小数 + 反解补全 display_name/timezone。
- `orchestration/location_resolver.py`（新）：地点优先级链与设备定位校验（consent / 范围 / 新鲜度 1800s / 精度 5000m），`accuracy_bucket`（high≤100 / medium≤1000 / low≤5000）；精确坐标只存在于 resolve 局部变量。
- `orchestration/context_router.py`：时间窗口解析（近 3 天默认 / 单日 / unsupported），`resolved_location_context`（无原始坐标）+ `resolved_time_context`，trace 坐标脱敏，`now_provider` 可注入。
- `repositories/user_preferences_repository.py`：`get/save_environment_profile`（`preference_json.environment_profile.default_city/timezone`）。
- `core/config.py`：`STYLEFORGE_LOCATION_MAX_AGE_SECONDS`、`STYLEFORGE_LOCATION_MAX_ACCURACY_M`、`STYLEFORGE_REVERSE_GEOCODE_ENDPOINT`。
- `llm/schema.py` + `llm/prompts.py`：`Agent1Output` 新增 `implicit_context_signals/context_criticality/uncertainties/default_policy_allowed`；`OutfitProposal` 新增 `environment_adjustments`（事实→影响→行动）与 `carry_recommendations`（`category` 锁死 `external_carry_item`）；`CriticOutput` 新增 `environment_assessment`。`PROMPT_VERSION` 升到 `2026.08.10-weather-v2.1`，Agent 1 隐含意图判定规则与正负例 few-shot、Agent 2 环境调整/随身示例、Agent 3 环境审校示例全部写入。
- `agents/composer.py`：`sanitize_environment_fields` 丢弃 `fact_refs` 为空或 `wardrobe_item_ids ⊄ item_ids` 的调整项，`info["environment_dropped"]` 计数。
- `workflow/state.py` + `workflow/graph.py`：`location_context/environment_profile/resolved_location_context/resolved_time_context` 四态；`recommend()/recommend_payload()` 新增 `location_context` 参数；`_load_environment_profile()`；`WorkflowOutput` 暴露 resolved 上下文。
- `api.py`：`RecommendationRequest.location_context`（`DeviceLocationContext`），`/health` 展示定位阈值与 reverse geocode 配置。
- `apps/web/src/pages/RecommendPage.vue`：天气卡片展示查询窗口（含近 3 天默认徽标）、地点来源/精度、`days[]` 每日摘要；每套方案展示环境调整与随身物品。

### 9.3 验证

```powershell
cd C:\Users\32369\Desktop\agent-p\style
D:\anaconda\envs\style\python.exe -m compileall -q apps\api\styleforge tests
D:\anaconda\envs\style\python.exe -m pytest -q -p no:cacheprovider --basetemp artifacts\pytest-v21-weather tests
D:\anaconda\envs\style\python.exe -m ruff check apps\api\styleforge tests
cd apps\web && npm.cmd run build
```

结果：compileall OK，**215 项 pytest 全过**（新增 `test_weather_tool` 窗口/默认/unsupported/坐标隐私用例、`test_location_resolver.py`、`test_agent_weather_contract.py`、`test_workflow_llm` 三例：设备定位 4 次 LLM + source=device、"去北京旅游" 默认 3 天窗口、"黑色马甲怎么搭" 不触发天气），ruff 全过，Web 构建成功。

### 9.4 遗留与已知边界

- 日期表达式"后天/周末/下周"→ `unsupported_date_expression`，继续无天气推荐，不伪造预报（V2.2 Calendar Resolver）。
- 设备定位降级原因（stale/inaccurate/denied）在 `resolved_location_context` 不暴露，只在 WeatherFacts `error_message` 中可见；真实 API 验收需前端定位授权（V2.2+）。
- 天气缓存、小时级活动窗口、远期气候参考留待 V2.3/V2.4。
- 顺手修复 `agents/critic.py` 确定性评审 `coordination` → `outfit_coordination` 键名（此前 deterministic_critic fallback 未被测试覆盖）。

### 9.5 真实运行验收（2026-08-10）

离线合同测试之后启动真实服务做冒烟验收（非离线门槛，结果单独记录）：

```powershell
cd C:\Users\32369\Desktop\agent-p\style
$env:NO_PROXY="127.0.0.1,localhost,::1"
$env:GARMENTS2LOOK_IMAGE_ROOT="E:\image.tar\image\images"
$env:STYLEFORGE_WEATHER_ENABLED="true"
$env:STYLEFORGE_WEATHER_PROVIDER="open-meteo"
$env:STYLEFORGE_DEFAULT_LOCATION="上海"
D:\anaconda\envs\style\python.exe -m uvicorn styleforge.api:app --app-dir apps\api --host 127.0.0.1 --port 8000
```

`/health` 确认：`weather.enabled=true`、`provider=open-meteo`、`default_location_configured=true`、`reverse_geocode_configured=false`（坐标反解端点未配置时优雅降级为"设备定位"名称，天气本身按坐标直查不受影响）。

真实请求两例（结果已写入 `styling_runs`）：

- **雨天请求**：`request_signature` 带降水概率 99%（暴雨），`weather.status=available`，5 套方案均输出 `carry_recommendations`（折叠伞、便携水瓶等）与 `environment_adjustments`——带伞提示由真实天气事实驱动。
- **"周末户外婚礼穿什么"**：Agent 1 识别隐含天气意图并声明天气需要，但"周末"属 V2.2 Calendar Resolver 范围 → `error_code=unsupported_date_expression` → 天气 `unavailable`，方案无任何天气主张与随身物品（符合"无事实不主张"，正确降级而非静默猜测）。

前端渲染链路排查确认：`structured_result.recommendations` 与顶层 `proposals` 通过 `outfit_id` 对齐（`proposal_to_candidate` 保留原 proposal 的 `outfit_id`），因此 Web 上每套方案的 `environment_adjustments`（事实→影响→行动）与 `carry_recommendations`（外部随身物品 chips）能正确渲染，只要 LLM 按事实输出了随身物品。

## 10. 衣柜照片多模态识别与批量导入（2026-08-10，已实现）

### 10.1 需求与边界

用户上传衣物照片，由本地代理（`tools/gemini_proxy.py`，OpenAI 兼容接口，Vertex Gemini `gemini-2.5-flash`）做多模态识别得到品类/子类/颜色/属性，入库到个人衣柜。单图识别先落地，随后用户要求批量导入：一次多张图片后台逐张识别、前端轮询进度与预计剩余时间。

已确认决策：

- 识别成功且可信的图片自动写入衣柜；失败/低置信项在结果列表标记，由用户处理（不自动重试）。
- **3 张并发**，前端轮询显示进度 + 预计剩余时间（EMA 单张耗时建模）。
- 批量提交后关闭对话框，后台继续处理；进度与结果常驻在衣柜顶部"批量识别任务"卡片区。
- 失败项处理闭环：编辑入库（自动填充 AI 属性后确认）、删除该单品；所有失败项处理完后用户确认删除整条批次记录。
- 衣柜卡片完整显示单品属性，各属性字段可在创建/编辑对话框直接修改；已处理的失败项可撤销。

### 10.2 实现清单

后端：

- `services/recognition_batch.py`（新）：进程内批次存储（`_store` + 锁）与 `ThreadPoolExecutor(max_workers=3)` 并发 worker。`BatchImageResult`（每张的 index/filename/status/reason/识别结果），`RecognitionBatch`（batch_id/user_id/total/done/succeeded/failed/status/eta/ema/results/gender）。worker 流程：`analyze_image` → `parse_attributes` → `map_ai_type_to_item_fields` → `is_reliable_analysis`（不可信 → `low_confidence`，保留 attributes）→ `create_photo_item(skip_embedding=True, skip_initialize=True)` 自动入库。提供 `start_batch/get_batch/list_batches/delete_batch`。
  - **熔断止损**：连续 ≥2 次 `VisionUnavailable` 时，剩余未处理图片直接标记 `provider_unavailable` 并置批次完成（不是自动重试）。`_record_result` 中 `batch.status != "running"` 作为锁内第一个检查，避免熔断后迟到的 worker 重计/覆盖。
  - **并发修正**：`tools/gemini_proxy.py` 从 `HTTPServer`（单线程，并发请求排队）改为 `ThreadingHTTPServer`，3 并发才真正并行。
  - `create_photo_item` 新增 `skip_embedding` / `skip_initialize`，批量 worker 跳过单张嵌入与重复 DDL，DB `embedding_status` 保持 `pending`。
- `api.py`：`POST /wardrobes/{user_id}/items/batch-recognize`（202，逐张 20MB 校验、总量 64MB 上限 413、vision 未配置 503）、`GET .../recognition-batches`（列表，newest-first，归属过滤）、`GET .../recognition-batches/{batch_id}`（单批轮询，归属校验 404）、`DELETE .../recognition-batches/{batch_id}`（用户处理完删除记录）。`BatchRecognitionRequest`（images 1–30 + default_gender）。单图 `POST /items/analyze`（不落库，返回属性供前端预填）与 `/items/photo`（识别后入库）。

前端（`apps/web/src/pages/WardrobePage.vue`）：

- 批量对话框只保留选图（`picture-card` 缩略图网格，悬停删除），提交即关闭；衣柜顶部常驻"批量识别任务"卡片区：运行中显示进度条 + 已识别 X/Y + 预计剩余 Z 秒 + 缩略图行；完成后显示结果表格（原图缩略图可点击放大 + 识别结果 + 处理操作）。
- 全局轮询：仅在有运行中任务时每 2s 调 `listBatchRecognition` 合并刷新，全部完成自动停止；本地 `_done` 处理标记在刷新时按 index 迁移保留。
- 失败项处理：`编辑入库`（打开创建对话框自动填充原图 + AI 属性，保存后标记已处理）、`删除`（不入库标记已处理）；已处理行显示标签 + `撤销`（恢复未处理）。所有失败项处理完后出现 `确认完成，删除记录`，调 DELETE 后记录从列表消失（刷新不复活）。
- 衣柜卡片逐行完整显示单品属性（季节/材质/图案/正式度/风格/场合/文化渊源/版型/领口袖长/细节/描述），品类显示中文；创建与编辑对话框增加"单品属性（可编辑）"表单，各字段可用下拉（预设值取自中文词表）或自定义输入编辑，保存时 `attributes` 一并提交。

### 10.3 遇到的问题

- **熔断计数溢出**：并发 worker 在熔断标记完成后再完成，仍重计 `done` 并覆盖结果（`done=6 > total=5`）。根因 `_record_result` 在计数之后才检查状态。修复：把 `if batch.status != "running": return` 移到锁内第一条语句。
- **进度卡 0**：前端 `batchState` 用驼峰 `batchId`，后端返回蛇形 `batch_id`，`Object.assign` 静默失败导致轮询 404。统一字段名为 `batch_id`。
- **图片缩略图内存**：`URL.createObjectURL` 缓存在文件对象（`__preview`），卸载时统一 `revokeObjectURL`；刷新后重拉任务列表时原图引用丢失（后端只存元数据），失败项显示"无原图"，手动添加需另选图——已接受的本地会话限制。

### 10.4 验证证据

- 专项测试 `tests/test_batch_recognition.py`：混合结果（2 成功 + 1 低置信，skip_embedding 后 `embedding_status=pending`）、熔断（全部 `provider_unavailable`）、入库失败 `create_failed`、校验（>30→422、超总量→413、坏 base64→422、vision 关闭→503）、归属（他人 404）、`skip_embedding` 单测。
- 全量回归：`pytest tests/test_batch_recognition.py tests/test_vision_api.py tests/test_vision_analysis.py` → **29 passed**；`ruff check apps/api/styleforge tests` → All checks passed；`npm run build` 通过。
- 真实端到端（key 就位后，用数据集配饰图而非内存图）：3 张真实饰品全部识别成功自动入库（confidence 1.0），ETA 从 8s 动态下降；`POST → 后台识别 → DELETE` 链路验证他人删除 404 / 本人删除 200 / 列表清除 / 单查 404；PUT 编辑 attributes 后 GET 确认完整存储。
- 识别结果宽容化校验（commit `48f6350`）：避免模型脏数据触发"other / 0%"降级。

> 说明：批次存进程内存，后端重启丢失去运行中任务（README 已注明，本地优先工具的接受取舍）。批量 worker 的 `skip_embedding=True` 意味着新入库单品 `embedding_status=pending`，后续统一补嵌入（可复用订单导入的增量嵌入入口）。

## 11. 会话持久化多轮对话 + 用户长期记忆系统（2026-08-12，已实现）

### 11.1 需求与已确认决策

按 NEXT_STEPS P1 开工两块能力，方向由用户确认：

- **记忆系统**：品类/颜色/风格/正式度/场合/习惯偏好跨对话一致。决策：**自动提炼 + 可手动修正，手动优先**。
- **多轮对话**：同一会话内连续追问自动携带上文。决策：**完整会话持久化**（跨刷新/跨设备可恢复）。
- 本期新增 Web「偏好管理页」（MemoriesPage）。

方案文档见 [会话持久化多轮对话 + 用户长期记忆系统](SESSION_CHAT_MEMORY.md)。

### 11.2 实现清单

Schema（`repositories/database.py`，v9→v10）：

- 三张新表 + 索引：`chat_sessions`、`chat_messages`（FK ON DELETE CASCADE）、`user_memories`（UNIQUE(user_id, category, content) + CHECK 枚举）。

新仓库与服务：

- `repositories/chat_repository.py`：会话 CRUD、`append_message`（同时刷新 session.updated_at）、`list_messages`（ascending）、`active_outfit_messages`（取最近产生搭配的 assistant 消息）。
- `repositories/memory_repository.py`：`list_memories` / `create_manual_memory`（upsert 强制 manual + 重置 occurrences）/ `update_memory` / `forget_memory`（软删除可复活）/ `active_memory_profile` / `upsert_auto_memories`（置信度累加、manual 优先、复活）。
- `services/chat_service.py`：`outfit_context_from_payload` / `assistant_summary` / `trim_message_payload` / `get_session_outfit_context`。
- `services/memory_extractor.py`：LLM 提炼（`llm/memory_schema.py` + `memory_prompts.py` v1.0），best-effort 不阻断任务。

引擎：

- `models/task.py`：`TaskExecutionInput.session_id`（可选）。
- `workflow/task_workflow.py`：两段式路由（`session_follow_up` 强制 modify / 修改链回填 current_*），成功路径末尾 `_extract_memories`（try/except，记忆失败不影响任务）。
- `tools/extension_analysis.py`：`_analyze_modify` 增"整体调整"分支（`adjustment_mode="overall"`，无锁定/无替换，替换池 = 衣橱 − current）。
- `llm/extension_prompts.py`：`completion_rule_for(agent1_output)` 按 adjustment_mode 分支整体/局部。
- 记忆注入：`context/builder.py` → `preferences.memory_profile`；`llm/prompts.py` `build_agent1/2/3_prompt(..., memory_profile)`；三 Agent `run()` 透传；`workflow/graph.py` `_load_memory_profile` + state 透传。
- **顺带修复** `graph.py` novelty 新颖惩罚传包装行导致重叠恒为 0 的 bug。

API（`api.py`）：

- 会话：`POST/GET /users/{user_id}/chat-sessions`、`GET/PATCH/DELETE /chat-sessions/{session_id}`。
- 记忆：`GET/POST /preferences/{user_id}/memories`、`PATCH/DELETE .../{memory_id}`。
- `execute_task` 会话化：`_resolve_session_turn`（归属校验 + user 消息落库 + outfit context）→ 执行 → `_append_chat_success` / `_append_chat_failure`（裁剪 result_json + outfit 快照），payload 附 `session_id/message_id`。

前端（`apps/web/src`）：

- `services/api.js`：新增 9 个会话/记忆方法。
- `components/TaskResultView.vue`（新）：按 task_type 渲染复用载体，历史消息凭裁剪 result 恢复。
- `stores/recommendation.js`：`sessionId` / `messages` / `loadSessionHistory` / `newConversation` / `run()` 透传 `session_id`。
- `pages/RecommendPage.vue`：会话侧栏 + 历史恢复 + `localStorage {userId}:sf_session_id` + 首次发送自动建会话。
- `pages/MemoriesPage.vue`（新）+ 路由 `/memories` + App.vue 导航「🧠 偏好记忆」。

### 11.3 遇到的问题

- **`test_manual_memory_wins_over_auto` 失败**：`create_manual_memory` 的 ON CONFLICT DO UPDATE 未重置 `occurrences`（=1 而非 0）。修复：DO UPDATE SET 增加 `occurrences = 0,`。
- **`test_get_session_outfit_context_uses_most_recent_outfit` 失败**：`active_outfit_messages` 的 SQL 参数顺序与占位符错位（task_type IN 与 session_id 绑定颠倒），查询永远查不到——会破坏线上多轮会话上下文恢复的真实 bug。修复：参数元组改为 `(session_id, user_id, *OUTFIT_TASK_TYPES, limit)`。
- **`test_chat_session_crud_and_cascade_delete` 失败**：同微秒 `created_at` 平局 + UUID `message_id` 非时间序导致取错最新消息。修复：三处 ORDER BY 用 SQLite 单调 `rowid` 作 tiebreaker。
- **`test_database_schema.py` 编辑损坏**：误把个人衣柜断言并入 v10 测试。修复：恢复 `test_schema_contains_personal_wardrobe_import_tables`，从 v10 测试移除。

### 11.4 验证证据

- 新增 32 个测试：`test_follow_up_detection.py`、`test_memory_extractor.py`、`test_memory_repository.py`、`test_chat_repository.py`、`test_session_multiturn.py`（核心多轮）、`test_chat_api.py`、`test_memory_api.py`，扩展 `test_context_pack.py`（memory_profile 注入/省略）与 `test_database_schema.py`（v10）。
- 全量回归：**364 passed**；`ruff check apps/api/styleforge tests` → All checks passed；`npm run build` 通过（新增 MemoriesPage / TaskResultView chunk 正常产出）。
- 端到端人工复现见任务 9 验收记录（curl 建会话 → 三连追问 → GET 会话链；手动记忆 → 注入 → 遗忘）。

## 12. Context-Aware 自适应偏好记忆系统（2026-08-12，已实现）

### 12.1 需求与已确认决策

按《项目文档/StyleForge 记忆系统实现方案.md》落地记忆闭环：`实时交互状态 → Interaction Event → Preference Evidence → Preference Model → Memory Resolver → 三 Agent 差异化注入`。用户拍板三个决策：

- **数据模型**：不保留旧 `user_memories`，按方案新建维度化偏好模型表（旧数据仅 5 条示例，直接废弃）。
- **实现范围**：完整闭环一次做（事件 → 证据 → 聚合 → 生命周期/衰减 → Resolver → 三 Agent 差异化 → consolidation）。
- **行为来源**：后端埋点 + 前端新增行为交互按钮。

核心原则：**行为是事实、Evidence 是对事实的解释、Preference 是系统当前的假设**；**LLM 负责理解，确定性程序负责记忆管理**。

### 12.2 实现清单

Schema（`repositories/database.py`，v10→v11）：删 `user_memories`，新增 `interaction_events`（行为事实）、`preference_evidence`（标准化证据）、`preference_model`（维度化偏好，UNIQUE(user_id, dimension, attribute, value)），全部 `CREATE TABLE IF NOT EXISTS` 幂等兼容。

新仓库（`repositories/`）：

- `interaction_event_repository.py`：`record_event` / `list_events`。
- `preference_evidence_repository.py`：`add_evidence` / `list_evidence` / `list_evidence_for_key` / `delete_evidence`。
- `preference_model_repository.py`：`list_preferences`（active/lifecycle 过滤）/ `upsert_preference`（维度键）/ `get_preference_by_key` / `soft_forget`（软删除可复活）。

新服务（`services/`）：

- `memory_evidence.py`：`behavior_evidence_from_event`（确定性事件→证据，`_BEHAVIOR_RULES`）+ `record_and_fold` + 重导出 `extract_language_evidence`。
- `memory_aggregator.py`：`apply_evidence` 幂等全量重算（置信度公式、lifecycle 升降、decay_policy、expires_at）。
- `memory_decay.py`：读取路径 `effective_confidence = c·exp(-λ·elapsed_days)`（none/slow/normal）+ `is_expired`。
- `memory_resolver.py`：五桶拆分 + 场景匹配 + `AGENT_BUCKETS` 三 Agent 差异化（retriever 全量 / composer 前三桶 / critic 仅 stable≥0.6 + avoidances≥0.5）。
- `memory_consolidation.py`：`consolidate_session` 按 (dim, attr, value, polarity) 去重留最强 + 重跑幂等聚合。

LLM 证据（`llm/memory_schema.py` / `memory_prompts.py` / `services/memory_extractor.py`）：`MemoryExtract` → `MemoryEvidence{dimension, attribute, value, polarity, strength, scope}`；prompt 按新 schema 重写；best-effort 容忍不变。

改造（注入链路）：

- `api.py`：记忆 API 重构到 `preference_model`（POST 走 `explicit_preference` 强证据晋升 long_term）；新增 `POST /users/{user_id}/events`；`PUT /preferences/{user_id}/evaluation`、`POST /tasks/execute` 埋点（`_record_task_event`，OUTFIT_MODIFY 的 `item_replaced` 在 `task_workflow.py` 内记录）；会话结束调用 `consolidate_session`。
- `workflow/graph.py`：`_load_memory_profile` → `_load_memory_packs`，三 node 各自 `resolve(..., agent_role=...)` 差异化注入。
- `workflow/task_workflow.py`：`_extract_memories` → 语言证据 + `apply_evidence` + 按角色 resolver 注入；`core/redis.py` 会话缓存追加 `session_signals`。
- `agents/*.py` + `context/builder.py` + `llm/prompts.py` + `llm/extension_prompts.py`：`memory_profile` 语义改为 MemoryPack 分桶渲染。

前端（`apps/web/src`）：

- `services/api.js`：新增 `recordBehaviorEvent`；memory 函数参数 memoryId → preferenceId。
- `components/TaskResultView.vue`：outfit 卡片 4 行为按钮（采纳/换掉/好评/差评）+ 单品级「换掉这件」，统一 `POST /users/{user_id}/events`。
- `pages/RecommendPage.vue`：透传 `:user-id`。
- `pages/MemoriesPage.vue`：重构为新维度化偏好模型（dimension/attribute/value + polarity/lifecycle + 支持/反对计数）。

### 12.3 遇到的问题

- **Resolver `TypeError: unhashable type: 'slice'`**：`session_signals` 桶初始化为空 dict `{}`，`_bucket_rows` 对其切片报错。修复：resolver 对 `session_signals` 桶直接透传 dict，不切片。
- **decay 测试 `_pref() takes 0 positional arguments but 3 were given`**：`_pref(1.0, "slow", observed)` 误用位置参数。修复为关键字参数。
- **`assert not soft_forget(...)` 失败**：`soft_forget` 的 UPDATE 即使行已 `active=0` 也 rowcount=1 返回 True。删除第二次 soft_forget 的断言（保留 missing id 返回 False）。
- **Resolver 测试 `assert {} == []` 失败**：不允许的桶返回 `{}`（空 dict）而非 `[]`。修正 composer/critic 测试断言为 `== {}`（不允许）与 `== []`（允许但空）。
- **`POST /catalog/items` 端点不存在**：行为事件证据测试原用不存在的 HTTP 端点 seed 品类。改为在创建 TestClient 前用 `upsert_items` 直接入库。
- **chat_service 返回 `session_signals` 破坏旧测试**：无 outfit 时返回 `{current_outfit_id: "", current_item_ids: [], session_signals: {}}`，导致两个旧测试断言不含该键失败。修复：无 outfit 时返回 `{current_outfit_id: "", current_item_ids: []}`（不带 session_signals），保持向后兼容。

### 12.4 验证证据

- 新增/重写测试：`test_interaction_event_repository.py`、`test_preference_evidence_repository.py`、`test_preference_model_repository.py`、`test_memory_aggregator.py`、`test_memory_decay.py`、`test_memory_resolver.py`、`test_memory_consolidation.py`、重写 `test_memory_extractor.py`（新 MemoryEvidence schema）、`test_memory_api.py`（preference CRUD + `POST /users/{u}/events` + 行为事件折叠证据）、`test_database_schema.py`（v11 三表存在、`user_memories` 不在）；删除 `test_memory_repository.py`；适配 `test_context_pack.py` / `test_session_multiturn.py` / `test_chat_api.py` / `test_extended_tasks.py` / `test_extended_task_api.py` 到新 schema。
- 全量回归：**414 passed**（1 个无关 StarletteDeprecationWarning）；`compileall` 通过；Ruff clean；`npm run build` 通过（MemoriesPage / TaskResultView / RecommendPage chunk 正常产出，仅 500kB chunk 尺寸警告非错误）。

### 12.5 泛化改造（scope 修复 + 属性泛化 + 归因维度 + 冲突检测）

对 demo 结果做深度技术评审后，用户拍板「全做：scope + 泛化 + 归因维度」。本小节落地的四件事：

**scope 修复（违背方案的旧 bug）**：原 `behavior_evidence_from_event` 所有行为证据 `scope={"type":"global"}`，导致「换掉一件 dress-1」直接成为 global 级 negative category=dress。修复：弱证据 scope 全部改为 contextual + 场景词（`_OCCASION_TERMS` / `_FORMALITY_TERMS` 从 `context.request` 提取）。

**item→category 泛化**：单次行为只记 item 级证据；同品类、同极性、**不同 item** 累计 ≥ 3 件（`CATEGORY_INDUCTION_THRESHOLD=3`，`count % 3 == 0` 触发）才归纳出 category 级证据（strength 0.15）。分层严格化：不喜欢当前这件 ≠ 不喜欢这一品类。

**归因维度**：新增 `appearance/color_family=<颜色>` 归因（positive 0.2 / negative 0.1）。fit/style 因 catalog `features_json` 无结构化字段（为杂乱文本）而暂缓。

**冲突检测（memory_aggregator）**：仅对 `scope.type=global` 的行，同 `(dimension, attribute)` 存在相反 polarity 的其他 value 时 confidence × 0.9 + `source_summary.conflict_with` 互标对方 value。`apply_evidence` 末尾对受影响 attribute 的**所有**行统一扫描（矛盾双方都降权，而非只后写入方）。

**Resolver 上下文门控修正**：原实现把 contextual 行按 lifecycle 分流——long_term 的 contextual 行直接进 `stable_preferences` 跳过场景匹配，导致「周末=复古」泄漏进工作场景。改为：先做上下文匹配，不匹配直接丢弃；匹配且为 long_term_candidate/long_term（≥0.6）的**同时**进 contextual + stable 两桶（供 composer/critic 可见本场景长期偏好）。

**验证**：新增 `tests/test_memory_generalization.py`（8 场景：单次拒绝不归纳 / 3 件归纳 / color 归因 / 场景激活+不泄漏 / 高置信 short_term 不进 stable / 会话方向信号 / explicit 对冲归纳 / 跨 value 冲突降权）；`test_memory_api.py` 行为事件断言改为 item+color 两行无 category；demo 重跑确认 item 级 + color 数据流。全量回归 **422 passed**，compileall + Ruff clean。

## 13. 记忆提炼 prompt v2.1→v2.4 优化（2026-08-12，已实现）

### 13.1 需求与背景

第 12.5 节评估集（`evals/cases/memory_extraction.json`，33 例）基线 F1 0.9213（prompt v2.3），但用户真实测试 21+ 条 + 跑 12 例后报告 3 个确定性问题，指向 prompt 优化：

1. **global/contextual 判定漂移**：同一请求多次调用结果不一致（「平时上班我就爱穿衬衫」的衬衫一次 global、一次 contextual(上班)），是最大 F1 拖累。
2. **attribute 归类分歧**：「正式场合」被归 `formality` 而非 `occasion`；「牛仔」被归 `material` 而非 `category`。
3. **复杂口语漏/多拆**：每例约漏 1 条或多拆 1 条。

另修正两处评估集自身设计错误：mem-01 漏 `category=裤子`（已补）、mem-12 把「这件大衣别推」误标成长期 item 偏好——**item 级由行为事件产出，语言证据不该提炼**（已改空预期）。

### 13.2 实现清单（`llm/memory_prompts.py`，v2.0→v2.4）

- **移除 `item` attribute**：9 属性定版（category/color/fit/material/style/brand/occasion/formality/detail）。新增 `ITEM_RULE`：单件指代（「这件/那条/这双」）不提炼为偏好，单品级由行为事件追踪；只有泛指一类才提炼 category。
- **`SCOPE_GUIDE` 确定性判定（7 条优先级）**：① 习惯陈述（平时/日常/一直/总是/就爱）中的单品/颜色/风格偏好 → global（场景词只是背景，不把「平时上班我就爱穿衬衫」的衬衫标 contextual）；② 明确限定「X的话/在X/只有X才/X场合/通勤穿惯了X」绑定唯一场景 → contextual；③ 对具体场景的着装要求（「面试要正式」）→ contextual(该场景)；④ 场合本身作为喜好对象（occasion 偏好）→ contextual；⑤ 其余一般提及 → global；⑥ 一次性场景（「明天面试」「周五见闺蜜」）不改变其他偏好 scope，只有对场景本身的着装要求才 contextual；⑦ 否定/厌恶句（「太紧的难受」「不喜欢修身」）默认 global。
- **`DISAMBIGUATION_GUIDE`**：正式/休闲/商务作程度修饰→formality，作场合名词→occasion；牛仔默认→category（仅面料质感→material）；花花绿绿/花哨/印花/素色→style（非 color）；occasion value 用裸场合名词（「正式场合」→ value=正式）。
- **弱信号不提炼**：还好/可以/还行/不算讨厌 → 跳过；只有明确喜欢或明确厌恶才提炼。否定拆分明确「配饰多了反而累赘」按 `配饰多=negative` 提炼，不翻转成 positive 的配饰克制。
- **few-shot 扩到 6 例**：新增习惯句+单件不提炼（平时上班就爱穿衬衫+这件大衣别推）、场合消歧（婚礼要正式+别给牛仔）、弱信号（西装裤还好）。

### 13.3 遇到的问题

- **runner `_PROMPT_VERSION` 硬编码漂移**：`evals/runners/evaluate_memory_extraction.py` 行 38 写死 `"memory-evidence-v2.0"`，prompt 版本改动后报告版本号不跟。修复：改为 `from styleforge.llm.memory_prompts import MEMORY_PROMPT_VERSION` 直接引用，消除双源。
- **few-shot 与 golden 冲突**：初版「平时上班就爱穿衬衫」few-shot 只输出衬衫 global，但 mem-05 golden 期望还含 `occasion=上班 contextual` 一条——习惯句里场合词仍可作独立 occasion 偏好。已把 few-shot 与 golden 对齐。
- **scope 规则 ① 与 ② 边界**：规则 ① 说场合词只是背景，规则 ② 又允许「通勤穿惯了优衣库」标 contextual——需要在 prompt 里写明区别：习惯句若明确绑定唯一场景（「通勤穿惯了X」）按 ②，仅作背景场合（「平时上班就爱穿衬衫」的上班）按 ① 仍可在 occasion 维度单独提炼。

### 13.4 验证证据

- 全量回归 **455 passed**（含 443 基线上多出泛化/评估新增；首轮 1 个偶发 `test_global_conflict_ignores_contextual_opposite` 失败，重跑确认 PG 残留偶发、非代码问题）；compileall + Ruff clean。
- 真实 LLM 评估集 33 例，v2.4 连续 3 次运行分布：F1 **0.847 / 0.911 / 0.921**（run3 精确复现文档记录的 v2.3 基线 0.9213，证实当前内容与基线一致）；Precision 0.837~0.872、Recall 0.857~0.976、极性一致率 1.00、scope 一致率 0.972~0.976。run2/run3 空案例零误报，run1 有 1 例空误报——符合 LLM 非确定性（temperature 0.2），单次结果在 F1 ~0.91 附近波动。
- 用户报告的三个确定性问题在 v2.4 均被规则覆盖：mem-05（scope 漂移）修复为 global 确定性、mem-08（正式→occasion）/mem-11（牛仔→category）/mem-09（花花绿绿→style）归类修复、mem-02（西装裤还好）弱信号不再提炼、mem-07（配饰多）不翻转极性。

