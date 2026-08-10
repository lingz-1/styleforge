# StyleForge 开发过程记录

> 更新时间：2026-08-10

本文按开发批次记录“为什么改、如何设计、实现顺序、遇到的问题和验证证据”。当前能力结论以[项目状态](PROJECT_STATUS.md)为准；具体故障按编号收录在[问题与解决记录](ISSUE_LOG.md)。

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
