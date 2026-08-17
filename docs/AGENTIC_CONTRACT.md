# Agentic Contract（Stage 0 冻结契约 · 收敛版）

> 目标架构：**Semantic Agent + Deterministic Environment**。
> 本文件定义新世界的数据边界与工具边界，**不改变任何运行时行为**。
> 契约类型定义在 `apps/api/styleforge/models/agentic_contract.py`。

最终哲学一句话：

> **程序不理解"用户想怎么穿"；Agent 不决定"数据库和物理结构是否合法"。**

```
                 User
                  ↓
           InteractionContext
                  ↓
          ┌ Semantic Agent ┐
          │ 理解目标与要求 │
          │ 自主搜索/决策  │
          └───────┬───────┘
                  ↓
            ModifyPlan
                  ↓
      ┌ Deterministic Environment ┐
      │ ID / ownership / transaction│
      │ region/layer/occupancy      │
      └───────────┬────────────────┘
                  ↓
           Semantic Review（LLM/Critic）
          用户原意 / 搭配质量
                  ↓
           PASS / REPLAN
```

## 1. 职责归属（三层，不是四层）

| 层 | 归属 | 管什么 | 不碰什么 |
|---|---|---|---|
| **Semantic Agent** | Agent | 理解目标与要求、搜索、决策、replan | 数据库与物理结构是否合法 |
| **Deterministic Environment** | 程序 | 事实供给、事务执行、结构合法性 | "不要红色 / 上衣别动 / 不要运动"等语义 |
| **Semantic Reviewer / Critic** | LLM | 是否违背用户原意、是否擅自放宽明显要求、搭配质量 | 物理合法性（那是 check_environment） |

**不存在** hard/soft constraint 判定。要求强弱、妥协空间、是否询问用户，全部由 Agent 从自然语言本身理解——程序不分类，防止重新长成 `constraint_spec v2`。

核心不变式：**程序不再预测"结果应该长什么样"，只验证"动作在环境中是否合法"；是否忠实用户原意，由 Semantic Reviewer 判定。**

## 2. InteractionContext（前端 grounding，边界不变）

```python
InteractionContext:
    active_outfit_id: str | None   # 点击"在此基础上修改"，或对话已定位
    selected_item_id: str | None   # 点击某件单品（仅 grounding，不产生操作）
```

**点击单品 ≠ REPLACE。** 点击只设 `selected_item_id` 并聚焦输入框，用户随后输入文字，由 Agent 理解。既支持纯聊天，也支持用 UI 消歧。

**grounding 消歧顺序（消息到达时）：**
1. 前端显式 `InteractionContext`（最强）
2. Agent 对照 `visible_outfits` 自主定位（如"第二套鞋子换乐福鞋"）
3. 两者都消不了歧且影响修改 → `ask_user`

点击是消除歧义的辅助，不是修改流程的前置条件。

## 3. EnvironmentFacts（程序供给）

```python
EnvironmentFacts:
    interaction: InteractionContext
    visible_outfits: [OutfitSnapshot]   # 对话中用户看到的搭配
    active_outfit: OutfitSnapshot | None
    selected_item: ItemSnapshot | None
    wardrobe_summary: {...}
    weather: {...} | None
    memory_profile: {...}
```

**明确不在 facts 里（Agent 侧不可写）：** 任何 hard/soft 分类、`replaced_item_ids` / `locked_item_ids`、`relaxation_level`、预期结果形状。物理合法性 → `check_environment`；原意忠实度 → `review_outfit`。

## 4. UserIntent（Agent 理解产物）

```python
UserIntent:
    message: str
    goal: str                        # 自然语言目标，不进状态机
    requirements: [str]              # 语义描述："上衣保持" / "鞋倾向深色"
```

**没有 Constraint.kind（hard/soft）。** 例如：

```text
用户："上衣别动，鞋最好深色，整体文艺一点"
Agent:
  goal: 整体更文艺
  requirements: ["上衣保持", "鞋倾向深色"]
```

强弱与妥协空间由 Agent 理解，程序不判定。`goal + requirements` 的作用：①引导 Agent 自身行动；②作为 `review_outfit` 核对原意的依据。

## 5. 工具契约（7 个高层工具 + 1 个语义 gate）

| 工具 | 输入 | 返回 | 边界 |
|---|---|---|---|
| `inspect_outfit` | outfit_id | `OutfitSnapshot` | 只读快照 |
| `search_wardrobe` | query + 过滤 | `{results, matched}` | **空结果只是事实**，程序不自动 ask_user |
| `modify_outfit` | outfit_id + `ModifyPlan` | 事务执行后的 `OutfitSnapshot` | 见 §7 事务执行 |
| `check_environment` | 候选 outfit + facts | `CheckEnvironmentResult` | **纯物理合法性**（见下） |
| `search_knowledge` | query | `[KnowledgeEvidence]` | RAG，"为什么这么搭" |
| `get_weather` | 地点/时间 | 天气事实 | 程序解析，Agent 消费 |
| `ask_user` | question | 暂停等用户回复 | 返回后从 UserIntent 重新进入 loop |
| `review_outfit`（gate，非业务 Tool） | ReviewInput | `ReviewResult` | 原意忠实度 + 质量 |

