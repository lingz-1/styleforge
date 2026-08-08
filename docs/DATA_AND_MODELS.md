# 数据与模型

## 1. 本地数据源

| 数据 | 本地位置 | 用途 |
|---|---|---|
| 商品元数据 | `polyvore_image_v1.0_2512.json` | 商品名称、颜色、品类、描述和图片路径 |
| 搭配数据 | `polyvore_outfit_v1.0_2512.json` | 严格搭配子集和离线兼容性评估 |
| 商品图片 | `E:\image.tar\image\images\women\<type>\<id>.jpg` | FashionCLIP 图片编码和 UI 展示 |
| SQLite | `data/styleforge.db` | 商品、搭配、衣柜和运行记录 |

外部图片目录没有复制进项目仓库。数据库保存相对路径，通过 `GARMENTS2LOOK_IMAGE_ROOT` 在运行时绑定。

## 2. 图片审计

- 预期商品图：126,928。
- 实际成功解码：126,928。
- 缺失：0。
- 解码错误：0。
- 格式：全部 JPEG。
- RGB：126,412；灰度图：516。
- 最短边低于 128 像素：1,377。
- 图片总大小：3,448,576,099 字节。

审计报告：`artifacts/data_audit/polyvore_image_audit.json`。

## 3. 搭配数据严格子集

原始搭配 JSON 包含 18,086 套搭配和 93,305 条关系，但存在 8,596 个无法在商品元数据中解析的 ID。

项目默认使用“所有引用商品都存在”的严格子集：

- 完整搭配：9,794。
- 搭配关系：46,413。
- Train：9,556。
- Test：238。
- 因引用不完整跳过：8,292。

这一策略保证外键和图片可追溯，但会引入样本选择偏差，评估报告必须注明。

## 4. FashionCLIP

- Hugging Face 模型：`patrickjohncyh/fashion-clip`。
- 固定 revision：`7e3ba62ce16b379a1ab479346b66f192e76f51b7`。
- 权重文件：`artifacts/models/model.safetensors`。
- 权重 SHA-256：`4977e3a54929eccf065ce449aeaf296f0e5cb6b28e8798c3c97d67cb2f6dafc9`。
- 图片/文本向量维度：512。
- 推理设备：NVIDIA GeForce RTX 5060 Ti。
- 推理精度：CUDA FP16；落盘向量为 Float32。
- 输出：L2 归一化。

FashionCLIP 的职责是图片/文本语义对齐和候选排序，不直接生成最终搭配。

## 5. 嵌入产物

- 文件：`artifacts/embeddings/fashionclip/embeddings.npy`。
- 形状：`(126928, 512)`。
- 大小：约 260 MB。
- ID 映射：`item_ids.json`。
- 状态：`state.json`，每批更新，可断点续跑。
- 清单：`manifest.json`，记录模型、数据和版本指纹。

全量生成参数：

- Batch size：256。
- 图片加载线程：8。
- 总耗时：466.155 秒。
- 平均吞吐：约 272.29 张/秒。

## 6. FAISS

- 类型：`IndexFlatIP`。
- 语义：归一化向量内积等价于余弦相似度。
- 条目：126,928。
- 维度：512。
- 文件：`artifacts/index/fashionclip/fashionclip.index`。
- 大小：约 260 MB。

当前选择精确索引而不是近似索引，因为 12.7 万条、512 维在本地内存中仍可接受，并且便于提供稳定基线。

## 7. 数据许可与发布注意事项

仓库不应提交以下内容：

- 原始数据集 JSON。
- 数据集商品图片。
- FashionCLIP 权重。
- 全量向量和 FAISS 索引。
- SQLite 数据库。

公开作品集时应提供数据集和模型来源、下载说明、固定 revision 与本地重建命令，而不是重新分发未经确认许可的大文件。
