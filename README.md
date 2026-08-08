# StyleForge

当前准确完成度、验证证据和未完成项见[项目状态](docs/PROJECT_STATUS.md)。Mytheresa 多受众数据已完成 62,457 件商品和 328,754 个图片引用的全量路径存在性审计；接入代码尚未完成主库导入、全量像素解码和合并嵌入。

完整文档入口：[docs/README.md](docs/README.md)。当前完成度、未验证改动和下一验收顺序请先阅读[项目状态](docs/PROJECT_STATUS.md)。

StyleForge 是一个本地优先的个人衣柜多 Agent 穿搭系统。用户输入自然语言需求，系统只从该用户显式衣柜白名单中选择可追溯商品 ID，并输出硬约束检查、评分、选择理由和执行轨迹。演示衣柜来自数据集抽样，不代表真实用户实际拥有这些商品。

购物订单建立个人衣柜的流程见[订单衣柜导入与增量嵌入](docs/ORDER_WARDROBE_IMPORT.md)。订单先经过收货状态白名单；若导出文件含退款、退货或售后列，再执行对应硬过滤，最后由用户预览确认。当前完整订单表没有独立售后列，不能保证识别所有历史售后。有图使用图像嵌入，无图使用文字嵌入；该最新版增量已通过回归与一次性数据库验收，待真实提交和嵌入验收。

## 当前能力

- Garments2Look-Polyvore：126,928 件商品、126,928 张可解码图片。
- Polyvore 搭配：9,794 套引用完整的严格子集。
- FashionCLIP：为全部商品生成 512 维、L2 归一化的图片向量。
- FAISS：`IndexFlatIP` 精确余弦检索，共 126,928 条。
- 检索评估：图片同品类 Precision@10 为 92.72%，文本品类宏平均 Precision@10 为 95.5%。
- LangGraph：Planner → Slot Retriever → Candidate Generator → Stylist → Reviewer → Persistence。
- FastAPI：推荐、目录搜索、衣柜增删、图片访问和健康检查。
- Streamlit：个人衣柜和穿搭推荐演示界面。
- 订单导入：Excel 脱敏预览、收货状态硬门槛、可选售后字段过滤、文件级幂等、人工确认和个人增量嵌入（回归与一次性数据库验收已通过，待真实提交）。
- Mytheresa：62,457 件多受众商品、328,754 个图片引用全部存在，类别映射遗漏为 0；尚未导入主库。
- 语义驱动三 Agent（v3.2.1）：可选接入 DeepSeek，Agent 1 语义检索（request_signature + 多查询加权检索 + Top-50 候选池）、Agent 2 搭配组合、Agent 3 评审判定（五维盲评 + accept/recompose/retrieve_more/wardrobe_gap 四决策分支）。已用真实 DeepSeek API 对 8 类代表性请求完成端到端验收（全部 accept、0 回退、推荐 100% 衣柜归属）；无 API Key 时自动回退确定性链路。

## 职责边界

FashionCLIP 负责将图片和文本映射到同一个向量空间，用于理解及召回衣物。它不直接决定最终搭配。

- Planner Agent：把需求解析成严格的 `TaskSpec`，生成分槽位检索提示。
- Slot Retriever：只在当前用户衣柜白名单内计算语义相关度。
- Candidate Generator：执行硬约束并生成完整组合。
- Stylist Agent：只从合法候选中排序和选择差异化结果。
- Reviewer Agent：复核衣柜归属、槽位完整性和硬约束，不得覆盖规则结果。

当前三个 Agent 使用可离线复现的确定性后端，接口已经独立，后续可以替换为结构化输出 LLM。系统没有 API Key 时仍可完整演示。

## 语义驱动三 Agent（可选，DeepSeek）

配置 `DEEPSEEK_API_KEY`（环境变量或 `.env`）后，工作流自动启用语义链路；未配置时走确定性回退链路：

```powershell
$env:DEEPSEEK_API_KEY="sk-..."
# 可选
$env:DEEPSEEK_BASE_URL="https://api.deepseek.com"
$env:DEEPSEEK_MODEL="deepseek-chat"
```

