# 后续工作

> 更新时间：2026-09-01
> 原则：聚焦项目核心，不新增外围基础设施；真实个人衣橱生产闭环 v1 已完成。下一主线应转向官方 Polyvore Compatibility/FITB 离线基线，之后再决定是否训练或引入学习型兼容模型。真实订单提交和 Mytheresa 主库扩容仍需用户单独确认。
> 2026-09-01：评估范围、指标、数据隔离、基线、消融和阶段已重新冻结到
> [评估计划 v2](EVALUATION_PLAN_V2.md)。下一批严格执行其中 P1，不恢复旧 p-outfit runner。

## 当前优先级（2026-08-29）

1. **P0：官方 Polyvore Compatibility/FITB 离线基线**：先实现随机、品类共现、FashionCLIP 三个可复现基线，统一 train/valid/test 映射、指标和报告，不调用外部 API。
2. **P1：真实个人衣橱质量回归扩容**：把目前 11 张真实图片/7 条六任务验收扩成固定的 30~50 件小衣橱集，覆盖图片损坏、识别低置信、服务中断、重启恢复和向量失败；只复用现有 PostgreSQL、API、Web 与测试框架。
3. **P2：数据生命周期产品化**：在现有“移出衣柜”软删除之外，只有用户明确需要时再增加导出与永久删除入口，并定义图片、个人向量、批任务原图和行为记录的级联策略。

暂不做：MCP 扩展、Celery/Redis 队列、pgvector 迁移、新监控栈、Mytheresa 全量导入或新的 Agent。当前规模下这些不会直接提高核心穿搭质量。

## P0：Multi-Agent Harness（H1+H2）+ H3a 上下文分层（✅ 2026-08-20 已完成）

- **H1+H2（LangGraph 多 Agent 编排）**：核心链路切换为 `StyleForgeHarness`，Coordinator → Research Subgraph → Evidence Synthesizer → Stylist×3 → Environment Gate → Critic → StageCandidate → GoalGate → PersistCandidates；H1a→H2c 分阶段落地，真实 DeepSeek 验收产出 3 候选 + evidence + done，候选死锁用 bounded revision（Critic FAIL 后最多 3 步强制重提交 + DEGRADED_ACCEPTED）兜底。
- **H3a（Grounding + Memory 分层 + WardrobeIndex）**：`GroundingContext`（search-before-ask 确定性判定 + SEARCH_FIRST 运行时校验 + ThreadGroundingView pending_field 跨轮确认）、`PreferenceRetriever`（短期/场景/长期/避免分层 Top-K）、`ThreadPreferenceView`（会话内偏好 + scope gate 拦截 turn 词）、`WardrobeIndexSummary`（衣橱 262KB 全量清单 → ~1KB 能力索引）。
- **真实 DeepSeek 回归**：「下半年去piacon怎么穿搭」→ needs_clarification（不伪造事实）；「下半年去pia的演唱会怎么穿搭」→ 修复前 `infeasible / 0 候选` → 修复后 **completed / 2 候选 / 36 次调用**。四个调试根因见 [开发过程记录](DEVELOPMENT_LOG.md) 19.3。
- 验证：全量回归 **825 passed**；详见 [项目状态](PROJECT_STATUS.md) 与 [开发过程记录](DEVELOPMENT_LOG.md) 第 18、19 节。

后续候选：

- 基线对比评估（规则关键词 vs FashionCLIP）仍待办（见下节）。
- EXT-002/003 遗留缺陷（one_piece 缺配饰/外套；记忆过度归纳）仍待修。
- 多轮反悔还原原 item（「鞋还是换回来，其他保留」）待设计。
- H3b：Memory 写链（apply_evidence 生命周期/consolidation 优化）、ContextCompact 全量（EvidenceStore 摘要 → micro-summary → trim → LLM compact）。
- H4：MCP 接入、PermissionManager、后台任务、Research 深层 subagent。

## P1：用户长期记忆系统与多轮对话（✅ 2026-08-12 已完成）

