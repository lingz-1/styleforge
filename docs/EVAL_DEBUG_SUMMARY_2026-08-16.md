# StyleForge 评估与调试总结（2026-08-14 ~ 2026-08-16）

> 本记录汇总最近两天的**评估**（单品搭配真实 LLM 验证、官方 Polyvore 基准评估体系落地、PR4A 决策验收）与**调试**（EXT-001 flexible 修复、REPLACE subject 语义缺口、局部修改 422 字段回填等），逐条记录现象、根因、解决方案与验证，并给出当前状况。
>
> 逐条问题编号参见 [ISSUE_LOG.md](ISSUE_LOG.md)；开发过程细节见 [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md) 第 16–17 节；评估报告见 [EVALUATION_REPORT.md](EVALUATION_REPORT.md) 与 [P_OUTFIT_EVAL_TEST_REPORT.md](P_OUTFIT_EVAL_TEST_REPORT.md)。

## 0. 时间线总览

| 日期 | 主线 | 关键产物 / 验证 |
|---|---|---|
| 08-14 | 单品搭配 + 多轮对话真实 LLM 评估 | `extend_advice.json` 报告、发现 EXT-001~003 |
| 08-15 | 数据与检索基建 | 单品 ID 全 UUID 化（SCHEMA v12）、item_advice 越界防护、官方 Polyvore 基准确认、记忆提炼 v2.4 → 纯 LLM |
| 08-16 | 语义层 PR1–PR3 + p-outfit 评估体系 + PR4A + 真实服务调试 | 540 → 542 → 551 → **554 passed**；PR4A 五类决策；局部修改 422 修复 |

---

## 1. 评估工作

### 1.1 单品搭配验证（08-14，真实 DeepSeek）

- 用例：`evals/cases/extend_advice.json`（8 条 item_advice + 5 条多轮链）；runner `evals/runners/evaluate_extend.py`（turn1 走三 Agent LLM 链，全程不碰 `parse_request`，断点续跑）。
- 结果（真实 LLM 全量跑通）：
  - item_advice：**锚定率 100%**，judge 均分 55.9，pass 50%（阈值 60）；one_piece 锚点只配 2 件（裙+鞋）。
  - multiturn：swap 替换正确率 100%（明确槽位替换全部命中），槽位缺失正确跳过（40%）。
  - **adjust（全局微调）5/5 崩溃** → EXT-001。
- 暴露三个系统缺陷：EXT-001（high，无槽位全局调整崩溃，**已修复**）、EXT-002（medium，one_piece 缺配饰/外套，待产品优化）、EXT-003（low，`category_induction` 把"换掉某双鞋"过度归纳为"不喜欢鞋"，待产品优化）。

### 1.2 官方 Polyvore 基准评估体系（08-15 确认基准 → 08-16 落地）

- 基准：官方 Polyvore Outfits `nondisjoint/train.json`，**Eval Case 从该集抽样 100 例完整 outfit**（golden 与请求无答案泄漏，请求无 follow-up 调整词，全部过人工审查）。
- 两类对照实验（互不混合，对应项目两种衣柜数据形态）：
  - **实验 A（order，订单导入式）**：官方文本字段 + 文字嵌入，无 color，`image_status=unbound`。
  - **实验 B（image，图片导入式）**：官方文本 + Gemini 多模态识别增强 + 图像嵌入，`image_status=available`。
- 独立 LLM 裁判 `agents/judge.py`（纯文本 DeepSeek）：五维 R/S/C/W/F 评分，与 Runtime Critic 分离，prompt 不含任何决策/推理片段（独立性硬门禁）。
- 真实运行（`--mode both`，100 例 × 2，597 次 DeepSeek 调用）：**0 gap / 0 硬违规 / 0 裁判失败**；order vs image 配对统计显著（t=3.60 p=0.0005、Wilcoxon z=3.27 p=0.001、d=0.36、McNemar p=0.024）；双模式 critic-judge 相关性 pearson<0.4（佐证裁判独立）。
- 门禁 `tests/test_p_outfit_eval.py` 29 例 + `test_p_outfit_analysis.py` 7 例 = 36 例全过（FakeLlm 脚本化，锁结构/公式/路径/隔离，不锁真实分数）；统计层不依赖 scipy，数学逐值锁定。

### 1.3 基线对比评估（待办）

- `rule-v1`（关键词规则）vs FashionCLIP 零样本分类的推荐质量对比，共享同一天真集、统一比较口径。**尚未开工**（先重跑两个评估器确认基线数字，参考 `docs/TESTING_AND_EVALUATION.md`）。

---

## 2. 问题与解决方案

### 2.1 【HIGH】EXT-001：无槽位全局调整使 extension 链 5/5 崩溃 → flexible 模式（08-16，第 16 节）

