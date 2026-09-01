# StyleForge 评估计划 v2.0

> 制定日期：2026-09-01
> 状态：当前执行规范
> 被测系统：六任务 Multi-Agent Harness 主链
> 历史参考：`项目文档/评估方案.txt`、`P_OUTFIT_EVAL_TEST_REPORT.md`
> 核心原则：评当前系统，不恢复 legacy；功能正确、生成质量、公开 Benchmark 分开报告。

## 1. 目标与边界

本计划回答四个问题：

1. 六类真实任务能否在当前用户衣柜和上下文内正确完成；
2. Coordinator、Research、Stylist、Critic、Extension、工具和门禁是否按合同协作；
3. 推荐是否满足请求、可穿、协调、稳定且成本有界；
4. StyleForge 相比规则、检索加单模型和公开服装基线是否产生可测增益。

本计划不做以下事情：

- 不恢复已经退役的 `recommend_payload` legacy runner；
- 不为评估新增 Agent、MCP、队列、数据库或监控服务；
- 不在第一阶段训练新的兼容性模型；
- 不把 Runtime Critic 自评分当作独立质量结论；
- 不把检索同品类准确率表述成整套搭配质量；
- 不把旧 p-outfit 的 order/image 实验表述成官方 Compatibility/FITB 结果。

## 2. 被测系统与任务范围

评估入口固定为当前 `POST /tasks/execute` 对应的 `MultiTaskWorkflow + StyleForgeHarness`。所有结果
必须经过当前 Schema、工具授权、Environment Gate、Critic 和持久化合同。

| 任务 | 主要完成条件 | 主要质量问题 |
|---|---|---|
| `outfit_recommend` | 返回当前衣柜内的完整搭配，或诚实无解/澄清 | 场景、约束、协调、实穿、新颖性 |
| `outfit_modify` | 只修改目标范围，保留锁定单品 | 目标槽命中、非目标保持、多轮连续性 |
| `style_advice` | 基于请求与证据给出结构化风格建议 | 建议是否具体、有据且适配衣柜 |
| `item_advice` | 保留锚点并补齐请求所需槽位 | 锚点保持、连衣裙外套/配饰补齐 |
| `wardrobe_compatibility` | 不落库地评估候选新品与衣柜 | 分数校准、证据覆盖、错误主张 |
| `wardrobe_gap` | 正确识别缺失槽位/功能，不强行推荐 | 缺口准确、优先级、无伪缺口 |

跨任务能力单列：多轮会话、偏好记忆、天气/Web/MCP/RAG、Prompt 注入防护、Provider 失败恢复、
长任务超时恢复、个人衣物图片与向量生命周期。

## 3. 四层评估结构

### L1：任务与硬合同

由代码判定，不交给 LLM：

- 路由、状态、Schema、必要结果字段正确；
- 输出单品全部属于当前用户衣柜；
- anchor、locked item、目标槽与必要槽位满足合同；
- 不出现重复 ID、衣柜外 ID、跨用户数据或不受支持的事实主张；
- 无解、澄清、依赖失败和降级状态诚实；
- Prompt 注入不能扩大工具权限、泄露 system 或污染长期记忆。

任一安全、归属或硬约束失败，Case 直接失败，软分不能抵消。

### L2：Agent、工具与轨迹

从结构化 trace 判定：

- Coordinator 路由与任务状态更新正确；
- Research 只在确有外部事实需求时调用工具；
- 工具选择、参数、结果利用和错误恢复正确；
- Handoff 保留请求、锚点、锁定项、约束、证据及不确定性；
- Stylist 只能使用可见工具和检索候选；
- Critic 的重组能改善失败候选，且不会制造无意义循环；
- 所有循环受步数、重试、上下文和超时预算约束。

### L3：质量、稳定性与效率

质量继续使用五维 Rubric：

| 维度 | 默认权重 | 说明 |
|---|---:|---|
| Request Relevance | 0.25 | 是否真正满足用户目标和硬要求 |
| Request Specificity | 0.25 | 是否针对本次场景而非泛化套话 |
| Outfit Coordination | 0.20 | 色彩、材质、廓形、层次与风格是否成立 |
| Wearability | 0.15 | 是否适合真实活动、天气和行动要求 |
| Freshness | 0.15 | 是否避免近期重复并提供有效变化 |

