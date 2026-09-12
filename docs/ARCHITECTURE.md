# StyleForge 架构文档

> 本文档描述**当前**系统的架构（全部六类任务走 Multi-Agent Harness 主链，legacy 已全量退役）。
> 更早的执行框架与演进见 git 历史：`44a10b4`（退役 Stage 2 残留）→ `4c79f83`（扩展任务迁入
> Harness）→ `84c7f13`（legacy 三 Agent 链 / `/recommendations` 全量删除）。

## 1. 系统总览

StyleForge 是一个基于衣橱的穿搭推荐系统：用户以自然语言提出需求（推荐 / 改搭 / 单品建议 /
风格建议 / 兼容性评估 / 衣橱缺口分析），系统用 **LangGraph 多 Agent Harness** 调度多个 LLM
Agent，配合确定性事实层与硬校验门控，产出符合契约的结果。所有输出只引用用户衣橱内的真实单品 ID。

### 1.1 六类任务

| TaskType | 说明 | 主链 |
|---|---|---|
| `OUTFIT_RECOMMEND` | 推荐一套或多套搭配 | Agentic Recommend（有 LLM）／确定性推荐（无 LLM） |
| `OUTFIT_MODIFY` | 修改当前搭配（换单品 / 加单品 / 调整风格） | Agentic Modify（必须 LLM） |
| `STYLE_ADVICE` | 风格知识建议 | Extension 子图 |
| `ITEM_ADVICE` | 单品搭配建议 | Extension 子图 |
| `WARDROBE_COMPATIBILITY` | 评估新品与衣橱的兼容性 | Extension 子图 |
| `WARDROBE_GAP` | 分析衣橱缺失的风格单品 | Extension 子图 |

### 1.2 分层结构

```
┌──────────────────────────────────────────────────────────────┐
│  Vue 3 前端 (apps/web)                                        │
│  RecommendPage / WardrobePage / ImportPage / MemoriesPage /   │
│  SettingsPage · TaskResultView / WeatherCard                  │
└───────────────────────┬──────────────────────────────────────┘
                        │ HTTP /api
┌───────────────────────▼──────────────────────────────────────┐
│  FastAPI (apps/api/styleforge/api.py)                        │
│  /tasks/execute · /tasks/route · /weather/now · 会话/偏好/衣橱 │
└───────────────────────┬──────────────────────────────────────┘
┌───────────────────────▼──────────────────────────────────────┐
│  编排层 (orchestration/)                                     │
│  TaskRouter(任务分类) · ContextRouter · LocationResolver      │
│  MultiTaskGraph（route-only，仅服务 /tasks/route，不执行业务）│
└───────────────────────┬──────────────────────────────────────┘
┌───────────────────────▼──────────────────────────────────────┐
│  工作流层 (workflow/task_workflow.py → MultiTaskWorkflow)     │
│  execute() 四路分发：Modify / Recommend / Deterministic /     │
│  Extension（都是 Multi-Agent Harness 或其确定性替代）          │
└───────────────────────┬──────────────────────────────────────┘
┌───────────────────────▼──────────────────────────────────────┐
│  Multi-Agent Harness (agentic/)                              │
│  Coordinator → Stylist/Research/Extension 子图 + 验证链       │
└───────────────────────┬──────────────────────────────────────┘
┌───────────────────────▼──────────────────────────────────────┐
│  数据层                                                       │
│  PostgreSQL（主库，19 个 repositories）· Redis（可选缓存）     │
│  Chroma（可选知识向量）· 本地文件（商品图片/数据集源）           │
└──────────────────────────────────────────────────────────────┘
```

## 2. 请求生命周期：`POST /tasks/execute`

`api.execute_task()` → `get_multi_task_workflow().execute(task_input, session_context)`。
`MultiTaskWorkflow.execute()` 顺序：

1. **路由**：`_route_with_session()` 先按请求单独路由（`TaskRouter.route`）；对短跟进请求
   （`is_follow_up`，如「更正式一点」）复用会话 `current_outfit` 重路由为 `OUTFIT_MODIFY`，
   支持「三套都改」——目标由 Harness 从会话上下文解析，而非注入 `task_input`。
