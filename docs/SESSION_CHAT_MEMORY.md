# 会话持久化多轮对话 + Context-Aware 自适应偏好记忆

> 版本：v2.1（2026-08-12）
> 范围：`chat_sessions` / `chat_messages` 会话两表（Schema v10）+ `interaction_events` / `preference_evidence` / `preference_model` 记忆三表（Schema v11，取代 v10 的 `user_memories`）；`POST /tasks/execute` 会话化、两段式路由、整体调整模式；行为事件 → 偏好证据 → 维度化偏好模型 → Memory Resolver → 三 Agent 差异化注入的完整闭环。
>
> v2.1 泛化改造：行为证据从「单次行为直接折叠到品类」改为 **item 级证据 + color 归因 + category 归纳（≥3 件不同单品）**，弱证据 scope 由 global 修正为 contextual+场景词（违背方案旧 bug）；新增跨 value 相反极性全局偏好冲突降权（×0.9 + `conflict_with` 互标）；Resolver 对 contextual 行改为先做上下文门控（不匹配绝不泄漏进其他桶）。测试新增 `tests/test_memory_generalization.py`（8 场景）。

## 1. 目标

StyleForge 原来是"单请求 → 单响应"：一次提问一次渲染，无会话概念。本批新增两块能力：

1. **会话持久化多轮对话**：同一会话内连续追问（"换一件外套""更正式一点"）自动携带上文；会话与消息落库，跨刷新、跨设备可恢复。
2. **Context-Aware 自适应偏好记忆**：行为驱动、上下文感知的偏好闭环——

   ```
   实时交互状态 → Interaction Event → Preference Evidence → Preference Model → Memory Resolver → 三 Agent 差异化注入
   ```

   原始行为是**事实**（`interaction_events`），`preference_evidence` 是对事实的标准化**解释**，`preference_model` 是系统当前对用户偏好的**假设**。LLM 只负责理解（提炼证据），确定性程序负责记忆管理（聚合、生命周期、衰减、注入裁剪）。

## 2. 数据模型（Schema v11）

会话两表沿用 v10：

```sql
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_updated
    ON chat_sessions(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    message_id  TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL,
    task_type   TEXT NOT NULL DEFAULT '',
    run_id      TEXT NOT NULL DEFAULT '',
    result_json TEXT,
    created_at  TEXT NOT NULL,
    message_seq BIGSERIAL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created
    ON chat_messages(session_id, created_at, message_seq);
```

记忆三表（v11 新增，取代 v10 的 `user_memories`）：

