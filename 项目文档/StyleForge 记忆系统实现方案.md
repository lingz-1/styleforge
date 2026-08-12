# StyleForge 记忆系统实现方案

StyleForge 的记忆系统不应只是保存“用户说过什么”，而应该持续理解用户在不同场景下表现出来的偏好，并区分当前会话里的临时要求、近期倾向和长期稳定偏好。最终目标是让三个 Agent 在每次推荐时拿到**当前真正相关的用户记忆**，而不是把所有历史偏好全部塞进 Prompt。

整体采用 **实时交互状态 + 行为事件 + 偏好证据 + 短长期偏好 + 上下文选择** 的结构：

```text
用户交互
   ↓
实时 Interaction State
   ↓
Interaction Event
   ↓
Preference Evidence
   ↓
Preference Aggregator
   ↓
短期 / 长期 Preference Memory
   ↓
Context-aware Memory Resolver
   ↓
Relevant Memory Pack
   ↓
Agent 1 / Agent 2 / Agent 3
```

其中最重要的设计原则是：

> **行为是事实，Evidence 是对事实的解释，Preference 是系统当前对用户偏好的假设。**

系统不应该因为用户换掉一次贝雷帽，就直接永久记住“用户不喜欢贝雷帽”。

---

## 一、实时交互状态

实时状态只处理当前会话和当前 Outfit。

例如当前推荐为：

```text
灰色外套 + 白衬衫 + 黑裤 + 乐福鞋
```

用户说：

> “鞋换休闲一点。”

系统立即保存：

```json
{
  "active_outfit_id": "outfit_103",
  "target_slot": "shoes",
  "locked_slots": ["outerwear", "top", "bottom"],
  "directional_preferences": [
    {
      "attribute": "formality",
      "direction": "decrease"
    }
  ]
}
```

这些信息立即参与下一轮 Local Replacement，但不会直接成为长期偏好。

这一层解决的是：

- “更休闲一点”
- “颜色浅一点”
- “鞋换掉”
- “上衣不要动”
- “还是上一套好”

这类高度依赖当前上下文的短句。

实现上主要保存在 LangGraph State 和 Redis/Session Cache 中，会话结束后清理或归档。

---

## 二、行为事件与偏好证据

所有可能反映用户偏好的行为，首先记录为原始 Event，例如：

```text
outfit_selected
outfit_rejected
item_replaced
item_rejected
feedback_submitted
style_requested
compatibility_checked
explicit_preference
```

例如用户在一次面试搭配中换掉一顶贝雷帽，只记录：

```json
{
  "event_type": "item_replaced",
  "item_id": "hat_014",
  "context": {
    "request": "明天面试穿什么",
    "occasion": "interview"
  },
  "item_features": {
    "category": "beret",
    "style": ["french", "vintage"]
  }
}
```

这一步只保存事实。

之后 Evidence Extractor 再判断这个行为说明了什么。

如果用户明确说：

> “我不喜欢贝雷帽。”

可以生成强证据：

```json
{
  "attribute": "category",
  "value": "beret",
  "polarity": "negative",
  "strength": 0.9,
  "scope": "global",
  "source": "explicit_statement"
}
```

如果用户只是：

> “这套里的帽子换掉。”

则只能形成弱证据：

```json
{
  "attribute": "category",
  "value": "beret",
  "polarity": "negative",
  "strength": 0.25,
  "scope": "current_context",
  "source": "item_replacement"
}
```

因此系统能够区分：

```text
不喜欢当前这件
≠
不喜欢这一品类
≠
不喜欢这个属性
≠
长期不喜欢
```

---

## 三、短期与长期偏好

Evidence 会经过 Preference Aggregator 合并，形成真正参与推荐的 Preference Model。

这里必须把 **生命周期** 和 **置信度** 分开。

例如：

> “这个月想多穿粉色。”

可以是：

```text
confidence = 0.95
lifecycle = short_term
```

而系统通过较长时间观察发现用户可能偏好 American Vintage：

```text
confidence = 0.72
lifecycle = long_term_candidate
```

所以 Preference 至少区分：

```text
session
short_term
long_term_candidate
long_term
```

短期记忆用于：

- 最近喜欢浅色；
- 最近想穿宽松；
- 最近想尝试新中式；
- 最近觉得推荐太正式。

它带有 `expires_at` 或时间衰减。

长期记忆用于：

- 不穿高跟鞋；
- 长期偏好宽松版型；
- 通勤喜欢极简；
- 明确避免荧光色；
- 长期喜欢某些风格或材质。

长期记忆可以由三种方式形成：

```text
用户主动确认
→ 直接长期

多次跨场景稳定出现
→ 自动升级

短期偏好持续被验证
→ short-term → long-term_candidate → long-term
```

---

## 四、上下文感知