2. **落库**：`start_task_run` 写入 `task_runs`（每次执行一条可追踪记录）。
3. **上下文构建**：`ContextPackBuilder.build` 产出 `ContextPack`（用户偏好 + 记忆 profile + 场景）。
4. **四路分发**：

```python
if route.task_type is TaskType.OUTFIT_MODIFY:
    return self._run_agentic_modify(...)              # ① 必须 LLM
if route.task_type is TaskType.OUTFIT_RECOMMEND:
    if self.llm_client is not None:
        return self._run_agentic_recommend(...)       # ② 有 LLM
    return self._deterministic_recommend(...)         # ③ 无 LLM 确定性兜底
if route.task_type in _EXTENSION_TYPES:               # ④ 四个扩展任务
    return self._run_agentic_extension(...)
```

| 条件 | 执行链 | 无 LLM 行为 |
|---|---|---|
| `OUTFIT_MODIFY` | `_run_agentic_modify` | `LlmUnavailable`（503）——语义修改无 LLM 无意义 |
| `OUTFIT_RECOMMEND` + 有 LLM | `_run_agentic_recommend` | — |
| `OUTFIT_RECOMMEND` + 无 LLM | `_deterministic_recommend`（`parse_request → recommend_for_user → present_result`） | `llm_enabled=false`，契约不变 |
| 四个扩展任务 | `_run_agentic_extension`（Extension 子图） | `LlmUnavailable`（503），failed run 仍持久化 |

### 2.1 Agentic Modify（`_run_agentic_modify`）

- `_agentic_targets` 解析修改目标：显式 `current_outfit_id`，或「无显式选择」时取会话近期待改
  候选（每个目标一次 `harness.invoke`，`target_candidates=1`），否则单 fallback。
- 每个目标：`list_items` → `build_facts` → 构造 `Environment`（含 web / weather / knowledge /
  skills provider）→ `_harness(environment)` → `invoke`。
- `base_draft` = 当前 outfit 快照（`resolve_active_outfit` 或显式快照）。
- 产出 `agentic_outcome` 经 `_agentic_modify_to_result` 映射为 `OutfitModifyResult` 契约；
  `completed` 时 `_persist_agentic_candidate` 写入 `candidate_outfits`——下一轮对话可按新
  `outfit_id` 重新锚定（多轮 grounding）。

### 2.2 Agentic Recommend（`_run_agentic_recommend`）

- 空 `base_draft`；单次 `harness.invoke` 产出全部候选（`target_candidates=3`）。
- 链路：Coordinator → Research（`search_before_ask` 先行，按需 search_web / get_weather /
  load_skill / search_knowledge）→ Research Synthesizer 合成证据 → Stylist 基于共享证据逐个产
  出候选 → 主图门控 → StageCandidate。
- 产出 `agentic_outcome` + `preference_context`（分层偏好视图）+ `environment_context`
  （Research 实际看到的天气/定位事实），前端据此渲染研究依据。
- `completed` 时 `_persist_agentic_recommend` 持久化候选。

### 2.3 Agentic Extension（`_run_agentic_extension`）

四个扩展任务不产 outfit 候选，**不走** Environment Gate / Critic / StageCandidate / Goal Gate：

- **确定性事实层预计算**：execute 侧先跑 `analyze_extension_task`（`tools/extension_analysis.py`）
  产出 Agent1 关键事实（`facts` / `candidate_item_ids` / `resolved_target` /
  `needs_clarification`），以 `extension_facts` 放进 invoke 初始 state——LLM 只做语义增强与
  综合，贴近「先检索后决策」的确定性哲学。
- **Extension 子图**：`extension_agent ↔ tool_step` 循环（`search_wardrobe` / `search_knowledge` /
  `search_web` / `inspect_outfit`），`READY` → closing 节点（`MAX_EXTENSION_STEPS = 8`）。