```sql
-- 1. 原始行为事实：用户点过的每个按钮 / 每个执行过的任务类型
CREATE TABLE IF NOT EXISTS interaction_events (
    event_id      BIGSERIAL PRIMARY KEY,
    user_id       TEXT NOT NULL,
    event_type    TEXT NOT NULL CHECK (
        event_type IN (
            'outfit_selected', 'outfit_rejected', 'item_replaced', 'item_rejected',
            'feedback_submitted', 'style_requested', 'compatibility_checked',
            'explicit_preference', 'wardrobe_adopted', 'wardrobe_removed'
        )
    ),
    item_id       TEXT NOT NULL DEFAULT '',
    context_json  TEXT NOT NULL DEFAULT '{}',   -- request / outfit_id / item_ids / target_slot
    features_json TEXT NOT NULL DEFAULT '{}',   -- replaced_item_ids / feedback / attribute / value ...
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interaction_events_user_created
ON interaction_events (user_id, created_at DESC);

-- 2. 标准化偏好证据：对事实的确定性/LLM 解释
CREATE TABLE IF NOT EXISTS preference_evidence (
    evidence_id BIGSERIAL PRIMARY KEY,
    user_id     TEXT NOT NULL,
    dimension   TEXT NOT NULL DEFAULT '',       -- style / garment / appearance / shopping
    attribute   TEXT NOT NULL,                  -- category / style / color_family / fit ...
    value       TEXT NOT NULL,                  -- 自由文本，不写死 ENUM
    polarity    TEXT NOT NULL CHECK (polarity IN ('positive', 'negative')),
    strength    REAL NOT NULL DEFAULT 0.5 CHECK (strength >= 0 AND strength <= 1),
    scope_json  TEXT NOT NULL DEFAULT '{}',     -- {"type":"global"} 或 {"type":"contextual","occasions":[...]}
    source      TEXT NOT NULL,                  -- llm_request / outfit_rejected / feedback / explicit_statement ...
    event_id    BIGINT REFERENCES interaction_events(event_id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_preference_evidence_user_attr
ON preference_evidence (user_id, attribute, value);

-- 3. 维度化偏好模型：系统当前假设（取代 user_memories）
CREATE TABLE IF NOT EXISTS preference_model (
    preference_id      BIGSERIAL PRIMARY KEY,
    user_id            TEXT NOT NULL,
    dimension          TEXT NOT NULL DEFAULT '',
    attribute          TEXT NOT NULL,
    value              TEXT NOT NULL,
    polarity           TEXT NOT NULL CHECK (polarity IN ('positive', 'negative')),
    lifecycle          TEXT NOT NULL DEFAULT 'short_term' CHECK (
        lifecycle IN ('short_term', 'long_term_candidate', 'long_term')
    ),
    scope_json         TEXT NOT NULL DEFAULT '{}',
    confidence         REAL NOT NULL DEFAULT 0,
    support_score      REAL NOT NULL DEFAULT 0,
    contradiction_score REAL NOT NULL DEFAULT 0,
    support_count      INTEGER NOT NULL DEFAULT 0,
    contradiction_count INTEGER NOT NULL DEFAULT 0,
    source_summary_json TEXT NOT NULL DEFAULT '{}',
    decay_policy       TEXT NOT NULL DEFAULT 'normal' CHECK (
        decay_policy IN ('none', 'slow', 'normal')
    ),
    last_observed_at   TEXT NOT NULL DEFAULT '',
    expires_at         TEXT NOT NULL DEFAULT '',
    active             INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    UNIQUE (user_id, dimension, attribute, value)
);
CREATE INDEX IF NOT EXISTS idx_preference_model_user_active
ON preference_model (user_id, active, updated_at DESC);
```

消息顺序以 PostgreSQL 的 `message_seq BIGSERIAL`（自增序列）作 tiebreaker（`message_id` 是 UUID，不保证时间序；同一微秒内的 `created_at` 平局由 `message_seq` 决出）。PG 没有 SQLite 的 `rowid`，`message_seq` 由 `BIGSERIAL` 生成，创建顺序即插入顺序。

## 3. 会话持久化契约

### 3.1 API

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/users/{user_id}/chat-sessions` | 新建会话；`title` 空则自动命名"会话 N" |
| `GET` | `/users/{user_id}/chat-sessions` | 列表（含最新消息 preview），newest-first |
| `GET` | `/chat-sessions/{session_id}?user_id=` | 单会话 + `messages[]`（归属校验 404） |
| `PATCH` | `/chat-sessions/{session_id}?user_id=` | 重命名 |
| `DELETE` | `/chat-sessions/{session_id}?user_id=` | 删除（级联删消息） |
| `POST` | `/tasks/execute` | 带可选 `session_id` 执行任务并落消息 |

### 3.2 execute 消息落库

`POST /tasks/execute` 收到 `session_id` 后：

1. 校验归属（404）。
2. 读会话最近产生搭配的 assistant 消息 → 会话 outfit context。
3. 先落 user 消息。
4. 执行任务；成功落 assistant 消息（`content` = `assistant_summary(payload)`，`result_json` = 裁剪后的 payload + `outfit_context` 快照）；失败落 failed assistant 消息（`{status: "failed", error}`），原异常映射（503/502/422/500）保留。
5. 响应 payload 附 `session_id` / `message_id`。

**裁剪规则**（`trim_message_payload`）：只保留 `{run_id, user_id, request, task_type, status, result}`，去掉 `context_pack` / `trace` / `agent_outputs`，前端可仅凭 `result` 恢复渲染，避免消息表膨胀。

### 3.3 会话 outfit context

`get_session_outfit_context`：取会话中最近一条 `task_type ∈ {outfit_recommend, outfit_modify}` 且产生搭配的 assistant 消息，返回 `{current_outfit_id, current_item_ids}`。这是多轮追问"当前搭配"的来源，跨刷新/跨设备保持一致。无搭配时返回 `{current_outfit_id: "", current_item_ids: []}`（不带 `session_signals`）。

## 4. 两段式路由与跟进识别

`MultiTaskWorkflow.execute(request, session_context=...)` 内做两段式路由：

```
route0 = router.route(request, current_outfit_id=task_input.current_outfit_id, ...)

