# 复现边界

## 1. 两条复现路径

### A. 复用当前机器产物

这条路径已经基本验证，前提是以下文件存在：

- `data/styleforge.db`
- `artifacts/models/` 完整 FashionCLIP 快照
- `artifacts/embeddings/fashionclip/`
- `artifacts/index/fashionclip/`
- `E:\image.tar\image\images`

按[本地部署](LOCAL_DEPLOYMENT.md)启动即可。

### B. 干净环境从零重建

这条路径尚未作为一个连续流程完成回归，因此当前文档不能承诺一键复现。发布作品集前必须补齐并实测以下顺序：

1. 创建 Python 3.10 环境。
2. 安装 PyTorch 2.10.0+cu128 与 TorchVision 0.25.0+cu128。
3. 通过清华镜像安装项目其他依赖。
4. 从 Garments2Look 获取商品元数据、搭配 JSON 和 Polyvore 图片。
5. 按 `images/women/<type>/<id>.jpg` 解压图片。
6. 下载固定 revision 的 FashionCLIP 完整本地快照。
7. 运行商品导入、搭配导入和图片审计。
8. 运行全量嵌入和 FAISS 构建。
9. 运行离线评估、seed、测试、API 和 UI 验收。

相关 CLI 的最终参数必须通过各模块`--help`和真实重建验证后再写入；当前不记录未经验证的命令。

## 2. FashionCLIP 本地快照

目录至少包含：

```text
artifacts/models/
├── config.json
├── merges.txt
├── model.safetensors
├── preprocessor_config.json
├── special_tokens_map.json
├── tokenizer.json
├── tokenizer_config.json
└── vocab.json
```

模型：`patrickjohncyh/fashion-clip`  
固定 revision：`7e3ba62ce16b379a1ab479346b66f192e76f51b7`

## 3. 数据来源与目录

- Garments2Look 镜像：`https://hf-mirror.com/datasets/ArtmeScienceLab/Garments2Look`
- 商品 JSON：`polyvore_image_v1.0_2512.json`
- 搭配 JSON：`polyvore_outfit_v1.0_2512.json`
- 图片根目录结构：`images/women/<type>/<id>.jpg`

数据集与模型的许可应以各自上游仓库当前说明为准。公开仓库只提供来源和重建说明，不重新分发原始数据、图片或权重。

## 4. 环境变量

| 变量 | 含义 |
|---|---|
| `GARMENTS2LOOK_METADATA_PATH` | 商品元数据 JSON |
| `GARMENTS2LOOK_OUTFIT_PATH` | 搭配 JSON |
| `GARMENTS2LOOK_IMAGE_ROOT` | `images` 根目录 |
| `STYLEFORGE_DATABASE_PATH` | SQLite 文件 |
| `STYLEFORGE_ARTIFACT_ROOT` | 模型、向量、索引和报告根目录 |
| `STYLEFORGE_API_URL` | Streamlit 调用的 API 地址 |

## 5. 发布前复现验收

- 在新的 Python 环境中执行完整流程。
- 保存所有命令、版本、耗时和退出码。
- 提交不含原始数据的轻量 manifest 与评估摘要。
- 验证 `/health` 的五个完整模式字段。
- 使用三个固定请求完成 CLI 与 UI 截图。
- 记录硬件差异下的 CPU 降级行为。
