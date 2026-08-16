# p-outfit LLM 评估 —— 门禁测试覆盖表

> 本文档对应已批准的 p-outfit 独立 LLM 评估方案（P1-P6 阶段），记录门禁测试套件
> `tests/test_p_outfit_eval.py` 中每个测例**覆盖什么功能**、**锁什么确定性**。
> 全部测例离线可跑（FakeLlm / db fixture），**不锁任何真实 LLM 分数**。

## 文件位置

| 文件 | 说明 |
|---|---|
| `tests/test_p_outfit_eval.py` | 门禁测试套件（P4 阶段创建，本表即其设计稿） |
| `evals/cases/p_outfit.json` | 100 个评估用例（自包含，P1b 生成，P3 人工审查后冻结） |
| `apps/api/styleforge/data/p_outfit.py` | p-outfit 数据纯函数模块（映射/抽样/起草） |
| `apps/api/styleforge/llm/judge_prompts.py` | 独立裁判 prompt（纯函数） |
| `apps/api/styleforge/agents/judge.py` | `IndependentJudge` 打分器 |
| `apps/api/styleforge/core/rubric.py` | 五维 rubric + `aggregate_score` |
| `evals/runners/evaluate_p_outfit.py` | 真实评估 runner（P4 创建，非门禁依赖） |

> **评估质量检查**（`docs/FUNCTIONAL_TEST_CASES.md` 用例五十一~五十五）：A 组
> `test_case_pass_criteria_preregistered` / `test_case_no_answer_leakage`，C 组
> `test_build_judge_prompt_independence`，E 组 `test_pipeline_wardrobe_gap_best_effort`，
> F 组 `test_report_provenance_complete` / `test_report_critic_judge_consistency_present`
> 共同承担裁判独立性、预注册、缺口覆盖、可复现与无泄漏五项评估可信度检查。

## A. Case 文件契约（无 DB / 无 LLM）

| 测试函数 | 覆盖功能 | 核心断言 | 防什么回归 |
|---|---|---|---|
| `test_case_file_valid` | case 文件整体合法 | `d["case_count"] == 100`；每个 case 字段齐全（`id/source_set_id/source_title/user_request/golden_item_ids/items/hard_assertions/pass_criteria`）；`set_id` 唯一；`golden ⊆ items`；`25 <= len(user_request) <= 120`（实际 28-45）；`hard_assertions` 含 `all_items_in_wardrobe`、`has_required_slots` | case 文件被改坏 / 重复抽样 / 请求越界 |
| `test_case_requests_are_fresh_briefs` | 请求必须走 OUTFIT_RECOMMEND 路径 | 对每条 request：不含 `_FOLLOW_UP_MARKERS` 中任一调整词（更/再/别/不/一点/太/有点/调整/改变/换/不要/避免/避开/不穿） | 请求被改成二次修改式，路由到 follow-up 分支 |
| `test_case_requests_reviewed` | 请求已经人工审查 | 每个 case `user_request` 带 `_reviewed == True` 标记（P3 冻结时打上） | 未经审查的机器初稿被当成最终用例 |
| `test_case_pass_criteria_preregistered` | 及格线预注册（用例五十二） | 每个 case `pass_criteria.min_judge_overall` 在 case 文件中固化（=60）；报告记录 pass 标准定义时间，且早于评估运行 commit | 事后按结果调及格线（评估无效） |
| `test_case_no_answer_leakage` | 请求无答案泄漏（用例五十五） | 逐 case：`user_request` 不含任何 golden item 的 `url_name`/`title` 逐字片段；需求级品类骨架词允许 | 人工请求把 golden 答案泄漏给系统（对照实验失效） |

## B. p-outfit 映射与槽位（纯函数）