- Agent 1 语义检索：生成 `request_signature`（theme / unique_mood / practical_context / generic_tendencies_to_avoid）和三类检索计划（core 0.40 / distinctive 0.30 / supporting 0.10），再叠加偏好 0.10 与新颖 0.10 得到 Top-50 候选池。
- Agent 2 搭配组合：从候选池选品组成 3-5 套方案，带 `composition_strategy`；禁止候选池外单品 ID（双层拦截）。
- Agent 3 评审判定：单次调用双阶段协议（先盲评单品数据，再核对解释），输出五维评分和四决策分支。
- 回退总次数 ≤ 1，LLM 调用次数 accept=3 / recompose=5 / retrieve_more=6。
- 跨请求记忆：持久化最近 5 次 `request_signature` 和结构签名，只做软新颖惩罚。
- 结构签名由品类结构、颜色家族、风格标签和层数生成（`styleforge/core/structure_signature.py`）。

LLM 不可用、JSON 解析失败或候选池为空时，对应 Agent 降级到确定性实现，并记录 `degraded_reason`；语义检索器降级时整体回退确定性链路。所有语义输出（request_signature、检索计划、候选池、方案、评审与决策）写入 `styling_runs.semantic_detail_json` 供离线评估。

演示衣柜可使用 `mixed-large` 配置生成约 200 件单品，均衡覆盖通勤正式、晚宴、极简、休闲、运动、浪漫、街头和复古风格：

```powershell
D:\anaconda\envs\style\python.exe -m styleforge.pipelines.seed_balanced_wardrobe `
  --user-id demo-user --profile mixed-large --replace
```

## 目录

```text
styleforge/
  agents/                # Planner / Stylist / Reviewer
  evaluation/            # 数据和检索质量评估
  pipelines/             # 导入、审计、嵌入、FAISS 构建
  repositories/          # SQLite 持久化
  services/              # 推荐、向量存储、展示服务
  tools/                 # 确定性候选生成与约束工具
  vision/                # FashionCLIP 编码器
  workflow/              # LangGraph 状态和工作流
artifacts/                # 本地模型、向量、索引和报告（不提交 Git）
data/                     # SQLite 数据库（不提交 Git）
```

## 本地运行

项目使用以下 Python：

```powershell
D:\anaconda\envs\style\python.exe
```

先在两个 PowerShell 窗口都设置图片目录：

```powershell
$env:GARMENTS2LOOK_IMAGE_ROOT="E:\image.tar\image\images"
```

启动 API：

```powershell
cd C:\Users\32369\Desktop\agent-p\style
D:\anaconda\envs\style\python.exe -m uvicorn styleforge.api:app --host 127.0.0.1 --port 8000
```

另一个窗口启动演示界面：

```powershell
cd C:\Users\32369\Desktop\agent-p\style
$env:GARMENTS2LOOK_IMAGE_ROOT="E:\image.tar\image\images"
D:\anaconda\envs\style\python.exe -m streamlit run styleforge\ui.py
```

浏览器打开 `http://localhost:8501`，API 文档位于 `http://127.0.0.1:8000/docs`。

## 命令行冒烟测试

```powershell
cd C:\Users\32369\Desktop\agent-p\style
D:\anaconda\envs\style\python.exe -m styleforge.workflow.graph `
  --user-id demo-user `
  --request "明天参加互联网公司的面试，希望正式但不要太老气，不穿红色。"
```

首次请求会将 FashionCLIP 加载到 GPU，通常比后续请求慢数秒。视觉组件缺失时，工作流会明确记录降级原因并继续使用规则候选生成。

配置了 `DEEPSEEK_API_KEY` 时上述命令自动走语义链路（输出含 `request_signature`、`retrieval_plans`、`pool`、`critic` 决策等字段）；可用 `--no-llm` 强制走确定性链路，或 `--llm-verbose` 输出每轮 LLM 的完整提示词与响应：

## 关键产物

- `artifacts/embeddings/fashionclip/embeddings.npy`：全量图片向量，约 260 MB。
- `artifacts/index/fashionclip/fashionclip.index`：全量 FAISS 索引，约 260 MB。
- `artifacts/evaluation/fashionclip_retrieval.json`：检索质量报告。
- `artifacts/data_audit/polyvore_image_audit.json`：图片完整性报告。
- `data/styleforge.db`：商品、搭配、衣柜和推荐运行记录。

## 测试

开发依赖通过清华镜像安装：

```powershell
D:\anaconda\envs\style\python.exe -m pip install "pytest>=8" "ruff>=0.6" `
  -i https://pypi.tuna.tsinghua.edu.cn/simple
```

运行：

```powershell
D:\anaconda\envs\style\python.exe -m pytest -q
D:\anaconda\envs\style\python.exe -m ruff check styleforge tests
```
