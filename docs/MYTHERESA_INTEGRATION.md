# Mytheresa 数据接入说明

> 更新日期：2026-08-03  
> 状态：本地元数据和 328,754 个图片引用已完成全量只读存在性审计；接入代码已编写，尚未执行最新 Pytest、Ruff、全量商品导入、图片像素解码和合并嵌入。

## 1. 数据位置与边界

- 元数据：`E:\style-dataset\mytheresa_image_v1.0_2512.json`
- 图片根目录：`E:\style-dataset\images\images.tar\images\images`
- 图片布局：`<image_root>/<item_id>/<filename>`
- 原始图片不会移动或复制进项目目录。

这里的 `images.tar` 已经是解包后的目录名，不是本流程需要再次解压的压缩文件。用户已为本项目明确选择安装了 CUDA、FashionCLIP、API 和测试依赖的 `D:\anaconda\envs\style\python.exe`；项目根目录 [AGENTS.md](../AGENTS.md) 将其声明为项目级权威环境，覆盖其他工作区的通用 Python 默认值。执行导入前应至少预留 3GB 项目盘空间，用于增长后的 SQLite、合并嵌入、FAISS 索引、报告和临时状态；170GB 原图仍留在 E 盘。

运行账户必须能只读访问上述 E 盘 JSON/图片目录，并能写入项目的 `data` 与 `artifacts`。元数据读取器要求 UTF-8、顶层 JSON object、以商品 ID 为 key；当前文件尚未记录独立 SHA-256，发布复现实验前需要补充。元数据审计/导入依赖标准库即可；API 和全量嵌入还分别需要项目的 API、视觉和 CUDA 依赖，具体环境见 [本地部署](LOCAL_DEPLOYMENT.md)。

本文所有`python -m styleforge...`命令都从仓库根目录执行。每个新PowerShell窗口先设置后端包目录：

```powershell
cd C:\Users\32369\Desktop\agent-p\style
$env:PYTHONPATH=(Resolve-Path ".\apps\api")
```

项目只在 SQLite 中保存商品元数据、相对路径和数据源对应的外部根目录。

导入、审计和嵌入代码会拒绝把数据库、报告或嵌入输出目录设置到外部图片根目录之下。程序对原图只使用存在性检查或只读图片打开操作。如果要求操作系统级绝对保证，还应使用只有读取权限的专用 Windows 账户运行；不要仅依赖文件夹“只读”属性，因为该属性不能可靠地禁止程序写入。

## 2. 已完成的只读审计

| 指标 | 结果 |
|---|---:|
| 商品数 | 62,457 |
| 图片引用数 | 328,754 |
| 平均图片数 | 5.26 张/商品 |
| 原始细分类 | 385 |
| 有颜色字段 | 62,457 |
| 有视觉描述 | 62,457 |
| 有特征字段 | 62,456 |
| 有官方描述 | 44,857 |
| 与现有 Polyvore 商品 ID 重叠 | 0 |

受众分布：

| 受众 | 商品数 |
|---|---:|
| women | 44,125 |
| men | 11,029 |
| girls | 3,761 |
| boys | 2,327 |
| baby | 1,176 |
| life | 39 |

已对全量 328,754 个 JSON 图片引用执行路径存在性检查，存在 328,754、缺失 0；类别映射后的`unmapped_item_count = 0`。该结果不能证明所有图片都能成功解码，像素完整性仍需单独使用图片审计流程验证。

## 3. 正确的数据分工

- Polyvore outfit 数据继续提供搭配关系、兼容性先验和搭配评估。
- Mytheresa 提供多受众商品目录、精细类别、描述、材质和多视角图片。
- 用户衣柜仍然是在线推荐的强制白名单。
- 当前本地没有 Mytheresa 搭配关系文件，因此不得把商品共现或目录顺序解释成真实搭配监督信号。

## 4. 本次接入代码

已编写但尚未运行验证：

- `TaskSpec.target_audiences` 与中英文受众解析。
- 候选生成前的受众硬过滤。
- 性别无关的 FashionCLIP 提示词；指定受众时动态生成 `women's`、`men's`、`girls'` 等前缀。
- Mytheresa 独立路径解析器和类别规范化器。
- `dataset_sources` 表：记录不同数据源的外部图片根目录。
- `catalog_item_images` 表：保存商品主图、细节图和模特图。
- Mytheresa 流式批量导入器和跨数据源 ID 冲突保护。
- API 多数据源图片读取，以及单品多图片列表/访问接口。
- 多数据源嵌入构建：每条商品记录根据自身数据源解析图片根目录。
- 演示衣柜在多受众目录下强制要求指定 `--audience`。

