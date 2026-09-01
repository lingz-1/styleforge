# Polyvore 官方离线基线

> 更新时间：2026-09-01
> 实现状态：只读 adapter、Random、Category Co-occurrence、FashionCLIP、报告与测试已完成；全量非视觉统计已运行，FashionCLIP 已完成双 split 真实图片抽样运行，全量视觉运行待使用同一缓存继续。

## 1. 评估边界

本评估使用 `E:\01-style-dataset\p-outfit` 中的官方文件，不调用外部 API，不修改数据集目录，所有报告和嵌入缓存只写入项目 `artifacts/evaluation/v2/`。

它只测服装基础能力：

- Compatibility：判断整套搭配是官方正例还是构造负例；
- FITB：根据其余单品，从 4 个候选中补回被挖掉的单品；
- Maryland hard negative：更难的同类候选题。

它不能证明六任务 Agent 编排、多轮对话、工具调用、记忆或 Prompt Security 的质量，也不能替代当前 Harness 质量集。

## 2. 已实现内容

核心实现：

- `evals/polyvore_benchmark.py`：官方格式 adapter、强合同、抽样、指标和三类评分器；
- `evals/runners/evaluate_polyvore_baselines.py`：多变体 runner、来源 hash、错误捕获、报告和 FashionCLIP 可续跑缓存；
- `tests/test_polyvore_benchmark.py`：小 fixture、乱序答案、脏数据、映射门禁、指标、三基线和报告测试。

基线口径：

1. Random：固定 seed 对 case/token 做 SHA-256 确定性打分；
2. Category Co-occurrence：只使用 train 正例，统计 `semantic_category + category_id` 类别对的加平滑对数共现概率；
3. FashionCLIP：复用项目本地模型和 L2 归一化图像向量，Compatibility 取跨语义类别单品对平均余弦，FITB 取候选与上下文平均余弦；不训练、不读取 valid/test 标签调模型参数。

报告包含：文件 SHA-256、源数据量、实际评估量、seed、模型修订、缓存命中、新编码数量、编码/评分耗时、ROC-AUC、冻结 valid 阈值后的 Accuracy、正负分数分布、FITB Top-1、MRR、缺失类别分桶和 95% Wilson 区间。

## 3. 官方格式与已发现的数据事实

### 3.1 disjoint

- Compatibility token 通过同 split 的 `{set_id}_{index}` 映射到 `item_id`；
- FITB 的 4 个答案经过打乱，正确项必须用原 outfit 的 `set_id + blank_position` 定位，不能假设第一项正确；
- train/valid/test 的引用 token、元数据、semantic category 和图片缺失均为 0。

### 3.2 nondisjoint

- Compatibility 和图片映射方式相同；
- FITB 发布格式把正确答案固定在第一项；
- train/valid/test 的引用 token、元数据、semantic category 和图片缺失均为 0。

### 3.3 Maryland hard negative

当前数据包只有 Compatibility/FITB token 文件，没有对应 outfit→item、元数据或图片映射。文件还包含：

- `blank_position` 与 token 后缀不一致的记录；
- 同一答案 token 重复出现的记录。

因此严格保留官方“第一答案为正确项”的发布口径，不删除、不修写题目。Random 可以完整复现；Category Co-occurrence 和 FashionCLIP 必须标记为 `unavailable`，不能伪造类别或图片结果。若后续取得与该 Maryland 版本严格对应的原始 item/image 映射，再补视觉结果。

## 4. 已运行结果

### 4.1 全量非视觉统计

报告：`artifacts/evaluation/v2/polyvore_baselines_statistical.json`（本地生成物，不提交 Git）。

| Variant | Baseline | Compatibility N | ROC-AUC | Accuracy | FITB N | Top-1 | MRR |
|---|---|---:|---:|---:|---:|---:|---:|
| disjoint | Random | 30,290 | 0.5016 | 0.5020 | 15,145 | 0.2529 | 0.5233 |
| disjoint | Category | 30,290 | 0.5000 | 0.5000 | 15,145 | 0.2516 | 0.5221 |
| nondisjoint | Random | 20,000 | 0.5063 | 0.5031 | 10,000 | 0.2430 | 0.5156 |
| nondisjoint | Category | 20,000 | 0.5001 | 0.5002 | 10,000 | 0.2525 | 0.5236 |
| Maryland | Random | 6,081 | 0.4915 | 0.5011 | 3,076 | 0.2516 | 0.5220 |

