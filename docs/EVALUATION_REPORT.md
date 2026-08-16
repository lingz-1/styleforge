# StyleForge 系统功能覆盖评估报告

> 撰写日期：2026-08-14
> 定位：回答「系统实现了哪些功能、每个功能被哪些评估用例覆盖、证据强度如何」。
> 设计依据：[FUNCTIONAL_TEST_CASES.md](FUNCTIONAL_TEST_CASES.md)（60 用例手册）
> p-outfit 专项细节见 [P_OUTFIT_EVAL_TEST_REPORT.md](P_OUTFIT_EVAL_TEST_REPORT.md)

## 0. 结论摘要

系统六大功能全部实现，**评估分两层**：功能级门禁（60 用例手册，锁行为契约/执行路径/隔离/降级）+ 质量级真实评估（独立 LLM 裁判 / 真实 API 验收，锁生成结果质量）。其中**推荐搭配**与**记忆偏好**已有质量级证据；**单品搭配 / 多轮对话**于 2026-08-14 补齐真实 LLM 评估（见 §3.2/3.3 与[开发过程记录](DEVELOPMENT_LOG.md)第 15 节），同时暴露**多轮对话 adjust 全局微调 5/5 崩溃**（EXT-001）与**锚定连衣裙缺配饰/外套**（EXT-002）两处真实系统缺陷，已如实记录。全量回归 **499 passed**（见 §5）。

## 1. 评估体系总览（两层互补）

| 层 | 手段 | 锁什么 | 覆盖 |
|---|---|---|---|
| **功能级** | 60 用例手册 → pytest 门禁（FakeLlm/db fixture，全离线） | 行为契约、执行路径、调用次数、槽位、用户隔离、降级健壮性 | 全部功能 |
| **质量级** | 真实 LLM 独立裁判 / 真实 API 端到端 | 生成结果被独立裁判认可、端到端链路真实可用 | 推荐搭配、记忆提炼、路由、p-outfit |

> 功能级 ≠ 质量级：门禁证明「功能能跑、契约正确」；独立裁判证明「生成结果质量达标」。两者不可互替，本报告如实分层。

## 2. 功能覆盖矩阵

| 功能 | 用例编号 | 门禁落地（测试函数数） | 质量级证据 | 证据强度 |
|---|---|---|---|---|
| 推荐搭配 | 一~五 | [test_workflow_llm.py](tests/test_workflow_llm.py)（11）+ p-outfit 门禁（36） | ✅ p-outfit 独立裁判 100 例×2 + 真实 API 8 请求 | **质量级** |
| 单品搭配（扩展业务） | 三十~三十四 | [test_extended_tasks.py](tests/test_extended_tasks.py)（15） | ✅ 真实 8 例独立裁判（2026-08-14）：锚定率 100%、均分 55.9、pass 50%；发现 one_piece 缺配饰/外套（EXT-002） | **质量级**（含缺陷发现） |
| 多轮对话 | 十八~二十三 | [test_session_multiturn.py](tests/test_session_multiturn.py)（10） | ✅ 真实 5 链（2026-08-14）：槽位替换 100% 命中 + 槽位缺失正确跳过；adjust 崩溃（EXT-001）**2026-08-16 已修复**为 flexible 自主重排 | 质量级（替换）+ 修复 |
| 记忆偏好 | 二十四~二十九 | [test_memory_generalization.py](tests/test_memory_generalization.py)（10）+ [test_memory_consolidation.py](tests/test_memory_consolidation.py)（5）+ [test_memory_extraction_eval.py](tests/test_memory_extraction_eval.py)（8） | ✅ 记忆提炼 33 例真实评估 F1 0.847~0.921 | **质量级**（评提炼，非评推荐增益） |
| 任务路由 | 十二~十七 | [test_task_routing_eval.py](tests/test_task_routing_eval.py)（3） | ✅ 60 条真实评估逐类 100% | **质量级** |
| 天气上下文 | 三十五~三十八 | [test_weather_tool.py](tests/test_weather_tool.py)（14） | — | 功能级 |
| 衣橱与订单 | 三十九~四十四 | 订单导入门禁 + 一次性数据库验收 | ✅ 374 准入 0 未知 | 功能级 + 验收 |
| p-outfit 评估本体 | 四十五~五十 | [test_p_outfit_eval.py](tests/test_p_outfit_eval.py)（29）+ [test_p_outfit_analysis.py](tests/test_p_outfit_analysis.py)（7） | ✅ 100 例×2 真实运行 | **质量级** |
| 评估质量检查 | 五十一~六十 | 并入上述门禁（独立性/预注册/泄漏/溯源/Golden Path） | ✅ | — |

