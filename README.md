# StyleForge

当前准确完成度、验证证据和未完成项见[项目状态](docs/PROJECT_STATUS.md)。Mytheresa 多受众数据已完成 62,457 件商品和 328,754 个图片引用的全量路径存在性审计；接入代码尚未完成主库导入、全量像素解码和合并嵌入。

完整文档入口：[docs/README.md](docs/README.md)。当前完成度、未验证改动和下一验收顺序请先阅读[项目状态](docs/PROJECT_STATUS.md)。

StyleForge 是一个个人衣柜多 Agent 穿搭系统，默认配置 DeepSeek 语义链。用户输入自然语言需求，系统只从该用户显式衣柜白名单中选择可追溯商品 ID，并输出硬约束检查、评分、选择理由和执行轨迹。演示衣柜来自数据集抽样，不代表真实用户实际拥有这些商品。

购物订单建立个人衣柜的流程见[订单衣柜导入与增量嵌入](docs/ORDER_WARDROBE_IMPORT.md)。订单先经过收货状态白名单；若导出文件含退款、退货或售后列，再执行对应硬过滤，最后由用户预览确认。当前完整订单表没有独立售后列，不能保证识别所有历史售后。有图使用图像嵌入，无图使用文字嵌入；该最新版增量已通过回归与一次性数据库验收，待真实提交和嵌入验收。

## 当前能力

- Garments2Look-Polyvore：126,928 件商品、126,928 张可解码图片。
- Polyvore 搭配：9,794 套引用完整的严格子集。
- FashionCLIP：为全部商品生成 512 维、L2 归一化的图片向量。
- FAISS：`IndexFlatIP` 精确余弦检索，共 126,928 条。
- 检索评估：图片同品类 Precision@10 为 92.72%，文本品类宏平均 Precision@10 为 95.5%。
- LangGraph：Planner → Slot Retriever → Candidate Generator → Stylist → Reviewer → Persistence。
- FastAPI：推荐、目录搜索、衣柜增删、图片访问、健康检查和 Prometheus 指标端点。
- 系统健康面板：Web `/health` 以 HTTP → 任务 → Agent → LLM → 工具 → PostgreSQL 链路展示调用、失败、重试、降级和耗时；`/api/metrics` 可由 Prometheus 抓取持久化。
- Streamlit：个人衣柜和穿搭推荐演示界面。
- 订单导入：Excel 脱敏预览、收货状态硬门槛、可选售后字段过滤、文件级幂等、人工确认和个人增量嵌入（回归与一次性数据库验收已通过，待真实提交）。
- Mytheresa：62,457 件多受众商品、328,754 个图片引用全部存在，类别映射遗漏为 0；尚未导入主库。
- 语义驱动三 Agent（v3.2.1）：可选接入 DeepSeek，Agent 1 语义检索（request_signature + 多查询加权检索 + Top-50 候选池）、Agent 2 搭配组合、Agent 3 评审判定（五维盲评 + accept/recompose/retrieve_more/wardrobe_gap 四决策分支）。2026-08-06真实API验收的8类请求全部accept、推荐100%衣柜归属；其中7类无回退，“高考”请求的Critic发生一次瞬时API失败并按标准推荐策略降级。
- v3.3 六任务执行链（已实现并定向验证）：Task Router 在三个 Agent 之前将请求路由为穿搭推荐、局部修改、风格知识、单品知识、衣橱兼容性或衣橱缺口；`POST /tasks/execute` 执行对应子图，统一使用 Context Pack，并把结果、证据和轨迹持久化到 `task_runs`。Web 的“智能造型”和小程序的“造型”页已接入五类扩展业务；输入输出见[扩展任务业务与 API](docs/EXTENDED_TASKS.md)。
- MCP 双向集成：StyleForge 作为 Client 通过官方 Time/Fetch MCP 获取时区与 Open-Meteo 天气事实（严格 HTTPS 主机白名单，失败回退现有直连 Provider），同时作为 Server 在 `/mcp/` 暴露五个用户隔离工具、一个能力资源和一个穿搭规划 Prompt；调用会写入去敏任务诊断和健康面板。详见[MCP 集成](docs/MCP_INTEGRATION.md)。
- P5 天气上下文：Agent 1 按请求决定是否需要天气，Context Router 通过 typed Weather Tool 获取事实，再把同一事实交给三个主 Agent；Weather Tool 不生成穿搭建议，Provider 主路径现为 Time/Fetch MCP。契约见[天气上下文工具](docs/WEATHER_CONTEXT.md)。
- P2.5 路由评估切片（已验证）：`evals/cases/task_routing.json` 固化 60 条中英文用例，六类各 10 条；基线准确率 100%，六类逐类准确率均为 100%，失败样本 0。该指标只评价固定集任务路由，不代表穿搭质量。
- 衣柜照片识别与批量导入：单图识别（`POST /items/analyze` 预填 + `/items/photo` 入库）和批量识别（`POST /items/batch-recognize`，后台 3 张并发、前端轮询进度/预计剩余、失败项可编辑入库或删除、处理完确认删除批次记录）。识别走已配置的多模态 API；输入原图、批次和逐图状态均持久化，API 重启会恢复未完成任务。可靠单品批量入库后统一生成个人 FashionCLIP 增量向量，识别失败与向量失败可分别重试，确定性单品 ID 保证恢复时不重复建衣物；衣柜页也能按 `pending/failed` 状态一键补齐当前用户的向量。详见[开发过程记录](docs/DEVELOPMENT_LOG.md)第 10、23、24 节。
- 会话持久化多轮对话 + Context-Aware 自适应偏好记忆：`POST /tasks/execute` 带 `session_id` 落库消息并恢复上文，同一会话内连续追问（"换件外套""更正式一点"）自动携带当前搭配；记忆走完整闭环（行为事件 → 偏好证据 → 维度化偏好模型 → Memory Resolver → 三 Agent 差异化注入），行为来自推荐结果上的「采纳/换掉/好评差评/换掉这件」按钮、后端埋点与 LLM 语言证据提炼，含生命周期、衰减与 consolidation；Web 推荐页聊天化 + 行为按钮 + 偏好管理页。契约见[会话与记忆](docs/SESSION_CHAT_MEMORY.md)。

