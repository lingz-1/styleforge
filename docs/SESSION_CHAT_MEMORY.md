# 会话持久化多轮对话 + 用户长期记忆系统

> 版本：v1.0（2026-08-12）
> 范围：`chat_sessions` / `chat_messages` / `user_memories` 三张新表、`POST /tasks/execute` 会话化、两段式路由、整体调整模式、确定性记忆提炼与注入

## 1. 目标

StyleForge 原来是"单请求 → 单响应"：一次提问一次渲染，无会话概念。本批新增两块能力：

1. **会话持久化多轮对话**：同一会话内连续追问（"换一件外套""更正式一点"）自动携带上文；会话与消息落库，跨刷新、跨设备可恢复。
2. **用户长期偏好记忆**：品类/颜色/风格/正式度/场合/习惯偏好跨对话一致；系统自动提炼 + 用户可手动增删改查（手动优先，不被自动提炼覆盖）。

## 2. 数据模型（Schema v10）

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
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created
    ON chat_messages(session_id, created_at, message_id);

CREATE TABLE IF NOT EXISTS user_memories (
    memory_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL,
    source      TEXT NOT NULL CHECK (source IN ('auto', 'manual')),
    category    TEXT NOT NULL CHECK (
        category IN ('category', 'color', 'style', 'formality', 'occasion', 'habit', 'general')
    ),
    content     TEXT NOT NULL,
    confidence  REAL NOT NULL DEFAULT 0,
    occurrences INTEGER NOT NULL DEFAULT 0,
    meta_json   TEXT NOT NULL DEFAULT '{}',
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (user_id, category, content)
);
CREATE INDEX IF NOT EXISTS idx_user_memories_user_active
    ON user_memories(user_id, active);
```

消息顺序以 SQLite 单调递增的 `rowid` 作 tiebreaker（`message_id` 是 UUID，不保证时间序；同一微秒内的 `created_at` 平局由 rowid 决出）。

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

`get_session_outfit_context`：取会话中最近一条 `task_type ∈ {outfit_recommend, outfit_modify}` 且产生搭配的 assistant 消息，返回 `{current_outfit_id, current_item_ids}`。这是多轮追问"当前搭配"的来源，跨刷新/跨设备保持一致。

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

## 6. 长期记忆系统

### 6.1 类别与自动提炼

`memory_extractor.extract_memories(request)` 用确定性规则从请求提炼 `{category, content, meta}`，无 LLM 依赖：

| 类别 | 来源 |
|---|---|
| `color` | 复用 `request_parser.COLOR_ALIASES`；否定极性（"不要黑色"）→ `avoid:xxx` |
| `category` | 复用 `SUBTYPE_ALIASES` |
| `occasion` | 复用 `OCCASION_ALIASES` |
| `formality` | 正式/休闲/商务/简约…词表 |
| `style` | 中文风格词表 → `STYLE_TAG_RULES` tag |
| `habit` | 每天/经常/总是…（低优先级） |

每类上限 2 条防噪声；`normalize_content()` 归一化去重（小写、压缩空白、限 64 字符）。

### 6.2 置信度

`confidence = min(1.0, 0.25 × occurrences + 0.1)`：第 1 次 0.35 → 第 2 次 0.6 → 第 3 次 0.85 → 第 4 次 1.0（封顶）。

### 6.3 手动优先

- `create_manual_memory` 命中同 `(user_id, category, content)` 时 `ON CONFLICT DO UPDATE` 强制 `source='manual'` 并重置 `occurrences=0`。
- `upsert_auto_memories` 读到 `source='manual'` 行时**跳过**，不覆盖。
- 遗忘是软删除（`active=0`）；再次被观察到时 `auto` 记忆复活（`active=1`），`manual` 记忆需重新添加。

### 6.4 注入

- `context/builder.py`：`preferences["memory_profile"] = active_memory_profile(user_id, limit=30)`（非空时注入），随 Context Pack 自动流入扩展任务三 Agent。
- `llm/prompts.py`：`build_agent1/2/3_prompt(..., memory_profile)` 在 user prompt 末尾追加「【用户长期偏好记忆】」段。
- `workflow/graph.py` 旧推荐路径：`_load_memory_profile(user_id)` 加载进 initial state，`_semantic_retriever_node` / `_composer_node` / `_critic_node` 透传。

### 6.5 记忆 API

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET/POST` | `/preferences/{user_id}/memories` | 列表 / 手动新增 |
| `PATCH/DELETE` | `/preferences/{user_id}/memories/{memory_id}` | 编辑（category/content/confidence 全可选）/ 遗忘 |

## 7. 前端契约（apps/web）

- `services/api.js`：新增会话与记忆 9 个方法。
- `components/TaskResultView.vue`：按 `task_type` 渲染的复用载体，`props={payload}`，天气卡/agent 轨迹留在 RecommendPage；历史消息用裁剪后的 `result` 恢复渲染。
- `stores/recommendation.js`：`sessionId` / `messages[]` / `loadSessionHistory` / `newConversation`；`run()` 透传 `session_id`，成功后把完整 payload 追加进 `messages`。
- `pages/RecommendPage.vue`：会话侧栏（列表 + 新建 + 切换 + 删除）；`localStorage {userId}:sf_session_id` 保存当前会话，onMounted 恢复；首次发送自动 `createChatSession`。
- `pages/MemoriesPage.vue`（`/memories`）：偏好记忆管理页（category 中文标签、source 自动/手动 tag、confidence、新增/编辑/遗忘）。

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
- 记忆是规则式提炼（确定性、可测），可能与 LLM 提炼有差距；以置信度 + 手动编辑兜底。
- 跨端：本期仅 Web 前端会话化；小程序仍只传 `user_id/request`（`session_id` 默认空串行为不变）。
- 会话标题自动命名"会话 N"，不做摘要式命名（后续可用 LLM 从首条请求生成）。
