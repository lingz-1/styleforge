# 测试与评估

> 当前执行规范：[StyleForge 评估计划 v2.0](EVALUATION_PLAN_V2.md)。本文件保留已实现测试与历史
> 基线说明；涉及新评估的任务范围、门槛、数据隔离、公开 Benchmark、基线和消融时，以 v2 为准。

## 1. 测试层次

### 静态检查

```powershell
D:\anaconda\envs\style\python.exe -m compileall -q apps\api\styleforge tests evals
D:\anaconda\envs\style\python.exe -m ruff check apps\api\styleforge tests evals
```

### 单元测试

```powershell
D:\anaconda\envs\style\python.exe -m pytest -q
```

测试用例覆盖以下行为：

- Stylist 不选择硬约束失败候选。
- Reviewer 拒绝衣柜外商品。
- 结果集优先选择互不重复的方案。
- 向量存储严格遵守商品 ID 白名单。
- 正式度规则区分西裤/酸洗牛仔裤和乐福鞋/运动鞋。
- 半身裙与裤子一级品类解析。
- 核心槽位子类解析与否定约束。
- 子类词典覆盖主要服装和配饰类别。
- 混合风格衣柜配置和分层选择。

## 2. 全量回归命令

```powershell
D:\anaconda\envs\style\python.exe -m pytest -q
D:\anaconda\envs\style\python.exe -m ruff check apps\api\styleforge tests evals
```

如果失败，先修单元测试，不要直接启动 UI。

订单相关测试还应覆盖：多商品子行继承订单状态、交易关闭/未收货排除、成功订单出现退款信号时排除，以及提交接口不能通过手工属性覆盖绕过准入门槛。

## 3. 端到端测试用例

### 空衣柜

预期：`infeasible`，列出 `top/bottom/footwear` 缺失，不加载 FashionCLIP。

### 面试＋硬子类

```text
需要正式但有年轻感的上班穿搭，衬衫配半身裙和乐福鞋，偏灰色或蓝色，不要红色，不要高跟鞋。
```

预期：

- `top → shirt`。
- `bottom → skirt`。
- `footwear → loafers`。
- 排除 `high_heels` 和红色。
- 方案间优先不重复单品。

### 泛称多配饰

```text
晚上约会，想穿中长连衣裙和平底鞋，再搭配一些配饰。
```

预期：

- 必需槽位包含 `accessory_1`、`accessory_2`。
- 两个配饰 ID 不相同。
- UI 展示连衣裙、鞋和两件配饰图片。

### 指定配饰子类

```text
衬衫配西裤和乐福鞋，搭配金属腕表和托特包，不要运动鞋。
```

预期：腕表为 `metal_watch`，包为 `tote_bag`，排除 `sneakers`。

## 4. 检索评估

报告：`artifacts/evaluation/fashionclip_retrieval.json`。

| 指标 | 结果 |
|---|---:|
| 图片同 item type Precision@10 | 0.9272 |
| 图片同 main category Precision@10 | 0.9574 |
| 图片同 item type Top-1 | 0.9440 |
| 随机 item type 一致率 | 0.102565 |
| 相对随机提升 | 9.04× |
| 文本品类 Macro Precision@10 | 0.9550 |

`exact_item_top1_rate` 低于 1 不代表索引错位。数据中存在相同或近似重复图，多条向量会并列；索引使用向量重建和最高相似度验证映射一致性。

## 5. 尚缺的评估

- 人工标注的“是否适合场合”测试集。
- 正式度规则的准确率、召回率和校准曲线。
- 搭配整体偏好的人类 A/B 评价。
- 多配饰组合的重复率和生成延迟。
- 子类解析中英文混合测试集。
- LLM Agent 接入后的结构化输出有效率和降级率。

## 6. v3.3 P2.5 任务路由基线（已验证）

- 固定用例：`evals/cases/task_routing.json`，60 条，六类任务各 10 条。
- runner：`D:\anaconda\envs\style\python.exe -m evals.runners.evaluate_task_routing`。
- 输出：`artifacts/evaluation/task_routing_baseline.json`，包含准确率、逐类准确率、混淆矩阵和错误明细。
- 结果（2026-08-12）：60/60 正确，总准确率 100%，六类逐类准确率均为 100%，失败样本 0。
- 门禁：`tests/test_task_routing_eval.py` 锁定 60 例、每类 10 例与 100% 准确率；`tests/test_task_router.py` 29 条单测覆盖规则分支。
- 边界：该评估只证明 Task Router 分类表现，不评价检索、搭配、五维分数或用户满意度。

