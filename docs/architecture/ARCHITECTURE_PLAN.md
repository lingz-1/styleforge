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
| **P2** | **Task Router + 多任务 Graph Skeleton**（TaskType 六类 → 子图） | 请求能路由到正确子图 |
| **P2.5** | **`evals/` baseline**：30-50 固定 Cases + Wardrobe Fixtures + 五维评分 + Task Routing Accuracy | 建立 v3.2.1 baseline，供后续对比 |
| **P3** | **Context Pack + Context Router + Tool Registry**（统一扩展接入接口） | 外部信息统一入 Context Pack 且带 source 溯源 |
| **P4** | **Style/Item RAG** + Knowledge Index Pipeline（`scripts/knowledge/` 离线构建） | 美拉德/American Vintage/Cowboy Boots 有知识依据 |
| **P5** | **Weather Tool**（Open-Meteo + Location Resolution）；需要展示 MCP 时再做真 MCP Server | "明天纽约户外活动穿什么"能查天气并影响推荐 |
| **P6** | **Vue Web MVP + API v1** | Web 端功能齐平 Streamlit（Wardrobe/Chat/Recommendation/Trace） |
| **P7** | **Feedback + Preference Memory** + 前端 Feedback UI | 用户反馈影响后续推荐（闭环） |
| **P8** | **Outfit Modify + Compatibility**（子图 + 锁定槽位）+ 对应 Web UI | "换双鞋"只改目标单品；"这件大衣搭吗"可答 |
| **P9** | **微信小程序**（复用稳定 API） | 衣柜/拍照/订单导入可用 |
| **后期** | Brand Knowledge、PostgreSQL、Nginx 正式部署、`evals/` 系统评估（对比 RAG/Weather/Memory 增益） | 生产就绪 + 实验报告 |

> **evals 提前到 P2.5**：给后续每次扩展（RAG/Weather/Memory）留对比基线，最终能报告"Style RAG 将 style-specificity 从 X 提升到 Y"。

## 7. 风险与注意

- **P1 重构**：纯移动 + import 不变；逐目录迁移 + 每步跑测试；`pyproject.toml` package path 指向 `apps/api/styleforge/`。
- **Vue**：需 Node.js；与 FastAPI 的 CORS / Vite 代理。
- **RAG 范围**：knowledge 资产从少量风格/单品起步，避免过度投入。
- **Weather 命名**：不实现 MCP 协议就不叫 MCP，避免技术栈展示失真。
- **小程序**：开发者工具「不校验合法域名」+ 局域网后端，无需正式域名。