Category 接近随机不是 runner 失效：官方 Compatibility 负例和 FITB 候选按类别位置匹配，粗/细类别共现几乎不含区分具体商品兼容性的信号。这一结果说明下一步需要视觉或学习型跨类别关系，而不是继续堆类别规则。

### 4.2 双 split 真实图片抽样三基线

主报告：`artifacts/evaluation/v2/polyvore_baselines.json`。固定 seed `20260901`；每个主 split 的 valid/test 分别抽 256 条 Compatibility 和 256 条 FITB，train 抽 2,048 条 Compatibility；全量映射审计仍覆盖所有官方引用。

| Variant | Baseline | Test ROC-AUC | Test Accuracy | FITB Top-1 | FITB MRR |
|---|---|---:|---:|---:|---:|
| disjoint | Random | 0.4935 | 0.5078 | 0.2227 | 0.5033 |
| disjoint | Category | 0.5170 | 0.5039 | 0.2812 | 0.5400 |
| disjoint | FashionCLIP | **0.7514** | **0.6719** | **0.4922** | **0.6953** |
| nondisjoint | Random | 0.5259 | 0.5000 | 0.2500 | 0.5228 |
| nondisjoint | Category | 0.5565 | 0.5039 | 0.2383 | 0.5173 |
| nondisjoint | FashionCLIP | **0.7344** | **0.6680** | **0.5156** | **0.7038** |

本轮共需要 13,055 个真实图像向量：disjoint 命中既有缓存 818、增量编码 5,500；nondisjoint 增量编码 6,737。新增图片编码约 7.95~8.00 ms/张，整份报告耗时 121.05 秒。

这些 FashionCLIP 数字是固定抽样结果，不得标成完整公开 Benchmark，也不得与原论文 Type-Aware 学习模型直接横向比较。FashionCLIP 这里只是无训练的视觉相似度基线，不等价于学习到的互补性模型。

## 5. 复现命令

定向合同测试：

```powershell
D:\anaconda\envs\style\python.exe -m pytest tests\test_polyvore_benchmark.py -q `
  --basetemp .tmp_pytest_polyvore -p no:cacheprovider
```

全量 Random + Category + Maryland 可用部分：

```powershell
D:\anaconda\envs\style\python.exe -m evals.runners.evaluate_polyvore_baselines `
  --variants disjoint,nondisjoint,maryland_polyvore_hardneg `
  --baselines random,category_cooccurrence `
  --report artifacts/evaluation/v2/polyvore_baselines_statistical.json
```

复现当前三基线抽样主报告：

```powershell
D:\anaconda\envs\style\python.exe -m evals.runners.evaluate_polyvore_baselines `
  --variants disjoint,nondisjoint,maryland_polyvore_hardneg `
  --baselines random,category_cooccurrence,fashionclip `
  --max-train-compatibility 2048 --max-compatibility 256 --max-fitb 256 `
  --embedding-batch-size 64 `
  --report artifacts/evaluation/v2/polyvore_baselines.json
```

继续跑完整 FashionCLIP（复用现有 SQLite 缓存）：

```powershell
D:\anaconda\envs\style\python.exe -m evals.runners.evaluate_polyvore_baselines `
  --variants disjoint,nondisjoint `
  --baselines fashionclip `
  --embedding-batch-size 64 `
  --report artifacts/evaluation/v2/polyvore_fashionclip_full.json
```

## 6. 下一决策

先完成全量 FashionCLIP，再决定是否进入 Type-Aware/学习型兼容模型。只有当全量视觉基线仍明显不足，并且训练数据、验证阈值和复现预算都已固定时才训练；当前不需要扩展 Agent、MCP 或 UI。