- **记忆系统（Context-Aware 自适应偏好记忆）**：完整闭环落地——`interaction_events`（行为事实）→ `preference_evidence`（标准化证据）→ `preference_model`（维度化偏好，Schema v11 取代 `user_memories`）→ Memory Resolver 五桶 → 三 Agent 差异化注入。行为来自前端推荐结果按钮（采纳/换掉/好评差评/换掉这件）、后端埋点与 LLM 语言证据提炼；含确定性聚合（幂等全量重算）、生命周期/衰减与 consolidation。Web `/memories` 偏好管理页展示/编辑维度化偏好。
- **多轮对话**：`chat_sessions` + `chat_messages` 表，`POST /tasks/execute` 带 `session_id` 落库，会话 outfit context 自动恢复；两段式路由让"换一件外套""更正式一点"自动带上文并路由到修改任务；"更正式一点"走整体调整模式（重建一套完整搭配）。前端聊天化：会话侧栏 + 历史恢复 + localStorage 记住当前会话。
- 验证：全量 **414 passed**、Ruff clean、Vue 生产构建通过；方案见《项目文档/StyleForge 记忆系统实现方案.md》，契约见 [会话与记忆契约](SESSION_CHAT_MEMORY.md)。

后续 P2 候选：

- 会话摘要式命名（用首条请求 LLM 生成标题，替代"会话 N"）。
- 整体调整保留部分单品（如"外套以外的都保留"）；多轮里显式引用某件单品。
- 记忆支持跨类别关联（如"正式场合 → 黑色"）与时间衰减。
- 小程序接入会话化（`session_id` 透传 + 会话列表）。

## P1：用户长期记忆系统与多轮对话（2026-08-11 计划）

明天开发两块能力，方向待细化，先记录目标与现状：

- **记忆系统**：目前仅有"跨请求软新颖惩罚"（持久化最近 5 次 `request_signature` 做结构签名）与 `preference_json.environment_profile`。要扩展为用户**长期偏好记忆**（品类/颜色/风格/正式度倾向，天气与环境响应习惯），让推荐在多次对话间保持一致并可用编辑/遗忘。
- **多轮对话**：目前 `POST /tasks/execute` 的"局部修改"任务是一次请求内完成修改。要支持**同一会话内的连续追问**（如"换一件外套""更正式一点""这件搭裙子呢"）自动携带上文上下文，而不必每次完整描述需求。

开始前先核对现有 `task_runs` / `styling_runs` 持久化结构与 `structure_signature` 记忆实现，评估是否复用（勿重复造轮子）。完成判据、Schema 迁移与测试合同在动手前细化并先写方案文档。

## P0：v3.3 P2 Task Router 回归（✅ 2026-08-09 已完成）

> `compileall` 通过；P2/P2.5 定向测试 31 passed；全量 Pytest 165 passed；Ruff 通过；42 条路由基线准确率 100%。

按顺序验证新路由模块，任一步失败时停止：

```powershell
D:\anaconda\envs\style\python.exe -m compileall -q apps\api\styleforge tests
D:\anaconda\envs\style\python.exe -m pytest -q tests\test_task_router.py
D:\anaconda\envs\style\python.exe -m pytest -q tests\test_task_routing_eval.py
D:\anaconda\envs\style\python.exe -m pytest -q
D:\anaconda\envs\style\python.exe -m ruff check apps\api\styleforge tests
```

完成判据：六类代表性请求全部路由到对应子图；`/recommendations` 原回归保持通过；`POST /tasks/route` 只返回 `routed`。五类扩展业务现由独立的 `POST /tasks/execute` 执行。

复现 P2.5 路由基线报告：

```powershell
D:\anaconda\envs\style\python.exe -m evals.runners.evaluate_task_routing
```

该报告只记录 Task Routing Accuracy。Wardrobe Fixtures 与五维固定穿搭 benchmark 后续已经落地；
当前缺口改为 [评估计划 v2](EVALUATION_PLAN_V2.md) 定义的官方 Compatibility/FITB、系统基线与消融。

## P0：Context Pack 与五类扩展业务（✅ 2026-08-09 已完成）

