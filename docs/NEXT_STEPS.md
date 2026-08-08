# 后续工作

> 更新时间：2026-08-03  
> 原则：先验证最新代码，再预览真实订单，最后才允许提交衣柜或导入 Mytheresa 主库。

## P0：最新代码回归

> ✅ 2026-08-04 已完成：`compileall` 退出码 0；Pytest 47 passed（0.67s）；Ruff `All checks passed`。

| 顺序 | 任务 | 完成判据 |
|---:|---|---|
| 1 | `compileall` | 退出码 0，无语法错误 |
| 2 | Pytest | 全部测试通过，记录用例数和耗时 |
| 3 | Ruff | `All checks passed` |
| 4 | 更新项目状态 | 把结果、日期和失败修复写入`PROJECT_STATUS.md` |

```powershell
D:\anaconda\envs\style\python.exe -m compileall -q styleforge tests
D:\anaconda\envs\style\python.exe -m pytest -q
D:\anaconda\envs\style\python.exe -m ruff check styleforge tests
```

## P0：完整订单表验收

> ✅ 2026-08-04 已完成：一次性数据库预览 `total_rows = 437`、`eligible = 374`、`ineligible = 63`、`unknown = 0`、`candidate = 111`。验收中修复了 15 个非服饰误分类（扩充 `NON_FASHION_PATTERN`、新增 `underwear/underpants`、收紧 `blazer`），并新增 12 个回归测试。证据见 `PROJECT_STATUS.md` 3.4。

先使用唯一命名的独立数据库生成预览，不加`--commit-candidates`：

```powershell
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
