# StyleForge 架构文档

> 当前架构基线：阶段 1（`4c79f83`）把扩展任务迁入 Harness，随后阶段 2 全量退役 legacy
> 三 Agent 链（`workflow/graph.py` + `agents/`）与 `/recommendations`。当前**全部六类任务**
> 都走 **LangGraph 多 Agent Harness** 主链。

## 1. 概述：当前框架与历史框架

本项目迭代了三代执行框架，当前生产链路只走一代：

| 框架 | 用途 | 状态 |
|---|---|---|
| **Multi-Agent Harness**（`agentic/`） | 六类任务主链：OUTFIT_RECOMMEND / OUTFIT_MODIFY / 四个扩展任务 | **当前唯一主链** |
| ~~legacy 三 Agent 链~~（`workflow/graph.py` + `agents/`） | 旧扩展任务 / 推荐链 | **已删除**（阶段 2），确定性推荐改由 `_deterministic_recommend` 直连 |
| ~~Stage 2 残留~~（`agentic/agent.py` AgentLoop、`agentic/shadow.py` ShadowRunner） | 测试期并行验证产物 | **已删除**（44a10b4） |
| ~~p-outfit 评估~~（`evals/runners/evaluate_p_outfit.py`） | 退役评估 runner | **已删除**（44a10b4），结论已存档 `docs/EVALUATION_REPORT.md` |

无 LLM 时行为按任务区分：OUTFIT_RECOMMEND 走确定性推荐（`parse_request → recommend_for_user →
present_result`），OUTFIT_MODIFY 与四个扩展任务 `raise LlmUnavailable`（503）——扩展任务与 modify
一致，legacy `run_extension` 本就没有 fallback。

## 2. 分层架构

```
┌──────────────────────────────────────────────────────────────┐
│  Vue 3 前端 (apps/web)                                        │
│  RecommendPage / WardrobePage / ImportPage / MemoriesPage /   │
│  SettingsPage + TaskResultView / WeatherCard                  │
└───────────────┬──────────────────────────────────────────────┘
                │ HTTP /api
┌───────────────▼──────────────────────────────────────────────┐
│  FastAPI (apps/api/styleforge/api.py)                        │
│  /tasks/execute · /tasks/route · /weather/now                │
│  /preferences/* · /users/*/chat-sessions · /wardrobes/* · /catalog/* │
└───────────────┬──────────────────────────────────────────────┘
┌───────────────▼──────────────────────────────────────────────┐
│  编排层 (orchestration/)                                     │
│  TaskRouter(任务分类) · ContextRouter · LocationResolver      │
│  graph.py（仅服务 /tasks/route，非业务链）                    │
└───────────────┬──────────────────────────────────────────────┘
┌───────────────▼──────────────────────────────────────────────┐
│  工作流层 (workflow/task_workflow.py → MultiTaskWorkflow)     │
│  execute() 按 task_type + LLM 有无分发四条链：                │
│   ① Agentic Modify   ② Agentic Recommend                    │
│   ③ Agentic Extension（4 扩展任务）                           │
│   ④ 确定性 Recommend（无 LLM，直连 parse→recommend→present） │
└───────┬──────────────────────┬──────────────────────┬────────┘
        │                      │                      │
┌───────▼──────────┐  ┌────────▼──────────┐  ┌────────▼─────────┐
│ Multi-Agent      │  │ Multi-Agent       │  │ Multi-Agent      │
│ Harness (modify) │  │ Harness (recommend)│ │ Harness(extension)│
│ (agentic/)       │  │ (agentic/)        │  │ (agentic/)       │
└───────┬──────────┘  └────────┬──────────┘  └────────┬─────────┘
        │                      │                      │
┌───────▼──────────────────────▼──────────────────────▼─────────┐
│  数据层                                                       │
│  PostgreSQL（主，STYLEFORGE_DATABASE_DSN）                    │
│  Redis（可选缓存）· Chroma（可选知识向量）· 本地文件（图像）     │
└──────────────────────────────────────────────────────────────┘
```