## 2.1 60 用例 ↔ 门禁 ↔ 实测结果 对照表

> 「实测结果」列：`✅` = 门禁在全量回归 498 passed 中通过（2026-08-14 实测）；
> 注明「真实」的为真实 LLM/数据运行证据。门禁测试数 = 文件内 `def test_` 计数。

**一、穿搭推荐主功能（用例一~五）**

| 用例 | 验证指向 | 实测结果 |
|---|---|---|
| 一 基础搭配推荐 | 主链路（回归内） | ✅ 回归通过；真实运行 p-outfit 100 例×2 |
| 二 空衣柜 | [TESTING_AND_EVALUATION.md §3](TESTING_AND_EVALUATION.md) | ✅ 返回 infeasible 不加载嵌入 |
| 三 衣柜缺单个品类 | [test_p_outfit_eval.py](tests/test_p_outfit_eval.py) E 组 `test_pipeline_wardrobe_gap_best_effort` | ✅ 缺口兜底全绿 |
| 四 未配置 LLM | [test_session_multiturn.py](tests/test_session_multiturn.py) `test_no_llm_skips_memory_extraction_gracefully` | ✅ 优雅跳过不虚构 |
| 五 结果集互不重复 | TESTING_AND_EVALUATION.md §1 | ✅ 回归通过 |

**二、解析与约束（用例六~十一）**

| 用例 | 验证指向 | 实测结果 |
|---|---|---|
| 六 面试＋硬子类 | §3 面试＋硬子类 | ✅ 回归通过 |
| 七 泛称多配饰 | §3 泛称多配饰 | ✅ 双配饰槽位 |
| 八 指定配饰子类 | §3 指定配饰子类 | ✅ metal_watch/tote_bag |
| 九 中英文混合子类 | §5 中英文混合 | ✅ 无 other 槽 |
| 十 否定约束排除 | §1 否定约束 | ✅ 被否定单品不出现在方案 |
| 十一 半身裙与裤子 | 槽位归属 | ✅ bottom→skirt/pants |

**三、任务路由（用例十二~十七）**

| 用例 | 验证指向 | 实测结果 |
|---|---|---|
| 十二~十六 六类路由识别 | [test_task_router.py](tests/test_task_router.py)（5 例） | ✅ 42 条固定 Cases 逐类 100%（真实，2026-08-09） |
| 十七 路由分类批量评估 | [test_task_routing_eval.py](tests/test_task_routing_eval.py)（3 例） | ✅ **60 条六类各 10 条逐类 100%**（真实，2026-08-12） |

**四、多轮对话（用例十八~二十三）**

| 用例 | 验证指向 | 实测结果 |
|---|---|---|
| 十八 一轮推荐后整体调整 | [test_session_multiturn.py](tests/test_session_multiturn.py)（10 例） | ✅ 门禁过；真实 5/5 崩溃（EXT-001）**2026-08-16 已修复**：无槽位整体调整走 flexible 模式，agent 自主重排整套 |
| 十九 一轮推荐后换单品 | 同上 | ✅ 门禁过；真实 5 链槽位替换 100% 命中 |
| 二十 新场景不被 follow_up 吞掉 | `test_mixed_chain_fresh_scene_does_not_rewrite` | ✅ 婚礼新计划不被改写 |
| 二十一 负反馈再换鞋链式 | 同上 | ✅ |
| 二十二 会话持久化 | 同上 | ✅ 重启恢复历史 |
| 二十三 多轮上下文用户隔离 | [test_memory_generalization.py](tests/test_memory_generalization.py) | ✅ A/B 完全隔离 |

