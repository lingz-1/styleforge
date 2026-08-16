# p-outfit 独立 LLM 评估 —— 测试用例覆盖完成情况报告

> 撰写日期：2026-08-14
> 对应设计稿：[P_OUTFIT_EVAL_TEST_COVERAGE.md](P_OUTFIT_EVAL_TEST_COVERAGE.md)
> 被测代码：`apps/api/styleforge/data/p_outfit.py`、`llm/judge_prompts.py`、`agents/judge.py`、
> `core/rubric.py`、`evals/runners/evaluate_p_outfit.py`、`evals/analysis/analyze_p_outfit.py`

## 0. 运行环境与结果

| 项 | 值 |
|---|---|
| 解释器 | `D:\anaconda\envs\style\python.exe`（style env，Python 3.10.20，pytest 9.1.1） |
| 数据库 | PostgreSQL 测试库，每测试独立 schema（`db_dsn` fixture），不触主库 |
| LLM / GPU / E:\ | **零真实调用**：FakeLlm 脚本化 + `_ScriptedEncoder` 常量向量 + 只读 case 文件 |
| 门禁结果 | `tests/test_p_outfit_eval.py` 29 例 + `tests/test_p_outfit_analysis.py` 7 例 = **36 passed / 36**（22.99s，实测） |
| 全量回归 | **498 passed / 498**（109.73s，实测） |

## 1. 覆盖设计 vs 实际完成对照

设计稿（`P_OUTFIT_EVAL_TEST_COVERAGE.md`）规划 A-F 六组 + 分析层，**全部落地、全部通过**：

| 组 | 设计例数 | 实际例数 | 完成 | 真实运行证据 |
|---|---|---|---|---|
| A case 文件契约 | 5 | 5 | ✅ | 100 例文件被 runner 直接消费 |
| B 映射与槽位 | 5 | 5 | ✅ | 100 例全部完成 `infer_slot != other` 映射 |
| C 独立裁判 | 6 | 6 | ✅ | 真实运行 0 judge 失败（200 次打分） |
| D 指标纯函数 | 2 | 2 | ✅ | 报告数值与手算一致 |
| E 执行路径 | 6 | 6 | ✅ | 真实运行 100 例 `decision=accept`、0 gap |
| F runner 集成 | 5 | 5 | ✅ | `--mode both` 100 例真实跑通，schema 无泄漏 |
| 分析层 | — | 7 | ✅ | `p_outfit_analysis.json` 配对统计实际产出 |

设计稿标注的两项 runner 实验能力（Ablation `--ablation`、Public Benchmark AUC/R@K）为**实验设计而非 CI 门禁**，按设计不落地为测试，不视为缺口。

## 2. A 组 —— case 文件契约（无 DB / 无 LLM）✅ 5/5