## 3. 请求主链路：`/tasks/execute`

`POST /tasks/execute` → `api.execute_task()` → `get_multi_task_workflow().execute(task_input, session_context)`

`MultiTaskWorkflow.execute()` 的完整流程：

1. **路由**：`_route_with_session()` 先按请求单独路由（`TaskRouter.route`），对短跟进请求
   （`is_follow_up`）复用会话 `current_outfit` 重路由为修改任务。agentic 模式下不把会话
   outfit 注入 `task_input`（让 Harness 自己从 `session_context` 解析 target，支持「三个都改」）。
2. **落库**：`start_task_run` 写入 task_runs。
3. **上下文构建**：`ContextPackBuilder.build` 产出 `ContextPack`（用户偏好 + 记忆 profile + 场景）。
4. **四路分发**：

```python
if route.task_type is TaskType.OUTFIT_MODIFY:
    return self._run_agentic_modify(...)        # ① 语义修改必须 LLM
if route.task_type is TaskType.OUTFIT_RECOMMEND and self.llm_client is not None:
    return self._run_agentic_recommend(...)     # ② 有 LLM 走 Harness
if route.task_type is TaskType.OUTFIT_RECOMMEND:
    return self._deterministic_recommend(...)   # ③ 无 LLM 确定性推荐
return self._run_agentic_extension(...)         # ④ 四个扩展任务 → Extension 子图
```

| 条件 | 执行链 | 无 LLM 行为 |
|---|---|---|
| OUTFIT_MODIFY | `_run_agentic_modify` | `raise LlmUnavailable`（语义修改必须 LLM） |
| OUTFIT_RECOMMEND + 有 LLM | `_run_agentic_recommend` | — |
| OUTFIT_RECOMMEND + 无 LLM | `_deterministic_recommend`（`parse_request → recommend_for_user → present_result`） | no-key 契约 `llm_enabled=false` |
| 扩展任务（item/style/wardrobe_*） | `_run_agentic_extension`（Coordinator → Extension 子图） | `raise LlmUnavailable`（与 legacy `run_extension` 一致，无 fallback） |

### 3.1 Agentic Modify（`_run_agentic_modify`）

- 无 LLM 直接 `raise LlmUnavailable("OUTFIT_MODIFY requires an LLM client")`。
- `_agentic_targets` 解析目标：显式 `current_outfit_id`，或「无显式选择」时取全部近期待改候选
  （「三套都改」→ 每个目标一次 `harness.invoke`）。
- 每个目标：`list_items` → `build_facts` → 构建 `Environment(connection, wardrobe_items, facts,
  search_limit=12, web_search_provider=...)` → `_harness(environment, target_candidates=1)`。
- `base_draft` = 当前 outfit 快照（`resolve_active_outfit` 或显式快照）。
- 产出 `agentic_outcome`（单目标为 dict），经 `_agentic_modify_to_result` 包装为
  `OutfitModifyResult` 契约；`completed` 时 `_persist_agentic_candidate` 写入 `candidate_outfits`
  ——**完成后可被下一轮对话按新 outfit_id 重新锚定**（多轮 grounding）。

### 3.2 Agentic Recommend（`_run_agentic_recommend`）

- `execute` 已保证有 LLM；空 `base_draft`（`outfit_id="base", item_ids=[]`）。
- 单次 `harness.invoke` 产出全部 3 个候选：Coordinator → Research（skill/web/weather 各跑一次）
  → Evidence Synthesizer → Stylist×3 共享证据 → 主图门控 → StageCandidate。
- 产出 `agentic_outcome` + `preference_context`（分层偏好视图）+ `environment_context`
  （Research 实际看到的天气事实），前端据此渲染研究依据与上下文。
- `completed` 时 `_persist_agentic_recommend` 持久化候选。

### 3.3 Agentic Extension（`_run_agentic_extension`）