- **closing 节点**：`chat_json` 对齐 `RESULT_MODELS` 的 JSON Schema 产出 task result → 跑
  `validate_task_result` + `sanitize_extension_references` + `validate_extension_draft` 硬校验；
  失败折回有界重试（`MAX_CLOSING_RETRIES = 2`）；`needs_clarification`（如 ITEM_ADVICE 锚点
  不明）由确定性草案胜出，绝不伪造完成。
- **产出**：`extension_result` 经 `_agentic_extension_to_result` 映射，`payload.result` 字段形状
  与前端 `TaskResultView` 契约逐字段一致。

### 2.4 记忆提取

每次 `_run_agentic_*` 执行成功后调用 `_extract_memories` → `extract_language_evidence`
（LLM 蒸馏偏好证据）→ `apply_evidence` 写库。失败静默吞掉（best-effort，绝不让任务失败）。

## 3. Multi-Agent Harness（`agentic/`）

`StyleForgeHarness` 是唯一组装入口，一个对象持有整条 Harness 依赖链：

```
CapabilityRegistry + register_local_tools(8 工具)
        → AgentRuntime(llm, registry, instructions, hooks, visibility, guard,
                       memory_retriever, runtime_capabilities)
        → EvidenceStore
        → compiled Main Graph (build_h2a_main_graph)
```

- `invoke(state)` 是唯一入口；`model_calls` 累计该 Harness 的 LLM 调用数。
- **运行时依赖（llm / environment / providers）绝不进序列化状态**——执行状态只含 request /
  drafts / 上下文 / 结果，可序列化、可追踪。

### 3.1 八个本地工具（`agentic/tools/local_tools.py`）

| 工具 | 能力 | requires | 可见 Agent |
|---|---|---|---|
| `inspect_outfit` | 查看当前/指定搭配快照 | — | stylist, extension |
| `search_wardrobe` | 衣橱单品检索 | — | stylist, extension |
| `search_web` | 联网搜索（Tavily） | `CAP_WEB_SEARCH` | research, stylist, extension |
| `get_weather` | 按地点查天气 | `CAP_WEATHER` | research, stylist |
| `search_knowledge` | 本地知识库检索（Chroma 降级 keyword） | `CAP_KNOWLEDGE` | research, stylist, extension |
| `load_skill` | 加载任务流程知识（`knowledge/skills`） | `CAP_SKILLS` | research, stylist |
| `modify_outfit` | 应用 `plan.ops`（replace/remove/add + placement） | —（precondition：working_draft 存在） | stylist |
| `update_plan` | 更新全局求解计划 | — | coordinator |

**能力分层（Layer 2）**：`_runtime_capabilities()` 按实际部署的 provider 生成
`frozenset`（`CAP_WEB_SEARCH / CAP_WEATHER / CAP_KNOWLEDGE / CAP_SKILLS`），`CapabilityRegistry`
据此过滤 Agent 可见工具——**工具清单随真实能力变化，不随请求变化**。没有 TAVILY key 时
`search_web` 根本不在目录里，Agent 不会幻觉「联网搜过」。工具的 `agents` / `requires` 在
harness 构造时固定（stable registration）。

### 3.2 Main Graph 编排（`agentic/graph/main.py`）

`build_h2a_main_graph` 组装（带 Coordinator 前缀）：

```
START → bootstrap ─┬─ 普通推荐/修改 → stylist
                  ├─ 已解析扩展任务 → extension
                  └─ 确有外部事实缺口 → coordinator
                        ├─ task_state.next_agent=STYLIST   → stylist
                        ├─ task_state.next_agent=RESEARCH  → research ─→ coordinator（证据回传）
                        ├─ task_state.next_agent=EXTENSION → extension ─→ end（不走 outfit 验证链）
                        ├─ NEEDS_CLARIFICATION → clarification → END
                        └─ PROTOCOL_ERROR → end_node(agent_protocol_error)

stylist 子图产出 handoff_result：
    COMPLETED           → environment_gate
    NEEDS_CLARIFICATION → clarification（主图持有）
    PROTOCOL_ERROR      → end_node

environment_gate ─ valid? ──┬─ 物理与明确意图均通过 → critic
                            ├─ 推荐的明确意图不满足 → grounded recovery → environment_gate
                            ├─ recovery 后仍不满足 → end_node（无候选，映射为 infeasible）
                            └─ 其他物理失败 → stylist（带着 gate_feedback 重新规划）

critic ─ approved? ─┬─ 是 → stage_candidate
                    └─ 否 → 已达重试上限? ─┬─ 是 → stage_candidate（DEGRADED_ACCEPTED）
                                           └─ 否 → stylist（replan）

stage_candidate → goal_gate ─ enough? ─┬─ 是 → end_node(done)
                                       └─ 否 → reset_candidate_draft → stylist
```