若 request 无显式 current 上下文 且 session_context 有 current_item_ids:
  ├─ route0 == OUTFIT_RECOMMEND 且 _is_follow_up(request)
  │    → 用 session outfit 填 task_input.current_*，强制重路由 OUTFIT_MODIFY
  │      （reason="session_follow_up"，confidence=0.9）
  └─ route0 == OUTFIT_MODIFY 且 session 有 current_item_ids
       → 仅填 task_input.current_*（修复"修改链第 3 轮仍拿原始方案"）
```

`_is_follow_up(request)`：**≤20 字符** 且含调整词（更/再/别/不/一点/太/有点/调整/改变/换成/换/改/替换/色系/风格/正式/休闲/简约/商务/酷/花）且不含新鲜场景词（推荐/面试/聚会/约会/通勤/上班/旅行/婚礼/出席/晚宴/周末/今天/明天/穿什么/搭配/选一套）。填充时过滤不在活跃衣橱的失效单品。

**已知残余误伤**：超短句如"不要蓝色"会被强制成 modify——仅在已有当前方案时触发，reason 可审计，属接受的取舍。

## 5. 整体调整模式

"更正式一点"这类无槽位、无目标单品的修改请求走 `adjustment_mode="overall"`：

- `locked = []`、`replaced = []`：不锁定、不替换任何单品。
- 替换池 = 活跃衣橱 − 当前搭配单品（≤40 件）。
- Agent 2 按方向（更正式/更休闲/更简约…）重建一套完整搭配，不保留当前单品。
- 硬校验对 `locked=[]` / `replaced=[]` 原样通过；`OutfitModifyResult.target_slot` 允许空串。

判定位置：`not current_ids` 之后、`needs_clarification` 之前，`not target_slot` 即整体调整。

## 6. Context-Aware 自适应偏好记忆系统

### 6.1 闭环总览

```
实时交互状态              Interaction Event              Preference Evidence
(session_signals)      →  (interaction_events)        →   (preference_evidence)
  Redis 会话信号            用户点按钮/任务执行               LLM 理解 + 确定性规则解释
        ↓                        ↓                              ↓
  三 Agent 差异化           Memory Resolver              Preference Model
  注入（MemoryPack）    ←  五桶拆分/场景匹配/置信过滤   ←  (preference_model)
                                                           确定性聚合 + 生命周期/衰减
