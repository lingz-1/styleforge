# 测试与评估

## 1. 测试层次

### 静态检查

```powershell
D:\anaconda\envs\style\python.exe -m compileall -q styleforge
D:\anaconda\envs\style\python.exe -m ruff check styleforge tests
```

### 单元测试

```powershell
D:\anaconda\envs\style\python.exe -m pytest -q
```

测试用例已经编写以覆盖以下行为；最近新增的用例尚未实际执行：

- Stylist 不选择硬约束失败候选。
- Reviewer 拒绝衣柜外商品。
- 结果集优先选择互不重复的方案。
- 向量存储严格遵守商品 ID 白名单。
- 正式度规则区分西裤/酸洗牛仔裤和乐福鞋/运动鞋。
- 半身裙与裤子一级品类解析。
- 核心槽位子类解析与否定约束。
- 子类词典覆盖主要服装和配饰类别。
- 混合风格衣柜配置和分层选择。

## 2. 下一步必须执行的回归测试

最近新增的子类、多配饰、beam search、Mytheresa 多数据源和订单状态硬过滤尚未执行最新整套回归。下一步先运行：

```powershell
D:\anaconda\envs\style\python.exe -m pytest -q
D:\anaconda\envs\style\python.exe -m ruff check styleforge tests
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

检索指标不能替代整体穿搭质量指标。作品集报告必须分开陈述。