四个扩展任务走 Coordinator → Extension 子图 → closing 节点，不产 outfit 候选，不经过
Environment Gate / Critic / StageCandidate / Goal Gate：

- **确定性事实层预计算**：execute 侧先跑 `analyze_extension_task`（`tools/extension_analysis.py`）
  产出 Agent1 关键事实（`facts` / `candidate_item_ids` / `resolved_target` /
  `needs_clarification`），以 `extension_facts` 放进 invoke 初始 state —— LLM 只做语义增强与
  综合，贴近 legacy Agent1 的「先检索后决策」哲学。
- **Extension 子图**：`extension_agent ↔ tool_step` 循环（`search_wardrobe` /
  `search_knowledge` / `search_web`），`READY` → closing 节点。
- **closing 节点**：`chat_json` 对齐 `RESULT_MODELS` 的 JSON Schema 产出 task result → 跑
  `validate_task_result` + `sanitize_extension_references` + `validate_extension_draft` 硬校验；
  失败折回有界重试（≤2 次）；`needs_clarification`（如 ITEM_ADVICE 锚点不明）确定性胜出。
- **产出**：`extension_result` 经 `_agentic_extension_to_result` 映射，`payload.result` 字段形状
  与 legacy 契约逐字段一致（前端 TaskResultView 不感知迁移）。
- 无 LLM：`raise LlmUnavailable("OUTFIT extension tasks require an LLM client")`（503），
  failed run 仍持久化（legacy 契约移植）。
- 会话多轮：`_route_with_session` 负责短跟进改写（`is_follow_up` → OUTFIT_MODIFY）。

## 4. Multi-Agent Harness（`agentic/`）

`StyleForgeHarness` 是唯一的组装入口，一个对象持有整条 Harness 依赖链：

```
CapabilityRegistry + register_local_tools(8 工具)
        → AgentRuntime(llm, registry, instructions, hooks, visibility, guard,
                       memory_retriever, runtime_capabilities)
        → EvidenceStore
        → compiled Main Graph (build_h2a_main_graph)
```

- `invoke(state)` 是唯一入口；`model_calls` 累计该 Harness 的 LLM 调用数。
- **Runtime 依赖（llm / environment / providers）绝不进序列化状态**（Execution State 只含
  request / drafts / 上下文 / 结果）。

### 4.1 八个本地工具（`agentic/tools/local_tools.py`）

| 工具 | 能力 | 说明 |
|---|---|---|
| `inspect_outfit` | 读 | 查看当前 outfit 快照 |
| `search_wardrobe` | 读 | 衣橱检索（facts + 检索） |
| `search_web` | 读 | 联网搜索（Tavily，`CAP_WEB_SEARCH`） |
| `get_weather` | 读 | 天气（`CAP_WEATHER`） |
| `search_knowledge` | 读 | 知识检索（`CAP_KNOWLEDGE`，Chroma 降级 keyword） |
| `load_skill` | 读 | 加载任务技能（`CAP_SKILLS`，`knowledge/skills`） |
| `modify_outfit` | 写 | 应用 `plan.ops`（replace/remove/add + placement） |
| `update_plan` | 写 | 更新求解计划 |

**能力分层（Layer 2）**：`_runtime_capabilities()` 按实际部署的 provider 生成
`frozenset`（`CAP_WEB_SEARCH/CAP_WEATHER/CAP_KNOWLEDGE/CAP_SKILLS`），`CapabilityRegistry`
据此过滤 Agent 可见的工具集——**工具清单随真实能力变化，不随请求变化**。没有 TAVILY key 时
`search_web` 根本不在目录里，Agent 直接走衣橱检索，不会幻觉「联网搜过」。

### 4.2 Main Graph 编排（`agentic/graph/main.py`）

`build_h2a_main_graph` 组装（H2a 起带 Coordinator 前缀）：