报告同时保留默认权重分和用户权重分，不得用用户权重替代统一对比口径。

稳定性与效率指标：重复运行通过率、各维度标准差、P50/P95 延迟、LLM/工具/MCP 调用数、重试数、
降级率、Token 和 Cost/Success。Provider 不返回 Token 时记录 `null`，不得填 0。

### L4：基线、消融与公开 Benchmark

L4 分为两个互不混用的实验：

1. **系统级实验**：同一自然语言请求与固定衣柜，对比规则、检索加单模型、完整 StyleForge；
2. **服装基础能力实验**：使用官方 Polyvore Outfits 的 Compatibility/FITB 标准任务。

系统级质量分不能冒充公开 Benchmark；公开 Benchmark 也不能证明多轮对话、工具与 Agent 协作。

## 4. 数据集与隔离

### 4.1 CI 确定性集

- 复用当前 60 条 `wardrobe_quality.json`：42 条 deterministic、18 条 configured 合同案例；
- 复用 60 条六任务路由集、会话/记忆/工具/安全和历史 Bug 回归；
- 全部使用固定 fixture、FakeLlm、假 Provider 和独立 PostgreSQL test schema；
- 每次提交运行，不访问外部 API、GPU 和外部只读数据集。

### 4.2 当前 Harness 质量集

新建 v2 configured 集，第一版冻结 **30 条，六任务各 5 条**：

- 每类至少包含正常、边界、无解/澄清、复杂约束和历史失败中的适用类型；
- 推荐、修改和单品建议必须使用真实 Polyvore 商品文本与图片；
- 保留当前 7 条真实六任务用例作为 smoke，不直接充当完整质量集；
- Validation 18 条用于开发，Holdout 12 条只在版本验收时运行；
- Holdout 一旦用于调参，下个版本必须补充或轮换。

### 4.3 稳定性与挑战集

冻结 12 条高风险 Case：

- Provider 超时/断网、工具失败、错误 JSON；
- Prompt 注入、伪造角色、隐藏工具、敏感信息外发；
- 长上下文、超预算、重复工具调用、Critic 连续拒绝；
- 小衣柜、缺图、向量失败、重启恢复；
- 多轮反悔、只改一件、保持其他单品、记忆冲突。

每条挑战集 Case 运行 5 次，用于稳定性而不是 Best-of-N 挑最好结果。

### 4.4 官方 Polyvore Outfits

只读使用 `E:\01-style-dataset\p-outfit`：

- Compatibility：train/valid/test 官方正负搭配；
- FITB：官方留一件补全题；
- Maryland hard negative：单列难例结果；
- split、item ID、图片和 semantic category 映射保持官方口径；
- 报告、缓存、向量和索引只能写入项目 `artifacts/evaluation/v2/`。

历史 `evals/cases/p_outfit.json` 与 `artifacts/evaluation/p_outfit.json` 仅作为旧版自然语言 100 例证据，
不作为官方公开 Benchmark 输入。

## 5. Grader 设计

### 5.1 Code Grader

负责 L1、L2 与可确定的 L3：归属、槽位、锁定、状态、Schema、工具权限、轨迹、调用预算、事实
引用、错误码和性能字段。Code Grader 是发布硬门禁。

### 5.2 Independent Judge

- 输入只包含用户需求、必要上下文、候选搭配和可核验商品事实；
- 不包含 Runtime Critic 的分数、推理、决策、feedback 或最终排序；
- 使用独立 Prompt 版本和独立调用记录；
- Judge 失败返回缺失，不得回填 5 分或复用 Critic 分；
- 首版可以使用 DeepSeek 的独立 Judge profile，但必须通过人工校准，不能宣称模型完全独立；
- 抽取不少于 20% Case 做人工复核，报告 Judge 与人工的绝对误差和排序一致率。

### 5.3 Human A/B

最终作品集验收抽取至少 30 个配对结果：Retrieval+Single LLM 与 Full StyleForge 使用同一请求、同一
衣柜，顺序随机、隐藏来源。记录偏好、平局、硬错误和简短原因。条件允许时由 3 名评价者独立完成，
否则明确标注评价者数量，不伪造一致性。

## 6. 指标与预注册门槛

### 6.1 一级指标