## 职责边界

FashionCLIP 负责将图片和文本映射到同一个向量空间，用于理解及召回衣物。它不直接决定最终搭配。

- Planner Agent：把需求解析成严格的 `TaskSpec`，生成分槽位检索提示。
- Slot Retriever：只在当前用户衣柜白名单内计算语义相关度。
- Candidate Generator：执行硬约束并生成完整组合。
- Stylist Agent：只从合法候选中排序和选择差异化结果。
- Reviewer Agent：复核衣柜归属、槽位完整性和硬约束，不得覆盖规则结果。

系统包含两套边界不同的链路：标准推荐可在无 API Key 时使用确定性链，配置 DeepSeek 后使用 Agentic 语义链；五类扩展业务要求配置模型。单品建议在模型结构化输出连续未通过硬校验时，允许基于 Agent 1 已检索、已评分且位于当前衣橱白名单内的候选生成 `deterministic_grounded_fallback`；该兜底不会生成新 ID，并在结果中显式标记生成模式与限制。

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
- 结构签名由品类结构、颜色家族、风格标签和层数生成（`apps/api/styleforge/core/structure_signature.py`）。

LLM 不可用、JSON 解析失败或候选池为空时，对应 Agent 降级到确定性实现，并记录 `degraded_reason`；语义检索器降级时整体回退确定性链路。所有语义输出（request_signature、检索计划、候选池、方案、评审与决策）写入 `styling_runs.semantic_detail_json` 供离线评估。

演示衣柜可使用`mixed-large`配置按当前配额最多选择204件单品，均衡覆盖通勤正式、晚宴、极简、休闲、运动、浪漫、街头和复古风格。它是可重建profile，不等同于2026-08-09主数据库的2058件默认快照：