- **主图拥有完整验证链**（Environment Gate → Critic → StageCandidate → Goal Gate）；子图只
  产出（stylist 产候选草案，research 产证据，extension 产任务结果）。
- **明确意图门**：只用于 `outfit_recommend`，基于衣橱 `features` 校验可客观判断的要求；
  `outfit_modify` 仍由 Critic 判断语义变化。若整个衣橱都没有对应特征元数据，则不做伪确定性
  否决。恢复候选仍重新经过 Environment Gate 和 Critic。
- **有界修订**：`MAX_CRITIC_RETRIES = 3`。真实 provider 面对有限衣橱可能陷入「类似 → 被拒 →
  重试」死循环，重试耗尽后 `route_after_critic` 强制接受物理有效候选，候选状态如实标记
  `DEGRADED_ACCEPTED`（诚实标注，绝不伪装 PASS）。
- **reset_candidate_draft**：新候选从 `base_draft` 重置（recommend=空、modify=原快照），绝不从
  上一候选继续；同时重置 Critic 修订预算。
- **clarification_node**：对城市/日期类问题确定性地写 `pending_field` 到 `thread_grounding`
  ——下一轮裸回复「上海」仍能被读成目的地（问题→答案→grounding 连续性）。

### 3.3 子图（`agentic/agents/`）

| 子图 | 职责 |
|---|---|
| `coordinator/` | 维护 `TaskState`，分配 `next_agent`（STYLIST / RESEARCH / EXTENSION），持有会话上下文 |
| `research/` | Research 循环：`search_before_ask` 先行，按需 search_web / get_weather / load_skill / search_knowledge；`CONTINUE`（调 1 工具）/ `RESEARCH_COMPLETE`（停手）/ `NEED_USER` |
| `research_synthesizer/` | 独立 profile，将 `raw_evidence`（子图内私有缓冲）合成 `ResearchEvidence` |
| `stylist/` | Stylist 循环：工具调用 + 候选产出（`CANDIDATE_READY` / `NEED_USER` / `CONTINUE`），走 `modify_outfit` 累积 `working_draft` |
| `critic/` | 主图验证节点：approved / issues / feedback |
| `extension/` | 扩展任务子图：`extension_agent ↔ tool_step` 循环 + closing 节点（综合 + 硬校验 + 有界重试） |

`research` 节点的 `raw_evidence` 缓冲留在子图内部，产物经 `EvidenceStore` journal 供追踪审计
（Runtime 依赖，不进状态）。

### 3.4 统一错误边界

- `common/errors.py` 定义稳定 `ErrorCode`、HTTP 状态映射、可重试语义和 `StyleForgeError`。
- API 中间件为每个请求生成或透传安全的 `X-Request-ID`；所有错误响应保留旧 `detail`，并增加
  `error.code / error.message / error.request_id / error.retryable`。
- Agent 与工具运行时把协议错误、参数错误、前置条件失败、超时、上游不可用和执行失败归一化，
  observation 只携带安全错误信息；数据库事务统一 rollback，清理失败不遮蔽原始异常。
- Web Axios 与小程序请求层消费同一错误结构；未知服务端异常统一返回 `INTERNAL_ERROR`，不回传
  Python 异常类型、堆栈或数据库细节。
- `common/observability.py` 使用 ContextVar 关联 `request_id/run_id`，对 HTTP、task、agent、LLM、
  tool、database 六层统计总量、失败、可重试失败、重试、降级和耗时；错误日志输出结构化 JSON。
