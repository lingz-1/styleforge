# 订单衣柜导入与增量嵌入

> 更新日期：2026-08-03  
> 当前状态：旧版解析器已对`订单数据 (2).xlsx`生成 437 行、160 个服饰候选的预览，未提交任何衣物；这个 160 不能作为 v3 或`订单数据 (3).xlsx`的候选数预期。收货/退款硬过滤 v3 已完成代码实现，但尚未运行最新测试，也尚未生成正式预览。

## 完整订单表分析结果

`订单数据 (3).xlsx`共有 437 个商品行。继承多商品订单的订单状态后：交易成功 374 行、交易关闭 51 行、买家已付款 9 行、卖家已发货 2 行、充值成功 1 行。

因此最新版规则的订单准入预期为：

```text
eligible_order_rows = 374
ineligible_order_rows = 63
unknown_order_rows = 0
```

最终服饰候选数尚不确定，需要实际运行 v3 预览后才能记录。当前文件没有独立退款/售后列，无法识别未反映在最终订单状态中的部分退款或历史售后；后续若导出这些列，系统会进行额外硬过滤。

## 目标

购物订单只作为个人衣柜候选来源。只有“已经收货且没有退款、退货或售后异常”的服饰商品，经过用户确认后才能成为当前拥有并参与推荐的衣物。

原始 Excel 保持只读。系统只保存白名单字段；姓名、电话、地址、账号等字段不会写入数据库。原始订单号不会明文保存，只保存 SHA-256 哈希。

## 严格准入规则

订单导入采用白名单策略，只有以下订单状态可以进入候选：

- `交易成功`
- `已完成`
- `订单完成`
- `已收货`
- `确认收货`
- `已签收`

以下情况一律排除：

- `买家已付款`、`卖家已发货`、`待收货`等尚未确认收货的状态；
- `交易关闭`、`订单关闭`、`已取消`等无效订单；
- 退款中、退款成功、申请退款、退货中、售后中、维权中等异常信号；
- 缺失订单状态或无法识别的状态；
- 充值、生活用品及无法识别为服饰的商品。

如导出文件包含`退款状态`、`售后状态`、`退货状态`、`物流状态`或`收货状态`等列，解析器会一并检查。即使订单主状态显示成功，只要存在退款、退货或售后异常信号，也不能导入。

淘宝导出表的多商品订单通常只在第一件商品所在行填写订单号和订单状态，其余商品行留空。解析器会向下继承所属订单的订单号、状态、时间和店铺，但不会继承商品金额或实付金额。

提交接口会再次检查`order_eligibility = eligible`，因此手工修改商品品类或直接调用 API 都不能绕过订单状态限制。

## 字段与隐私

支持的字段别名包括：

- 订单：订单号、订单提交时间、订单状态、店铺名称；
- 售后：退款状态、售后状态、退货状态、物流状态、收货状态；
- 商品：商品名称、商品链接、型号款式、商品数量、商品金额、实付金额。

淘宝商品链接会删除渠道跟踪参数，只保留平台、商品 ID 和规范链接。相同文件通过`(user_id, platform, file_sha256)`去重；未提交批次在解析规则升级后会原地刷新预览。

## 数据流

```text
Excel 上传
  -> 白名单读取、订单子行状态继承、文件 SHA-256 去重
  -> 收货/退款/退货/售后硬过滤
  -> 服饰品类与属性预测
  -> 用户预览、选择和修正属性
  -> 提交层再次校验订单准入状态
  -> 写入个人衣柜
  -> 有图生成图像嵌入；无图生成文字嵌入
  -> 推荐时与目录向量合并评分
```

## 数据库

Schema v5 包含：

- `wardrobe_import_batches`：文件级审计、去重和提交状态；
- `wardrobe_import_rows`：脱敏后的订单状态、准入结论、逐行预测和用户决策；
- `personal_wardrobe_items`：已确认衣物、订单来源哈希、购买信息和拥有状态；
- `personal_item_embeddings`：个人单品的增量 FashionCLIP 向量。

只有同时满足以下条件的记录参与推荐：

```text
ownership_status = owned
review_status = confirmed
wardrobe_items.active = 1
```

## 安装 Excel 读取依赖

```powershell
D:\anaconda\envs\style\python.exe -m pip install "openpyxl>=3.1,<4" `
  -i https://pypi.tuna.tsinghua.edu.cn/simple
```

## Streamlit 操作

1. 启动 API 和 Streamlit，进入“订单导入”。
2. 上传`.xlsx`文件，选择默认目标人群。
3. 生成预览，检查订单状态、准入结论和排除原因。
4. 只选择实际仍然拥有的候选衣物，修正品类、子类、颜色、尺码和人群。
5. 保持“自动生成 FashionCLIP 嵌入”开启并提交。

无图片时先使用商品名称、款式和属性生成文字嵌入。之后上传实拍图，会用图像嵌入替换文字嵌入。嵌入失败不会回滚已经确认的衣柜记录。

## 命令行预览

默认命令只生成预览，不提交、不嵌入。每次验收使用新的时间戳数据库，避免复用旧批次：

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

只有检查过预览后，才使用`--commit-candidates`批量确认。该参数只会提交已通过现有导出字段准入门槛的服饰候选，不能提交已被识别为关闭、未收货、退款或退货的记录；无法识别当前文件未导出的历史售后事实。

### 非准入记录不可绕过验收

通过 API 生成预览后，可验证手工属性覆盖仍不能提交关闭订单。将下列`$batchId`替换为预览响应中的批次 ID：

```powershell
$batchId = "替换为实际批次ID"
$import = Invoke-RestMethod `
  "http://127.0.0.1:8000/wardrobes/personal-user/imports/$batchId"
$blocked = $import.rows | Where-Object { $_.order_eligibility -ne "eligible" } | `
  Select-Object -First 1
$before = (Invoke-RestMethod `
  "http://127.0.0.1:8000/wardrobes/personal-user").count
$body = @{
  selections = @(@{
    row_id = $blocked.row_id
    item_type = "top"
    audience = "women"
  })
  auto_embed = $false
} | ConvertTo-Json -Depth 5

try {
  Invoke-RestMethod -Method Post `
    "http://127.0.0.1:8000/wardrobes/personal-user/imports/$batchId/commit" `
    -ContentType "application/json" -Body $body
  throw "Expected HTTP 422, but the blocked row was accepted"
} catch {
  if ($_.Exception.Response.StatusCode.value__ -ne 422) { throw }
}

$after = (Invoke-RestMethod `
  "http://127.0.0.1:8000/wardrobes/personal-user").count
if ($after -ne $before) { throw "Blocked commit changed wardrobe count" }
```

合法记录优先通过 Streamlit 逐条选择 3～5 件提交。响应中的`committed_item_count`应等于选择数；衣柜接口中的对应商品应为`embedding_status = ready`。上传实拍图后再次读取衣柜，图片状态应为`available`。重复上传同一 Excel 时应返回相同批次，而不是创建重复衣物。

## API

- `POST /wardrobes/{user_id}/imports`：上传 Base64 编码的`.xlsx`并创建预览；
- `GET /wardrobes/{user_id}/imports/{batch_id}`：读取批次和脱敏行；
- `POST /wardrobes/{user_id}/imports/{batch_id}/commit`：提交明确选择且通过准入校验的行；
- `POST /wardrobes/{user_id}/items/{item_id}/image`：上传实拍图并重新嵌入。

API 面向本地部署。订单文件和图片默认分别限制为 20 MB；图片会验证并转换为最长边不超过 2400 像素的 JPEG。
