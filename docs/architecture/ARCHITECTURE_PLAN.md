# StyleForge 架构规划 v3.3-plan.1

> 版本：v3.3-plan.1 | 更新：2026-08-06
> 定位：在 v3.3-plan 基础上做一次架构收口，定死职责边界后开工。
> 来源：《项目文档/参考目录.txt》《项目文档/扩展版方案.txt v3.3-extension》《评估方案.txt》
> 核心原则：Agent ≠ Service ≠ Retriever ≠ Tool ≠ Validator；Task Router / Retriever / Validator / Memory / RAG / MCP 都是系统能力，不是新 Agent。

## 1. 决策记录（v3.3-plan.1 收口）

| 决策项 | 结论 |
|---|---|
| 后端结构 | 务实分层；**保持 `styleforge` Python package namespace**（物理位置移到 `apps/api/styleforge/`，import 不变） |
| Web 前端 | Vue 3 + Vite + Pinia + Vue Router + Element Plus + Axios |
| 移动端 | 微信小程序（接口见 `docs/WARDROBE_MOBILE_API.md`） |
| 知识库 | 引入 RAG（`knowledge/styles|items|brands|palettes` + Chroma，文本 embedding） |
| 评估体系 | `evals/`（离线 benchmark）与后端 `scoring/`（运行时五维）**严格区分** |
| 数据库 | 当前 SQLite；**Repository Protocol 现在就建**，PostgreSQL 后期只换实现 |
| 任务路由 | **TaskType 只由 Task Router 负责**；Agent 1 不做任务分类 |
| 衣橱检索 | 走 `WardrobeRetriever`（Orchestrator 执行），**不设 wardrobe_search Tool** |
| Tool Registry | 只管理真正的外部调用（Weather 等）；"LLM 决策、程序执行" |
| Weather | 当前是 **Weather Tool**（Open-Meteo HTTP）；实现 MCP 协议前不叫 MCP |
| 共享包 | 不引入 pnpm monorepo；前后端通过 FastAPI JSON API 对接 |
| 部署 | 不用 Docker；Nginx + systemd |

## 2. 目标架构（Context-Aware Multi-Agent Decision Graph）

六层：Frontend → FastAPI Gateway → LangGraph Orchestrator → 三 Agent → Capability Layer → Data Layer。