## 5. 手动验证顺序

在项目目录执行，前一步失败时不要继续下一步。

### 5.1 静态和单元测试

```powershell
D:\anaconda\envs\style\python.exe -m compileall -q apps\api\styleforge tests
D:\anaconda\envs\style\python.exe -m pytest -q
D:\anaconda\envs\style\python.exe -m ruff check apps\api\styleforge tests evals
```

这些命令不会读取或修改 170GB 图片数据。

### 5.2 类别映射预检

先运行不检查图片文件的元数据预检：

```powershell
D:\anaconda\envs\style\python.exe -m styleforge.pipelines.audit_mytheresa `
  --metadata "E:\style-dataset\mytheresa_image_v1.0_2512.json"
```

查看 `artifacts/data_audit/mytheresa_audit.json`：

- `total_items` 必须为 `62457`。
- 对当前固定数据版本，`unmapped_item_count`必须保持为 0；非零即视为相对现有审计结果的回退。
- 数据版本变化时，先人工检查`unmapped_raw_types`，补齐映射并记录变更，不能用宽松比例直接放行。
- `unique_image_references` 是正式导入后 `stored_image_records` 的精确预期值。

当前报告已经通过`--image-root`检查全部 328,754 个图片引用。需要复现时使用同一参数重跑；该模式只调用文件存在性检查，不解码图片，即使缺失数为零，也不能排除图片截断或内容损坏。

### 5.2.1 全量像素解码门槛（当前阻塞）

现有`styleforge.pipelines.audit_images`使用 Garments2Look 的单图路径解析器，不适配 Mytheresa 的`<item_id>/<多图片文件>`结构，不能直接用于这 328,754 张图片。当前项目尚无可执行的 Mytheresa 全量像素解码 CLI。

在正式主库导入前，需要先实现一个复用`catalog_item_images`/Mytheresa 路径解析器的只读审计入口，并满足：

- `expected_images = 328754`
- `missing_images = 0`
- `decode_errors = 0`
- `all_images_pass = true`

该工具只能用 Pillow 只读打开并`load()`图片，报告写入项目`artifacts/data_audit`，不得修改或移动 E 盘原图。此门槛完成前，Mytheresa 正式主库导入保持阻塞。

实现该 CLI 后必须重新执行`compileall → Pytest → Ruff`，三项通过后才能启动全量解码；不能沿用实现工具之前的测试结果。

### 5.3 导入前备份 SQLite

先停止 API、Streamlit 和其他可能写入 SQLite 的进程。项目使用 WAL 模式，运行中的数据库不能只复制一个 `.db` 文件作为一致快照。确认所有进程退出后，创建带时间戳的备份目录，并同时保存可能存在的 WAL/SHM 文件：

```powershell
$backupStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupDir = "C:\Users\32369\Desktop\agent-p\style\data\backup-$backupStamp"
New-Item -ItemType Directory -Path $backupDir
Get-ChildItem -LiteralPath "C:\Users\32369\Desktop\agent-p\style\data" `
  -Filter "styleforge.db*" | Copy-Item -Destination $backupDir
```

备份目录中至少应出现 `styleforge.db`。恢复时也必须先停止所有数据库进程，再用该备份恢复对应文件。

安全恢复采用“隔离当前文件，再复制备份”的方式，不能把备份 `.db` 直接覆盖到仍残留旧 WAL/SHM 的目录：

```powershell
$restoreSource = "C:\Users\32369\Desktop\agent-p\style\data\backup-YYYYMMDD-HHMMSS"
$restoreStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$quarantineDir = "C:\Users\32369\Desktop\agent-p\style\data\failed-import-$restoreStamp"
New-Item -ItemType Directory -Path $quarantineDir
Get-ChildItem -LiteralPath "C:\Users\32369\Desktop\agent-p\style\data" `
  -Filter "styleforge.db*" | Move-Item -Destination $quarantineDir
Get-ChildItem -LiteralPath $restoreSource `
  -Filter "styleforge.db*" | Copy-Item -Destination "C:\Users\32369\Desktop\agent-p\style\data"
```

执行前必须把 `$restoreSource` 替换为已核对的具体备份目录。原失败数据库被移动到隔离目录，可人工恢复，不会被直接删除。

### 5.4 在一次性数据库上演练中断与重跑

在修改主数据库前，必须先通过故障注入演练。使用全新的数据库文件，不要把 `--simulate-failure-after` 指向主数据库：