**五、记忆系统（用例二十四~二十九）**

| 用例 | 验证指向 | 实测结果 |
|---|---|---|
| 二十四 长期偏好沉淀 | test_memory_generalization.py（10 例） | ✅ 牛仔偏好入长期记忆 |
| 二十五 记忆提炼批量评估 | [test_memory_extraction_eval.py](tests/test_memory_extraction_eval.py)（8 例） | ✅ **真实 33 例 F1 0.847/0.911/0.921**，极性一致 1.00 |
| 二十六 无 LLM 优雅跳过 | test_session_multiturn.py | ✅ 不落证据不虚构 |
| 二十七 记忆合并幂等 | [test_memory_consolidation.py](tests/test_memory_consolidation.py)（5 例） | ✅ 全量重算幂等 |
| 二十八 记忆用户隔离 | test_memory_generalization.py | ✅ 跨用户零泄漏 |
| 二十九 偏好管理页 | [SESSION_CHAT_MEMORY.md](SESSION_CHAT_MEMORY.md) | ✅ 查看/编辑/删除已实现 |

**六、扩展业务——单品搭配等（用例三十~三十四）**

| 用例 | 验证指向 | 实测结果 |
|---|---|---|
| 三十 共享上下文 | [test_context_pack.py](tests/test_context_pack.py)（3 例） | ✅ 五要素入包 |
| 三十一 本地知识检索 | [test_knowledge_retriever.py](tests/test_knowledge_retriever.py)（3 例） | ✅ 命中+来源标注 |
| 三十二 单品锚点（单品搭配） | [test_extended_tasks.py](tests/test_extended_tasks.py)（15 例） | ✅ 锚定单品、3 次调用、无 degraded；真实 8 例锚定率 100%、裁判均分 55.9（EXT-002：one_piece 缺配饰/外套） |
| 三十三 新品不落库 | 同上 | ✅ 衣橱外 ID 硬失败 |
| 三十四 缺口与失败持久化 | 同上 | ✅ 失败可重试，拒绝不发布 |

**七、天气上下文（用例三十五~三十八）**

| 用例 | 验证指向 | 实测结果 |
|---|---|---|
| 三十五 天气事实注入 | [test_weather_tool.py](tests/test_weather_tool.py)（14 例）+ `test_workflow_llm.py` | ✅ 共享三 Agent，工具仅一次 |
| 三十六 Provider 失败 | test_weather_tool.py 失败分支 | ✅ 优雅降级 |
| 三十七 默认地点 | test_weather_tool.py 默认地点 | ✅ |
| 三十八 普通请求不增成本 | test_workflow_llm.py | ✅ 无地点不调天气 |

**八、衣橱与订单（用例三十九~四十四）**

| 用例 | 验证指向 | 实测结果 |
|---|---|---|
| 三十九 订单导入式衣橱 | 订单导入门禁 + 一次性库验收 | ✅ 374 准入 / 0 未知 |
| 四十 图片导入式衣橱 | 同上 | ✅ |
| 四十一 订单状态继承 | 同上 | ✅ 多商品订单 0 mismatch |
| 四十二 退款信号排除 | 同上 | ✅ 51 关闭+9 付款等排除 |
| 四十三 准入校验不可绕过 | 同上 | ✅ 手工改品类不绕过 |
| 四十四 偏好管理数据正确性 | 管理页契约 | ✅ 实时反映存储 |

**九、p-outfit 独立 LLM 评估（用例四十五~五十）**