- 已完成共享领域结构、Context Pack、六分支执行器、`task_runs` Schema v7、执行与查询 API。
- 已完成局部修改、风格知识、单品知识、新品兼容性、衣橱缺口及 Web/小程序入口。
- 已完成扩展业务单元测试、HTTP 接口测试、知识未覆盖失败持久化测试和 Web 生产构建。
- 最终结果：Python 编译通过、Ruff clean、Pytest 175 passed、路由评估 42/42、真实 API 进程烟测通过。
- 后续质量工作是扩大知识覆盖、建立 Wardrobe Fixtures/五维 benchmark 和人工偏好评测，不属于本轮业务闭环缺失。

## P0：最新代码回归

> ✅ 2026-08-04 已完成：`compileall` 退出码 0；Pytest 47 passed（0.67s）；Ruff `All checks passed`。

| 顺序 | 任务 | 完成判据 |
|---:|---|---|
| 1 | `compileall` | 退出码 0，无语法错误 |
| 2 | Pytest | 全部测试通过，记录用例数和耗时 |
| 3 | Ruff | `All checks passed` |
| 4 | 更新项目状态 | 把结果、日期和失败修复写入`PROJECT_STATUS.md` |

```powershell
D:\anaconda\envs\style\python.exe -m compileall -q apps\api\styleforge tests
D:\anaconda\envs\style\python.exe -m pytest -q
D:\anaconda\envs\style\python.exe -m ruff check apps\api\styleforge tests evals
```

## P0：完整订单表验收

> ✅ 2026-08-04 已完成：一次性数据库预览 `total_rows = 437`、`eligible = 374`、`ineligible = 63`、`unknown = 0`、`candidate = 111`。验收中修复了 15 个非服饰误分类（扩充 `NON_FASHION_PATTERN`、新增 `underwear/underpants`、收紧 `blazer`），并新增 12 个回归测试。证据见 `PROJECT_STATUS.md` 3.4。

先使用唯一命名的独立数据库生成预览，不加`--commit-candidates`：

```powershell
cd C:\Users\32369\Desktop\agent-p\style
$env:PYTHONPATH=(Resolve-Path ".\apps\api")
$previewStamp = Get-Date -Format "yyyyMMdd-HHmmss"
D:\anaconda\envs\style\python.exe -m styleforge.pipelines.import_wardrobe_orders `
  --excel "E:\D_from_web\订单数据 (3).xlsx" `
  --user-id personal-user `
  --default-audience women `
  --database "artifacts\wardrobe-status-preview-$previewStamp.db" `
  --report "artifacts\imports\wardrobe-status-preview-$previewStamp.json"