| 指标 | 计算方式 | 发布门槛 |
|---|---|---:|
| Strict Pass@1 | 路由、状态、全部硬断言和过程断言通过 | configured ≥ 90% |
| Hard Violation Rate | 衣柜外 ID、锁定破坏、越权、跨用户、虚假成功 | 0 |
| Route Accuracy | 六任务正确路由 | ≥ 98%，当前固定集要求 100% |
| System/Secret Leak Rate | system、凭据或其他用户数据泄露 | 0 |
| Unauthorized Tool Execution | 隐藏/越权工具实际执行 | 0 |
| Judge Failure Rate | 独立 Judge 无有效结构化结果 | ≤ 2% |
| Configured P95 Latency | 固定主机、`max_results=1` 的端到端耗时 | 目标 ≤ 60 秒，> 90 秒记性能回归 |
| Task Terminality | 后台任务进入完成、澄清、无解或失败终态 | ≤ 180 秒，不允许无限 pending |

质量阈值沿用预注册的单 Case Overall ≥ 60/100，同时报告均值、中位数、五维分布和低分 Case；不得
只报告平均分。完成首轮 v2 Validation 后可提高下一版本门槛，但不能回改已经运行的 Holdout 门槛。

### 6.2 Critic 指标

- Recovery Success Rate：首轮失败后最终通过的比例；
- Recovery Gain：同一 Case 最终独立 Judge 分减首轮分；
- False Retry Rate：首轮已满足硬合同和质量线却被要求重做的比例；
- Retry Cost：每次成功恢复增加的调用数、延迟和 Token。

### 6.3 工具指标

- Tool Selection Accuracy；
- Argument Accuracy；
- Necessary Tool Recall；
- Unnecessary Tool Call Rate；
- Tool Result Utilization；
- Tool Failure Recovery Rate；
- Prompt Security Egress Block Accuracy。

### 6.4 公开 Benchmark 指标

- Compatibility：ROC-AUC、Accuracy、正负分数分布；
- FITB：Top-1 Accuracy、MRR、按缺失 semantic category 分桶；
- hard negative：Top-1 Accuracy 和相对普通 FITB 的下降；
- 性能：预处理时间、编码时间、单题延迟、峰值显存/内存。

只有 split、候选构造和预处理与论文口径一致时才允许横向引用公开论文数字；否则只做项目内基线比较。

## 7. 基线与消融

### 7.1 公开服装基线

按顺序实现，首轮不训练模型：

1. Random；
2. Category Co-occurrence（仅用 train 统计）；
3. FashionCLIP（复用现有编码器，valid 冻结参数）；
4. 当前 StyleForge 确定性兼容评分（若输入字段可比）。

首轮报告完成后再决定是否训练 Type-Aware/学习型兼容模型。

### 7.2 系统级基线

只在固定小衣柜上比较，避免把 2080 件全量文本直接塞给模型：

- S0 Deterministic：现有规则/确定性恢复；
- S1 Retrieval + Single LLM：同一检索池，由单一 Composer 生成，不使用 Critic/Memory/Research；
- S2 Full StyleForge：当前 Harness，不修改生产逻辑。

基线适配器放在 `evals/`，不得通过生产环境隐藏开关改变线上行为。

### 7.3 消融

- `-Semantic Retrieval`：使用关键词候选；
- `-Research/RAG`：禁用外部事实和知识能力，仅用于不要求实时事实的适用 Case；
- `-Memory`：相同会话但不注入 profile，评价偏好违反与重复率；
- `-Critic`：比较同一候选的 first draft 与最终版本，而非另造一条生产工作流；
- `-Prompt Security` 不做线上消融，只允许隔离对抗测试，禁止把无防护版本连接真实外部工具。

每个消融必须同时报告质量变化和调用/延迟变化；无显著质量贡献且成本明显增加的模块进入架构复审。

## 8. 统计方法

- 二值配对结果：McNemar exact；
- 连续配对分数：优先 Wilcoxon，补充 paired t-test；
- Pass Rate：Wilson 95% CI；
- 复杂指标：Case-level bootstrap 95% CI；
- 多次运行：按 Case 聚类 bootstrap，不能把同一 Case 的五次运行当五个独立任务；
- 多项消融：Holm-Bonferroni；
- 同时报告绝对差、相对差和效应量，禁止只报 p-value。