| 用例 | 验证指向 | 实测结果 |
|---|---|---|
| 四十五 实验 A 订单导入式 | [test_p_outfit_eval.py](tests/test_p_outfit_eval.py)（29 例） | ✅ 真实 order 58.57 / pass 50% |
| 四十六 实验 B 图片导入式 | 同上 | ✅ 真实 image 63.09 / pass 65% |
| 四十七 独立裁判打分 | C 组 `test_judge_score_*` | ✅ 200 次打分 0 失败 |
| 四十八 golden 真实套锚点 | 报告 | ✅ 48.14 / 47.90 |
| 四十九 A/B 对照统计检验 | [test_p_outfit_analysis.py](tests/test_p_outfit_analysis.py)（7 例） | ✅ 真实 +4.52 p=0.0005 d=0.36 McNemar p=0.024 |
| 五十 评估门禁覆盖 | A-F 组 | ✅ 29 例全绿（本报告撰写日实测） |

**十、评估质量检查（用例五十一~五十五）**

| 用例 | 验证指向 | 实测结果 |
|---|---|---|
| 五十一 裁判独立性 | C 组词汇级硬门禁 | ✅ prompt 无 critic 词汇 |
| 五十二 及格线预注册 | A 组 `test_case_pass_criteria_preregistered` | ✅ 60 冻结三处一致 |
| 五十三 衣橱缺口路径覆盖 | E 组 `test_pipeline_wardrobe_gap_best_effort` | ✅ |
| 五十四 评估可复现性 | F 组 provenance | ✅ model/hash/版本齐全 |
| 五十五 请求无答案泄漏 | A 组 `test_case_no_answer_leakage` | ✅ 无 golden 逐字 token |

**十一、评估方案对齐（用例五十六~六十）**

| 用例 | 验证指向 | 实测结果 |
|---|---|---|
| 五十六 Handoff 信息保全 | E 组 `test_pipeline_handoff_preservation` | ✅ 约束跨跳不丢 |
| 五十七 Ablation 消融 | runner `--ablation` | ⚠️ 实验设计，非 CI 门禁 |
| 五十八 性能指标采集 | F 组 `test_report_performance_metrics` | ✅ P50/P95 延迟、平均调用数 |
| 五十九 Public Benchmark | runner 报告能力 | ⚠️ 实验设计，非 CI 门禁 |
| 六十 Trace Golden Path | E 组 `test_trace_golden_path_nodes` | ✅ 7 节点有序、无越界 |

## 3. 逐功能证据

### 3.1 推荐搭配（outfit_recommend）—— 质量级 ✅

- **功能级**：`test_workflow_llm.py` 11 例锁主链 accept/recompose/retrieve_more/wardrobe_gap 决策与调用预算；p-outfit E 组 6 例在评估上下文复核。
- **质量级（真实 API）**：2026-08-06 真实 DeepSeek 8 类代表性请求（面试/音乐剧/演唱会/美拉德/小雨/高考/打篮球/海边度假）全部 `accept`、推荐 100% 衣柜归属、7 类无回退。
- **质量级（独立裁判）**：p-outfit 独立纯文本裁判对 100 例真实搭配打分（见 §4），order 58.57 / image 63.09，两形态均显著优于真实套锚点附近基线。

### 3.2 单品搭配与扩展业务（item_advice / style_advice / wardrobe_compatibility / wardrobe_gap / outfit_modify）—— 质量级 ✅（含缺陷）

- **功能级**：`test_extended_tasks.py` 15 例锁「单品锚点 / 新品不落库 / 目标风格缺口 / 失败持久化 / missing-slot→澄清」契约：生成方案锚定指定单品、真实三次模型调用、无 degraded trace、衣橱外 ID 硬失败、Agent 3 拒绝不发布草稿。
- **质量级（真实独立裁判，2026-08-14）**：8 例锚定真实单品（one_piece/footwear/bottom/top），独立纯文本 DeepSeek 裁判评 top 套：**锚定率 100%**、裁判均分 55.9、pass 50%（阈值 60）。非 one_piece 锚点均 3 件完整套（上+底+鞋），one_piece 锚点仅 2 件（裙+鞋）。
- **缺陷（EXT-002，medium）**：锚定 one_piece（连衣裙）时 composer 只补鞋，用户请求的配饰/外套不填充（item-001 要"配齐配饰"仍 2 件，judge 32.0 全组最低）。如实记录未修。