```

原则：**行为是事实，Evidence 是对事实的解释，Preference 是系统当前的假设**；LLM 负责理解，确定性程序负责记忆管理。聚合是全量重算（幂等），consolidation 去重后可安全重跑。

### 6.2 行为事件采集

**新端点** `POST /users/{user_id}/events`：body `{event_type, item_id?, context?{request, outfit_id, item_ids, target_slot}, features?}`。前端行为按钮与后端埋点共用，写入 `interaction_events` 后立即折叠证据进 `preference_model`。

| 事件类型 | 触发点 | 折叠规则（`_BEHAVIOR_RULES`） |
|---|---|---|
| `outfit_selected` | 前端「采纳这套」 | positive 0.5，对 `context.item_ids` 逐件折叠 |
| `outfit_rejected` | 前端「换掉这套」 | negative 0.1，对 `context.item_ids` 逐件折叠 |
| `item_replaced` | 前端「换掉这件」/ OUTFIT_MODIFY 执行 | negative 0.25，对 `features.replaced_item_ids` 逐件折叠 |
| `item_rejected` | （预留） | negative 0.2 |
| `feedback_submitted` | 前端好评/差评 | 符号取 `features.feedback`（±），strength 0.7，对 `context.item_ids` 逐件折叠 |
| `wardrobe_adopted` | 采纳单品入库 | positive 0.5 |
| `wardrobe_removed` | 从衣柜删除单品 | negative 0.15 |
| `explicit_preference` | 记忆 API 手动新增 / 改五维权重 | strength 0.9（手动）或 0.3（权重），scope=global |
| `style_requested` | STYLE_ADVICE / ITEM_ADVICE 任务 | 无单品信号，不折叠 |
| `compatibility_checked` | WARDROBE_COMPATIBILITY 任务 | 无单品信号，不折叠 |

**逐件折叠规则**（`behavior_evidence_from_event`，对一个行为事件的目标单品各产三类证据，全部 scope=contextual）：

1. `garment/item=<item_id>`（强度=上表规则值）——用户操作的是哪一件，始终只记到 item 级；
2. `appearance/color_family=<颜色>`（positive 0.2 / negative 0.1）——对该单品的颜色做弱归因（catalog.color）；
3. `garment/category=<品类>`（0.15，`category_induction`）——**仅在归纳阈值触发时**产出：同品类、同极性、**不同 item** ≥ 3 件（`CATEGORY_INDUCTION_THRESHOLD`），且跨过阈值（`count % 3 == 0`，即 3 件产一条、6 件产第二条）。单件被拒绝不归纳出「不喜欢这个品类」。

scope 由 `context.request` 提取场景词（`_OCCASION_TERMS` / `_FORMALITY_TERMS`）写入 `{"type":"contextual", "occasions":[...], "formality":...}`——**行为弱证据绝不写 global**（违背方案的旧 bug 已修）。

埋点位置（`api.py`，best-effort 吞异常，绝不阻断请求）：`PUT /preferences/{user_id}/evaluation`、`POST /preferences/{user_id}/memories`、`POST /tasks/execute`（`_record_task_event`，OUTFIT_MODIFY 的 `item_replaced` 在 `task_workflow.py` 内记录）、衣柜采纳/删除。

### 6.3 证据提取

**语言证据**（`services/memory_extractor.py` + `llm/memory_schema.py` + `llm/memory_prompts.py`）：任务成功路径末尾调用一次 LLM，从请求提炼结构化 `MemoryEvidence{dimension, attribute, value, polarity, strength, scope{type, occasions, formality}}`。否定词 → `polarity=negative`；`scope.type` 区分「明确长期（global）」vs「本次场景（contextual）」。best-effort：无 LLM / 调用失败 / 全无效 → 空列表，绝不阻断任务。四维度固定 `style / garment / appearance / shopping`，attribute/value 自由文本（不写死 ENUM）。

**行为证据**（`services/memory_evidence.py::behavior_evidence_from_event`，确定性，无 LLM）：事件 → 弱/强标准化证据（映射与逐件折叠规则见 6.2）。`explicit_preference` 直接携带 `features.attribute/value/polarity/strength`（scope=global）；其余事件查 catalog 把 `item_id → 品类/颜色`，产出 item 级 + color 归因证据，并视归纳阈值补 category 证据。

分层原则（方案核心）：**不喜欢当前这件（item）≠ 不喜欢这一品类（category 归纳）≠ 不喜欢这个属性 ≠ 长期不喜欢**。单次行为永不产生 category/global 级假设。

### 6.4 聚合 + 生命周期（`services/memory_aggregator.py`）

`apply_evidence(conn, user, evidence)` 写 `preference_evidence`，随后**从完整证据历史全量重算**受影响的 `preference_model` 行（幂等，consolidation 重跑安全）：

- 支持/反对：`support_score += Σstrength(positive)`，`contradiction_score += Σstrength(negative)`，计数同步。
- `confidence = clamp(0.2 + 0.55 × dominant/(dominant+other+0.5) + 0.25 × min(dominant_count,5)/5, 0, 1)`。
- `polarity = net(支持分, 反对分)`；同 attribute 同 scope 相反 value 由计数与分值自然分出强弱。
- lifecycle 升降：`explicit_*` 证据 → 直接 `long_term`；`dominant_count ≥ 3` → `long_term_candidate`，`≥ 6` → `long_term`；已晋升但 `contradiction_ratio > 2.0` 逐级降档。
- `decay_policy`：`long_term`=slow（显式=none）、其余 normal；`expires_at`：short_term +30d、long_term_candidate +90d、long_term 永不过期。
- **跨 value 冲突降权**（`_CONFLICT_PENALTY=0.9`）：仅对 `scope.type=global` 的行，若同 `(dimension, attribute)` 存在相反 polarity 的**其他 value**（如 `style=极简 positive` vs `style=复古 negative`），confidence × 0.9 且 `source_summary.conflict_with` 互记对方 value。每次 `apply_evidence` 末尾对受影响 attribute 的**所有**行统一扫描——矛盾双方都打折标记，而非只有后写入的一方。contextual 行按场景隔离，不参与。重解释而非删除，保留可追溯性。

### 6.5 衰减（`services/memory_decay.py`，读取路径）

`effective_confidence(pref, now) = confidence × exp(-λ·elapsed_days)`，λ：`none`=0、`slow`=0.004、`normal`=0.02；超过 `expires_at` 且无更新视为失效（resolver 过滤）。不依赖后台任务，全部在读取时计算。

### 6.6 Memory Resolver + 三 Agent 差异化（`services/memory_resolver.py`）

`resolve(preferences, request_signature, agent_role, session_signals) -> MemoryPack` 是纯函数：先按 active/expiry/衰减后置信度过滤排序，再拆五桶，按 `AGENT_BUCKETS` 裁剪。

**五桶**（方案第八节）：

| 桶 | 内容 | 置信门槛 |
|---|---|---|
| `session_signals` | 当前会话实时信号（Redis：最近 OUTFIT_MODIFY 的 target_slot + 方向，如「更休闲」→ formality:decrease） | — |
| `short_term_preferences` | short_term 生命周期（global scope） | ≥ 0.25 |
| `stable_preferences` | long_term_candidate / long_term（global，或**已命中当前场景**的 contextual） | ≥ 0.6 |
| `contextual_preferences` | scope=contextual 且与当前 request_signature 场景词命中（无论生命周期） | ≥ 0.25 |
| `avoidances` | polarity=negative | retriever ≥ 0.3 / critic ≥ 0.5 |

**contextual 门控在 lifecycle 之前**：场景偏好无论生命周期，先匹配当前场景；不匹配的**直接丢弃、绝不泄漏**进其他桶（如「周末=复古」绝不进入工作场景的 stable）。匹配且为 long_term_candidate/long_term（≥0.6）的**同时**进入 `stable_preferences`——这样只有 stable 桶的 composer/critic 也能看到本场景的长期偏好；匹配的 short_term 只进 contextual 桶。

**三 Agent 差异化**（`AGENT_BUCKETS`）：retriever=全五桶、composer=session+short_term+stable、critic=仅 stable(≥0.6)+avoidances(≥0.5)。契约：**不允许的桶返回 `{}`（空 dict），允许但为空的桶返回 `[]`（list）**。每桶上限 8 条，按衰减后置信度降序。

注入：标准链路 `graph.py` 的 `_load_memory_profile` 读全量偏好入 state，三 node 各自 `_resolve_memory_pack(raw, request_signature, agent_role)` 差异化注入；扩展链路 `task_workflow.py` 在成功路径提炼语言证据 → `apply_evidence` → 以 `request_signature`（Agent 1 已产出）按角色解析注入。

### 6.7 consolidation（`services/memory_consolidation.py`）

`consolidate_session(conn, user, session_id)`：会话结束时调用（`api.py` DELETE/查询会话路径）。按 `(dimension, attribute, value, polarity)` 去重，同键只留 strength 最强一条，删除冗余证据后对受影响键重跑幂等聚合。不做「会话总结=唯一入口」式提炼，保证过去可复现。

### 6.8 记忆 API（`preference_model` 语义）

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/preferences/{user_id}/memories?lifecycle=` | 列表（维度化偏好行，含 support/contradiction 计数） |
| `POST` | `/preferences/{user_id}/memories` | 手动新增：写 `explicit_preference` 事件 → 强证据晋升 long_term |
| `PATCH` | `/preferences/{user_id}/memories/{preference_id}` | 直接编辑一行（dimension/attribute/value/polarity/lifecycle 全可选） |
| `DELETE` | `/preferences/{user_id}/memories/{preference_id}` | 软删除（`active=0`，可复活） |