```
styleforge/
├── apps/
│   ├── api/
│   │   └── styleforge/                  # 保留 Python package 名（import 不变）
│   │       ├── main.py
│   │       │
│   │       ├── api/                     # Gateway：HTTP 接口 + API Schema
│   │       │   ├── routes/
│   │       │   │   ├── wardrobe.py / outfits.py / recommendation.py
│   │       │   │   ├── feedback.py / compatibility.py / preferences.py
│   │       │   │   └── websocket.py
│   │       │   └── schemas/             #   HTTP Request/Response/WebSocket Event
│   │       │
│   │       ├── agents/                  # ◆Core：仅 3 个 LLM Agent（小包结构）
│   │       │   ├── semantic_retrieval/  #   agent.py / prompt.py / schema.py
│   │       │   ├── outfit_composer/     #   agent.py / prompt.py / schema.py
│   │       │   └── outfit_critic/       #   agent.py / prompt.py / schema.py
│   │       │
│   │       ├── orchestration/           # ◆Core：LangGraph Orchestrator
│   │       │   ├── graph.py             #   主链路 + 多任务子图
│   │       │   ├── state.py             #   StyleForgeState
│   │       │   ├── task_router.py       #   ★TaskType 分类（唯一职责）→ 子图
│   │       │   ├── context_router.py    #   ★Context Requirements → Tool Registry
│   │       │   └── retry_controller.py  #   retry_count 限流
│   │       │
│   │       ├── retrieval/               # ◆Core：Capability - 检索（Orchestrator 执行）
│   │       │   ├── wardrobe_retriever.py
│   │       │   ├── style_retriever.py   #   ★EXT RAG
│   │       │   ├── item_retriever.py    #   ★EXT RAG
│   │       │   ├── query_builder.py
│   │       │   ├── candidate_merger.py
│   │       │   └── novelty_reranker.py
│   │       │
│   │       ├── scoring/                 # ◆Core：运行时五维（产品逻辑）
│   │       │   ├── rubric.py / weights.py / scoring.py / projections.py
│   │       │
│   │       ├── validators/              # ◆Core：确定性校验（不是 Agent）
│   │       │   └── outfit_validator.py  #   存在性/归属/重复/槽位/锁定单品/Schema
│   │       │
│   │       ├── models/                  # ◆Core：领域对象（Domain Model）
│   │       │   ├── wardrobe.py / outfit.py / request.py / preference.py / feedback.py
│   │       │
│   │       ├── context/                 # ★EXT：Context Pack（先于 RAG 建立）
│   │       │   └── builder.py           #   request/environment/knowledge/user + source 溯源
│   │       │
│   │       ├── knowledge/               # ★EXT：RAG 库代码（API 只消费 Index）
│   │       │   ├── ingestion.py / chunking.py / schemas.py
│   │       │   └── indexer.py
│   │       │
│   │       ├── tools/                   # ★EXT：外部工具（LLM 决策、程序执行）
│   │       │   ├── weather/             #   provider.py / client.py / schemas.py
│   │       │   └── registry.py          #   Tool Registry（参数校验）
│   │       │
│   │       ├── memory/                  # ★EXT：记忆
│   │       │   ├── request_history.py / outfit_history.py
│   │       │   ├── preference_memory.py / feedback_memory.py / exposure_tracker.py
│   │       │
│   │       ├── services/                # ★EXT：业务服务（多任务子图入口）
│   │       │   ├── recommendation_service.py
│   │       │   ├── modification_service.py
│   │       │   └── compatibility_service.py
│   │       │
│   │       ├── repositories/            # 数据访问（Protocol 现在建，SQLite→PG 后期换实现）
│   │       │   ├── protocols.py
│   │       │   └── sqlite/
│   │       │       ├── wardrobe_repository.py / outfit_repository.py
│   │       │       ├── feedback_repository.py / preference_repository.py
│   │       │       └── ...
│   │       │
│   │       ├── integrations/            # 第三方适配
│   │       │   ├── llm/                 #   deepseek（OpenAI 兼容）
│   │       │   ├── embeddings/          #   ★RAG 文本 embedding（FashionCLIP 之外）
│   │       │   ├── vectorstores/        #   chroma（RAG）；faiss（衣柜，见 retrieval）
│   │       │   └── storage/             #   本地图片
│   │       │
│   │       ├── observability/           # tracing / trace_id / token_usage
│   │       └── core/                    # config / exceptions / constants
│   │
│   ├── web/                             # Vue 3 + Vite + Element Plus
│   │   └── src/                         #   pages / features / components / services / stores
│   │
│   └── miniprogram/                     # 微信小程序（API 稳定后接入）
│
├── knowledge/                           # ★EXT：RAG 知识资产（markdown/JSON）
│   ├── styles/  items/  brands/  palettes/
│
├── evals/                               # 离线系统评估（与 scoring/ 严格区分）
│   ├── cases/  runners/  metrics/  reports/
│
├── tests/                               # 程序正确性（tests ≠ evals）
│   ├── unit/  integration/  api/  graph/  fixtures/
│
├── scripts/
│   ├── data/                            # 数据脚本
│   └── knowledge/                       #   build_index.py / update_index.py（离线构建 Index）
│
├── deploy/                              # 部署配置（不用 Docker）
│   ├── nginx/                           #   styleforge.conf
│   └── systemd/                         #   styleforge-api.service
│
├── docs/                                # architecture / api / evaluation / development
├── data/  artifacts/                    # 不变（gitignore）
└── pyproject.toml                       # package path 指向 apps/api/styleforge
```