**`check_environment` 只验（纯确定性程序）：**
- ID 真实、属于当前用户
- 操作对象存在、ModifyPlan 可执行
- `(region, layer) × exclusive` 结构冲突（§8）
- 数据/schema 有效、可落库

**它完全不理解**："不要红色"、"上衣别动"、"不要太运动"、"整体文艺"。这些全部属于语义层。

**`review_outfit`（LLM/Critic）输入：** 原始用户消息 + InteractionContext + 修改前 outfit + 修改后 outfit + Agent goal/requirements。负责判断：是否违背用户原意、是否擅自放宽明显要求、是否真正完成目标、搭配质量是否合理。

**`search_wardrobe` 的结果必须暴露每件单品的结构信息**（`ItemSnapshot.structure: {region, allowed_layers, occupancy, exclusive}`）——否则 Agent 没有依据决定 placement。`ModifyOp.placement` 同时适用于 `add / replace`（replace 同样改变结构层级）。

**不要继续拆工具**：`replace_shoes / find_pink_item / add_accessory / resolve_subject / relax_color` 都不允许——否则只是把 case explosion 从规则层搬到 Tool 层。

## 6. 状态模型

```
Session（会话内，前端/程序维护）:
    active_outfit_id
    selected_item_id

Database:
    saved_outfit           # 由 save_outfit(outfit_id) 命令创建
```

**没有** `confirmed_outfit` 长期状态。"添加至穿搭集"是 `SaveOutfitCommand` 事件，不存在"确认了但暂不保存"的中间态。

## 7. Bounded Agent Loop（事务执行）

```
用户输入 + InteractionContext + EnvironmentFacts + Memory + Skill/RAG
        ↓
   Agent 理解（goal + requirements）
        ↓
   自主调工具（7 个高层工具）
        ↓
   modify_outfit（事务执行）
        ↓
   check_environment（物理合法性门禁）
        ↓
   review_outfit（语义门禁：原意忠实度 + 质量）
        ↓
   PASS → 返回穿搭 + 更新 active_outfit
   任一失败 → 问题作为 Observation 返回 Agent replan
```

**事务执行 invariant（commit gate = environment PASS + semantic review PASS）：**
```
Current Outfit
  → clone / draft
  → Apply ModifyPlan（全部 add/remove/replace）
  → check_environment（物理合法性，验证完整结果态）
  → review_outfit（原意忠实度 + 质量）
  → PASS → COMMIT
  → FAIL → DISCARD → issues 作 Observation 返回 Agent replan
```

**不要以 `check_environment` 为 commit gate。** 否则 check PASS 即已 commit，语义 review 之后才发现"违背用户原意"（如用户说"上衣别动"、Agent 却换了上衣），真实会话状态已被污染，replan 建立在脏状态上。因此 commit 延迟到两个 gate 都 PASS。

**必须验证完整 ModifyPlan 执行后的候选状态，不能逐步校验。** 例：
```
replace:
  remove old_top
  add new_top
```
逐步校验会在 `remove old_top` 后误判"缺上衣 → INVALID"，杀掉原本合法的替换。事务执行由程序保证。

**护栏：** `max_steps=8`、`check_environment` 强制门禁、`ask_user` 上限 2、最终候选必须过 `review_outfit`、每步工具调用与理由进 trace（可归因）。

## 8. 结构 ontology（region × layer × occupancy）

传统 `top×1 / bottom×1` 会把 `T恤+衬衫+针织+大衣` 误判为冲突。改为极小的结构本体：

```python
region:  upper_body | lower_body | full_body | feet | accessory
layer:   base | mid | outer
occupancy: 该衣服物理占用的 body region 集合
```

每件衣服定义自己的 `allowed_region / allowed_layers / occupancy`：

| 单品 | allowed_region | allowed_layers | occupancy |
|---|---|---|---|
| T恤 | upper_body | base | upper_body |
| 衬衫 | upper_body | base \| mid | upper_body |
| 开衫 | upper_body | mid \| outer | upper_body |
| 大衣 | upper_body | outer | upper_body |
| 连衣裙 | full_body | base | upper_body + lower_body |
| 半身裙 | lower_body | base | lower_body |
| 鞋 | feet | — | feet |