## 7. 前端契约（apps/web）

- `services/api.js`：新增会话/记忆 9 个方法 + `recordBehaviorEvent(userId, payload)`（`POST /users/{user_id}/events`）。
- `components/TaskResultView.vue`：按 `task_type` 渲染的复用载体，`props={payload, userId}`。outfit_recommend / outfit_modify 卡片各加 4 个行为按钮——**采纳这套**（outfit_selected）、**换掉这套**（outfit_rejected）、**👍好评 / 👎差评**（feedback_submitted，features.feedback 符号），每张单品图加 **换掉这件**（item_replaced，features.replaced_item_ids=[itemId]）；点击后 `recordBehaviorEvent`，用 ElMessage 提示。历史消息凭裁剪后的 `result` 恢复渲染。
- `stores/recommendation.js`：`sessionId` / `messages[]` / `loadSessionHistory` / `newConversation`；`run()` 透传 `session_id`，成功后把完整 payload 追加进 `messages`。
- `pages/RecommendPage.vue`：会话侧栏（列表 + 新建 + 切换 + 删除）；`localStorage {userId}:sf_session_id` 保存当前会话，onMounted 恢复；首次发送自动 `createChatSession`；`<TaskResultView :user-id="userId" />` 传行为上报用户。
- `pages/MemoriesPage.vue`（`/memories`）：维度化偏好管理页（dimension 中文标签 + attribute/value + polarity 偏好/回避 tag + lifecycle tag + 置信% + 支持/反对计数 + decay/过期；新增/编辑/遗忘）。