### 3.3 多轮对话（会话持久化 / follow-up）—— 质量级（替换链路）✅ + 缺陷 ⚠️

- **功能级**：`test_session_multiturn.py` 10 例锁「整体调整 / 换单品槽位锁定 / 新场景不被 follow_up 吞掉 / 负反馈链式修改 / 会话重启恢复 / 跨用户隔离」。
- **质量级（真实运行，2026-08-14）**：5 条链从自然人话 turn1（走 `recommend_payload` LLM 三 Agent 链，不碰确定性解析器）出发，逐 swap 验证：**槽位替换 100% 命中请求槽位**（swap_correct_rate=1.0）；槽位缺失（如连衣裙套无外套/裤槽）按设计正确跳过回澄清（skip 40%），all-skipped 链标 infeasible 不计入 pass 率。
- **修复（EXT-001，2026-08-16）**：原 **adjust 全局微调（"整体再正式一点"）5/5 触发 `_validate_modify` 硬抛 ValueError 崩溃**——无明确槽位请求被 `is_follow_up` 重定向到 OUTFIT_MODIFY 后 agent2 越界，重试仍失败。已改为 **flexible 模式**：无槽位或槽位缺失请求由 agent 自主理解意图、灵活重排整套（候选池=知识方向匹配 + 全衣柜兜底），硬校验只守数据边界（引用在池内、确有所调整、required_slot 必落位）。真实冒烟：整体调整产出 2 套重排不崩溃；连衣裙套补 accessory 槽位成功（one_piece+footwear→+accessory）。根因链见[开发过程记录](DEVELOPMENT_LOG.md)第 15 节 15.3 与第 16 节。

### 3.4 记忆偏好 —— 质量级（提炼侧）✅，推荐增益未评估 ⚠️

- **功能级**：`test_memory_generalization.py`（10）+ `test_memory_consolidation.py`（5）锁「偏好沉淀 / 合并幂等 / 用户隔离 / 冲突降权 / 上下文门控」。
- **质量级（提炼）**：`test_memory_extraction_eval.py`（8）+ 2026-08-13 真实 DeepSeek 33 例口语化评估集，F1 **0.847 / 0.911 / 0.921**（v2.0 基线 0.63）、极性一致率 1.00、scope 一致率 0.97+。
- **缺**：评的是「记忆提炼正确性」，**未评估「带记忆的推荐比不带记忆更好」**（端到端增益）。

### 3.5 任务路由 —— 质量级 ✅

- 42 条中英文固定 Cases 逐类 100%（2026-08-09）→ 60 条六类各 10 条真实评估逐类 100%（2026-08-12 基线），门禁锁 60 例计数。

### 3.6 天气 / 衣橱订单 / 知识检索

- 天气：`test_weather_tool.py` 14 例锁事实注入、Provider 失败降级、默认地点、不增 LLM 成本。
- 衣橱订单：30 条门禁 + 一次性数据库验收 374 准入 / 0 未知 / 15 非服饰误分类已修复。
- 知识检索：`test_knowledge_retriever.py` 3 例锁命中与来源标注。

## 4. p-outfit 独立 LLM 裁判评估（2026-08-14 专项，质量级）

官方 Polyvore Outfits 基准抽样 100 例真实搭配 + 同人工请求，独立纯文本 DeepSeek 裁判（五维 R/S/C/W/F）分别在**订单导入式（order，弱数据）**与**图片导入式（image，强数据）**两类衣橱下评分，互不混合。