```
START → bootstrap → coordinator
                        ├─ task_state.next_agent=STYLIST  → stylist
                        ├─ task_state.next_agent=RESEARCH  → research ─→ coordinator（证据回传）
                        ├─ task_state.next_agent=EXTENSION → extension ─→ end（不走 outfit 验证链）
                        ├─ NEEDS_CLARIFICATION → clarification → END
                        └─ PROTOCOL_ERROR → END(agent_protocol_error)

stylist subgraph 产出 handoff_result：
    COMPLETED           → environment_gate
    NEEDS_CLARIFICATION → clarification（主图持有，frozen #20）
    PROTOCOL_ERROR      → END

environment_gate ─ valid? ──┬─ 是 → critic
                            └─ 否 → stylist（带着 gate_feedback 重新规划）

critic ─ approved? ─┬─ 是 → stage_candidate
                    └─ 否 → 已达重试上限? ─┬─ 是 → stage_candidate（DEGRADED_ACCEPTED）
                                           └─ 否 → stylist（replan）

stage_candidate → goal_gate ─ enough? ─┬─ 是 → END(done)
                                       └─ 否 → reset_candidate_draft → stylist
```

- **有界修订（frozen #7/#19）**：`MAX_CRITIC_RETRIES = 3`。真实 provider 面对有限衣橱可能陷入
  「类似 → 被拒 → 重试」死循环，重试耗尽后 `route_after_critic` 强制接受物理有效候选，并在
  `gate_feedback` 标注「已达多样性重试上限」。候选状态如实标记为 `DEGRADED_ACCEPTED`，
  **绝不伪装 PASS**（`_CANDIDATE_STATUS_STAGED="STAGED"` / `DEGRADED_ACCEPTED`）。
- **reset_candidate_draft**：新候选从 `base_draft` 重置（recommend=空、modify=原快照），
  **绝不从上一候选继续**；同时重置 Critic 修订预算。
- **clarification_node（H3a-3）**：对城市/日期类问题确定性地写 `pending_field` 到
  `thread_grounding`——下一轮裸回复「上海」仍能被读成目的地（问题→答案→grounding 连续性）。

### 4.3 子图（`agentic/agents/`）

| 子图 | 职责 |
|---|---|
| `coordinator/` | 维护 `TaskState`，分配 `next_agent`（STYLIST / RESEARCH / EXTENSION），持有会话上下文 |
| `research/` | Research 循环：`search_before_ask` 先行，search_web / get_weather / load_skill 各跑一次 |
| `research_synthesizer` | 独立 profile，将 `raw_evidence`（子图内私有缓冲）合成 `ResearchEvidence` |
| `stylist/` | Stylist 循环：工具调用 + 候选产出（`CANDIDATE_READY` / `NEED_USER` / `CONTINUE`） |
| `critic/` | 主图验证节点：approved / issues / feedback |
| `extension/` | 扩展任务子图：`extension_agent ↔ tool_step` 循环 + closing 节点（综合 + 硬校验 + 有界重试） |

`research` 节点在主图内包一层：`raw_evidence` 缓冲留在子图内部（frozen #18），产物经
`EvidenceStore` journal 供追踪审计（Runtime 依赖，不进状态）。

## 5. Agent 上下文层（H3a）

`agentic/context/` 负责「给 Agent 喂什么」，四块核心：

| 模块 | 职责 |
|---|---|
| `grounding.py` | `GroundingResolver`：search-before-ask（缺省地点/日期时先搜再问）+ `pending_field` 连续性 |
| `thread_preferences.py` | `ThreadPreferenceView`：会话内偏好视图 + **scope gate**（单轮请求绝不提升到 profile） |
| `memory_context.py` + `prompt_assembler.py` | `PreferenceRetriever` 分层 Top-K 召回 → 拼进 Stylist prompt |
| `wardrobe_index.py` | `WardrobeIndexSummary`：衣橱 262KB 原始信息压缩为 ~1KB 摘要喂给 Agent |

- `visibility.py` `ContextVisibilityPolicy`：各 Agent 可见性过滤（`trajectory_*` 等私有字段不跨
  子图泄漏）。
