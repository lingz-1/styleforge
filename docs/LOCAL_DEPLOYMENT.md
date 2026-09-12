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

Vue 开发服务器默认把 `/api/*` 代理到 `http://127.0.0.1:8000`。API 使用其他端口时，在启动 Vue 的窗口设置：

```powershell
$env:STYLEFORGE_API_PROXY_TARGET="http://127.0.0.1:18000"
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
E:\PostgreSQL\pgsql\bin\pg_ctl.exe -D E:\PostgreSQL\data -l E:\PostgreSQL\pg.log start
```

- 停止：

```powershell
E:\PostgreSQL\pgsql\bin\pg_ctl.exe -D E:\PostgreSQL\data stop
```

- 建库（首次）：

```powershell
E:\PostgreSQL\pgsql\bin\psql.exe -U postgres -h 127.0.0.1 -c "CREATE ROLE styleforge LOGIN;"
E:\PostgreSQL\pgsql\bin\psql.exe -U postgres -h 127.0.0.1 -c "CREATE DATABASE styleforge OWNER styleforge ENCODING 'UTF8';"
E:\PostgreSQL\pgsql\bin\psql.exe -U postgres -h 127.0.0.1 -c "CREATE DATABASE styleforge_test OWNER styleforge ENCODING 'UTF8';"
```

`STYLEFORGE_DATABASE_DSN` 形如 `postgresql://styleforge@127.0.0.1:5432/styleforge`。API 首次启动会自动建表并执行幂等增量迁移（`initialize_database`，SCHEMA_VERSION=14）。`artifacts/init_pg_schema.py` 只是同一初始化函数的命令行入口，不再维护第二份 SQL。

Schema v14 将批量照片识别任务和逐图状态持久化到 PostgreSQL。API 启动时会恢复 `accepted/recognizing/retrying/embedding` 状态的任务；输入图片仅暂存在项目 `artifacts/recognition_batches/`，删除终态批次时同步清理。`E:\image.tar\image\images` 与 `E:\style-dataset` 始终按只读数据集处理。

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

## 5. 演示衣柜

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
- Prometheus 指标：`http://127.0.0.1:8000/metrics`
- API 文档：`http://127.0.0.1:8000/docs`

访问 API 根路径 `/` 返回 404 是正常行为。

### `/health` 完整模式验收

HTTP 200 之外，还应检查：

- `catalog_items > 0`，且与本次选择的数据集/演示快照一致
- `embedding_ready_items > 0`，且与本次需要启用视觉检索的商品范围一致
- `embedding_manifest_available == true`
- `index_manifest_available == true`
- `image_root_configured == true`
- `weather.enabled == true`
- `weather.provider == "open-meteo"`

索引缺失不会阻断小衣柜 NumPy 检索，但意味着全目录检索评估产物不完整；嵌入或图片根目录缺失会导致视觉降级或图片不可用。

### 可观测性与系统健康面板

- `/health.observability` 是当前 API 进程启动后的聚合快照，包含 HTTP、任务、Agent、LLM、工具、数据库的调用/失败/可重试失败/重试/降级计数，以及累计、平均和最大耗时。
- `/metrics` 输出 Prometheus text exposition 0.0.4，可直接配置为抓取目标；外部 Prometheus 负责跨进程重启持久化和告警。本项目不强制内置 Prometheus 服务。
- Web 启动后访问 `http://127.0.0.1:5173/health` 查看“系统脉搏”面板，默认每 15 秒刷新；统一启动器指定其他端口时使用其输出地址。
- 公共健康数据只含低基数分类，不包含用户输入、工具参数、异常正文、`request_id` 或 `run_id`。

### Prometheus + Grafana 本地监控

Docker Desktop 运行后，从仓库根目录执行：

```powershell
.\scripts\start_monitoring.ps1 -Action Check
.\scripts\start_monitoring.ps1 -Action Start -ApiPort 8000
.\scripts\start_monitoring.ps1 -Action Status
```

如果 StyleForge API 使用了其他端口，`-ApiPort` 必须与之保持一致。例如当前机器的 8000 端口被其他项目占用时：

