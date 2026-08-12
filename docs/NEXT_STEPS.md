# 后续工作

> 更新时间：2026-08-10  
> 原则：先验证最新代码，再预览真实订单，最后才允许提交衣柜或导入 Mytheresa 主库。

## P1：用户长期记忆系统与多轮对话（✅ 2026-08-12 已完成）

- **记忆系统**：`user_memories` 表 + 确定性自动提炼（复用 request_parser 词典，每类上限 2 条）+ 置信度累加 + 手动增删改查（手动优先）+ 注入三 Agent prompt。Web 新增 `/memories` 偏好管理页。
- **多轮对话**：`chat_sessions` + `chat_messages` 表，`POST /tasks/execute` 带 `session_id` 落库，会话 outfit context 自动恢复；两段式路由让"换一件外套""更正式一点"自动带上文并路由到修改任务；"更正式一点"走整体调整模式（重建一套完整搭配）。前端聊天化：会话侧栏 + 历史恢复 + localStorage 记住当前会话。
- 验证：全量 **364 passed**、Ruff clean、Web 构建通过；方案与契约见 [会话与记忆契约](SESSION_CHAT_MEMORY.md)。

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

该报告只记录 Task Routing Accuracy。P2.5 的 Wardrobe Fixtures 与五维穿搭 benchmark 尚未实现，完成前不要把 P2.5 整体标记为已完成。

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
- 原始订单号没有明文写入 SQLite 或报告

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
2. 对当前主 SQLite 建立基线备份。
3. 补充适配 Mytheresa 多图片结构的像素解码审计 CLI；现有`audit_images`只适配 Garments2Look 单图路径，不能直接用于该数据。
4. 新 CLI 编写完成后重新执行`compileall → Pytest → Ruff`，通过后再检查 328,754 张图片。
5. 一次性数据库的故障注入、断点重跑、幂等和备份恢复演练。
6. 导入 62,457 件 Mytheresa 商品到同一一次性数据库并验收计数。
7. API 验收 women、men、girls、boys、baby、life 多受众查询及多图片接口。
8. 紧邻正式导入前再次备份主 SQLite，然后导入主库。
9. 在新目录构建 189,385 件 Polyvore + Mytheresa 跨数据源主图合并嵌入和 FAISS 索引。

详细命令见[Mytheresa 数据接入说明](MYTHERESA_INTEGRATION.md)。

## P1：推荐质量

- 建立 100～300 条代表性中文穿搭请求集。
- 对正式度、子类、颜色、配饰数量和无解路径分别建立断言。
- 人工抽样标注 300～500 件商品的子类和正式度。
- 把关键词规则与 FashionCLIP 零样本分类进行对比。
- 将“搭配兼容性”和“检索品类正确性”分开评估。

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