“短期/长期”只表示记忆持续多久，还需要另一个维度：

> **这条偏好在什么场景下成立？**

例如用户完全可能同时存在：

```text
上班 → 喜欢极简
周末 → 喜欢 American Vintage
演唱会 → 喜欢 Streetwear
夏季 → 喜欢浅色
冬季 → 喜欢深色
```

所以 Preference 需要保存 Scope：

```json
{
  "attribute": "style",
  "value": "minimalist",
  "lifecycle": "long_term",
  "scope": {
    "type": "contextual",
    "occasion": ["work"]
  }
}
```

而：

```json
{
  "attribute": "style",
  "value": "american_vintage",
  "lifecycle": "long_term",
  "scope": {
    "type": "contextual",
    "occasion": ["casual_weekend"]
  }
}
```

这样两个偏好不会互相冲突。

只有当：

```text
相同 attribute
+ 相同 context
+ 相反 preference
```

时，才算真正冲突。

---

## 五、隐式偏好识别

隐式偏好不能简单采用：

```text
出现 3 次 → 用户喜欢
```

更适合 StyleForge 的方法是 **对比式偏好推断**。

假设系统给出 A/B/C 三套方案，用户选择 B。

系统不应该把 B 中所有属性都记成偏好，而应该比较：

```text
B 与 A/C 有什么稳定差异？
```

例如：

```text
B：宽松、低饱和、休闲鞋
A/C：修身、高饱和、正式鞋
```

那么这一次行为可能贡献：

```text
relaxed fit +
muted colors +
casual footwear +
```

如果不同请求中反复出现相同模式，才逐渐形成偏好。

同样，用户反复选择某品牌，也不能立即推出：

> 用户喜欢这个品牌。

真正偏好的可能是这个品牌背后的：

- 价格；
- 极简；
- 版型；
- 材质；
- 色彩；
- 实穿性。

因此行为分析应该比较“被选择项”和“未选择项”的属性差异。

---

## 六、Preference Model

最终偏好建议保存为类似：

```json
{
  "dimension": "garment",
  "attribute": "fit",
  "value": "relaxed",
  "polarity": "positive",

  "lifecycle": "long_term",

  "scope": {
    "type": "contextual",
    "occasion": ["casual"]
  },

  "confidence": 0.81,

  "support_score": 4.7,
  "contradiction_score": 0.8,

  "support_count": 6,
  "contradiction_count": 1,

  "source_summary": {
    "explicit": 1,
    "selection": 3,
    "replacement": 2
  },

  "decay_policy": "slow"
}
```

不要把类别全部写死成固定 ENUM。

建议采用：

```text
dimension
attribute
value
```

例如：

```text
style.aesthetic = american_vintage
garment.fit = relaxed
garment.material = linen
appearance.color_family = muted
shopping.brand = COS
shopping.price_band = budget
```

以后新增 neckline、pattern、layering、accessory 等都不用改数据库结构。

---

## 七、实时调整与衰减

Preference Model 不是生成以后就不变。

新 Evidence 到来后应该实时更新：

```text
新行为
↓
Event
↓
Evidence
↓
Preference Aggregator
↓
更新 support / contradiction / recency
↓
更新 confidence
```

例如用户长期偏好宽松，但连续多次在正式场景选择修身：

系统不应该立刻删除“喜欢宽松”，而是可能形成：

```text
casual → relaxed
formal → fitted
```

即把原本看似冲突的偏好重新解释成 Contextual Preference。

不同记忆还应该拥有不同衰减速度：

```text
明确长期偏好 → none / slow
普通长期推断 → slow
近期倾向 → normal
一次性需求 → session_only
```

因此“最近喜欢多巴胺风”几个月不再出现，可以逐渐失效；而“我不穿高跟鞋”不会因为时间过去就自动消失。

---

## 八、Memory Resolver

真正让“上下文感知”发生的是 Memory Resolver。

数据库里可能最终存在大量 Preference，但每个请求不能全部给 Agent。

例如用户问：

> “夏天去海边穿什么？”

系统应该激活：

```text
喜欢宽松
近期喜欢浅色
避免高跟鞋
夏季偏好轻薄材质
```

而压制：

```text
冬天喜欢高领
工作偏好西装
正式活动偏好修身
```

Resolver 根据：

```text
当前 request_signature
当前环境
当前场景
Preference confidence
Scope 匹配度
Recency
```

选出真正相关的记忆。

最后生成一个简洁的：

```json
{
  "session_signals": [
    "当前希望比上一套更休闲"
  ],

  "short_term_preferences": [
    "近期更偏好浅色和轻盈配色"
  ],

  "stable_preferences": [
    "通常避免高跟鞋",
    "整体偏好宽松版型"
  ],

  "contextual_preferences": [
    "休闲周末偏好 American Vintage"
  ],

  "avoidances": [
    "避免荧光色"
  ]
}
```