| 指标 | order | image |
|---|---|---|
| 生成套裁判均分 | 58.57 | **63.09** |
| 真实套锚点均分 | 48.14 | 47.90 |
| pass 率（≥60） | 50% | **65%** |
| 硬违规 / gap / 裁判失败 | 0 / 0 / 0 | 0 / 0 / 0 |
| critic-vs-judge pearson | 0.331 | -0.045 |

**配对检验（n=100）**：image−order **+4.52**（SD 12.56），paired t **p=0.0005**、Wilcoxon **p=0.001**、Cohen's **d=0.36**、McNemar pass 差异 **p=0.024**（27 例 image 胜出）。

- **结论**：图片强数据衣橱生成质量统计显著优于订单文本弱数据衣橱，符合实验假设；裁判独立（pearson<0.4）。
- **失败案例分析**（order 50 / image 35，100% 为裁判<60）：低分全部是**风格关键词脱靶**与**电商文本噪声**（如「Free UK Shipping...」营销废话淹没属性）——弱数据衣橱的检索下限即推荐质量下限，属方案预期局限。
- **设计边界**：本评估刻意不含记忆（快照用户无记忆参与，`-Memory` 消融不适用），记忆贡献由 60 用例手册二十四~二十九验证；评估不含多轮（请求刻意排除 follow-up 词）。

## 5. 全量回归

- **499 passed / 499**（2026-08-14 实测，116.62s，pytest 9.1.1 / style env / PG 每测试独立 schema）。Ruff clean。本批新增 `test_modify_missing_slot_returns_clarification_not_failure` 1 例。
- 覆盖：上述全部门禁文件 + 订单导入 / 数据审计 / 检索基线 / UI 契约等历史测试。

## 6. 盲区与诚实声明

| # | 盲区 | 现状 | 处置 |
|---|---|---|---|
| 1 | ~~单品搭配无独立裁判质量评估~~ | ✅ 2026-08-14 已评 | 8 例真实独立裁判：锚定率 100%、均分 55.9、pass 50%；发现 EXT-002（one_piece 缺配饰/外套）待产品优化 |
| 2 | ~~多轮对话无质量评估~~ | ✅ 2026-08-14 已评 | 5 链真实运行：槽位替换 100% 命中 + 缺失正确跳过；发现 EXT-001（adjust 全局微调 5/5 崩溃）2026-08-16 已修复 |
| 3 | 记忆「推荐增益」未端到端量化 | 仅评提炼正确性 | 可设计带记忆 vs 不带记忆的配对对比 |
| 4 | p-outfit 门禁不锁真实 LLM 分数 | FakeLlm 注入 | 真实质量由真实运行 + 人工报告评审确认 |
| 5 | GPU 资源层（encoder 元加载/OOM 批处理）离线不可测 | 已修复 | 真实运行 3.4-5.8GB 显存有界验证 |
| 6 | ~~多轮 adjust 全局微调崩溃（EXT-001）~~ | ✅ 2026-08-16 已修复 | flexible 模式：无槽位/槽位缺失请求由 agent 自主重排整套；`_validate_modify` 按模式分支不再硬抛 |

## 7. 下一步

1. ✅ 单品搭配 + 多轮对话独立裁判评估（2026-08-14 完成，见 §3.2/3.3）：产物 `artifacts/evaluation/extend_advice.json` + 全程日志 `artifacts/evaluation/env/order/logs/`；EXT-001（adjust 崩溃）**2026-08-16 已修复**为 flexible 自主重排，遗留 EXT-002（one_piece 缺配饰）/ EXT-003（记忆过度归纳）
2. 记忆推荐增益端到端对比（可选，v2）
3. 保持 60 用例手册门禁随功能增量同步补完
4. ✅ 复核评估报告 + 全程日志定位缺陷（2026-08-16 完成）：EXT-001 根因已修；EXT-002/003 仍待产品优化（详见报告 `findings`）