> ◆CORE = v3.2.1 核心；★EXT = v3.3 扩展（挂载在 Agent 周围，不新增 Agent）。

## 3. 职责边界（v3.3-plan.1 定死）

### 3.1 Task Router vs Agent 1
```
User Request → Task Router → TaskType → 选择子图
                                            ↓（需要语义检索时）
                                     Semantic Retrieval Agent
                                     （只负责语义理解 / Context Requirements / Request Signature / Retrieval Plan）
```
- TaskType 分类**只由 Task Router 负责**；Agent 1 不重复分类。

### 3.2 Retriever vs Tool
- 衣橱检索 = `WardrobeRetriever`（Orchestrator 按 Retrieval Plan 执行），**不是 Tool**。
- `Tool Registry` 只管理真正的外部调用（Weather、未来商品搜索等）。
- 删除 `wardrobe_search` Tool 的概念，避免与 Retriever 职责打架。

### 3.3 scoring vs evals（严格区分）
| | 位置 | 含义 |
|---|---|---|
| scoring/ | apps/api/styleforge/scoring/ | **运行时**五维 Rubric / 权重 / 打分逻辑 |
| evals/ | 根目录 | **离线** benchmark cases / runner / metrics / reports |

### 3.4 三层 Schema
| 层 | 位置 | 含义 |
|---|---|---|
| API Schema | `api/schemas/` | HTTP Request / Response / WebSocket Event |
| Agent Schema | `agents/*/schema.py` | LLM Structured Output |
| Domain Model | `models/` | Wardrobe / Outfit / Preference 等领域对象 |

### 3.5 Weather：Tool 不是 MCP
- 当前：`tools/weather/{provider,client,schemas}.py` → Open-Meteo，是 **Weather Tool**。
- 只有真正实现 MCP 协议（`mcp/weather_server/`）才叫 MCP。不做协议不挂 MCP 名字。

### 3.6 Memory 版本演进
- v3.2.1 已完成：Recent Outfit / Exposure / Cross-request Novelty Memory（"最近穿过什么"）。
- v3.3 新增：Explicit Feedback Memory / Preference Memory（"用户明确喜欢/不喜欢什么"）。

## 4. 当前 → 目标迁移映射

| 当前文件 | 目标位置 | 动作 |
|---|---|---|
| `styleforge/api.py` | `apps/api/styleforge/api/routes/*.py` + `main.py` | **拆分** |
| `styleforge/agents/*.py` | `apps/api/styleforge/agents/*/{agent,prompt,schema}.py` | 移动 + 小包化 |
| `styleforge/workflow/*.py` | `orchestration/`（graph/state/task_router/context_router/retry_controller） | 移动 + 新增路由 |
| `styleforge/llm/` | `integrations/llm/` + `agents/*/schema.py` | 移动 |
| `styleforge/core/schemas.py` | `api/schemas/` + `agents/*/schema.py` + `models/` | 拆分 |
| `styleforge/core/rubric.py` | `scoring/rubric.py` | 移动 |
| `styleforge/core/scoring.py` | `scoring/scoring.py` | 移动 |
| `styleforge/services/semantic_retrieval.py` | `retrieval/` | 移动 |
| `styleforge/tools/candidate_*.py` | `retrieval/` + `services/` | 移动 |
| `styleforge/repositories/` | `repositories/protocols.py` + `repositories/sqlite/` | 移动 + 抽象 |
| `styleforge/ui.py` | `apps/web/` | **重写为 Vue** |
| `styleforge/pipelines/` | `scripts/data/` | 移动 |

> **P1 原则：保持 `from styleforge.xxx import` 不变，只移动物理位置（package path 指向 `apps/api/styleforge/`），避免全项目 import 重构。**

## 5. 技术栈（定稿）