这就是：

> **Relevant Memory Pack**

---

## 九、三个 Agent 如何使用记忆

三个 Agent 不应该拿完全一样的 Memory Pack。

**Semantic Retrieval Agent** 更需要稳定偏好、近期倾向、场景偏好和单品曝光历史，用于改变检索 query 和候选排序。

**Outfit Composer Agent** 更需要当前 Session Signal、最近拒绝原因、版型/色彩/风格偏好以及 Outfit History，用于实际组合取舍。

**Outfit Critic Agent** 只读取比较可靠的长期偏好、显式约束和当前会话反馈，避免低置信隐式记忆影响最终评审。

因此可以形成：

```text
Preference Memory
       ↓
Memory Resolver
       ↓
┌──────────────┬──────────────┬──────────────┐
│ Retrieval    │ Composer     │ Critic       │
│ Memory Pack  │ Memory Pack  │ Memory Pack  │
└──────────────┴──────────────┴──────────────┘
```

---

## 十、与五维 Rubric 的关系

Memory 不增加“个性化”第六维。

它作为现有五维的用户证据：

```text
需求还原度
→ 是否满足当前用户的有效偏好

请求特异性
→ 是否体现当前请求和用户语境

搭配协调性
→ 仍然主要评价 Outfit 自身

实穿性
→ 结合用户长期穿着习惯和避雷项

新鲜感
→ 直接结合历史 Outfit 与单品曝光
```

用户主动配置的五维权重仍然是最高层的显式设置。

Memory 不应该在后台偷偷改这些权重，只作为三个 Agent 的额外偏好上下文。

---

## 十一、数据存储

建议不要把所有内容都塞进一个 `user_memories` 表。

至少分为：

```text
session_context
```

保存当前会话状态。

```text
interaction_events
```

保存所有原始行为事实。

```text
preference_evidence
```

保存标准化偏好证据。

```text
user_preferences
```

保存系统当前聚合出的短期/长期 Preference Model。

这样数据关系是：

```text
Event
→ Evidence
→ Preference
```

任何一步出了问题，都可以追溯。

而且将来修改 Preference 算法，可以用 Event Store 重新计算，不需要重新收集用户数据。

---

## 十二、代码模块

后端可以集中成一个 `memory/`：

```text
memory/
├── session/
│   └── state_manager.py
│
├── events/
│   └── recorder.py
│
├── evidence/
│   ├── behavior_extractor.py
│   └── language_extractor.py
│
├── preference/
│   ├── aggregator.py
│   ├── conflict_resolver.py
│   └── decay.py
│
├── retrieval/
│   ├── resolver.py
│   └── memory_pack.py
│
└── consolidation/
    └── session_summary.py
```

其中 LLM 主要负责：

```text
自然语言偏好理解
模糊反馈解析
复杂 Scope 判断
会话总结
```

普通代码负责：

```text
Event 存储
Evidence 管理
时间衰减
支持/反对证据聚合
上下文过滤
Preference 更新
```

也就是：

> **LLM 负责理解，确定性程序负责记忆管理。**

---

## 十三、最终工作流

一个完整的记忆闭环最终是：

```text
用户请求
   ↓
Memory Resolver
   ↓
获取与当前请求相关的记忆
   ↓
Agent 1
   ↓
Agent 2
   ↓
Agent 3
   ↓
推荐结果
   ↓
用户选择 / 拒绝 / 修改 / 反馈
   ↓
Interaction State 实时更新
   ↓
Event Store
   ↓
Evidence Extractor
   ↓
Preference Aggregator
   ↓
短期 / 长期 Preference 更新
   ↓
下一次请求继续使用
```

会话结束时再进行一次 Consolidation，对本次会话已经产生的 Evidence 做合并、去重和一致性检查，而不是把会话总结作为唯一的记忆生成入口。

---

这套记忆系统最终可以定义为 **Context-Aware Adaptive Preference Memory**：它同时具有实时状态、短期记忆、长期记忆、隐式行为学习、上下文感知、冲突解析和记忆衰减能力，并且不会把一次性的用户操作错误地固化为长期画像。:::

你可以这样映射：

我们的记忆设计	LangGraph 里怎么实现
当前 Outfit、修改目标、“更休闲”	Graph State + Checkpointer
当前会话多轮状态	thread_id + checkpoint
短期偏好	Store / PostgreSQL 中带 lifecycle=short_term
长期偏好	Store / PostgreSQL 中 lifecycle=long_term
用户行为事件	普通 DB 表 interaction_events
Preference Evidence	preference_evidence 表
Preference 聚合	LangGraph node 或独立 service
上下文选择	memory_resolver node/service
给 3 个 Agent 注入不同记忆	在各 Agent node 前调用 Resolver