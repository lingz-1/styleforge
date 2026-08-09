# 测试与评估

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

- 固定用例：`evals/cases/task_routing.json`，42 条，六类任务各 7 条。
- runner：`D:\anaconda\envs\style\python.exe -m evals.runners.evaluate_task_routing`。
- 输出：`artifacts/evaluation/task_routing_baseline.json`，包含准确率、逐类准确率、混淆矩阵和错误明细。
- 结果：42/42 正确，总准确率 100%，六类逐类准确率均为 100%，失败样本 0。
- 边界：该评估只证明 Task Router 分类表现，不评价检索、搭配、五维分数或用户满意度。

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