- **现象**："整体再正式一点"等无槽位调整请求走单槽位替换链路，agent2 越界 → `_validate_modify` 硬抛 `ValueError`。
- **根因**：路由正确（follow-up → OUTFIT_MODIFY），但**数据端停留单槽位语义**——overall 候选池硬截断 40 件且与调整方向无关；`_validate_modify` 仍逐字比对 `replaced_item_ids == agent1.replaced(=[])`，agent2 只要真的动手换就硬抛。
- **解决方案**：统一无槽位整体调整与槽位缺失请求为 **flexible 模式**（`adjustment_mode="flexible"`）：
  - 候选池 = required_slot 槽位单品 → 知识方向匹配单品 → 全衣柜兜底（去掉 40 件硬截断）；
  - `_validate_modify` 按模式分支：flexible 只守数据边界（引用 ⊆ 候选∪当前、确有差异、required_slot 落位），不再硬抛 replaced/locked 比对；单槽位分支保持严格不变。
- **验证**：`tests/test_extended_tasks.py` 更新缺槽位用例 + 新增 flexible 非空 replaced/locked 回归；全量 **500 passed**；真实 LLM 冒烟：整体重排产出 2 套、连衣裙套"加配饰"成功落位 accessory。

### 2.2 【HIGH】REPLACE subject 捕获语义缺口（08-16，第 17.1–17.4 节）

- **现象**：PG 恢复后全量回归 532/3 失败，其中 `test_mixed_chain_fresh_scene_does_not_rewrite` 暴露真实架构缺口——"把这件大衣换成西装"被解析为 `REPLACE target=suit` + 一条误生 `PREFER 大衣` 约束，"大衣"无法被定位为被换掉的当前单品。
- **根因**：`_replace_matches_current` 用 target 派生槽位匹配当前单品（`suit→one_piece` 对不上外套）；anaphora 一律 NEEDS_CLARIFICATION。
- **解决方案**（通用修复，无 case 专用分支）：`Change` 增加 `subject: EntityRef | None`；`_collect_subject_spans` 识别把/将字句与"X 换 Y"裸主谓；`_partition_current` 有 subject 时**只用 subject 匹配**当前单品；会话内 subject 命中当前单品 → anaphor 已解析 → EXACT。
- **验证**：新增 5 例语义回归；全量 **540 passed**。

### 2.3 【HIGH】局部修改 422：`locked/replaced` 与 Agent 1 事实不一致（08-16，本会话）

- **现象**：前端真实链路，"帮我搭配一套粉色文艺风穿搭"成功后，追问**"不要运动鞋"**返回 422：`局部修改结果的被替换单品与 Agent 1 事实不一致；局部修改备选方案丢失锁定单品`（重试后连锁定集合也不一致）。日志确认 422 来自 api.py 的 `ValueError → HTTP 422`。
- **根因**：`locked_item_ids` / `replaced_item_ids` 是 Agent 1 候选池对当前穿搭的**确定性 partition**（`pool.lock_ids + pool.keep_ids` / `pool.replace_ids`），却作为 LLM 可填字段暴露在 Agent 2 schema 里。LLM 自行填写必然可能与 facts 集合不相等，而 [extension_validation.py](apps/api/styleforge/tools/extension_validation.py) 的 `_validate_modify` 做**集合严格相等**校验；`_critic_with_repair` 只重试一次，二次失败 ValueError 穿透到 API → 整单 422。
- **解决方案**（与 PR4A 的 decision 同构——**确定性程序回填、LLM 不填**）：
  1. [agents/composer.py](apps/api/styleforge/agents/composer.py)：结构化 modify 模式（`adjustment_mode != "flexible"`）在 validate 后把 `result.locked_item_ids` / `result.replaced_item_ids` **用 Agent 1 facts 值强制回填覆盖**，无论 LLM 填什么；
  2. [llm/extension_prompts.py](apps/api/styleforge/llm/extension_prompts.py)：completion rule 声明"locked/replaced 由系统自动回填、无需填写"，并强化"锁定单品绝不丢弃或替换"；
  3. flexible 模式保持 LLM 自主（该分支校验本就不同这两个字段）。
- **验证**：新增 [tests/test_modify_restore.py](tests/test_modify_restore.py)（3 例：acceptance 全锁定回填、REPLACE 非空 replaced 回填、prompt 声明断言），回填后 `validate_extension_draft` 通过；**全量 554 passed + Ruff clean**；后端已重启待用户复测。

### 2.4 【MEDIUM】Concern A：PREFER 颜色缺口从未被报告（08-16，第 17.5 节）

- **现象**："想要粉色但衣橱只有金色发夹"被静默吞掉，Agent 2 无从得知偏好被放弃。
- **根因**：`build_relaxation_plan` 只处理 MUST 颜色与品类放宽，PREFER 是软约束，exact 不拦截。
- **解决方案**：`ConstraintCoverage` 新增 `prefer_missed_ids` / `unmet_prefer_colors`；放宽链重编号 **L0 exact → L1 放弃 PREFER 色 → L2 放弃 MUST 色 → L3 品类拓宽到槽位**；**MUST/MUST_NOT 永不自动放宽**（color→type→slot 只是允许放宽项间的优先级）。
- **验证**：验收 case"不要帽子，要粉色系发夹"报告 `unmet_prefer_colors=["pink"]`、无帽子进候选；542 passed。

