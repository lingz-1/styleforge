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

数据库、Redis 与 Chroma 的凭据和开关放在仓库根目录 `.env`，由 `styleforge.core.config` 在启动时自动加载（无需逐窗口设置）：

| 变量 | 含义 |
|---|---|
| `STYLEFORGE_DATABASE_DSN` | PostgreSQL 主库连接串 |
| `STYLEFORGE_TEST_DATABASE_DSN` | PostgreSQL 测试库连接串（pytest 用） |
| `STYLEFORGE_REDIS_ENABLED` | 会话状态缓存开关（`0`/`1`，默认 `0`） |
| `STYLEFORGE_REDIS_URL` | Redis 连接串（如 `redis://127.0.0.1:6379/0`） |
| `STYLEFORGE_CHROMA_DIR` | Chroma 持久化目录（默认 `artifacts/chroma/`） |

`.env` 中的真实连接串和凭据不提交到 Git。

## 4. 外部依赖服务

### 4.1 PostgreSQL（主库与测试库）

- 安装目录：`E:\PostgreSQL\`（PG 17.10，trust 认证，监听 `127.0.0.1:5432`）。
- 应用角色：`styleforge`；数据库：`styleforge`（主库）、`styleforge_test`（测试库）。
- 启动：

```powershell
E:\PostgreSQL\bin\pg_ctl.exe -D E:\PostgreSQL\data -l E:\PostgreSQL\pg.log start
```

- 停止：

```powershell
E:\PostgreSQL\bin\pg_ctl.exe -D E:\PostgreSQL\data stop
```

- 建库（首次）：

```powershell
E:\PostgreSQL\bin\psql.exe -U postgres -h 127.0.0.1 -c "CREATE ROLE styleforge LOGIN;"
E:\PostgreSQL\bin\psql.exe -U postgres -h 127.0.0.1 -c "CREATE DATABASE styleforge OWNER styleforge ENCODING 'UTF8';"
E:\PostgreSQL\bin\psql.exe -U postgres -h 127.0.0.1 -c "CREATE DATABASE styleforge_test OWNER styleforge ENCODING 'UTF8';"
```

`STYLEFORGE_DATABASE_DSN` 形如 `postgresql://styleforge@127.0.0.1:5432/styleforge`。API 首次启动会自动建表（`initialize_database`，SCHEMA_VERSION=10）。

### 4.2 Redis（会话状态缓存，可选）

- 目录：`E:\Redis\Redis-8.4.0-Windows-x64-msys2-with-Service\`，默认端口 `6379`。
- 启动：运行目录内 `start.bat`，或安装为 Windows 服务后启动服务。
- 启用：`.env` 设置 `STYLEFORGE_REDIS_ENABLED=1` 与 `STYLEFORGE_REDIS_URL=redis://127.0.0.1:6379/0`。
- 作用：同一会话两次请求的穿搭产出走读穿缓存（TTL 默认 24h）。Redis 不可用时自动回落数据库，不影响功能。

### 4.3 Chroma RAG（知识检索，可选）

- 持久化目录默认 `artifacts/chroma/`（`.env` 的 `STYLEFORGE_CHROMA_DIR` 可覆盖）。
- 首次需要构建索引：

```powershell
$env:PYTHONPATH=(Resolve-Path ".\apps\api")
D:\anaconda\envs\style\python.exe -m styleforge.knowledge.indexer
```

- 作用：知识检索在关键词命中之外叠加向量路径（FashionCLIP 512 维文本嵌入，cosine 相似度）。Chroma 未初始化或异常时自动降级纯关键词，不影响功能。

## 5. 演示衣柜（最新实现尚待回归）

重建约 204 件的混合风格演示衣柜：

```powershell
D:\anaconda\envs\style\python.exe -m styleforge.pipelines.seed_balanced_wardrobe `
  --user-id demo-user `
  --profile mixed-large `
  --replace
```

`--replace` 只删除并重建 `demo-user` 的衣柜映射，不删除商品、图片、向量或索引。

## 6. 启动 API

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

## 7. 启动 Streamlit

窗口 2：

```powershell
cd C:\Users\32369\Desktop\agent-p\style
$env:PYTHONPATH=(Resolve-Path ".\apps\api")
$env:STYLEFORGE_API_URL="http://127.0.0.1:8000"
$env:GARMENTS2LOOK_IMAGE_ROOT="E:\image.tar\image\images"
D:\anaconda\envs\style\python.exe -m streamlit run apps\api\styleforge\ui.py
```

首次启动如出现 `Email:`，直接留空并按 Enter。随后访问 `http://localhost:8501`。

## 8. CLI 验证

```powershell
$env:PYTHONPATH=(Resolve-Path ".\apps\api")
D:\anaconda\envs\style\python.exe -m styleforge.workflow.graph `
  --user-id demo-user `
  --request "明天参加互联网公司面试，衬衫配半身裙和乐福鞋，不要红色，不要高跟鞋。"
```

## 9. 常见问题

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