## 8. 顺带修复

`graph.py` novelty 新颖惩罚此前把包装行传给 `novelty_scores`，重叠恒为 0。改为与 composer 一致的提取方式：

```python
recent_signatures = [
    memory.get("structure_signature", {})
    for memory in state.get("recent_memories", [])
    if memory.get("structure_signature")
]
novelty = novelty_scores(wardrobe_items, recent_signatures)
```

## 9. 边界与取舍

- 整体调整 = 重建完整搭配（严格替换池，不保留当前单品）；"保留部分单品"列为后续。
- 无 LLM 时多轮第 1 轮可降级；修改/扩展任务严格三 Agent → 503 并落 failed 消息，会话记录不受影响。
- 行为证据强度是确定性启发式（方案示例值：item_replaced 0.25 / outfit_rejected 0.1 / feedback 0.7 / explicit 0.9…），后续可用 LLM 精修；语言证据 best-effort，无 LLM 时该轮仅靠行为事件积累。
- `session_signals` 只存最近一次 OUTFIT_MODIFY 的槽位 + 方向，不存历史全量（Redis 会话缓存自带过期）。
- 跨端：本期仅 Web 前端会话化与行为按钮；小程序仍只传 `user_id/request`（`session_id` 默认空串行为不变），记忆只通过后端埋点积累。
- 会话标题自动命名"会话 N"，不做摘要式命名（后续可用 LLM 从首条请求生成）。