### 2.5 【MEDIUM】Concern B：会话内已解析 subject 残留 effective unresolved（08-16，第 17.5 节）

- **现象**：`_structured_modify` 直接把 spec dump 进 facts，`unresolved_fields` 里的 `anaphoric_reference` 原样保留，Agent 2 看到过期澄清义务。
- **解决方案**：feasibility 非 NEEDS_CLARIFICATION 且 spec 含 anaphor 时，用 `spec.model_copy` 移除 `anaphoric_reference` 再 dump effective spec。
- **验证**：`test_resolved_anaphor_not_kept_as_effective_unresolved`；542 passed。

### 2.6 【MEDIUM】PR4A 决策映射：SATISFIABLE_WITH_RELAXATION→RETRIEVE_MORE 可疑 + MUST 色自动放宽（08-16，第 17.6 节，用户评审后修正）

- **用户评审反馈**（提交前）：决策应基于 `unsatisfied_hard / unsatisfied_soft / coverage` 分解事实而非只看 coarse status；"MUST 色不自动放宽"应推广为**所有 MUST（MUST/MUST_NOT/LOCK，类型/场合/槽位同规则）**；`candidate_pool` 完整 dump 有 token 膨胀风险。
- **解决方案**：重写 [core/decision.py](apps/api/styleforge/core/decision.py) 用 `Agent2DecisionFacts{feasibility, relaxation_plan}` 分解信号——`option.unmet`（任一放宽层级无候选）→ WARDROBE_GAP；`minimal_level≥2`（MUST 色放弃/品类拓宽）→ RETRIEVE_MORE；`unmet_prefer_colors` + EXACT → RELAX_PREFERENCE；未解析指代 → ASK_USER；否则 EXACT_MATCH。决策只读 `feasibility_report.state + relaxation_plan`，**不 dump 候选池**。`decision` 从 LLM schema 剔除，composer 校验后挂回（覆盖 LLM 值）。
- **验证**：`tests/test_decision.py` 9 例；探测 8 场景全部符合预期；551 passed。

### 2.7 【LOW】EXT-002 / EXT-003（待产品优化）

- EXT-002：锚定 one_piece 时不补用户要求的配饰/外套。
- EXT-003：记忆提炼把单次换鞋过度归纳为"不喜欢鞋"（shoes/tops/bottoms 负面）。
- 按用户决策如实记录不修，供人工复核；EXT-001 修复后可重跑 `evaluate_extend.py` 让报告反映修复。

### 2.8 运维 / 自测调试（08-16 本会话）

- **curl 中文 body 400**：Git Bash 终端 GBK 编码破坏内联中文 JSON → 改为 UTF-8 文件 `--data-binary @file`（或走 Swagger）。
- **前端 5173 ERR_CONNECTION_REFUSED**：Vite dev server 未启动 → `apps/web` 后台 `npm run dev`（`/api` 代理到 8000）。

---

## 3. 当前状况

### 3.1 代码质量

- **全量回归 554 passed**（110s 级，含 PR1–PR3、p-outfit 评估、PR4A 决策、本会话 422 修复的回归测试）、Ruff clean。
- 遗留 1 条非阻塞警告：FastAPI TestClient 的 httpx 弃用提示（StarletteDeprecationWarning，第三方）。

### 3.2 服务

- 后端 uvicorn **8000**（新进程已加载 422 修复，/health 正常，catalog 2084 件）；前端 Vite **5173** 运行中，`/api/*` 代理到后端。真实服务可测。

### 3.3 未提交的工作区（待用户复测确认后提交）

| 文件 | 内容 |
|---|---|
| `core/decision.py`、`tests/test_decision.py`、`models/agent_tasks.py`、`docs/DEVELOPMENT_LOG.md`(17.6) | PR4A：Agent 2 五类结构化决策 |
| `agents/composer.py`、`llm/extension_prompts.py`、`tests/test_modify_restore.py` | 本会话「不要运动鞋」422 修复（locked/replaced 确定性回填） |

> 提交将按用户此前批准的方向拆两笔：① PR4A 代码+测试（decision / agent_tasks / composer 决策注入 / extension_prompts / 测试）；② 文档 + 本会话修复。

### 3.4 待办

- 用户复测「不要运动鞋」（及 PR4A 验收 case「不要帽子，要粉色系发夹」）确认通过。
- **PR4B：Validator–Critic 边界**（用户指定 PR4A 稳定后做；本会话的 422 修复已触及该边界的一个角：确定性字段回填）。
- 基线对比评估（rule-v1 vs FashionCLIP）。
- EXT-002 / EXT-003 产品优化（可选）。
- `extension_prompt_version` 仍为 `extension-three-agent-v3.2`，PR4A 加入决策但未 bump 版本（潜在待办）。