```powershell
.\scripts\start_styleforge.ps1 -Action Start -ApiPort 18000 -UiPort 15173
.\scripts\start_monitoring.ps1 -Action Start -ApiPort 18000 -GrafanaPort 3300
```

- Prometheus：`http://127.0.0.1:9090`，固定镜像 `prom/prometheus:v3.5.5`，15 秒抓取，时序数据保留 15 天。
- Grafana：`http://127.0.0.1:3300/d/styleforge-system-overview`，固定镜像 `grafana/grafana:13.1.0`，数据源与 9 块面板自动预置。
- Grafana 本地默认登录为 `admin / styleforge-local`。可在启动前设置 `GRAFANA_ADMIN_USER`、`GRAFANA_ADMIN_PASSWORD` 覆盖；服务只绑定 `127.0.0.1`，不对局域网开放。
- 7 条 Prometheus 告警规则覆盖 API 下线、HTTP 高失败率/高平均耗时、数据库失败、连续 LLM 失败、重试风暴和降级执行。当前没有接入 Alertmanager，所以规则会在 Prometheus/Grafana 中进入告警状态，但不会发送邮件或即时消息。
- 指标与 Grafana 数据只写入 `artifacts/monitoring/`；停止不会删除历史数据：

```powershell
.\scripts\start_monitoring.ps1 -Action Stop
```

需要变更 Web 端口时可用 `-PrometheusPort`、`-GrafanaPort`；默认分别为 9090、3300。启动器同时兼容独立 `docker-compose.exe` 和 Docker Compose 插件，并为 file-SD 生成 UTF-8 无 BOM 的动态 API 目标文件。

## 7. 启动 Vue Web

窗口 2：

```powershell
cd C:\Users\32369\Desktop\agent-p\style
Set-Location .\apps\web
npm.cmd install
npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

访问 `http://127.0.0.1:5173`。前端通过 Vite 代理访问 FastAPI；API 不在 8000 端口时，先设置上一节的 `STYLEFORGE_API_PROXY_TARGET`。

`apps/api/styleforge/ui.py` 和启动器的 `-Ui Streamlit` 仅用于维护旧界面，不属于当前功能验收、E2E 或文档主路径。

## 8. API 验证

```powershell
$body = @{
  user_id = "demo-user"
  request = "明天参加互联网公司面试，衬衫配半身裙和乐福鞋，不要红色，不要高跟鞋。"
  max_results = 3
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/tasks/execute" `
  -ContentType "application/json" -Body $body
```

## 9. 常见问题

### `ModuleNotFoundError: No module named 'styleforge'`

后端包位于`apps\api\styleforge`，仓库根目录本身不是包根目录。从仓库根目录启动 Uvicorn 时必须带`--app-dir apps\api`；运行`python -m styleforge...`形式的CLI前必须把`apps\api`写入`PYTHONPATH`。这不是重新安装依赖能够解决的问题。

### localhost 拒绝连接

- API 和 Vue 两个服务必须在不同窗口中保持运行。
- 确认 Vue 使用 `5173`（或启动器输出端口），API 使用 `8000`（或代理目标端口）。
- API 使用非默认端口时，确认 `STYLEFORGE_API_PROXY_TARGET` 与其一致。

### API 根路径返回 404

这是正常的。使用 `/health` 或 `/docs`。

### 页面显示旧结果或接口结构未更新

先确认浏览器访问的是当前 Vue 端口而不是旧 Streamlit 端口；后端代码修改后重启 API，前端开发服务器通常会热更新。生产构建部署时需要重新执行 `npm.cmd run build`。

### 推荐没有图片

- 检查 API 窗口是否设置 `GARMENTS2LOOK_IMAGE_ROOT`。
- 打开 `/items/<item_id>/image` 验证单图。
- 检查数据库中的 `relative_image_path` 是否仍相对于 `images` 根目录。

### 第一次推荐较慢

第一次请求需要把约 605 MB FashionCLIP 权重加载到 GPU。后续请求复用模型实例。

### 指定子类后返回无解

子类约束目前基于商品名称、描述和特征。若衣柜中没有明确命中关键词的商品，系统会返回无解，而不会猜测通过。