于是：
- `T恤(base) + 开衫(mid) + 大衣(outer)` → **结构合法**
- `连衣裙(full/base) + 半身裙(lower/base)` → **冲突**（同 `(lower, base)` 且两者 exclusive）
- `连衣裙(full/base) + 大衣(upper/outer)` → **不冲突**（`(upper,base)` vs `(upper,outer)` 不同 layer）

**冲突判定粒度是 `(occupied_region, layer)`，不是 `region` 本身。** 否则连衣裙+大衣会被误判冲突。

**accessory 是结构 cardinality，不是唯一性：** 耳环、项链、手表、包都落在 `(accessory, *)`，显然可共存。`GarmentStructure.exclusive: bool`：

| 单品 | (region, layer) | exclusive |
|---|---|---|
| 鞋 / 裤 / 裙 | 各自唯一 cell | `True` |
| 项链 / 耳环 / 手表 / 包 | accessory | `False`（可多个并存） |

`exclusive` 是真正的结构 cardinality（物理上能否同时存在），不是穿搭语义规则，因此放 Environment 合理——避免日后为"为什么项链和耳环不能同时出现"再加 case patch。

**程序验证的是"结构上能不能同时存在"，不是"这样穿好不好看"**——后者归 Agent + Skill/RAG + Reviewer。

**placement 决策权在 Agent：**
```python
add(shirt_2, placement={region: upper_body, layer: mid})
```
程序只验证：shirt_2 是否允许 mid、当前 mid occupancy 是否冲突。即 **Agent 决定"怎么穿"，程序只判断"这个动作在环境中是否合法"**。

## 9. ModifyOp 收敛

```python
ModifyOp.action: add | remove | replace      # 只有真正改变环境的动作
```

**没有 `lock / keep`。** 它们不是 Environment Action，而是用户语义。保留 `lock(item)` / `keep(item)` 会重新长成 `locked_item_ids / kept_item_ids / replaced_item_ids` → 回到 422 老路。Agent 理解"上衣别动"，但不必编码成环境状态；是否遵守由 `review_outfit` 判定。

## 10. 新旧边界对照（迁移映射）

| 旧概念（现存代码） | 新世界归属 |
|---|---|
| `OutfitContext.current_outfit_id` | → `InteractionContext.active_outfit_id` |
| `TaskExecutionInput.item_id`（点选直达） | → `selected_item_id`（仅 grounding，不再自动路由/REPLACE） |
| `request_spec` 状态机（REPLACE/subject/target/strength） | 删除；语义归 `UserIntent.goal + requirements` |
| `relaxation.py` L0-L3 | 删除；放宽策略归 Agent 自主 |
| `decision.py` 语义分类 | 删除；不判定 hard/soft |
| `candidate_service` feasibility 状态机 | 只留检索 = `search_wardrobe` |
| `extension_validation` | 拆成 `check_environment`（物理）+ `review_outfit`（语义） |
| `slots.py` 单槽位模型 | → region/layer/occupancy 本体（§8） |
| EXT-001 flexible 补丁 / subject 捕获 / replaced-locked partition | 删除 |
| `context/builder.py` + memory 系统 | 保留升级 = EnvironmentFacts 供给方 |

## 11. 实施阶段（并行试点，先不删旧）

```
Stage 0  冻结契约（本文件）                     ← 当前
Stage 1  前端 grounding（InteractionContext + SaveOutfit 命令）
Stage 2  OUTFIT_MODIFY Agent Loop shadow mode（与 legacy 并行双跑）
Stage 3  unseen phrasing A/B（真实 DeepSeek）
Stage 4  切流
Stage 5  删除 legacy 状态机
```

**强调：不要 Stage 1 后立刻删 `request_spec / relaxation / decision`，先并行验证。**

**成功指标**（不只是 `554 passed → xxx passed`）：

> **新增自然语言修改用例时，需要修改系统代码的比例。**
> 旧架构：新增 20 个语义 case → 改约 8 次程序逻辑；
> 新架构：新增 20 个 case → 只改 0–1 次工具/硬边界。

**Stage 3 unseen phrasing 用例集**（含组合语义——真正要测的不是"识别换鞋同义词"，而是组合需求 / 跨 outfit 指代 / 多轮修正 / 局部保留+全局目标并存时，还需不需要改系统代码）：

```
"鞋换一下"
"这套太正式了"
"上衣留着其他随便"
"第二套挺好但鞋不喜欢"
"换得轻松一点但不要运动感"
"就这个，再加点配饰"
"粉色没有的话你看着办"
"必须黑色但衣柜没有黑色鞋"（应触发询问，不得静默换棕）
"这双鞋保留，但整体别这么运动"
"上衣不要换，裤子你看着改，鞋最好深色"
"第二套挺好，改得和第一套差不多，但别用第一套那双鞋"
"刚才那样不对，鞋还是换回来，其他保留"
```