**2026-08-12 扩语料暴露并修复的路由盲区**（多样化输入驱动，非语料标注问题）：

1. `换成` 未匹配：`把外套换成风衣` 原落 `outfit_recommend`。`_MODIFY_PATTERNS[0]` 的 `换(...)` 分支补入 `成|到|为`。
2. `衬衫` 等上衣词不在 modify 槽位清单：`这件衬衫不好看` 原落 recommend。`_MODIFY_PATTERNS[1]` 补入 `衬衫|毛衣|卫衣|大衣|风衣|夹克|西装|牛仔裤`。
3. `该补点什么` 的插入语 `点` 中断 `该补什么` 字面匹配：`帮我看看该补点什么颜色` 原落 recommend。`_GAP_PATTERNS[1]` 改为 `该补(?:点)?什么|应该补(?:点)?什么`。

当前仅完成 P2.5 的路由切片；Wardrobe Fixtures 和五维固定穿搭 benchmark 仍在尚缺列表中。

检索指标不能替代整体穿搭质量指标。作品集报告必须分开陈述。

## 7. 五类扩展业务验收

核心测试文件：

- `tests/test_context_pack.py`：请求、衣橱统计、五维偏好进入共享上下文。
- `tests/test_knowledge_retriever.py`：本地知识分段、来源和检索命中。
- `tests/test_extended_tasks.py`：五类业务均验证真实三次模型调用、无 degraded trace、局部锁定、单品锚点、新品不落库、目标风格缺口和失败持久化。
- `tests/test_extended_task_api.py`：通过真实 FastAPI HTTP 层执行任务、按用户读取运行记录和越权 404。

扩展专项还必须验证：未配置 LLM 时返回失败而非确定性结果；Agent 2 引用衣橱外 ID、锁定槽位被改、证据来源越界时硬失败；Agent 3 拒绝时不发布草稿。

前端验收包括 `apps/web` 的 `npm.cmd run build` 和小程序主推荐脚本的 `node --check`。小程序 WXML/WXSS 的开发者工具真机预览仍属于人工验收项。

2026-08-10 严格三 Agent、Schema v8 与完成契约 v3.1 结果：`compileall` 通过、Ruff clean、Pytest 183 passed；覆盖 Agent 1 多事实候选汇总、v7→v8 数据保留迁移、合法澄清状态、Agent 2 不完整草稿修复、Agent 3 拒绝后的有限重做、缺口清单与 `missing_elements` 一致性，以及健康检查运行版本标识。Vue 生产构建成功，小程序脚本及配置 JSON 检查通过。唯一 warning 是 FastAPI `TestClient` 的第三方适配层弃用提示。

## 8. 天气上下文验收

- `tests/test_weather_tool.py`：地点解析、日期解析、逐日事实映射、湿度聚合、Provider 失败和默认地点，全部使用注入 Transport，禁止实时网络依赖。
- `tests/test_workflow_llm.py::test_workflow_weather_context_is_shared_with_three_agents`：固定四次 LLM 响应和一次天气工具响应，验证同一 Agent 1 二次细化、Agent 2/3共享事实、工具只调用一次及输出 trace。
- 普通请求回归必须仍为三次 LLM 调用，避免 Context Router 无条件增加成本。
- 实时 Provider 烟测只验证外部连通性，不替代离线合同测试。

2026-08-10 P5 Weather Tool 验收结果：`compileall`通过、全项目Ruff clean、Pytest 189 passed、Vue Vite生产构建成功。Pytest仅有FastAPI TestClient第三方弃用提示；Vite仅有现有大chunk和第三方PURE注释提示。

## 9. 记忆提炼评估（LLM 基线）

纯 LLM 的偏好证据提炼（`llm/memory_prompts.py`）无法用确定性单测衡量，单独建评估集：