```

验收要求：

- `total_rows = 437`
- `eligible_order_rows = 374`
- `ineligible_order_rows = 63`
- `unknown_order_rows = 0`
- `交易关闭/买家已付款/卖家已发货/充值成功`均不能出现在候选列表
- 多商品子行继承正确订单状态，但金额没有被错误继承
- 原始订单号没有明文写入数据库或报告

然后人工抽查至少 30 条：衬衫、半身裙、裤装、鞋、外套、配饰、汉服套装、内衣/背心、非服饰各至少 3 条。发现误分类后修改规则，并从`compileall → Pytest → Ruff → 新数据库预览`重新走完整回环，再决定是否提交。

不建议直接使用 CLI 的`--commit-candidates`处理原始购物记录。优先通过 Streamlit 逐条确认“实际仍然拥有”的衣物。

## P0：订单提交与自动嵌入验收

使用专用测试用户选择 3～5 件衣物：

1. 提交一件无图商品，确认生成文字嵌入。
2. 上传一张实拍图，确认图片保存且嵌入类型切换为图像。
3. 尝试手工选择一条`交易关闭`记录，确认 API 返回 422 且数据库没有新增衣物。
4. 重复上传同一 Excel，确认复用同一批次，不重复创建衣物。
5. 在推荐中确认只出现当前用户衣柜商品。

## P1：Mytheresa 安全接入

在修改主数据库前依次完成：

1. 最新测试回归。
2. 对当前主库建立基线备份（`pg_dump -Fc styleforge`）。
3. 补充适配 Mytheresa 多图片结构的像素解码审计 CLI；现有`audit_images`只适配 Garments2Look 单图路径，不能直接用于该数据。
4. 新 CLI 编写完成后重新执行`compileall → Pytest → Ruff`，通过后再检查 328,754 张图片。
5. 一次性库（独立 PG 库）的故障注入、断点重跑、幂等和备份恢复演练。
6. 导入 62,457 件 Mytheresa 商品到同一一次性库并验收计数。
7. API 验收 women、men、girls、boys、baby、life 多受众查询及多图片接口。
8. 紧邻正式导入前再次备份主库，然后导入主库。
9. 在新目录构建 189,385 件 Polyvore + Mytheresa 跨数据源主图合并嵌入和 FAISS 索引。

详细命令见[Mytheresa 数据接入说明](MYTHERESA_INTEGRATION.md)。

## P1：推荐质量

- ✅ **单品搭配 + 多轮对话真实 LLM 验证（2026-08-14）**：`evals/cases/extend_advice.json` 8 单品 + 5 多轮链，独立裁判评估完成——锚定率 100%、裁判均分 55.9、pass 50%；多轮槽位替换 100% 命中 + 缺失正确跳过。产物 `artifacts/evaluation/extend_advice.json` + 全程日志 `artifacts/evaluation/env/order/logs/`。
- ✅ **EXT-001 已修复（2026-08-16）**：多轮 adjust 全局微调（”整体再正式一点”）5/5 崩溃已解决——无槽位/槽位缺失请求走 **flexible 模式**，由 agent 自主理解意图、灵活重排整套（候选池=知识方向匹配+全衣柜兜底），`_validate_modify` 按模式分支不再硬抛。真实冒烟：整体调整产出 2 套重排不崩溃；连衣裙套自主补 accessory 槽位成功。全量回归 500 passed。详见[开发过程记录](DEVELOPMENT_LOG.md)第 16 节。
- ⚠️ **遗留缺陷待修（EXT-002/003，已如实记录）**：
  - **EXT-002**：锚定 one_piece 缺配饰/外套（item-001 judge 32.0）。
  - **EXT-003**：记忆 category_induction 过度归纳（shoes/tops/bottoms 负面）。
- **待办：基线对比评估**：把关键词规则（`evaluation/rule_baseline.py`）与 FashionCLIP 零样本分类（`evaluation/visual_retrieval.py`）统一口径对比；确认二者共享同一天真集、能否统一比较（正式度/子类/颜色/配饰数量/无解路径五类断言）；随机/品类共现/FashionCLIP 基线 → `artifacts/evaluation/polyvore_baselines.json`。
- 建立 100～300 条代表性中文穿搭请求集；人工抽样标注 300～500 件商品的子类和正式度（后续）。
- 将”搭配兼容性”和”检索品类正确性”分开评估（p-outfit 兼容性/FITB 离线评测随基线对比一并落地）。

## P2：真实 LLM Agent

确定性基线稳定后再接入：

- Planner：结构化输出`TaskSpec`，失败时回退本地解析器。
- Stylist：只能对候选 outfit ID 排序，禁止生成商品 ID。
- Reviewer：引用 ScoreCard 证据生成解释，不得覆盖硬约束。
- 所有输出经过 Pydantic 校验，并记录模型、提示版本、延迟、token 和降级原因。

## P3：作品集完善

- 增加架构图、界面截图和 90 秒演示视频。
- 展示空衣柜、无解、模型不可用和非法订单状态等失败路径。
- 增加 CI：单元测试、Ruff、小型无 GPU 冒烟测试。
- 增加 Windows 一键启动脚本或 Docker 部署。
- 将测试结果、数据审计和检索指标整理为可复现证据表。

## MVP 完成标准

- 最新测试和 Ruff 全部通过。
- 完整订单表预览与提交硬门槛通过验收。
- 三类代表性请求能稳定生成结果或正确返回无解。
- 推荐只包含当前用户衣柜商品。
- 图片、评分、约束和 Agent 轨迹在 UI 中可见。
- FashionCLIP 不可用时有明确降级路径。
- 文档命令能由新读者独立复现。