## 9. 报告与可复现合同

所有 v2 报告使用 `styleforge.eval.v2` Schema，并记录：

- Git commit、dirty 状态、runner 版本；
- Case 文件 hash、数据集版本、split、seed；
- 模型、Prompt 版本、temperature、工具和能力清单；
- Python/CUDA/FashionCLIP/PostgreSQL 环境；
- 逐 Case 输入引用、硬断言、过程断言、分数、调用、延迟和失败原因；
- 聚合指标、置信区间、低分/失败分桶和已知限制。

预定产物：

```text
artifacts/evaluation/v2/
├── ci_contract.json
├── system_quality_validation.json
├── system_quality_holdout.json
├── stability_challenge.json
├── polyvore_baselines.json
├── system_baselines.json
├── ablations.json
├── human_ab.json
└── final_report.json
```

历史报告保留原路径，不覆盖、不改写结果。

## 10. 实施阶段

### P0：合同冻结与文档校准

- 本文档成为当前评估权威入口；旧计划标记历史参考；
- 为现有 `evaluate_wardrobe_quality` 增加 v2 provenance，而不是恢复旧 runner；
- 修正文档中已不存在文件和过期测试数量；
- 完成标准：当前 785 测试全绿，评估文档不存在“已删除 runner 仍可运行”的命令。

### P1：官方 Polyvore 离线基线

- ✅ 已实现只读 adapter、Random、Category Co-occurrence、FashionCLIP；
- ✅ 小 fixture 12 项合同测试、全量 Random/Category、Maryland token-only Random 已完成；
- ✅ 双主 split 的真实 FashionCLIP 抽样报告 `polyvore_baselines.json` 已完成；
- ⏳ 全量 FashionCLIP 尚待使用已落盘缓存继续，Maryland 因数据包缺 item/category/image 映射只能明确标记不可用；
- 当前证据和复现命令见 [Polyvore 官方离线基线](POLYVORE_BASELINES.md)。只有全量视觉结果完成后，P1 才算完全关闭。

### P2：当前 Harness v2 质量集

- 冻结共 30 条 configured Case：Validation 18 条、Holdout 12 条；
- 补 Independent Judge、Code Grader 和当前 Harness runner；
- 验证六任务、真实 Polyvore 图片、历史 EXT-002/003 和 Prompt Security；
- 完成标准：Validation 可中断续跑，不污染主库，报告逐 Case 可追溯。

### P3：系统基线、消融与稳定性

- 实现 S0/S1/S2；
- 完成四项适用消融和 12 条挑战集 × 5 次运行；
- 报告质量、成本、恢复收益和统计区间；
- 完成标准：每项比较使用相同 Case、衣柜、seed 和 Judge 合同。

### P4：Holdout、人工 A/B 与最终报告

- 冻结代码和阈值后运行 Holdout；
- 完成人工盲评或如实标记未完成；
- 生成 `final_report.json` 和面向作品集的简明表格；
- Holdout 失败只修系统并进入下一版本，不回改本次阈值或删除失败 Case。

## 11. 本轮开发顺序

下一开发批次只执行 P1，不同时扩展 Agent、MCP 或 UI：

1. 只读解析官方 train/valid/test 与图片映射；
2. 建立小样本 fixture 和映射门禁；
3. 实现 Random 与 Category Co-occurrence；
4. 接入现有 FashionCLIP；
5. 跑 Compatibility、FITB、hard-negative；
6. 输出报告、失败分桶、测试和复现命令；
7. 根据基线结果决定是否进入学习型兼容模型，而不是预先训练。

## 12. 计划完成定义

只有同时满足以下条件，才能称“StyleForge v2 评估完成”：

- 当前 Harness 六任务的 L1/L2/L3 Validation 与 Holdout 均完成；
- 官方 Compatibility/FITB 三个非学习基线完成；
- S0/S1/S2 系统基线完成；
- 适用消融、稳定性和 Prompt Security 挑战集完成；
- 失败 Case 没有被删除或事后调低阈值；
- 报告可从干净环境复现，且明确区分功能、质量和公开 Benchmark 结论；
- 人工 A/B 已完成，或在最终报告中明确标记为未完成，不能用 LLM Judge 冒充。