- 案例：`evals/cases/memory_extraction.json`，33 例**口语化**请求（多属性拆分、否定拆分、一次性场景忽略、global/contextual 判定、appearance/shopping 维度归位、双否定、重复回避，以及习惯/条件规则/比较/材质三分/印花归 style/英语混排/强厌恶/天气习惯/多场合风格拆分等）。8 例为无长期偏好证据（应提炼空），用于锁空误报。
- runner：`D:\anaconda\envs\style\python.exe -m evals.runners.evaluate_memory_extraction`，真实 LLM 逐例提炼后按 `(dimension, attribute, value)` 归一化匹配，输出 Evidence 级 P/R/F1、极性一致率、scope 一致率。
- 输出：`artifacts/evaluation/memory_extraction.json`。
- 门禁：`tests/test_memory_extraction_eval.py` 锁定 33 例计数与关键规则覆盖（习惯/条件/材质三分/场景绑定/英语混排/一次性/多场合拆分/印花归 style 等）；`match_case` 为纯函数，离线可测。
- 基线（2026-08-12，真实 DeepSeek，prompt `memory-evidence-v2.4`）：连续 3 次运行分布 F1 **0.847 / 0.911 / 0.921**、Precision 0.837~0.872、Recall 0.857~0.976、极性一致率 1.00、scope 一致率 0.972~0.976；run2/run3 空案例零误报，run1 有 1 例空误报。run3 精确复现 v2.3 基线（F1 0.9213），证实 v2.4 与 v2.3 内容等价、指标一致。

**LLM 非确定性提示**：`extract_language_evidence` 使用默认 temperature=0.2，同一请求多次调用结果会漂移（例如某例在 5/5 与空之间切换），单次跑 F1 会在此基线附近波动（本批 0.85~0.92）。多轮聚合指标意义有限，产品行为以单次提炼为准；评估对比应取多次运行区间而非单点。

**v2.4 已修复的偏差**（prompt v2.0→v2.4，`SCOPE_GUIDE` 7 条确定性规则 + `DISAMBIGUATION_GUIDE` + 移除 item + 弱信号）：

1. **global/contextual 判定不稳** ✅ 习惯陈述（平时/日常/一直/总是/就爱）中的单品偏好 → global；否定/厌恶句默认 global；一次性场景不改其他偏好 scope。mem-05 由漂移变为确定性命中。
2. **attribute 归类分歧** ✅ 「正式场合」→ occasion（仅作程度修饰才 formality）；「牛仔」默认 category（仅面料质感才 material）；「花花绿绿/印花」→ style 非 color；occasion value 用裸场合名词。
3. **多属性拆分漏/多** ✅ 弱信号（还好/可以/还行）不提炼；「配饰多了累赘」按配饰多=negative 不翻转；few-shot 扩到 6 例覆盖习惯句/场合消歧/弱信号。
4. **item 级不提炼** ✅ `ITEM_RULE`：单件指代（这件/那条/这双）不提炼为偏好，item 级由行为事件产出。
5. **语境修饰词过提取** ✅ 本次 3 次运行中 2 次空案例零误报；contextual occasion/category 过提取明显收敛（残留 1 次 run 的 1 例空误报属非确定性波动）。

门槛建议：F1 ≥ 0.85 已达成且多跑稳定在区间内。每次 prompt 改动后用本 runner 回归对比，取多次运行区间。

## 10. 多轮对话与记忆健壮性补测（D2 / D4 / D6）

围绕多轮对话 follow_up 路由与记忆系统生命周期/健壮性补齐的测试与评估：

### D2：follow_up 独立评估集