- `guard.py` `ContextGuard`：上下文截断，防超长。
- `evidence_store.py` `EvidenceStore`：Research 产物 journal，trace/审计用。
- `assembler.py`：组装各层为执行状态上下文。

## 6. 记忆系统（多轮对话）

链路：**提取 → 应用 → 召回**，详见 `docs/SESSION_CHAT_MEMORY.md`。

- **提取**：`_extract_memories`（每条 agentic 执行后）→ LLM `extract_language_evidence` 蒸馏偏好证据。
  单轮/短跟进请求被 `_scope_gate` 拦截，只留在 ThreadPreferenceView，**不写入 profile**。
  提取失败静默吞掉（best-effort，绝不让任务失败）。
- **应用**：`apply_evidence` 聚合进 `preference_evidence` / `preference_model`。
- **召回**：`PreferenceRetriever` 分层 Top-K（短期/场景/长期/避免，≤ 8 行）——recommend 与
  modify 共享一个 retriever；Thread 层留在 `thread_context`，**两条链绝不互相提升**。
- 会话锚定：`api.py` 在用户消息后更新 `current_outfit_id/item_ids` + `session_signals`；
  `_thread_context` 把这些带进 Harness；`candidate_outfits` 让下一轮可 `outfit_id` 重新锚定。

## 7. 降级与失败策略

| 场景 | 行为 |
|---|---|
| OUTFIT_MODIFY 无 LLM | `LlmUnavailable`（不降级——语义修改无 LLM 无意义） |
| OUTFIT_RECOMMEND 无 LLM | `_deterministic_recommend`（`parse_request→recommend_for_user→present_result`），`llm_enabled=false` |
| 扩展任务无 LLM | `LlmUnavailable`（与 legacy `run_extension` 一致，failed run 仍持久化） |
| search_web 无 TAVILY key | 工具不在 Agent 目录（Layer 2），链条照常走衣橱检索 |
| get_weather 无 provider | 工具不在目录，观察返回 `unavailable` |
| search_knowledge 无 Chroma | 降级纯 keyword 检索 |
| Critic 连续拒绝 ≥3 次 | `DEGRADED_ACCEPTED` 强制接受物理有效候选（诚实标注，非伪 PASS） |
| 记忆提取异常 | 吞掉，任务照常成功 |
| 环境 profile 缺失 | 回退 global default city |
| `/weather/now` 无定位无默认城市 | 返回 `unavailable` + `error_code=location_required`（诚实降级） |

## 8. API 路由清单（`api.py`）

| 路由 | 说明 |
|---|---|
| `POST /tasks/route` | 纯路由（不执行），返回 task_type + 置信度 |
| `POST /tasks/execute` | 主执行入口（本架构核心） |
| `GET /weather/now` | 从 `get_multi_task_workflow().weather_provider` 构建 `WeatherTool` 直连，`unavailable` 契约 |
| `GET /users/{id}/chat-sessions` / `GET /chat-sessions/{id}` | 会话 + 消息历史 |
| `POST /chat-sessions/{id}/messages` | 写入消息 + 更新会话 outfit 锚点 |
| `GET/PUT /preferences/*` | 用户偏好 |
| `/wardrobes/*`、`/catalog/*`、`/items/*`、`/items/{id}/image` | 衣橱 / 商品目录 / 单品与图片 |

## 9. 数据层

- **PostgreSQL（主库）**：`STYLEFORGE_DATABASE_DSN`（SQLite 文件路径已退役）。`repositories/`
  下 21 个仓库，覆盖 task_runs、candidate_outfits、catalog、wardrobe、preference_evidence、
  preference_model、chat、import_run、interaction_event 等。
- **Redis（可选缓存）**：`STYLEFORGE_REDIS_ENABLED/URL/TTL`，`core/redis.py`。
- **Chroma（可选知识向量）**：`STYLEFORGE_CHROMA_DIR`，降级纯 keyword。
- **本地文件**：商品图片 / 数据集源。

