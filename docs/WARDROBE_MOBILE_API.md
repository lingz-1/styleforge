# 小程序衣柜管理接口文档

> 版本：v0.1（微信小程序前端 + StyleForge FastAPI 后端）
> 更新：2026-08-06
> 范围：衣柜管理（分类展示 / 拍照上传创建 / 修改 / 删除 / 订单导入 / 实拍图）

## 1. 总览

### 1.1 Base URL

| 阶段 | Base URL | 说明 |
|---|---|---|
| 本地开发 | `http://127.0.0.1:8000` | 微信开发者工具需勾选「不校验合法域名」 |
| 真机预览 | `http://<电脑局域网IP>:8000` | 手机与电脑同一局域网；工具开启调试模式 |
| 正式发布 | `https://<备案域名>` | 需 HTTPS + ICP 备案（后续） |

### 1.2 通用约定

- 请求/响应均 `application/json`（图片以 base64 字符串传输）。
- 错误统一为：`{"detail": "<错误信息>"}`。
- 开发阶段无鉴权；正式部署前需补充（见 5 前端建议）。

### 1.3 数据来源说明

衣柜商品分两类来源：

| 来源 | 场景 | source 字段 |
|---|---|---|
| 订单导入 | 用户导入购物订单 Excel，缺图用户再补 | `personal-*`（带订单哈希） |
| 手动拍照 | 无订单信息的衣物，用户拍照上传并填写基本信息 | `personal-*`（不带订单哈希） |
| 目录添加 | 从数据集目录挑选（可选能力） | `polyvore` / `mytheresa` |

## 2. 核心数据结构

### 2.1 Item（衣柜商品）

`GET /wardrobes/{user_id}` 返回的 `items[]` 元素字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| item_id | string | 商品唯一 ID |
| source | string | 数据来源（`personal-*`/`polyvore`/`mytheresa`） |
| gender | string | 目标人群（women/men/girls/boys/baby/life） |
| item_type | string | 品类（top/pants/shoes/dress/outwear/…），**前端分组依据** |
| main_category | string | 主类目 |
| name | string | 商品名称 |
| color | string | 颜色 |
| description | string | 描述 |
| image_status | string | `available`/`missing`/`unbound` |
| embedding_status | string | `pending`/`ready`/`failed`（嵌入是否就绪） |
| image_url | string | 商品图相对路径（拼接 Base URL 使用） |
| personal | object | 个人商品附加信息（订单号哈希、购买时间等，订单导入才有） |

## 3. 接口列表

### 3.1 获取衣柜（按衣物类型分类展示）

**已存在** · `GET /wardrobes/{user_id}`

返回当前用户衣柜全部 `active=1` 商品。

请求参数：

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| user_id | path | 是 | 用户 ID |

响应 `200`：

```json
{
  "user_id": "demo-user",
  "count": 315,
  "items": [
    {
      "item_id": "139692178_2",
      "source": "polyvore",
      "gender": "women",
      "item_type": "top",
      "main_category": "top",
      "name": "Silk Blouse",
      "color": "White",
      "description": "",
      "image_status": "available",
      "embedding_status": "ready",
      "image_url": "/items/139692178_2/image"
    }
  ]
}
```

**前端分组**：按 `item_type` 前端分组渲染（上装/下装/鞋/连衣裙/外套/配饰…）。分组标签映射见 `docs` 或前端常量表。

### 3.2 拍照上传创建衣物（无订单信息）

**需新增** · `POST /wardrobes/{user_id}/items/photo`

用户拍照/选图上传一件新衣物，可选填基本信息，后端创建个人商品 → 加入衣柜 → 自动生成嵌入。

请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| filename | string | 是 | 文件名（如 `photo.jpg`），限 255 |
| content_base64 | string | 是 | 图片 base64，≤20MB（上限约 30M 字符） |
| name | string | 否 | 衣物名称（默认用文件名） |
| item_type | string | 否 | 品类（top/pants/shoes/…）；为空则自动识别或前端必填 |
| subtype | string | 否 | 子类（如 shirt/jeans） |
| color | string | 否 | 颜色 |
| gender | string | 否 | 目标人群（默认 women） |
| size | string | 否 | 尺码 |

> 说明：`item_type` 建议前端必填（下拉选择），避免自动识别误判；后端对 `item_type` 做校验（非法值返回 422）。

响应 `201`：

```json
{
  "item_id": "personal:7f3a...",
  "item": {
    "item_id": "personal:7f3a...",
    "source": "personal-7f3a...",
    "gender": "women",
    "item_type": "top",
    "name": "我的白色衬衫",
    "color": "white",
    "image_status": "available",
    "embedding_status": "ready",
    "image_url": "/items/personal:7f3a.../image"
  },
  "embedding": {
    "status": "completed",
    "embedding_kind": "image"
  }
}
```