| 层 | 技术 |
|---|---|
| 后端 | Python 3.10+ · FastAPI · LangGraph · Pydantic v2 |
| 数据库 | SQLite（当前）→ PostgreSQL（后期，Repository Protocol 已隔离） |
| LLM | DeepSeek（OpenAI 兼容，`.env`） |
| 衣柜检索 | FashionCLIP + FAISS |
| **RAG 检索** | 文本 Embedding Model + **Chroma**（与 FAISS 边界明确） |
| Web | Vue 3 + Vite + Pinia + Vue Router + Element Plus + Axios |
| 小程序 | 微信原生 |
| Web 服务器 | Nginx（静态 + `/api` 反向代理） |
| 部署 | 不用 Docker；systemd 服务 |

### 5.1 实施前提
| 前提 | 说明 |
|---|---|
| PostgreSQL | （后期）迁移时新增 `repositories/postgres/` 实现，Service 层不变 |
| Node.js | Vue 构建（`npm run dev` / `npm run build`） |
| Nginx + systemd | `deploy/nginx/styleforge.conf` + `deploy/systemd/styleforge-api.service` |
| Chroma + RAG embedding | P4 时确定文本 embedding model |

## 6. 分阶段实施路线（v3.3-plan.1）

| 阶段 | 内容 | 完成判据 |
|---|---|---|
| **P1** | 后端目录重构（保持 `styleforge` namespace）+ Repository Protocol + 三层 Schema 落地 | 134 测试全绿；import 不变；路由可访问 |
| **P2（已验证）** | **Task Router + 多任务 Graph Skeleton**（TaskType 六类 → 子图） | 请求能路由到正确子图 |
| **P2.5（路由切片已验证）** | **`evals/` baseline**：30-50 固定 Cases + Wardrobe Fixtures + 五维评分 + Task Routing Accuracy | 建立 v3.2.1 baseline，供后续对比 |
| **P3（核心已完成）** | **Context Pack** 共享领域契约；Context Router / Tool Registry 留给外部工具接入 | 请求、当前搭配、衣橱、偏好、记忆、候选新品和证据进入统一结构 |
| **P4（本地检索已完成）** | **Style/Item grounded retrieval** + 可追溯 Markdown 知识资产；向量化索引为后续增强 | 美拉德/American Vintage/Cowboy Boots 等条目有本地证据 |
| **P5（V1已验证）** | **Weather Tool**（Open-Meteo + Location Resolution）；需要展示 MCP 时再做真 MCP Server | 显式地点与今天/明天/ISO日期能查日级天气并影响推荐 |
| **P5.1—P5.4（已规划）** | 隐含天气识别、设备定位、完整时间语义、小时天气、Event Lookup、远期气候参考和可解释随身建议 | 自然语言无需显式提天气；输出可追溯的“事实→影响→行动”，详见天气V2方案 |
| **P6（核心已完成）** | **Vue Web MVP + API** | 衣橱、推荐、扩展任务与 Trace 可操作 |
| **P7** | **Feedback + Preference Memory** + 前端 Feedback UI | 用户反馈影响后续推荐（闭环） |
| **P8（已完成）** | **Outfit Modify + Compatibility + Wardrobe Gap**（子图 + 锁定槽位）+ Web UI | "换双鞋"只改目标单品；新品兼容和衣橱缺口可解释 |
| **P9（核心已完成）** | **微信小程序**（复用稳定 API） | 衣柜、拍照、订单、推荐和五类扩展任务可操作 |
| **后期** | Brand Knowledge、PostgreSQL、Nginx 正式部署、`evals/` 系统评估（对比 RAG/Weather/Memory 增益） | 生产就绪 + 实验报告 |

> **evals 提前到 P2.5**：给后续每次扩展（RAG/Weather/Memory）留对比基线，最终能报告"Style RAG 将 style-specificity 从 X 提升到 Y"。

### 6.1 P2 实现记录（2026-08-09）