- `/health.observability` 只暴露进程级聚合值，不包含用户请求、工具参数、异常正文、request_id 或
  run_id。指标随 API 进程重启清零；需要长期趋势时再接 Prometheus/OpenTelemetry，而不是把高基数
  明细塞进健康接口。

### 3.5 MCP 双向边界

- **Client 侧**：`integrations/mcp/client.py` 统一 stdio 与 Streamable HTTP；每次操作执行 MCP
  initialize、`tools/list` 和 `tools/call`，用稳定错误码与安全 trace 收口。
- **外部事实侧**：`integrations/mcp/weather.py` 通过 official-time 与 official-fetch 获取时区日期、
  Open-Meteo 地理编码和预报；Fetch 仅允许两个 Open-Meteo HTTPS 域名，失败后回退直连 Provider。
- **Agent 侧**：Agent 仍只看到 typed `get_weather`，不感知 MCP 传输；同一任务的同一地点+时间窗口
  复用不可变天气事实，避免循环 Agent 重复外部调用。
- **Server 侧**：`mcp_server.py` 提供五个扁平强类型工具、`styleforge://capabilities` 资源和
  `styleforge_plan_outfit` Prompt；FastAPI 在 `/mcp/` 挂载 Streamable HTTP，CLI 支持 stdio。
- **可观察性**：`diagnostics.mcp` 与 `/mcp/status` 只保留 server/tool/transport/耗时/结果大小/
  稳定错误码，不保留用户参数和完整返回。完整说明见 [MCP 集成](MCP_INTEGRATION.md)。

### 3.6 Prompt 注入防护

- `PromptAssembler` 把稳定、版本化指令保留在 system role；用户输入、Web/MCP、RAG、衣物文本、
  会话、记忆、工具观察和跨 Agent 证据全部放入带不可伪造边界的 user-role 数据区。
- `prompt_security.py` 做中英文注入信号扫描、边界转义和外部工具参数出站校验；诊断仅保留类别与
  数量，不保留命中原文。
- `AgentRuntime` 复核模型调用的工具必须属于本轮可见目录；`ToolRuntime` 在 Hook 和 Handler 前
  完成 Schema 与出站安全校验，分别返回 `TOOL_NOT_AUTHORIZED` 或
  `PROMPT_INJECTION_BLOCKED`。
- 检测不替代工具 Schema、前置条件、决策契约、ContextGuard 和 Environment Gate。完整威胁模型、
  调用顺序和扩展要求见 [Prompt 注入防护](PROMPT_SECURITY.md)。

## 4. Agent 上下文层（`agentic/context/`）

| 模块 | 职责 |
|---|---|
| `grounding.py` | `GroundingResolver`：search-before-ask（缺省地点/日期时先搜再问）+ `pending_field` 连续性 |
| `thread_preferences.py` | `ThreadPreferenceView`：会话内偏好视图 + **scope gate**（单轮请求绝不提升到 profile） |
| `memory_context.py` + `prompt_assembler.py` | `PreferenceRetriever` 分层 Top-K 召回 → 拼进 Stylist prompt |
| `wardrobe_index.py` | `WardrobeIndexSummary`：把整个衣橱压缩为 ~1KB 摘要喂给 Agent |
| `visibility.py` | `ContextVisibilityPolicy`：各 Agent 可见性过滤（`trajectory_*` 等私有字段不跨子图泄漏） |
| `guard.py` | `ContextGuard`：上下文截断，防超长 |
| `prompt_security.py` | 动态内容边界、注入信号扫描、外部工具参数出站保护 |
| `evidence_store.py` | `EvidenceStore`：Research 产物 journal，trace/审计用 |
| `assembler.py` | 把各层组装为执行状态上下文 |

## 5. 记忆系统（多轮对话）

链路：**提取 → 应用 → 召回**，详见 `docs/SESSION_CHAT_MEMORY.md`。

- **提取**：`_extract_memories`（每条 agentic 执行后）→ LLM `extract_language_evidence` 蒸馏偏好
  证据。单轮/短跟进请求被 `_scope_gate` 拦截，只留在 ThreadPreferenceView，不写入 profile。
  提取失败静默吞掉。
