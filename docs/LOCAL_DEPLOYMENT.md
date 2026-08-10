# 本地部署

## 1. 适用范围

本页首先描述“复用当前机器已有数据库、模型、向量和索引”的启动流程。干净环境从零重建仍缺少统一回归，详见[复现边界](REPRODUCIBILITY.md)。

## 2. 环境

- 操作系统：Windows。
- 项目目录：`C:\Users\32369\Desktop\agent-p\style`。
- Python：`D:\anaconda\envs\style\python.exe`。
- PyTorch：2.10.0+cu128。
- TorchVision：0.25.0+cu128。
- CUDA 可用：是。

普通 Python 包使用清华镜像；PyTorch CUDA wheel 使用阿里云 PyTorch wheel 镜像。

后续命令统一使用完整解释器路径，不依赖 PowerShell 是否已激活 Conda 环境。

从仓库根目录运行任何`python -m styleforge...`命令前，当前PowerShell窗口必须设置包目录：

```powershell
cd C:\Users\32369\Desktop\agent-p\style
$env:PYTHONPATH=(Resolve-Path ".\apps\api")
```

Pytest会通过`pyproject.toml`自动加入该路径，但普通Python、Uvicorn和数据管线进程不会自动读取Pytest配置。

## 3. 必要环境变量

每个新 PowerShell 窗口都需要设置图片目录：

```powershell
$env:GARMENTS2LOOK_IMAGE_ROOT="E:\image.tar\image\images"
```

Streamlit 窗口设置 API 地址：

```powershell
$env:STYLEFORGE_API_URL="http://127.0.0.1:8000"
```

天气上下文默认启用 Open-Meteo。建议配置用户默认城市；请求显式地点优先：

```powershell
$env:STYLEFORGE_WEATHER_ENABLED="true"
$env:STYLEFORGE_WEATHER_PROVIDER="open-meteo"
$env:STYLEFORGE_DEFAULT_LOCATION="上海"
$env:STYLEFORGE_WEATHER_TIMEOUT="10"
```

如果系统开了 HTTP 代理，本地 API 地址应绕过代理：

```powershell
$env:NO_PROXY="127.0.0.1,localhost,::1"
```

## 4. 演示衣柜（最新实现尚待回归）

重建约 204 件的混合风格演示衣柜：

```powershell
D:\anaconda\envs\style\python.exe -m styleforge.pipelines.seed_balanced_wardrobe `
  --user-id demo-user `
  --profile mixed-large `
  --replace
```

`--replace` 只删除并重建 `demo-user` 的衣柜映射，不删除商品、图片、向量或索引。

## 5. 启动 API

窗口 1：

```powershell
cd C:\Users\32369\Desktop\agent-p\style
$env:GARMENTS2LOOK_IMAGE_ROOT="E:\image.tar\image\images"
D:\anaconda\envs\style\python.exe -m uvicorn styleforge.api:app `
  --app-dir apps\api `
  --host 127.0.0.1 `
  --port 8000
```

验证：

- 健康检查：`http://127.0.0.1:8000/health`
- API 文档：`http://127.0.0.1:8000/docs`

访问 API 根路径 `/` 返回 404 是正常行为。

### `/health` 完整模式验收

HTTP 200 之外，还应检查：

- `catalog_items == 126928`
- `embedding_ready_items == 126928`
- `embedding_manifest_available == true`
- `index_manifest_available == true`
- `image_root_configured == true`
- `weather.enabled == true`
- `weather.provider == "open-meteo"`

索引缺失不会阻断小衣柜 NumPy 检索，但意味着全目录检索评估产物不完整；嵌入或图片根目录缺失会导致视觉降级或图片不可用。

## 6. 启动 Streamlit

窗口 2：

```powershell
cd C:\Users\32369\Desktop\agent-p\style
$env:PYTHONPATH=(Resolve-Path ".\apps\api")
$env:STYLEFORGE_API_URL="http://127.0.0.1:8000"
$env:GARMENTS2LOOK_IMAGE_ROOT="E:\image.tar\image\images"
D:\anaconda\envs\style\python.exe -m streamlit run apps\api\styleforge\ui.py
```

首次启动如出现 `Email:`，直接留空并按 Enter。随后访问 `http://localhost:8501`。

## 7. CLI 验证

```powershell
$env:PYTHONPATH=(Resolve-Path ".\apps\api")
D:\anaconda\envs\style\python.exe -m styleforge.workflow.graph `
  --user-id demo-user `
  --request "明天参加互联网公司面试，衬衫配半身裙和乐福鞋，不要红色，不要高跟鞋。"
```

## 8. 常见问题

### `ModuleNotFoundError: No module named 'styleforge'`

后端包位于`apps\api\styleforge`，仓库根目录本身不是包根目录。从仓库根目录启动 Uvicorn 时必须带`--app-dir apps\api`；运行`python -m styleforge...`形式的CLI前必须把`apps\api`写入`PYTHONPATH`。这不是重新安装依赖能够解决的问题。

### localhost 拒绝连接

- Streamlit 可能仍停留在首次邮箱询问。
- 两个服务必须在不同窗口中保持运行。
- 确认 URL 使用 `8501`，API 使用 `8000`。

### API 根路径返回 404

这是正常的。使用 `/health` 或 `/docs`。

### 页面提示 `'str' object has no attribute 'get'`

旧版 UI 把文字建议当成结构化结果。当前 API 通过 `structured_result` 提供稳定的商品 ID 和评分结构。修改代码后必须重启 API 和 Streamlit。

### 推荐没有图片

- 检查 API 窗口是否设置 `GARMENTS2LOOK_IMAGE_ROOT`。
- 打开 `/items/<item_id>/image` 验证单图。
- 检查数据库中的 `relative_image_path` 是否仍相对于 `images` 根目录。

### 第一次推荐较慢

第一次请求需要把约 605 MB FashionCLIP 权重加载到 GPU。后续请求复用模型实例。

### 指定子类后返回无解

子类约束目前基于商品名称、描述和特征。若衣柜中没有明确命中关键词的商品，系统会返回无解，而不会猜测通过。