```powershell
cd C:\Users\32369\Desktop\agent-p\style
$env:PYTHONPATH=(Resolve-Path ".\apps\api")
D:\anaconda\envs\style\python.exe -m styleforge.pipelines.seed_balanced_wardrobe `
  --user-id demo-user --profile mixed-large --replace
```

## 目录

```text
apps/api/styleforge/
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

推荐使用统一启动器。它会检查固定 Python 环境、必需包、PostgreSQL 连接和可选图片目录，
再启动 API 与 Vite Web；进程状态和日志只写入工作区 `artifacts/runtime/`：

```powershell
.\scripts\start_styleforge.ps1 -Action Check
.\scripts\start_styleforge.ps1 -Action Start
.\scripts\start_styleforge.ps1 -Action Status
.\scripts\start_styleforge.ps1 -Action Stop
```

可选的本地监控栈使用固定版本 Prometheus + Grafana，默认抓取 API 8000 端口，数据写入 `artifacts/monitoring/`：

```powershell
.\scripts\start_monitoring.ps1 -Action Check
.\scripts\start_monitoring.ps1 -Action Start -ApiPort 8000
.\scripts\start_monitoring.ps1 -Action Status
.\scripts\start_monitoring.ps1 -Action Stop
```

Prometheus 位于 `http://127.0.0.1:9090`；Grafana 仪表盘位于 `http://127.0.0.1:3300/d/styleforge-system-overview`，本地默认账号为 `admin / styleforge-local`，可在启动前通过 `GRAFANA_ADMIN_USER` 和 `GRAFANA_ADMIN_PASSWORD` 覆盖。

如需旧版 Streamlit 界面，使用 `-Action Start -Ui Streamlit`。以下手工启动命令仍保留用于调试。

先在两个 PowerShell 窗口都设置后端包路径和图片目录：

```powershell
$env:PYTHONPATH=(Resolve-Path ".\apps\api")
$env:GARMENTS2LOOK_IMAGE_ROOT="E:\image.tar\image\images"
```

启动 API：

```powershell
cd C:\Users\32369\Desktop\agent-p\style
D:\anaconda\envs\style\python.exe -m uvicorn styleforge.api:app `
  --app-dir apps\api `
  --host 127.0.0.1 `
  --port 8000
```

另一个窗口启动演示界面：

```powershell
cd C:\Users\32369\Desktop\agent-p\style
$env:PYTHONPATH=(Resolve-Path ".\apps\api")
$env:GARMENTS2LOOK_IMAGE_ROOT="E:\image.tar\image\images"
D:\anaconda\envs\style\python.exe -m streamlit run apps\api\styleforge\ui.py
```

浏览器打开 `http://localhost:8501`，API 文档位于 `http://127.0.0.1:8000/docs`。

FastAPI 同时提供 MCP Streamable HTTP endpoint：`http://127.0.0.1:8000/mcp/`；
`GET /mcp/status` 查看对外工具和外部 MCP 调用状态。也可用 stdio 独立运行：

```powershell
$env:PYTHONPATH=(Resolve-Path ".\apps\api")
D:\anaconda\envs\style\python.exe -m styleforge.mcp_server
```

## 命令行冒烟测试

```powershell
cd C:\Users\32369\Desktop\agent-p\style
$env:PYTHONPATH=(Resolve-Path ".\apps\api")
D:\anaconda\envs\style\python.exe -m styleforge.workflow.graph `
  --user-id demo-user `
  --request "明天参加互联网公司的面试，希望正式但不要太老气，不穿红色。"
```

首次请求会将 FashionCLIP 加载到 GPU，通常比后续请求慢数秒。视觉组件缺失时，工作流会明确记录降级原因并继续使用规则候选生成。

配置了 `DEEPSEEK_API_KEY` 时上述命令自动走语义链路（输出含 `request_signature`、`retrieval_plans`、`pool`、`critic` 决策等字段）；可用 `--no-llm` 强制走确定性链路，或 `--llm-verbose` 输出每轮 LLM 的完整提示词与响应：