```powershell
$drillStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$drillDb = "C:\Users\32369\Desktop\agent-p\style\data\mytheresa-drill-$drillStamp.db"

D:\anaconda\envs\style\python.exe -m styleforge.pipelines.import_mytheresa `
  --metadata "E:\style-dataset\mytheresa_image_v1.0_2512.json" `
  --image-root "E:\style-dataset\images\images.tar\images\images" `
  --database $drillDb --batch-size 500 --simulate-failure-after 500 `
  --report "C:\Users\32369\Desktop\agent-p\style\artifacts\imports\mytheresa-drill-failed.json"
```

第一次命令必须以 `Simulated import failure` 失败，并在数据库中保留 500 件已提交商品。随后不带故障参数连续执行两次正式导入到同一个 `$drillDb`：

```powershell
D:\anaconda\envs\style\python.exe -m styleforge.pipelines.import_mytheresa `
  --metadata "E:\style-dataset\mytheresa_image_v1.0_2512.json" `
  --image-root "E:\style-dataset\images\images.tar\images\images" `
  --database $drillDb `
  --report "C:\Users\32369\Desktop\agent-p\style\artifacts\imports\mytheresa-drill-replay-1.json"

D:\anaconda\envs\style\python.exe -m styleforge.pipelines.import_mytheresa `
  --metadata "E:\style-dataset\mytheresa_image_v1.0_2512.json" `
  --image-root "E:\style-dataset\images\images.tar\images\images" `
  --database $drillDb `
  --report "C:\Users\32369\Desktop\agent-p\style\artifacts\imports\mytheresa-drill-replay-2.json"
```

两次重跑的 `mytheresa_catalog_items`、`stored_image_records` 和数据库分类计数必须一致，且失败运行在 `dataset_import_runs` 中保持 `failed`。

随后在这个一次性数据库上实际演练备份恢复。先备份已收敛的 drill 数据库文件：

```powershell
$drillName = [System.IO.Path]::GetFileName($drillDb)
$drillBackupDir = "C:\Users\32369\Desktop\agent-p\style\data\drill-backup-$drillStamp"
New-Item -ItemType Directory -Path $drillBackupDir
Get-ChildItem -LiteralPath "C:\Users\32369\Desktop\agent-p\style\data" `
  -Filter "$drillName*" | Copy-Item -Destination $drillBackupDir
```

再运行一次故障注入命令制造备份后的数据库变更。该命令必须失败：

```powershell
D:\anaconda\envs\style\python.exe -m styleforge.pipelines.import_mytheresa `
  --metadata "E:\style-dataset\mytheresa_image_v1.0_2512.json" `
  --image-root "E:\style-dataset\images\images.tar\images\images" `
  --database $drillDb --batch-size 500 --simulate-failure-after 500 `
  --report "C:\Users\32369\Desktop\agent-p\style\artifacts\imports\mytheresa-drill-after-backup.json"
```

所有进程退出后，隔离发生变更的 drill 文件，再恢复备份：

```powershell
$drillQuarantine = "C:\Users\32369\Desktop\agent-p\style\data\drill-changed-$drillStamp"
New-Item -ItemType Directory -Path $drillQuarantine
Get-ChildItem -LiteralPath "C:\Users\32369\Desktop\agent-p\style\data" `
  -Filter "$drillName*" | Move-Item -Destination $drillQuarantine
Get-ChildItem -LiteralPath $drillBackupDir `
  -Filter "$drillName*" | Copy-Item -Destination "C:\Users\32369\Desktop\agent-p\style\data"
```

最后比较备份与恢复文件的 SHA-256：

```powershell
$expectedHashes = Get-ChildItem -LiteralPath $drillBackupDir -Filter "$drillName*" | `
  Get-FileHash | Select-Object @{Name="Name";Expression={Split-Path $_.Path -Leaf}},Hash
$actualHashes = Get-ChildItem -LiteralPath "C:\Users\32369\Desktop\agent-p\style\data" `
  -Filter "$drillName*" | Get-FileHash | `
  Select-Object @{Name="Name";Expression={Split-Path $_.Path -Leaf}},Hash
Compare-Object $expectedHashes $actualHashes -Property Name,Hash
```

`Compare-Object` 必须没有输出。然后再次运行一次不带故障参数的导入，计数仍必须与前两次成功重跑一致。演练数据库、备份和隔离目录都保留在项目`data`目录供复核，不自动删除。