- **应用**：`apply_evidence` 聚合进 `preference_evidence` / `preference_model`（含衰减与整合）。
- **召回**：`PreferenceRetriever` 分层 Top-K（短期 / 场景 / 长期 / 避免，≤ 8 行）——recommend 与
  modify 共享一个 retriever；Thread 层留在 `thread_context`，绝不互相提升。
- **会话锚定**：`api.py` 在用户消息后更新 `current_outfit_id/item_ids` + `session_signals`；
  `_thread_context` 把这些带进 Harness；`candidate_outfits` 让下一轮可按新 `outfit_id` 重新锚定。

## 6. 数据层

- **PostgreSQL（主库）**：`STYLEFORGE_DATABASE_DSN`。`repositories/` 下 **19 个仓库**，覆盖
  task_runs、run（候选）、candidate_outfits、catalog、wardrobe、preference_evidence、
  preference_model、request_memory、chat、import_run、interaction_event、personal_embedding、
  item_image、saved_outfit、dataset_source、user_preferences 等。
- **Redis（可选缓存）**：`STYLEFORGE_REDIS_ENABLED/URL/TTL`，`core/redis.py`。
- **Chroma（可选知识向量）**：`STYLEFORGE_CHROMA_DIR`，`search_knowledge` 降级纯 keyword。
- **本地文件**：商品图片 / 数据集源（mytheresa / polyvore 等）。

## 7. API 清单（`api.py`）

| 路由 | 说明 |
|---|---|
| `POST /tasks/execute` | 主执行入口（本架构核心） |
| `POST /tasks/route` | 纯路由（不执行），返回 task_type + 置信度（走 `orchestration/MultiTaskGraph`） |
| `GET /weather/now` | 从 `get_multi_task_workflow().weather_provider` 构建 `WeatherTool` 直连，`unavailable` 契约 |
| `GET /users/{id}/chat-sessions` / `GET /chat-sessions/{id}` | 会话 + 消息历史 |
| `POST /chat-sessions/{id}/messages` | 写入消息 + 更新会话 outfit 锚点 |
| `GET/PUT /preferences/*` | 用户偏好 / 记忆 / 评估权重 |
| `/wardrobes/*`、`/catalog/*`、`/items/*`、`/items/{id}/image` | 衣橱 / 商品目录 / 单品与图片 |

## 8. 前端（`apps/web`）

- 页面（`src/pages/`）：`RecommendPage`（主推荐 + 会话）、`WardrobePage`、`ImportPage`、
  `MemoriesPage`、`SettingsPage`。
- 组件（`src/components/`）：`TaskResultView`（渲染各 task_type 的结果契约）、`WeatherCard`。
- `services/api.js`（`/tasks/*`、`/preferences/*`、会话）＋ `services/user.js`。
- RecommendPage 直达 `item_advice` 等扩展任务请求，经 `/tasks/execute` 走 Extension 子图；
  前端与结果契约解耦，不感知执行链迁移。

## 9. 目录导览（`apps/api/styleforge/`）

| 目录 | 内容 |
|---|---|
| `agentic/` | **当前主框架**：harness / graph / agents（coordinator / research / research_synthesizer / stylist / critic / extension）/ context / gates / runtime / tools / hooks / instructions |
| `workflow/` | `task_workflow.py`（四路分发核心，唯一工作流模块） |
| `orchestration/` | TaskRouter / ContextRouter / LocationResolver + `MultiTaskGraph`（route-only） |
| `services/`（18） | 记忆聚合/提取/衰减/整合/解决、语义检索、视觉搜索、推荐/展示、订单导入、聊天、目录向量、个人图片/嵌入等 |
| `core/` | config / request_parser / request_spec / schemas / rubric / scoring / decision / slots / taxonomy / relaxation / candidate_service / categories / structure_signature |
| `models/` | task / task_results（`RESULT_MODELS` 契约）/ context / agent_tasks / agentic_contract（共享契约） |
| `repositories/`（19） | 数据访问层 |
| `tools/` | weather / web_search / registry / 扩展分析 / 扩展校验（extension_analysis / extension_validation / extension_items） |
| `llm/` | DeepSeek 客户端 + prompts + memory schema |
| `data/` | 数据集加载（mytheresa / garments2look / outfits / p_outfit） |
| `integrations/` / `knowledge/` / `vision/` / `common/` | 外部集成 / 知识检索 / 视觉 / 公共工具 |