| 测试函数 | 覆盖功能 | 核心断言 | 防什么回归 |
|---|---|---|---|
| `test_semantic_to_item_type_slot_safe` | 11 类 semantic_category 映射后槽位不丢 | 对 `SEMANTIC_TO_ITEM_TYPE` 全部键：`semantic_to_item_type(cat) in ALLOWED_ITEM_TYPES` 且 `infer_slot(...) != "other"` | 新品类导致候选被静默排除（other 槽） |
| `test_refine_item_type_fidelity` | 桶内关键词细化保真 | `refine_item_type("bottoms", "...skirt...") == "skirt"`；`"jumpsuit" == "jumpsuit"`；`"tuxedo" == "suit"`；且细化前后 `infer_slot` 不变 | 半身裙/连体裤被错标成长裤/连衣裙 |
| `test_outfit_completeness` | 完整搭配判定 | `{top,bottom,footwear}` 子集 → True；`{one_piece,footwear}` → True；缺鞋 → False | 配件堆砌被当完整搭配抽样 |
| `test_normalize_p_outfit_item_modes` | 两类实验 image_status 隔离 | order 模式：`ImageStatus.UNBOUND`、空 image 字段；image 模式：`ImageStatus.AVAILABLE`、`image_filename == "{id}.jpg"` | order/image 数据形态串线 |
| `test_item_text_block_format` | 裁判输入格式稳定 | `POutfitItem(...).to_text_block()` == `"品类 | 名称 | 描述"`；描述为空时省略第三段 | 裁判输入格式漂移 |

## C. 独立裁判（纯函数 + FakeLlm）

| 测试函数 | 覆盖功能 | 核心断言 | 防什么回归 |
|---|---|---|---|
| `test_build_judge_prompt_independence` | 裁判与 Runtime Critic 语义分离 | `build_judge_prompt(...)` 的 system+user 拼接**不含** `决策枚举/阶段二/recompose/retrieve_more/wardrobe_gap/request_signature/解释核对` 等 critic 词汇 | 两个评估静默合并 |
| `test_judge_score_success` | 成功路径解析 | FakeLlm 返回合法 JudgeOutput → `scores` 为五维 dict、`diag["degraded"] is False`、`fake.call_count == 1` | 成功路径解析漂移 |
| `test_judge_score_failure_no_mask` | 失败不作中性掩盖 | FakeLlm 抛异常/非法 JSON → `scores is None`、`diag["degraded"] is True`（返回 None 而非 5 分） | 失败被 5 分掩盖进均值 |
| `test_judge_no_llm_degraded` | 无客户端降级 | `score_outfit(..., llm=None)` → `scores is None`、`degraded is True`、reason 含 `no llm` | 无 key 时假装打过分 |
| `test_aggregate_score_matches_critic` | 聚合公式与 Runtime 等价 | `aggregate_score(dims) == _critic_score({"outfit_assessment": {"outfit_id": "x", "dimension_scores": dims}}, "x")` 对固定 dims 逐位相等 | 两条打分公式漂移 |
| `test_aggregate_score_empty` | 空输入安全 | `aggregate_score(None) == 0.0`；`aggregate_score({}) == 0.0` | 未打分当满分/当及格 |

## D. 指标纯函数（P4 runner）

| 测试函数 | 覆盖功能 | 核心断言 | 防什么回归 |
|---|---|---|---|
| `test_aggregate_metrics_numeric` | 聚合指标数值正确 | 给定固定 per-case 结果 → `mean_judge_overall`/`pass_rate`/`judge_failure_rate`/`hard_violation_rate` 与手算一致 | 聚合公式漂移 |
| `test_pearson_known_value` | 一致性相关系数 | 已知输入序列 → 已知相关系数（±1e-9） | critic-vs-judge 一致性指标失真 |

## E. 执行路径（db fixture + FakeLlm，两条主路径）