Web 和小程序的主推荐输入统一调用 `POST /tasks/execute`。Task Router 会自动识别普通推荐、局部修改、风格知识、单品搭配、新品兼容性和衣橱缺口。五类扩展任务严格执行同一套 Agent 1/2/3；没有配置 LLM 或任一 Agent 输出非法时会明确失败，不提供确定性结果降级。标准穿搭推荐仍保留原有离线降级能力。带上 `session_id` 时请求会持久化为一个可恢复的多轮会话（Web 聊天化已接入；小程序暂只传 `user_id/request`，行为不变）。

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
D:\anaconda\envs\style\python.exe -m ruff check apps\api\styleforge tests evals
```

完整质量门禁（全量 pytest、前端构建、浏览器 E2E、真实 FashionCLIP 检索）：

```powershell
D:\anaconda\envs\style\python.exe scripts\run_quality_gate.py
```

每次运行使用独立 PostgreSQL schema 和工作区临时目录；结束后自动清理数据库、
测试衣物、向量、图片以及 API/Vite 进程树。截图和 JSON 报告保存在
`artifacts/quality-gate/<时间>-<运行ID>/`。可使用 `--skip-unit`、`--skip-build`、
`--skip-browser`、`--skip-semantic` 缩短本地检查，或使用 `--keep-runtime` 保留现场。
门禁固定包含 42 条无 LLM、无 GPU 推荐质量基准；任何用例失败都会使门禁失败。

固定推荐质量集共 60 条：42 条 `deterministic` 用例覆盖通勤、雨天、运动、有限衣柜和无解语义，
18 条 `configured` 用例覆盖推荐、修改、风格建议、单品建议、兼容性和衣柜缺口六类任务。
默认不调用模型；显式使用 `configured` 才会调用 DeepSeek。控制台默认只输出汇总，完整逐例结果写入
`--report`；调试时可加 `--print-full-report`。真实数据验收集来自
Polyvore Outfits 官方套装，数据集目录只读，运行时仅把冻结的商品文本元数据导入隔离 schema：

```powershell
D:\anaconda\envs\style\python.exe -m evals.runners.evaluate_wardrobe_quality `
  --mode configured --cases evals\cases\wardrobe_quality_real.json `
  --report artifacts\evaluation\wardrobe_quality_real_deepseek.json
```

2026-08-28 验收结果：真实连衣裙婚礼/约会 2 例均通过路由、状态、锚点和点名槽位硬约束；
完整事实在主图入口直达 Extension 收口，避免 Coordinator 重复规划和错误澄清。两例均以明确标记的
grounded fallback 完成，单请求 Harness 模型调用降为 1 次，报告见
`artifacts/evaluation/wardrobe_quality_real_deepseek_direct.json`。

同日完整真实基线已扩展为 7 例、覆盖六类任务：推荐 1、修改 1、风格建议 1、单品建议 2、
兼容性 1、衣橱缺口 1；Polyvore 官方商品文本 + DeepSeek 验收 7/7 通过，路由/状态准确率均为 100%。
主图把 `MultiTaskWorkflow` 确定的任务类型作为权威路由，Coordinator 不得跨类型误派；推荐链支持
`one_piece + footwear`，通用 `accessory` 已补齐物理结构。模型协议失败或 Critic 检出严重意图错位时，
只允许从当前用户衣橱按场景特征恢复候选，并继续经过环境门禁与 Critic，结果明确标记降级状态。
最终报告见 `artifacts/evaluation/wardrobe_quality_real_six_task_final.json`。

仓库已提供 `.github/workflows/ci.yml`：远程 CI 执行 Ruff、无 GPU 全量 pytest、42 条轻量质量基准、
PostgreSQL API health 冒烟和 Vite 构建；真实 DeepSeek 与 CUDA/FashionCLIP 继续通过显式本地质量门禁运行，
不在普通 PR 中消耗外部 API 或要求 GPU runner。

记忆泛化边界：普通 `item_replaced` 只记录具体单品，不参与品类归纳；弱行为证据必须在同一
场景中累计至少 3 个不同单品、3 次不同明确判断，才生成低强度品类假设。不同场景不会合并，
同一次事件不会重复累计，显式语言偏好仍可直接覆盖弱归纳。