错误：

| 状态码 | 场景 |
|---|---|
| 413 | 图片超过 20MB |
| 422 | base64 非法 / item_type 非法 / 缺必要字段 |
| 500 | 嵌入生成失败（衣物已创建，可稍后补嵌） |

### 3.3 修改衣物信息

**需新增** · `PUT /wardrobes/{user_id}/items/{item_id}`

更新某件衣物的基本信息。仅更新提供的字段；若 `name`/`item_type`/`color` 变化会触发**文字嵌入重新生成**。

请求体（全部可选，至少一个）：

```json
{
  "name": "白色真丝衬衫",
  "item_type": "top",
  "subtype": "shirt",
  "color": "white",
  "gender": "women",
  "size": "M"
}
```

响应 `200`：同 3.2 的 `{item_id, item, embedding}`（embedding 为重新嵌入结果或 `{"status":"unchanged"}`）。

错误：

| 状态码 | 场景 |
|---|---|
| 404 | 商品不存在或不在该用户衣柜 |
| 422 | 非法 item_type / 空请求体 |

### 3.4 删除衣物

**已存在** · `DELETE /wardrobes/{user_id}/items/{item_id}`

软删除（`active=0`），不物理删除数据。

响应 `200`：

```json
{ "user_id": "demo-user", "item_id": "139692178_2", "active": false }
```

错误：`404`（商品不在衣柜）。

### 3.5 订单导入（预览 / 提交）

**已存在**，小程序复用 PC 端流程：

| 接口 | 说明 |
|---|---|
| `POST /wardrobes/{user_id}/imports` | 上传订单 Excel（`filename` + `content_base64` + `default_audience`），返回脱敏预览 `{batch, count, rows[]}` |
| `GET /wardrobes/{user_id}/imports/{batch_id}` | 获取某批次预览 |
| `POST /wardrobes/{user_id}/imports/{batch_id}/commit` | 提交选中行加入衣柜（`selections[]` + `auto_embed`），自动生成文字嵌入 |

响应 `rows[]` 关键字段（前端预览用）：

| 字段 | 说明 |
|---|---|
| row_id | 行 ID（提交时用） |
| order_status | 订单状态 |
| order_eligibility | `eligible`/`ineligible`/`unknown`（收货门槛） |
| product_name | 商品名 |
| predicted_item_type / subtype / color | 自动识别结果 |
| confidence | 置信度 |
| decision | `candidate`/`excluded`/`committed`/`rejected` |
| catalog_item_id | 已提交后的商品 ID（提交后回显） |

### 3.6 给已有衣物上传实拍图

**已存在** · `POST /wardrobes/{user_id}/items/{item_id}/image`

给订单导入的衣物补实拍图；图片保存后嵌入自动切换为图像嵌入。

请求体：`{filename, content_base64}`（≤20MB）。

响应 `200`：

```json
{
  "item_id": "personal:...",
  "image_status": "available",
  "embedding": { "status": "completed", "embedding_kind": "image" }
}
```

### 3.7 商品详情与图片

**已存在**：

| 接口 | 说明 |
|---|---|
| `GET /items/{item_id}` | 商品详情（同 Item 结构） |
| `GET /items/{item_id}/image` | 主图（返回图片字节流） |
| `GET /items/{item_id}/images` | 多图列表（Mytheresa 等一衣多图） |

## 4. 错误码约定

| 状态码 | 含义 | 前端处理建议 |
|---|---|---|
| 400 | 请求体解析失败 | 提示"请求格式错误" |
| 404 | 资源不存在 | 提示并刷新列表 |
| 409 | 状态冲突（如图片不可用） | 提示具体原因 |
| 413 | 上传过大 | 提示压缩图片 |
| 422 | 校验失败 | 展示 detail 提示 |
| 500 | 服务端错误 | 提示稍后重试 |

## 5. 前端实现建议

- **衣柜页**：`GET /wardrobes/{user_id}` → 按 `item_type` 分组（`Map<item_type, Item[]>`）渲染九宫格；无图商品显示占位"暂无实拍图"。
- **拍照/上传**：`wx.chooseMedia({sourceType: ['camera','album']})` → `wx.compressImage` → `FileReader` 转 base64 → `POST /items/photo`（个人新增）或 `POST /items/{id}/image`（补图）。
- **修改/删除**：衣物卡片长按弹操作菜单 → `PUT /items/{id}` / `DELETE /items/{id}`。
- **订单导入**：`wx.chooseMessageFile` 选 Excel → base64 → `POST /imports` → 列表多选 → `POST /imports/{batch}/commit`。
- **图片加载**：`image_url` 拼接 Base URL；`image_status != available` 时显示占位。
- **新增后端接口依赖**：3.2（photo 创建）、3.3（PUT 修改）需后端实现后联调。