此时`$drillDb`应已完整包含 62,457 件 Mytheresa 商品。在修改主数据库前，先让 API 使用该一次性数据库完成多受众和多图片验收；women、men、girls、boys、baby、life 都要至少查询一个样本。只有像素解码、中断恢复、重复执行、恢复后重跑、一次性库全量计数和 API 验收全部通过，才能进入主库导入。

### 5.4.1 一次性数据库 API 验收

在新的 PowerShell 窗口中，把`$drillDb`设为前述演练数据库的实际绝对路径，然后启动 API：

```powershell
$drillDb = "C:\Users\32369\Desktop\agent-p\style\data\mytheresa-drill-实际时间戳.db"
$env:STYLEFORGE_DATABASE_PATH = $drillDb
$env:GARMENTS2LOOK_IMAGE_ROOT = "E:\image.tar\image\images"
D:\anaconda\envs\style\python.exe -m uvicorn styleforge.api:app `
  --app-dir apps\api `
  --host 127.0.0.1 --port 8000
```

在另一个窗口查询全部六种受众：

```powershell
$audiences = @("women", "men", "girls", "boys", "baby", "life")
foreach ($audience in $audiences) {
  $result = Invoke-RestMethod `
    "http://127.0.0.1:8000/catalog/search?source=mytheresa&audience=$audience&limit=1"
  if ($result.items.Count -lt 1) { throw "No sample for audience: $audience" }
  if ($result.items[0].gender -ne $audience) { throw "Audience mismatch: $audience" }
}
```

同时验证`/health`中的`catalog_items_by_source.mytheresa = 62457`，以及至少一个已确认样本的主图和多图片接口。这里验收的是`$drillDb`，不能把环境变量指向主库。

### 5.5 通过一次性库验收后导入主数据库

停止一次性库 API 和所有可能写主 SQLite 的进程。紧邻主库导入前再次建立新备份，不复用 5.3 的旧备份目录：

```powershell
$finalBackupStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$finalBackupDir = "C:\Users\32369\Desktop\agent-p\style\data\backup-before-mytheresa-$finalBackupStamp"
New-Item -ItemType Directory -Path $finalBackupDir
Get-ChildItem -LiteralPath "C:\Users\32369\Desktop\agent-p\style\data" `
  -Filter "styleforge.db*" | Copy-Item -Destination $finalBackupDir
```

确认备份目录至少含`styleforge.db`后，再执行正式导入：

```powershell
D:\anaconda\envs\style\python.exe -m styleforge.pipelines.import_mytheresa `
  --metadata "E:\style-dataset\mytheresa_image_v1.0_2512.json" `
  --image-root "E:\style-dataset\images\images.tar\images\images" `
  --database "C:\Users\32369\Desktop\agent-p\style\data\styleforge.db"
```

该步骤只读取外部 JSON/图片文件状态，并写入项目内 SQLite 和导入报告；不会移动或改写图片。

导入器启动时会调用`initialize_database`，把当前数据库迁移到`SCHEMA_VERSION = 5`。Schema v3 引入的`dataset_sources`和`catalog_item_images`仍保留；Schema v4/v5 又增加个人订单衣柜、订单准入状态和个人嵌入表。导入报告必须包含`database_schema_version == 5`。如果数据库 schema 高于当前代码支持版本，初始化会在导入前拒绝执行，避免旧代码覆盖未来数据库。

导入按批次提交，不是覆盖全部 62,457 件商品的单一事务。若中途失败，已完成批次会保留，`dataset_import_runs` 会记录 `failed`；修复原因后重复执行同一命令即可通过 upsert/图片替换继续收敛。不要通过删除主数据库处理普通导入失败。

成功后应重点检查：

- `processed_count == 62457`
- `mytheresa_catalog_items == 62457`
- 导入前基线为 126,928 时，总商品数必须为 `189385`
- `stored_image_records` 必须等于预检报告中的 `unique_image_references`

### 5.6 启动 API 并检查多数据源图片

Polyvore 尚未重新注册到 `dataset_sources` 时，继续保留兼容环境变量：

```powershell
$env:GARMENTS2LOOK_IMAGE_ROOT="E:\image.tar\image\images"
$env:STYLEFORGE_DATABASE_PATH="C:\Users\32369\Desktop\agent-p\style\data\styleforge.db"
D:\anaconda\envs\style\python.exe -m uvicorn styleforge.api:app `
  --app-dir apps\api `
  --host 127.0.0.1 --port 8000