| 用例 | 覆盖功能 | 完成情况 |
|---|---|---|
| `test_case_file_valid`（[L298](tests/test_p_outfit_eval.py#L298)） | 文件整体合法：100 例、字段齐全、set_id 唯一、golden⊆items、请求长度 25-120、url_name 非空、硬断言齐全、runner `load_cases` 可读 | ✅ 锁死文件结构，防改坏/重复抽样 |
| `test_case_requests_are_fresh_briefs`（[L322](tests/test_p_outfit_eval.py#L322)） | 请求无 follow-up 调整词（更/再/别/一点/换…），保证走 OUTFIT_RECOMMEND | ✅ 100 例全部为 fresh brief |
| `test_case_requests_reviewed`（[L329](tests/test_p_outfit_eval.py#L329)） | 请求已过人工审查（`_reviewed` 标记） | ✅ P3 冻结标记全打上 |
| `test_case_pass_criteria_preregistered`（[L334](tests/test_p_outfit_eval.py#L334)） | 及格线 60 预注册（runner `_DEFAULT_PASS_MIN` 与 case 文件、硬断言三处一致） | ✅ 防事后调及格线 |
| `test_case_no_answer_leakage`（[L343](tests/test_p_outfit_eval.py#L343)） | 请求不含任一 golden item 的 title/url_name 逐字 token | ✅ 防 golden 答案泄漏（对照实验失效） |

**完成度结论**：A 组把「数据准备可信」锁死。case 文件是实验地基，任何一条失败都意味着评估数据不可信，且均在真实运行前离线可查。

## 3. B 组 —— p-outfit 映射与槽位（纯函数）✅ 5/5

| 用例 | 覆盖功能 | 完成情况 |
|---|---|---|
| `test_semantic_to_item_type_slot_safe`（[L358](tests/test_p_outfit_eval.py#L358)） | 11 类 semantic_category → 全 ∈ `ALLOWED_ITEM_TYPES` 且 `infer_slot != "other"` | ✅ 候选不被静默排除 |
| `test_refine_item_type_fidelity`（[L365](tests/test_p_outfit_eval.py#L365)） | 桶内关键词细化：skirt/jumpsuit/suit 且细化前后槽位不变 | ✅ 半身裙/连体裤/套装保真 |
| `test_outfit_completeness`（[L375](tests/test_p_outfit_eval.py#L375)） | `{top,bottom,footwear}` 或 `{one_piece,footwear}` 判完整，缺鞋/纯配件为否 | ✅ 抽样不选配件堆砌 |
| `test_normalize_p_outfit_item_modes`（[L385](tests/test_p_outfit_eval.py#L385)） | order→`UNBOUND`+空图字段；image→`AVAILABLE`+`123.jpg` | ✅ 两类实验数据形态隔离 |
| `test_item_text_block_format`（[L396](tests/test_p_outfit_eval.py#L396)） | 裁判输入 `"品类 \| 名称 \| 描述"`，空描述省略末段 | ✅ 裁判输入格式稳定 |

**完成度结论**：B 组锁死「order/image 互不混合」与「槽位语义不丢」——这是两类对照实验成立的结构前提。真实运行 100 例 `infer_slot` 无 one 兜底，佐证映射完备。

## 4. C 组 —— 独立裁判（纯函数 + FakeLlm）✅ 6/6

| 用例 | 覆盖功能 | 完成情况 |
|---|---|---|
| `test_build_judge_prompt_independence`（[L407](tests/test_p_outfit_eval.py#L407)） | judge prompt 不含 recompose/retrieve_more/wardrobe_gap/request_signature/阶段二/解释核对/critic/decision | ✅ **裁判独立性硬门禁** |
| `test_judge_score_success`（[L419](tests/test_p_outfit_eval.py#L419)） | 合法返回 → 五维 dict、`degraded=False`、1 次调用 | ✅ 成功路径解析 |
| `test_judge_score_failure_no_mask`（[L429](tests/test_p_outfit_eval.py#L429)） | 异常 → `scores=None`、`degraded=True` | ✅ 失败不进均值 |
| `test_judge_no_llm_degraded`（[L439](tests/test_p_outfit_eval.py#L439)） | 无客户端 → 降级、reason 含 no llm | ✅ 无 key 不假装打分 |
| `test_aggregate_score_matches_critic`（[L448](tests/test_p_outfit_eval.py#L448)） | `aggregate_score` 与 Runtime `_critic_score` 逐位相等 | ✅ 两条打分公式不漂移 |
| `test_aggregate_score_empty`（[L454](tests/test_p_outfit_eval.py#L454)） | `None`/`{}` → 0.0（不作满分/及格） | ✅ 空输入安全 |

**完成度结论**：C 组锁死裁判与 Runtime Critic 的**语义分离 + 失败不掩盖**。真实运行 200 次打分 0 失败、双模式 pearson<0.4，与独立性设计一致。

## 5. D 组 —— 指标纯函数 ✅ 2/2

| 用例 | 覆盖功能 | 完成情况 |
|---|---|---|
| `test_aggregate_metrics_numeric`（[L463](tests/test_p_outfit_eval.py#L463)） | n/mean/pass_rate/judge_failure/hard_violation/gap 率、pearson、mean_abs_delta、p50 与手算一致 | ✅ 聚合公式不漂移 |
| `test_pearson_known_value`（[L499](tests/test_p_outfit_eval.py#L499)） | ±1 已知值 + 长度不符 → None | ✅ 相关系数边界 |

**完成度结论**：D 组锁住报告数字的来源正确。真实报告 `p_outfit.json` 的 metrics 由同一聚合函数产出，无需重算。

## 6. E 组 —— 执行路径（db fixture + FakeLlm）✅ 6/6

| 用例 | 覆盖功能 | 完成情况 |
|---|---|---|
| `test_pipeline_accept_path`（[L511](tests/test_p_outfit_eval.py#L511)） | `[a1,a2,a3(accept)]` → 3 次调用、accept、3 套推荐、槽位完整、critic=o1 | ✅ 正常路径不重试不丢结果 |
| `test_pipeline_recompose_path`（[L529](tests/test_p_outfit_eval.py#L529)） | `[a1,a2,a3(recompose),a2,a3(accept)]` → 5 次调用、fallback=1、feedback 传入第二次 composer | ✅ **锁死 graph.py:887-892** 单次重组预算 |
| `test_pipeline_wardrobe_gap_best_effort`（[L553](tests/test_p_outfit_eval.py#L553)） | 缺口决策 → 仍返回 best_effort 3 套 | ✅ 缺品类不空手 |
| `test_pipeline_repeated_recompose_clamps`（[L578](tests/test_p_outfit_eval.py#L578)） | 连续两次 recompose → ≤5 次调用、fallback=1、无死循环 | ✅ 防无限重试 |
| `test_pipeline_handoff_preservation`（[L600](tests/test_p_outfit_eval.py#L600)） | `generic_tendencies_to_avoid` 硬约束跨跳保全 | ✅ Agent 链约束不逐跳丢失 |
| `test_trace_golden_path_nodes`（[L614](tests/test_p_outfit_eval.py#L614)） | 7 个 Golden Path 节点存在、有序、accept 路径无 best_effort | ✅ 执行路径可追溯 |

**完成度结论**：E 组直接回应「CI 全绿但真实分支失败」的教训——用脚本 LLM 锁死 accept/recompose/wardrobe_gap/recompose-clamp **四条真实决策路径**的调用次数、回退预算与 payload 契约。`retrieve_more` 分支由通用套件 [test_workflow_llm.py:352](tests/test_workflow_llm.py#L352) 覆盖（6 调用链），两套分工明确。

## 7. F 组 —— runner 集成 ✅ 5/5

| 用例 | 覆盖功能 | 完成情况 |
|---|---|---|
| `test_build_wardrobe_snapshot`（[L635](tests/test_p_outfit_eval.py#L635)） | order/image 快照用户隔离、item 数一致、image_status 按 mode、UUID 化、`dataset_item_id` 保留 raw | ✅ 快照不串线不污染 |
| `test_runner_end_to_end_fake`（[L722](tests/test_p_outfit_eval.py#L722)） | `mode=both` 2 例跑通、报告结构完整、临时 schema 已 DROP 无泄漏 | ✅ 主流程 + 清理 |
| `test_report_provenance_complete`（[L753](tests/test_p_outfit_eval.py#L753)） | model/case hash/prompt 版本/runner 版本/时间齐全 | ✅ 分数可溯源 |
| `test_report_critic_judge_consistency_present`（[L767](tests/test_p_outfit_eval.py#L767)） | `pearson_r`/`mean_abs_delta`/`independence_warning` 字段存在（脚本数据 100% 相关时告警触发） | ✅ 独立性软告警 |
| `test_report_performance_metrics`（[L776](tests/test_p_outfit_eval.py#L776)） | P50/P95 延迟、平均 LLM 调用数、逐例延迟 | ✅ 性能可观测 |

**完成度结论**：F 组用 2 例快速回归 runner 全流程（门禁速度考虑），100 例规模由真实运行验证。schema 泄漏检查（`pg_namespace LIKE 'eval_%'` 增量断言）保证实验不污染测试库。

## 8. 分析层 —— 配对统计（已知值锁定）✅ 7/7

| 用例 | 覆盖功能 | 完成情况 |
|---|---|---|
| `test_student_t_cdf_known_tails` / `test_t_critical_known_values`（[test_p_outfit_analysis.py:24](tests/test_p_outfit_analysis.py#L24)、[L31](tests/test_p_outfit_analysis.py#L31)） | 纯 Python t 分布 CDF / 逆 CDF 与标准 t 表一致 | ✅ 无 scipy 数学正确 |
| `test_paired_t_test_known`（[L37](tests/test_p_outfit_analysis.py#L37)） | 已知序列 → t=4.2426、p<0.02 | ✅ |
| `test_wilcoxon_known`（[L46](tests/test_p_outfit_analysis.py#L46)） | W⁺=55、z=2.8031、p=0.00506 | ✅ |
| `test_mcnemar_exact_known`（[L54](tests/test_p_outfit_analysis.py#L54)） | 精确二项 McNemar：0.0625 / 1.0 / 1.0 | ✅ |
| `test_cohens_d_constant_zero`（[L61](tests/test_p_outfit_analysis.py#L61)） | 常数差 → d=0、CI 收于均值 | ✅ |
| `test_analyze_report_structure`（[L78](tests/test_p_outfit_analysis.py#L78)） | 报告 → 配对 n / delta / t df / 维度 delta / McNemar 零不一致对 | ✅ |

**完成度结论**：统计层不依赖 scipy，7 例把每步数学锁定到已知值，防静默回归扭曲显著性结论。真实 `p_outfit_analysis.json`（t=3.60 p=0.0005、z=3.27 p=0.001、d=0.36、McNemar p=0.024）即由同一套代码产出。

## 9. 与真实运行证据的闭环

门禁全绿 ≠ 真实质量达标，两层互补：

1. **门禁（36 例）**锁结构/公式/路径/隔离——本次实测全绿（22.99s）；
2. **真实运行**（P5c，`--mode both` 100 例 × 2，597 次 DeepSeek 调用）验证数值端到端：报告 `p_outfit.json` + 配对分析 `p_outfit_analysis.json` 产出，0 gap / 0 硬违规 / 0 裁判失败，镜像门禁锁定的行为。

## 10. 覆盖盲区与风险（诚实声明）

| # | 盲区 | 说明 | 处置 |
|---|---|---|---|
| 1 | 门禁不锁真实 LLM 分数 | FakeLlm 脚本化，分数是注入的 | 真实质量由 P5c 人工评审 + 报告评审确认，属评估性质而非门禁 |
| 2 | GPU/资源层（encoder 元加载、OOM 批处理 `batch_size=64`）离线不可测 | 依赖 CUDA/显存，FakeLlm 无法覆盖 | P5a/b 人工修复 + 真实运行 3.4-5.8GB 显存有界验证 |
| 3 | `retrieve_more` 分支不在 p_outfit 门禁 | 由通用 [test_workflow_llm.py:352](tests/test_workflow_llm.py#L352) 覆盖 | 分工明确，非缺口 |
| 4 | F 组用 2 例 | 门禁速度折衷 | 100 例规模由真实运行 + 持久 env 续跑覆盖 |
| 5 | 分析层 Wilcoxon 用 z 近似（非精确秩检验） | 数学近似已由已知值锁定，但仍是近似 | 报告如实标注 z 近似；关键结论同时有 paired t 交叉验证 |

## 11. 总结

p-outfit 独立 LLM 评估的测试覆盖**按设计稿全部落地且全部通过**（36/36）。六组门禁 + 分析层依次锁死：数据可信（A）、语义隔离（B）、裁判独立（C）、数字正确（D）、执行路径（E）、集成与溯源（F）、统计可靠（分析层）。设计稿标注的 runner 实验能力（Ablation / Public Benchmark）明确为实验设计而非 CI 门禁，不构成缺口。剩余盲区均为「需真实环境」类（真实 LLM 分数、GPU 资源），已由 P5c 真实运行证据与持久化 env 补齐。