- 案例：`evals/cases/follow_up.json`，58 条**口语化**请求（28 follow_up + 26 fresh + 1 edge + 4 known_gap），人工按意图标注。
- runner：`D:\anaconda\envs\style\python.exe -m evals.runners.evaluate_follow_up`，输出 `artifacts/evaluation/follow_up.json`。
- 结果（2026-08-12）：54 条非 known_gap 案例准确率 1.0，`false_positive_follow_up_rate` 0，`missed_follow_up_rate` 0；4 条 known_gap 被排除出主准确率并单列报告（`known_gaps`）。
- **known_gap 设计**：标注为 `known_gap: true` 的案例是检测器的**真实盲区**——如补充信号（`加一顶帽子`）当前判 fresh、方向动词（`袖子卷起来`）、无 adjust 词的新搭配词（`给我搭一身休闲的`）。它们不被计入主准确率，避免回归门禁被已知盲区污染；门禁断言每个 known_gap 案例的检测结果**确实偏离**意图，防止盲区无声消失。
- 覆盖的语义规则：短调整、槽位替换、否定调整、属性抱怨、样式调整、歧义短跟进（`别要蓝色`）、物品疑问式跟进（`这件大衣是不是有点旧了`）、场景新需求、fresh 词覆盖 adjust 词（`更正式一点的搭配建议`→fresh）、无 adjust 词的提问、元指令（`别推荐了`→fresh）。
- 门禁：`tests/test_follow_up_eval.py` 在 CI 锁定行为，路由改动导致漂移会先在此失败（含 known_gap 案例必须真实偏离的断言）。

### D4：生命周期与 consolidation 补用例

- `tests/test_memory_consolidation.py` 新增：**幂等重跑**（二次 consolidate 为 no-op）、**用户隔离**（consolidate u1 不动 u2）、**正负极性不合并**（矛盾是数据不是重复，两条证据都保留）。
- `tests/test_memory_generalization.py` 新增：**global 冲突忽略 contextual 对立**。
- **修复的真实 bug**：`_apply_conflict_penalty` 原先对同 attribute 的**任意对立行**打 `conflict_with` 标记并降置信度，会把 contextual 场景偏好误判为全局冲突，且只惩罚 global 侧造成不对称。已改为只认同为 global 的对立行。回归用例锁定该行为。

### D6：健壮性补用例

- `tests/test_memory_generalization.py::test_preference_memory_is_isolated_between_users`：u1 事件/证据/偏好不泄露给 u2，跨用户矛盾不打标记。
- `tests/test_session_multiturn.py::test_no_llm_skips_memory_extraction_gracefully`：无 LLM 时任务正常完成，记忆提炼优雅跳过、不落任何证据（不崩溃、不虚构）。

### D7：多轮对话流程（2026-08-12，M3 风格口语链）

- `test_session_multiturn.py::test_mixed_chain_fresh_scene_does_not_rewrite`：推荐 → `换成`换外套 → fresh 婚礼场景 → 整体改色。验证 **fresh 场景在活跃会话内不被 follow_up 改写吞掉**（保留 recommend 路由），且新计划成为下一轮修改的基线（R2 的 blazer 不泄漏进婚礼计划）。
- `test_session_multiturn.py::test_negative_feedback_then_footwear_swap`：推荐 → `这套太严肃了`（整体 session_follow_up）→ `换成乐福鞋`（footwear 槽位）。验证否定反馈走整体调整、下一轮换鞋槽位锁定连衣裙，修改链逐轮携带最新搭配。

> 注：consolidation 的 `_dedupe_key` 不含 scope——同 dimension/attribute/value/polarity 的 global 与 contextual 证据会被去重为一条。当前为既有行为，是否应把 scope 纳入去重键待设计确认。

## 11. Polyvore 官方 Compatibility/FITB 基线（2026-09-01）

- adapter/指标/基线：`evals/polyvore_benchmark.py`；
- runner：`D:\anaconda\envs\style\python.exe -m evals.runners.evaluate_polyvore_baselines`；
- 合同测试：`tests/test_polyvore_benchmark.py`，12 项；
- 全量统计：disjoint 30,290/15,145、nondisjoint 20,000/10,000、Maryland 6,081/3,076 条 Compatibility/FITB；
- 映射门禁：两个主 split 的 token、metadata、semantic category、image 缺失均为 0；
- 视觉运行：两个主 split 各 256 条 test Compatibility/FITB，真实编码/复用 13,055 张图片；
- Maryland 限制：包内无 item/category/image 映射，Category/FashionCLIP 如实标记 unavailable。

详细方法、指标表、数据异常和复现命令见 [Polyvore 官方离线基线](POLYVORE_BASELINES.md)。全量统计与视觉抽样必须分开陈述，抽样 FashionCLIP 不得标成完整公开 Benchmark。