| 测试函数 | 覆盖功能 | 核心断言 | 防什么回归 |
|---|---|---|---|
| `test_pipeline_accept_path` | 正常接受路径 | FakeLlm 序列 `[a1, a2, a3(decision=accept)]` → `llm_call_count == 3`、`decision == "accept"`、`len(result) == 3`、无 composer 回调 | 正常路径不再 3 次调用 / 静默重试 |
| `test_pipeline_recompose_path` | 一次 recompose 后接受 | FakeLlm 序列 `[a1, a2, a3(recompose+feedback), a2(重做), a3(accept)]` → `llm_call_count == 5`、`fallback_count == 1`、经历一次 composer_feedback、最终 `decision == "accept"`。**锁死 graph.py:887-892**：`fallback_count < 1 且 llm_call_count <= 3` 才回 composer，重试后超限走 best_effort | **CI 全绿但真实 recompose 分支失败**（用户指出过的教训） |
| `test_pipeline_wardrobe_gap_best_effort` | 衣橱缺口兜底 | FakeLlm 序列 `[a1, a2, a3(wardrobe_gap)]` → `decision == "wardrobe_gap"`、仍返回 best_effort 推荐 | 衣橱缺品类时无兜底 |
| `test_pipeline_repeated_recompose_clamps` | recompose 防死循环 | FakeLlm 序列连续两次 recompose → `llm_call_count <= 5`、`fallback_count == 1`、最终走 best_effort、无死循环 | recompose 无限循环 |
| `test_pipeline_handoff_preservation` | Handoff 信息保全（用例五十六） | 交接 payload 中 `hard_constraints` 列表长度未缩短、关键词（"no heels" 等）未丢失 | Agent 链中约束逐跳丢失 |
| `test_trace_golden_path_nodes` | Trace Golden Path（用例六十） | Router→Agent1→Agent2→Agent3→Final 必备节点全部存在、无跳过；禁止节点（生成衣橱外 item）未出现 | 执行路径跳过关键步骤 / 越界产出 |

## F. Runner 集成（P4，db fixture）

| 测试函数 | 覆盖功能 | 核心断言 | 防什么回归 |
|---|---|---|---|
| `test_build_wardrobe_snapshot` | 快照构建隔离 | 小 case 集 order/image 模式 → 用户隔离（`eval-order` vs `eval-image`）、item 数正确、image_status 按 mode、item_id 已 UUID 化 | 快照污染/串线 |
| `test_runner_end_to_end_fake` | 完整流程可跑通 | FakeLlm 跑小 case 集 → 报告含 `modes.{order,image,comparison}`、per_case 记录齐全、临时 schema 已 DROP（`to_regclass('eval_...')` 为空） | runner 主流程崩溃 / 临时 schema 泄漏到主库 |
| `test_report_provenance_complete` | 评估可复现性记录（用例五十四） | 报告含 model id / `judge_prompt_version` / case 文件 hash / `seed` / runner 版本 / 模式 / 运行时间；`critic_judge_consistency` 字段存在（`pearson_r`、`mean_abs_delta`） | 分数变更无法溯源 / 裁判独立性无告警信号 |
| `test_report_critic_judge_consistency_present` | 裁判独立性软告警（用例五十一） | 报告在 `r ≥ 0.9` 时标记"裁判独立性可疑"；prompt 词汇独立性为硬门禁（C 组 `test_build_judge_prompt_independence`） | 裁判退化为 Critic 回声而不被察觉 |
| `test_report_performance_metrics` | 性能指标采集（用例五十八） | 报告含每例延迟 / Token / 工具调用次数与 P50/P95 延迟、Cost/Success | 性能恶化无记录（§6.3） |

> **非门禁的 runner 实验能力**：用例五十七（Ablation，`--ablation` 变体 Full/-Critic/-Retrieval）
> 与用例五十九（Public Benchmark，Compatibility AUC + Complementary R@K）是 P4/P5 的
> runner 报告能力，属实验设计而非 CI 门禁；-Memory 消融在 p-outfit 单次评估中不适用
> （无记忆参与），记忆贡献由系统级用例二十四~二十九验证。

## 运行方式

```powershell
# 门禁（离线，锁确定性）
D:\anaconda\envs\style\python.exe -m pytest tests/test_p_outfit_eval.py -q

# 全量回归
D:\anaconda\envs\style\python.exe -m pytest -q
D:\anaconda\envs\style\python.exe -m ruff check apps\api\styleforge evals tests

# 真实评估（需 .env DEEPSEEK_API_KEY；B 组需 GEMINI_API_KEY）
D:\anaconda\envs\style\python.exe evals\runners\evaluate_p_outfit.py --mode both
```

## 依赖关系

- A 组依赖 P3 冻结的 `evals/cases/p_outfit.json`（含 `_reviewed` 标记）
- B/C 组是纯函数，只依赖 P1b/P2 已实现的模块
- E/F 组依赖 P4 的 runner（`build_wardrobe_snapshot` 等）与 db fixture