- 新增 `styleforge/orchestration/task_router.py`：`TaskType` 六类、显式上下文优先、可审计确定性规则、默认推荐降级和局部修改槽位提取。
- 新增 `styleforge/orchestration/graph.py`：LangGraph 路由节点与六个隔离的子图占位节点；占位节点只报告 `routed`，不冒充已实现业务能力。
- 新增 `POST /tasks/route`：返回任务类型、目标子图、置信度、路由原因、所需能力和 trace；保留 `/recommendations` 现有行为。
- 新增代表性中英文路由测试和六分支图测试。修复 Pytest 保留参数名冲突及“只换外套”漏判后，`compileall`、31 个定向测试、165 个全量测试和 Ruff 均通过。

### 6.2 P2.5 路由评估切片（2026-08-09）

- `evals/cases/task_routing.json` 固化 42 条中英文任务用例，六类任务各 7 条，避免类别不均衡掩盖少数类错误。
- `evals/runners/evaluate_task_routing.py` 输出总准确率、逐类准确率、混淆矩阵、失败样本和评估限制，并将报告写入 `artifacts/evaluation/`。
- `evals/` 与运行时 `styleforge/core/scoring.py` 保持分离；路由准确率不能替代五维穿搭质量。
- 路由 runner 已验证 42/42 正确，六类逐类准确率均为 100%，失败 0。P2.5 尚未全部完成：Wardrobe Fixtures 和五维固定穿搭 benchmark 仍待实现。

### 6.3 五类扩展业务实现记录（2026-08-09）

- `models/context.py` 与 `context/builder.py` 建立 Context Pack：请求路由、当前搭配锁定、衣橱统计、五维偏好、最近记忆、候选新品和带来源证据使用同一契约。
- `knowledge/` 使用索引元数据和 Markdown 分段资产实现本地、可追溯事实检索；知识是 Agent 依据，不是独立结果生成器。
- `MultiTaskWorkflow` 将五类扩展统一接入现有 Agent 1/2/3；三者成功时各真实调用模型一次，不存在扩展任务确定性结果降级。
- 事实工具只解析衣橱、锚点、知识、候选和目标元素；Validator 只执行 ID 白名单、锁定槽位、证据来源、新品不落库和链接边界检查。
- 局部修改硬锁所有非目标槽位；单品搭配以衣橱锚点组合；衣橱缺口区分整体模式和目标风格模式。
- FastAPI、Vue Web 和微信小程序均通过主推荐自然语言入口自动路由。完整契约和限制见[扩展任务业务与 API](../EXTENDED_TASKS.md)。

### 6.4 P5 Weather V1实现与V2规划（2026-08-10）

- V1已实现并验证：Agent 1声明`context_requirements.weather`，Context Router调用typed Open-Meteo Tool；天气事实进入三个Agent、Context Pack、trace、持久化和Web结果。
- 当前V1仅支持显式地点或全局默认城市、今天/明天/ISO单日、未来16天内日级事实，不等于完整的隐含时空理解。
- V2已完成方案设计但尚未实现：本地请求使用用户授权的本次设备定位，显式目的地和事件场馆优先于当前位置；增加近3天默认窗口、相对时间/节日、小时天气、Event Lookup、远期气候参考和环境建议grounding。
- V2仍坚持三个主Agent和“工具只给事实”；随身物品由Agent 2生成、Agent 3审校，不新增确定性结果Service。详细合同、场景矩阵和分期见[天气与时空上下文 V2 详细方案](../WEATHER_CONTEXT_V2_PLAN.md)。

## 7. 风险与注意

- **P1 重构**：纯移动 + import 不变；逐目录迁移 + 每步跑测试；`pyproject.toml` package path 指向 `apps/api/styleforge/`。
- **Vue**：需 Node.js；与 FastAPI 的 CORS / Vite 代理。
- **RAG 范围**：knowledge 资产从少量风格/单品起步，避免过度投入。
- **Weather 命名**：不实现 MCP 协议就不叫 MCP，避免技术栈展示失真。
- **小程序**：开发者工具「不校验合法域名」+ 局域网后端，无需正式域名。