## 10. 前端（`apps/web`）

- 页面：`RecommendPage`（主推荐 + 会话）、`WardrobePage`、`ImportPage`、`MemoriesPage`、
  `SettingsPage`。
- 组件：`TaskResultView`（渲染结果契约）、`WeatherCard`（天气卡片）。
- `services/api.js`（`/tasks/*`、`/preferences/*`、会话）＋ `services/user.js`。
- RecommendPage 直达 `item_advice` 等扩展任务请求，经 `/tasks/execute` 走 Extension 子图；
  `api.js` 已删除无调用者的 `recommend()` 死代码。

## 11. 测试与验证

- `tests/` 全量回归（当前 684 用例全绿），`tests/llm/fake_llm.py` 脚本化 Fake 支撑无网络测试。
- 无 legacy 模式开关（`STYLEFORGE_*_MODE` 已删除），全部测试默认跑 Harness 主链。
- 专项：`test_agentic_modify_primary.py`（主修改链端到端）、`test_agentic_recommend_primary.py`、
  `test_agentic_extension_primary.py`（四个扩展任务 Harness 链）、`test_agentic_multi_outfit.py`（多目标）、
  `test_session_multiturn.py`（多轮，rewrite 为 agentic 脚本）、`test_weather_now_api.py`。
- 评估：`evals/runners/`（follow_up / memory_extraction / task_routing 独立评估保留）。

## 12. 目录导览（`apps/api/styleforge/`）

| 目录 | 内容 |
|---|---|
| `agentic/` | **当前主框架**：harness / graph / agents（coordinator / research / stylist / critic / extension）/ context / gates / runtime / tools / instructions |
| `workflow/` | `task_workflow.py`（四路分发核心，唯一工作流模块） |
| `orchestration/` | TaskRouter / ContextRouter / LocationResolver + `graph.py`（仅服务 `/tasks/route`） |
| `services/`（18） | 记忆聚合 / 提取 / 衰减 / 语义检索 / 视觉搜索 / 订单导入 / 推荐 / 展示等 |
| `core/` | config / request_parser / rubric / scoring / decision / slots / taxonomy / relaxation / candidate_service |
| `models/` | task / task_results / context / agent_tasks / agentic_contract（共享契约，不可删） |
| `repositories/`（21） | 数据访问层 |
| `data/` | 数据集加载（mytheresa / garments2look / outfits / p_outfit 数据） |
| `tools/` | weather / web_search / registry / 扩展分析 / 扩展校验（extension_*） |
| `llm/` | DeepSeek 客户端 + prompts + memory schema |
| `integrations/` / `knowledge/` / `vision/` / `common/` | 外部集成 / 知识检索 / 视觉 / 公共工具 |

## 13. 已退役内容（历史参考）

- **legacy 三 Agent 链**（`workflow/graph.py` + `workflow/state.py` + `agents/`：
  SemanticRetriever / Composer / Critic + `_build_graph` 节点）+ `tools/basic_validation.py` +
  `tools/candidate_pool.py`：阶段 2 全量删除。确定性推荐不再走图，改 `_deterministic_recommend`
  直连；候选池逻辑迁入 `core/candidate_service.py`。
- **`POST /recommendations` + 前端 `recommend()`**：阶段 2 删除（无调用者）。
- **Stage 2 AgentLoop / ShadowRunner**（`agentic/agent.py`、`agentic/shadow.py`、
  `agentic/reviewer.py`、`tools/smoke_agentic.py`）：测试期并行验证产物，44a10b4 已删。
- **p-outfit 评估**（`evals/runners/evaluate_p_outfit.py` / `evaluate_extend.py`）：退役，结论见
  `docs/EVALUATION_REPORT.md`。
- **v3.3 Task Router / Planner / Slot Retriever / Stylist / Reviewer agents**：已被本架构取代，
  不再存在。