## 10. 测试与验证

- `tests/` 提供全量回归门禁，具体用例数以当前 `pytest --collect-only` 和 CI 结果为准；2026-09-12
  本地 PostgreSQL 隔离运行 **842/842 passed**。`tests/llm/fake_llm.py` 脚本化 Fake 支撑无网络测试（dict /
  tuple(decision, tool_specs) / exception 顺序消费同一脚本）。
- 无 legacy 模式开关，全部测试默认跑 Harness 主链。
- 专项：`test_agentic_modify_primary.py`（主修改链端到端）、`test_agentic_recommend_primary.py`、
  `test_agentic_extension_primary.py`（四个扩展任务 Harness 链 + no-LLM 持久化）、
  `test_agentic_multi_outfit.py`（多目标）、`test_session_multiturn.py`（多轮）、
  `test_extended_tasks.py`（扩展事实层纯函数）、`test_weather_now_api.py`。
- 评估：`evals/runners/` 保留 follow_up / memory_extraction / task_routing，并新增固定衣橱
  `wardrobe_quality` 基准。默认确定性模式无外部调用；显式 configured 模式可用 DeepSeek 在
  隔离 PostgreSQL schema 中运行。真实 EXT-002 验收使用 Polyvore Outfits 官方商品与套装关系。
- 单品建议收口先尝试模型结构化结果并执行硬边界校验；连续修复失败时，仅当确定性事实已解析
  锚点且点名槽位均有候选，才允许生成带 `deterministic_grounded_fallback` 标记的结果。
  fallback 只能组合 Agent 1 候选 ID，不调用数据集写操作，也不把缺失事实伪装成成功。
- 对上述事实完整的 `item_advice`，Main Graph 在 bootstrap 后直接路由到 Extension closing，
  不再让 Coordinator 重复判断已确定的任务。真实 Polyvore + DeepSeek 两例均为 1 次 Harness
  模型调用、2/2 通过；其他事实完整的扩展任务同样直达 closing，信息不足时才保留
  Coordinator/Research 路由。
- `MultiTaskWorkflow` 的确定性任务类型是 Main Graph 权威路由：Coordinator 可在任务类型内安排
  Research/Stylist/Extension，但不得把推荐误派给 Extension。推荐完整结构同时支持
  `one_piece + footwear` 与 `top + bottom + footwear`；模型协议耗尽或 Critic 严重意图错位时，
  系统只从当前用户衣橱按场景特征恢复候选，并继续通过环境门禁与 Critic，绝不编造商品 ID。
- Polyvore 官方商品文本 + DeepSeek 的完整真实基线为 7/7，覆盖推荐、修改、风格建议、单品建议、
  兼容性和衣橱缺口六类任务；路由和状态准确率均为 100%，报告为
  `artifacts/evaluation/wardrobe_quality_real_six_task_v14_final.json`。
- 记忆的品类归纳只接受明确接受/拒绝类行为，排除普通替换；证据必须同场景、至少 3 个不同
  单品且来自 3 个不同事件。归纳按阈值里程碑去重，避免一次多单品事件重复提高支持次数。
- `.github/workflows/ci.yml` 负责无 GPU PR 门禁；`scripts/start_styleforge.ps1` 统一管理 Windows
  环境自检、API/UI 启停、health 等待与工作区日志。真实 LLM/GPU 验收仍是显式本地门禁。

## 11. 历史

更早的执行框架（legacy 三 Agent 链、Stage 2 残留）已全量删除；p-outfit 旧 runner 已删除，
但冻结案例和真实数据验收合同仍作为当前 `wardrobe_quality` 基准的数据来源。演进见 git 历史
`44a10b4` → `4c79f83` → `84c7f13`，此处不再赘述。