```

检查；本节针对正式主库，一次性数据库应已在 5.4.1 完成同类验收：

- `http://127.0.0.1:8000/health`
- 对 women、men、girls、boys、baby、life 重复 5.4.1 的查询矩阵
- `http://127.0.0.1:8000/items/P01073381/image`
- `http://127.0.0.1:8000/items/P01073381/images`

验收要求：`/health` 返回 HTTP 200，且 `catalog_items_by_source.mytheresa == 62457`；搜索结果中的 `source` 为 `mytheresa`、`gender` 为 `men`；主图接口返回 HTTP 200 和 JPEG；多图接口 `count >= 1`。`P01073381` 是本次只读抽样中确认存在目录和图片的样本商品 ID。

## 6. 嵌入策略

第一阶段构建“跨数据源主图合并索引”：每件商品只使用一张主图，Polyvore 与 Mytheresa 商品进入同一向量/索引产物。该能力不同于“单商品多视角向量聚合”；后者尚未实现。多视角图片先保存元数据并通过 API 展示，不立即生成 328,754 个独立向量。

为了保留当前 Polyvore 嵌入，建议把多数据源产物写入全新的目录。这里会从两套商品主图重新构建一套合并产物，不会在运行时同时加载两个独立索引，也不会复用旧 Polyvore 向量文件：

```powershell
$env:GARMENTS2LOOK_IMAGE_ROOT="E:\image.tar\image\images"
$env:STYLEFORGE_EMBEDDING_DIR="C:\Users\32369\Desktop\agent-p\style\artifacts\embeddings\fashionclip-multisource"
$env:STYLEFORGE_INDEX_DIR="C:\Users\32369\Desktop\agent-p\style\artifacts\index\fashionclip-multisource"

D:\anaconda\envs\style\python.exe -m styleforge.pipelines.build_embeddings `
  --output-dir $env:STYLEFORGE_EMBEDDING_DIR `
  --model-dir "C:\Users\32369\Desktop\agent-p\style\artifacts\models" `
  --device cuda --precision float16 --batch-size 256 --workers 8

D:\anaconda\envs\style\python.exe -m styleforge.pipelines.build_faiss_index `
  --embedding-dir $env:STYLEFORGE_EMBEDDING_DIR `
  --output-dir $env:STYLEFORGE_INDEX_DIR
```

新 API 进程也必须设置相同的 `STYLEFORGE_EMBEDDING_DIR` 和 `STYLEFORGE_INDEX_DIR`，否则仍会加载旧 Polyvore 产物。

如果输出目录中已有同签名的未完成状态，嵌入命令会断点续跑；如果数据签名不同，命令会拒绝继续。不要在未核对目录内容时使用 `--force`，因为它会删除该输出目录中的旧嵌入状态文件。FAISS 输出目录同样建议使用新的空目录。

## 7. 多受众演示衣柜

多受众目录中不允许省略受众后自动混装。示例：

```powershell
D:\anaconda\envs\style\python.exe -m styleforge.pipelines.seed_balanced_wardrobe `
  --user-id demo-men --profile mixed-large --audience men --replace
```

`--replace` 会删除并重建 `demo-men` 的衣柜映射，只应对专用演示用户使用；它不会删除商品、图片、嵌入或索引。

推荐输入也应显式包含受众，例如：

```text
男士互联网公司面试穿搭，需要衬衫、长裤和皮鞋，偏深蓝或灰色，不要红色。
```

这里需要区分两个阶段：演示衣柜初始化面对完整多受众目录，因此必须显式传 `--audience`；推荐请求只在已经建立的用户衣柜白名单内运行，未指定受众时使用中性提示词并允许该衣柜中的全部受众。正式产品应进一步把默认受众存入用户档案。

## 8. 尚未完成

- 尚未运行新测试套件和 Ruff。
- 尚未执行 62,457 件商品的正式导入。
- 已完成全量图片路径存在性检查，但尚未执行像素解码和损坏检测。
- 尚未生成合并后的 189,385 件商品嵌入和 FAISS 索引。
- 尚未实现多视角向量聚合。
- 尚未建立 Mytheresa 人工抽样类别映射准确率指标。
- 尚未实际运行批次导入失败、重复执行和备份恢复演练；正式主库导入在演练通过前处于阻塞状态。

若 E 盘盘符或目录发生变化，不要修改数据库商品路径或移动图片；使用新的 `--image-root` 重跑 Mytheresa 导入即可更新 `dataset_sources` 和图片状态。Polyvore 后续也应正式注册到 `dataset_sources`，在此之前继续使用 `GARMENTS2LOOK_IMAGE_ROOT` 兼容路径。
