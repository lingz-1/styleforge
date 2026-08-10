# StyleForge 项目状态

> 更新时间：2026-08-10
> 定位：用于实习求职展示的本地个人衣柜多 Agent 穿搭项目  
> 当前阶段：Polyvore 演示主链路已形成可运行基线；Mytheresa 接入和订单衣柜导入已完成代码实现，订单衣柜最新版已通过回归与一次性数据库验收，但真实订单尚未提交、个人商品嵌入和 Mytheresa 全量接入仍待端到端验收。2026-08-05 修复了运动场合推荐不合理、UI 提交强制逐条填写、推荐结果不更新等问题，详见[问题与解决记录](ISSUE_LOG.md)。2026-08-05 完成 v3.2.1 语义驱动三 Agent 改造（DeepSeek）；2026-08-06 用真实 DeepSeek API 对 8 类代表性请求完成端到端验收（全部 accept、正常 3 次 LLM 调用、0 回退、推荐 100% 衣柜归属），备选方案已补确定性评分。2026-08-06 落地《评估方案.txt》五维统一评估框架：统一 Rubric 贯穿三 Agent、第三维改名 `outfit_coordination`、`request_signature` 新增 `explicit_style`、用户可配置五维权重（真实链路验证首选分=用户权重加权结果）。2026-08-06 完成架构规划 v3.3-plan.1（参考目录 + 扩展版方案收口，见 [ARCHITECTURE_PLAN.md](architecture/ARCHITECTURE_PLAN.md)），后端移动至 `apps/api/styleforge/`（保持 import），双端前端（Vue Web + 小程序）骨架与核心功能完成（衣柜/上传/编辑/补图/订单导入/推荐/偏好），新增拍照创建与修改衣物接口；小程序真机预览因校园网隔离待通。2026-08-09 完成默认模拟衣柜重建（catalog=衣柜零冗余）、FashionCLIP 嵌入 + FAISS 索引全量构建、衣柜可折叠（双端）、推荐跨页面持久化、推荐评分改为按 LLM 五维分排序并双分数展示（LLM+规则归一化到 100）、中英分类结构文档与上传表单大类必选/细分类可选联动、小程序真机 http 图片本地化修复，详见下方各节。

> 2026-08-09 扩展进度：v3.3 P2 Task Router 和 P2.5 路由切片已验证；随后完成 Context Pack、任务运行持久化、局部修改、风格知识、单品知识、新品衣橱兼容性和衣橱缺口五类扩展业务，并接入 FastAPI、Vue Web 和微信小程序。当批验收：`compileall` 通过、Ruff clean、Pytest 175 passed；42 条中英文固定路由集准确率 100%；Web Vite 生产构建和小程序新增脚本语法检查通过；独立数据库上的真实 API 进程烟测 `health=ok`，任务执行和持久化查询成功。

> 2026-08-10 P5进度：Weather Tool、Context Router和三个主Agent的天气事实共享已完成，全量Pytest 189 passed、compileall、Ruff和Vue生产构建通过。API包位于`apps/api`，从仓库根目录启动必须使用`--app-dir apps\api`；本批次完整过程见[开发过程记录](DEVELOPMENT_LOG.md)。

> 2026-08-10 P5 V2规划：已完成[天气与时空上下文 V2 详细方案](WEATHER_CONTEXT_V2_PLAN.md)，覆盖隐含天气需求、设备定位、近3天默认窗口、相对时间/节日、事件场次与场馆、小时级活动窗口、远期气候语义、环境调整说明和随身物品建议。该部分目前是规划，不属于189个已通过用例覆盖的实现。

> 历史统计口径修正：2026-08-06真实API的8类请求全部`accept`且100%衣柜归属；其中7类无回退，“高考”请求的Critic发生一次瞬时API失败并按标准推荐策略降级。文中旧的“8请求0回退”摘要以本说明为准。

## 1. 当前结论

StyleForge 已经具备“用户衣柜 → 自然语言需求 → 多 Agent 协作 → 约束内搭配建议 → 商品图片与执行轨迹”的完整项目形态。已验证的 Polyvore/FashionCLIP/FAISS 基线足以演示核心思路；新增的多受众 Mytheresa 目录解决了数据覆盖问题但仍是待验收增量，个人订单衣柜已完成回归与一次性数据库验收，待真实提交验收。

标准推荐包含两条链：Planner/Stylist/Reviewer是可离线复现的确定性链，SemanticRetriever/Composer/Critic是可选DeepSeek语义链；无API Key时标准推荐回退确定性链。五类扩展业务只允许SemanticRetriever/Composer/Critic严格三Agent执行，模型、JSON、Schema或审校失败时记录`failed`，不生成确定性业务结果。不能把这两套降级政策混为一谈，也不能表述为“LLM已完成全部穿搭决策”。

## 2. 状态总览

| 模块 | 当前状态 | 结论 |
|---|---|---|
| Polyvore 商品与图片 | 已验证 | 126,928 件商品及图片完成导入、存在性和像素解码检查 |
| Polyvore 搭配关系 | 已验证 | 9,794 套引用完整的严格子集，可用于关系与评估 |
| FashionCLIP 图片嵌入 | 已验证 | 126,928 个 512 维归一化向量，GPU 全量构建完成 |
| FAISS 全目录索引 | 已验证 | `IndexFlatIP`，126,928 条向量 |
| 检索离线评估 | 已验证 | 图片同品类 P@10 92.72%；文本品类宏平均 P@10 95.5% |
| LangGraph 推荐主链路 | 已验证基线 | 空衣柜能返回无解，演示衣柜能生成可追溯搭配 |
| 子类、正式度、多配饰、beam search | 已验证 | 2026-08-05 全量回归 56 passed |
| 场合约束（运动等） | 已验证 | 运动场合排除汉服/裙子/非运动鞋、必选运动子类，2026-08-05 修复 |
| 订单导入 UI 交互 | 已验证 | 无需逐条填字段，性别/品类自动兜底，2026-08-05 修复 |
| Mytheresa 元数据与路径审计 | 已验证 | 62,457 件、328,754 个图片引用全部存在、0 个未映射类别 |
| Mytheresa 像素解码 | 未完成 | 当前只证明路径存在，尚未证明 328,754 张图片均可解码 |
| Mytheresa 导入/合并嵌入 | 已实现，未执行 | 尚未导入主数据库，也未构建 189,385 件合并向量和索引 |
| 订单衣柜导入 v3 | 已验证 | 收货/退款/售后硬门槛 + Schema v5 回归通过；完整订单表一次性数据库验收通过（374 准入，0 未知），15 个非服饰误分类已修复 |
| 个人商品自动嵌入 | 已实现，待验收 | 支持无图文字向量和实拍图重嵌入，尚未用真实提交完整验收 |
| FastAPI/Streamlit | 基线可启动，最新 UI 待回归 | API 曾成功启动；最新订单预览和图片展示需人工验收 |
| 语义驱动三 Agent (v3.2.1) | 已验证 | DeepSeek 三 Agent + 四决策分支 + 跨请求记忆；当时里程碑134测试通过；2026-08-06真实API 8请求全部accept、100%衣柜归属，7类无回退、1类Critic瞬时降级 |
| 五维统一评估框架（评估方案 v1.1） | 已验证 | 统一 Rubric 贯穿三 Agent；`outfit_coordination` 改名 + `explicit_style` 字段；用户可配置五维权重（API/UI），真实链路验证首选分=用户权重加权 |
| 架构 v3.3-plan.1 | 已规划 | 参考目录 + 扩展版方案收口，规划文档 `docs/architecture/ARCHITECTURE_PLAN.md`，含职责边界与分阶段路线 |
| v3.3 Task Router + 六任务执行 | 已验证 | `/tasks/route` 只分类；`/tasks/execute` 统一执行六个隔离子图；Context Pack、trace 和 `task_runs` 可审计持久化 |
| v3.3 P2.5 路由评估 | 路由切片已验证 | 42 条中英文固定 Cases（六类各 7 条）准确率 100%；Wardrobe Fixtures 与五维 benchmark 未完成 |
| 五类扩展业务 | 已验证 | 局部修改硬锁非目标单品；风格/单品建议使用本地证据；新品兼容不落库；衣橱缺口检查槽位、场景与重复度 |
| P5 天气上下文工具 | 已验证 | Agent 1 按需声明天气；Context Router 调用 typed Open-Meteo Tool；事实共享给三个 Agent、Context Pack、trace 与持久化；当前不是 MCP Server；全量 Pytest 189 passed |
| P5 天气与时空上下文 V2 | 已规划，未实现 | 隐含环境需求、设备定位、时间/事件解析、小时天气、远期气候参考、事实→影响→行动解释和随身物品清单 |
| 后端结构 | 已移动 | `styleforge/` → `apps/api/styleforge/`（保持 `from styleforge.*` import 不变，pyproject package path）；迁移当时134测试全绿，当前全量为189 passed |
| 拍照创建 / 修改衣物接口 | 已验证 | `POST /wardrobes/{user_id}/items/photo`（上传图创建个人商品+嵌入）、`PUT /items/{item_id}`（改信息重嵌入），已验证 |
| Vue Web（v3.3 前端） | 核心功能已构建 | 衣柜、推荐、订单导入、五维偏好和“智能造型”五类扩展任务；Vite 生产构建通过 |
| 小程序（v3.3 前端） | 核心功能已实现 | 衣柜、上传、订单、推荐、偏好和“造型”五类扩展任务；新增脚本通过 Node 语法检查，真机联调仍受局域网条件影响 |
| 默认模拟衣柜（2026-08-09） | 已验证 | 重建干净数据库，catalog=衣柜 2058 件零冗余（1822 mytheresa + 236 polyvore），200 套搭配（150+50）、889 条关系、2058 张图片全部 available |
| 演示嵌入 + 索引（2026-08-09） | 已验证 | FashionCLIP 2058/2058 嵌入（GPU 87.5s）+ FAISS IndexFlatIP，自检索 score=1.0，`/health` embedding_ready_items=2058，推荐端到端正常 |
| 推荐评分（2026-08-09） | 已验证 | 全部候选统一走 Critic 五维 LLM 评分，按 LLM 分降序排序（规则分作并列打破）；候选同时携带 `llm_score`/`rule_score`（均归一化到 100），Web 头部分开展示两分 |
| 中英分类结构 + 上传联动（2026-08-09） | 已验证 | `core/taxonomy.py` 单一数据源（27 大类 + 70 细分类中英标签，大类按常用度排序）；`GET /catalog/taxonomy`；`docs/category_taxonomy.md`；Web/小程序上传表单大类必选 + 细分类可选联动 |
| 衣柜可折叠（2026-08-09） | 已验证 | Web 用 el-collapse、小程序用分组折叠，均默认收起 + 全部展开/收起；Web 推荐状态 Pinia store 跨页面持久化（路由切换不丢结果） |
| 小程序真机图片（2026-08-09） | 已验证 | 微信真机 `<image>` 不支持 http 链接，改为 `wx.downloadFile` 下载成本地临时文件再渲染；衣柜分组惰性下载，推荐结果图同样处理 |

## 3. 已验证证据

### 3.1 Polyvore 基线

- 商品元数据：126,928 条。
- 商品图片：126,928 张，缺失 0、解码错误 0。
- 严格搭配子集：9,794 套、46,413 条商品关系。
- FashionCLIP：512 维、L2 归一化，RTX 5060 Ti 全量运行 466.155 秒。
- FAISS：`IndexFlatIP`，余弦相似度通过归一化内积实现。
- 图片到图片同品类 Precision@10：92.72%。
- 文本到图片 20 类宏平均 Precision@10：95.5%。

证据文件位于：

- `artifacts/data_audit/polyvore_image_audit.json`
- `artifacts/embeddings/fashionclip/manifest.json`
- `artifacts/index/fashionclip/manifest.json`
- `artifacts/evaluation/fashionclip_retrieval.json`

### 3.2 Mytheresa 数据审计

全量元数据和路径存在性审计结果：

- 商品数：62,457。
- 图片引用：328,754，存在 328,754，缺失 0。
- 受众：women 44,125、men 11,029、girls 3,761、boys 2,327、baby 1,176、life 39。
- 类别映射：`unmapped_item_count = 0`。
- 数据仍保留在 `E:\style-dataset`，项目没有移动或复制 170GB+ 原图。

这里的“全部存在”只表示文件路径存在，不等于全部图片已经通过 Pillow 像素解码。

### 3.3 完整订单表只读分析

`订单数据 (3).xlsx`包含 437 个商品行。订单级字段只出现在多商品订单的第一行，因此解析时必须向下继承订单号、时间、状态和店铺，但不能继承金额。

按商品行继承状态后的分布：

| 状态 | 商品行 | 导入规则 |
|---|---:|---|
| 交易成功 | 374 | 进入服饰识别阶段 |
| 交易关闭 | 51 | 排除 |
| 买家已付款 | 9 | 排除 |
| 卖家已发货 | 2 | 排除 |
| 充值成功 | 1 | 排除 |

374 行只是通过订单状态，不代表最终会导入 374 件衣物；非服饰、无法分类商品仍会排除，用户还需要在预览中确认实际仍然拥有的衣物。

当前 Excel 没有独立的退款、退货或售后字段。因此对这份文件只能使用最终订单状态作为代理信号；系统已兼容未来导出中的退款/售后/物流字段，但不能从当前文件恢复未导出的历史售后事实。

### 3.4 订单衣柜导入 v3 一次性数据库验收（2026-08-04）

回归与预览均已通过，使用唯一命名的一次性数据库，未提交任何衣物：

- 回归：`compileall` 退出码 0；Pytest 47 passed；Ruff `All checks passed`。
- 预览数据库：`artifacts/wardrobe-status-preview-20260804-232159.db`。
- 预览报告：`artifacts/imports/wardrobe-status-preview-20260804-232159.json`。

| 验收项 | 预期 | 实际 |
|---|---:|---:|
| `total_rows` | 437 | 437 |
| `eligible_order_rows` | 374 | 374 |
| `ineligible_order_rows` | 63 | 63 |
| `unknown_order_rows` | 0 | 0 |
| `committed_item_count` | 0（只预览） | 0 |
| `candidate_rows`（服饰候选） | 解析器实际运行确定 | 111 |

规则修复前首版预览的候选数为 126，其中 15 行是明确非服饰（卫生巾、电脑 CPU 套装、棉花娃娃娃衣、修眉刀、睡袋床单、拼豆 DIY、刻刀、美甲工具、消毒液、首饰盒、毛绒挂件钥匙扣、cos 眼镜框道具）。修复 `NON_FASHION_PATTERN` 与分类规则后这些行全部移出候选，剩余 111 行为真实服饰或配饰。

逐行对照验证（解析器输出 vs 预览数据库）：

- 437 行在订单哈希、状态、金额、准入结论、决策字段上 0 mismatch。
- 原始订单号全部以 64 位 SHA-256 存储，无明文；报告 JSON 中无长数字 token。
- 51 个多商品订单的状态向下继承正确，0 行状态与首行不一致。
- 金额保持逐商品行读取：多商品订单实付金额仅存首行，子行为 `None`，未被错误继承；`rows_with_paid_amount = 299` 与解析统计一致。

分类修复详情见 4.1。人工抽查（衬衫、半身裙、裤装、鞋、外套、配饰、汉服套装、内衣/背心、非服饰各至少 3 条）仍需真人执行后决定是否提交。

## 4. 最新代码增量

### 4.1 订单衣柜导入

- Excel 白名单字段读取和文件 SHA-256 幂等。
- 订单子行继承订单上下文，金额保持逐商品行读取。
- 原始订单号只保存 SHA-256，不保存明文。
- 只有`交易成功/已完成/订单完成/已收货/确认收货/已签收`可进入候选。
- 未收货、关闭、取消、退款、退货、售后、维权和未知状态默认排除。
- 解析层、预览层、提交层三重校验，手工修改品类不能绕过订单状态门槛。
- Schema v5 保存脱敏订单状态、准入结论、个人拥有状态和增量嵌入。
- 无实拍图时生成 FashionCLIP 文字嵌入；上传实拍图后替换为图像嵌入。
- 2026-08-04 修复误分类：扩充 `NON_FASHION_PATTERN`（卫生巾/安睡裤、消毒液、娃衣/棉花娃娃、CPU/主板等硬件、修眉/美甲工具、睡袋床单、拼豆/材料包、刻刀/剪刀、首饰盒/收纳盒、毛绒挂件/钥匙扣、cos 道具），新增 `underwear/underpants`（内裤/三角裤/平角裤/安全裤）规则，并把 `blazer` 规则收紧为“西装外套/西服外套/西服”避免把“马面裙西装半身裙”误判为外套。新增 12 个回归测试覆盖这些场景。

### 4.2 Mytheresa 多数据源接入

- women、men、girls、boys、baby、life 多受众解析和硬过滤。
- 385 个原始细分类全部映射到规范品类/槽位。
- 单商品多图片元数据和跨数据源图片路径解析。
- 流式批量导入、幂等 upsert、失败运行记录和数据源注册。
- 多数据源嵌入构建入口和多图片 API。

### 4.3 推荐约束

- 上衣、下装、鞋、外套、连衣裙、包和饰品的子类约束。
- 必选/排除子类，例如“衬衫”“半身裙”“不要高跟鞋”。
- 正式度评分、结果去重、多配饰槽位和有界 beam search。
- 推荐只能引用当前用户衣柜中的真实商品 ID。

### 4.4 语义驱动三 Agent（v3.2.1）

- `apps/api/styleforge/llm/`：DeepSeek OpenAI 兼容客户端（JSON mode + 三层重试兜底 + 诊断记录）、三个 Agent 的 Pydantic 契约、Prompt 构建器（含 few-shot）。
- Agent 1 语义检索：LLM 生成 `request_signature`（theme/unique_mood/practical_context/generic_tendencies_to_avoid）与三类检索计划（core 0.40/distinctive 0.30/supporting 0.10），多查询加权检索叠加偏好 0.10 与新颖 0.10，按品类配额构建 Top-50 候选池（配额不足动态转移并记录日志）。
- Agent 2 搭配组合：从候选池选品组成 3-5 套方案 + `composition_strategy`；池外单品 ID 由 `sanitize_pool_ids` 与基础校验双层拦截。
- Agent 3 评审判定：单次调用双阶段协议（先盲评单品数据、再核对解释），五维评分 + accept/recompose/retrieve_more/wardrobe_gap 四决策分支；回退总次数 ≤ 1，LLM 调用 accept=3/recompose=5/retrieve_more=6。
- 跨请求记忆：`request_memory` 表持久化最近 5 次 `request_signature` 与结构签名（品类结构/颜色家族/风格标签/层数），`novelty_scores` 做软新颖惩罚。
- 降级：任一 Agent LLM 失败或 JSON 校验失败时用确定性实现替代；语义检索器降级时整体回退旧确定性链路。无 `DEEPSEEK_API_KEY` 时工作流默认走确定性链路。
- 可观测：语义输出（request_signature、检索计划、候选池、方案、评审与决策）写入 `styling_runs.semantic_detail_json`；CLI 支持 `--no-llm` / `--llm-verbose`。

### 4.5 真实 API 端到端验收（2026-08-06）

对 demo-user 衣柜（315 件）用真实 DeepSeek API 批量验收 8 类代表性请求，探针与可读摘要位于 `artifacts/llm_acceptance.json` 与 `artifacts/llm_acceptance_summary.txt`：

| 请求 | 决策 | request_signature 摘要 | 推荐要点 | 结论 |
|---|---|---|---|---|
| 互联网面试 | accept | 专业/现代/自信，室内正式 | 黑色连体裤+银耳圈+裸色高跟鞋 | ✅ |
| 《悲惨世界》音乐剧 | accept | 悲壮/克制/复古文学感，剧场久坐 | 黑西装+法式蕾丝吊带+马面裙 | ✅ 与方案示例一致 |
| 周杰伦演唱会 | accept | 街头/复古/个性，户外站立 | 提花毛衣+吊带+半裙+运动鞋 | ✅ |
| 美拉德穿搭 | accept | 温暖/复古/秋日 | 卡其半裙+棕玛丽珍+米色高领（大地色系） | ✅ 无映射表也能理解风格词 |
| 明天下小雨 | accept | 防雨/舒适/利落 | 羽绒服+防滑马丁靴 | ✅ 理解湿滑与防泼水 |
| 高考 | accept | 自信/平静/舒适，考场久坐 | 浅蓝牛仔裤+白T+运动鞋 | ⚠️ 识别"幸运红"但未落到单品；critic 一次瞬时降级 |
| 打篮球 | accept | 活力/动感/清爽 | 篮球背心+短裤+运动鞋 | ✅ 运动硬约束 |
| 海边度假 | accept | 清爽/自由/度假感 | 碎花连衣裙+白玛丽珍+珍珠项链 | ✅ |

统一观察：8请求全部`accept`、推荐单品100%来自demo-user衣柜；7请求正常3次LLM调用且无回退，“高考”请求的Critic一次瞬时API失败并降级。`generic_tendencies_to_avoid`每次完整3条；Critic改进建议具体（"加棒球帽/防晒衫/胸针"）。备选方案评分已补确定性`score_outfit`。

### 4.6 v3.3 P2 Task Router（2026-08-09，已验证）

- `TaskType` 由独立 Task Router 唯一负责，六类分别为 `outfit_recommend`、`outfit_modify`、`style_advice`、`item_advice`、`wardrobe_compatibility`、`wardrobe_gap`；没有新增业务 Agent。
- 结构化上下文优先：已有当前搭配时进入局部修改，有新品上下文时进入衣橱兼容性；显式任务类型可用于受控调用。
- 文本路由使用有优先级、可解释的确定性规则；无法可靠匹配时降级为标准穿搭推荐，不调用 LLM 猜测任务。
- LangGraph 已建立 Task Router 节点和六个隔离子图占位节点，返回目标子图、能力依赖、路由原因、置信度、槽位提取与 trace。
- `POST /tasks/route` 始终只做路由；六类任务的真实执行统一由 `POST /tasks/execute` 进入。五类扩展由 `MultiTaskWorkflow` 严格串联三 Agent，路由接口本身不会产生业务结果。
- 新增典型中文/英文请求分类、上下文优先、目标槽位提取和六分支图测试；修复 Pytest 保留参数名冲突及“只换外套”漏判后，定向测试 31 passed、全量 Pytest 165 passed、Ruff 通过。

### 4.7 v3.3 P2.5 路由评估切片（2026-08-09，已验证）

- 新增 42 条固定任务路由 Cases，`outfit_recommend`、`outfit_modify`、`style_advice`、`item_advice`、`wardrobe_compatibility`、`wardrobe_gap` 各 7 条，中英文均有覆盖。
- 新增独立离线 runner，报告总准确率、逐类准确率、混淆矩阵和错误明细，报告目标为 `artifacts/evaluation/task_routing_baseline.json`。
- runner 已从仓库根目录成功执行：42/42 正确，总准确率 100%，六类逐类准确率均为 100%，失败样本 0；报告写入 `artifacts/evaluation/task_routing_baseline.json`。
- 这只是 P2.5 的任务路由切片。Wardrobe Fixtures、固定穿搭结果和统一五维 benchmark 仍未实现，不能把路由准确率表述为穿搭质量。

### 4.8 Context Pack 与五类扩展业务（2026-08-09，已验证）

- Context Pack 统一承载请求路由、当前搭配与锁定槽位、衣橱统计、五维偏好、最近请求记忆、候选新品以及带 `source/source_id/section` 的证据。
- 五类扩展已从独立确定性 Service 重构为严格共享三 Agent 链：Agent 1 检索并理解、Agent 2 生成任务专属结果、Agent 3 审校。
- 五类扩展没有确定性结果降级；任一模型调用、JSON、Schema 或审校失败都会写入失败运行，不返回伪成功结果。确定性代码只负责事实读取和硬边界校验。
- 单品搭配先解析衣橱锚点，再由 Agent 组合衣橱内支撑单品；新增黑色马甲语义与知识。衣橱缺口区分 general 和 targeted，新增中世纪灵感目标元素。
- `POST /tasks/execute` 和运行查询接口保持统一；Web 与小程序移除独立任务选择页，所有自然语言从主推荐入口自动路由。
- 严格三 Agent、Schema v8 与任务完成契约 v3.1 验收：编译通过、Ruff clean、Pytest 183 passed；Vue Vite 生产构建成功（1674 modules），小程序主推荐脚本和 JSON 配置检查通过。单品 `completed` 必须含锚点、兼容分组、完整样例与组合理由；缺口结果必须等于 Agent 1 的 `missing_elements`；Agent 3 可反馈 Agent 2 有限重做，不使用确定性结果降级。`/health` 暴露启动时间和契约版本用于排除旧进程。

## 5. 尚未验证或未完成

以下事项不能在简历、README 或面试中描述为已经稳定完成：

1. 完整订单表 v3 预览已通过（2026-08-04），但真实订单尚未提交到个人衣柜，也未生成文字/图像嵌入。
2. 订单候选的品类人工抽查尚未由真人完成；当前只做了规则级自动化校验与修复。
3. `personal-user` 衣柜为空：`catalog_items` 有 111 件 `personal-*` 商品，但 `wardrobe_items` 无 `personal-user` 的 `active=1` 记录，该用户推荐一直无解，待排查（见[问题与解决记录](ISSUE_LOG.md)）。
4. Mytheresa 62,457 件商品尚未导入主数据库。
5. Mytheresa 328,754 张图片尚未执行全量像素解码。
6. 尚未构建 Polyvore + Mytheresa 共 189,385 件商品的合并嵌入与 FAISS 索引。
7. Mytheresa 导入的中断续跑、重复执行、备份恢复演练尚未完成。
8. 最新 Streamlit 订单状态列、提交错误提示和个人图片重嵌入尚未人工验收。
9. 语义三 Agent 已用假 LLM 完成工作流级回归，并于2026-08-06用真实DeepSeek API验收8类请求（全部accept、100%衣柜归属；7类无回退，“高考”请求Critic瞬时降级一次）。
10. 搭配质量尚无人工偏好评测；现有 95.5% 是检索品类指标，不是穿搭满意度。

## 6. 已知限制

- 当前订单表缺少独立售后字段，不能识别未体现在订单最终状态中的部分退款或线下退货。
- 基于关键词的服饰品类和子类识别仍可能误判，尤其是汉服套装、内衣/外穿背心和复合商品标题。
- 风格词、正式度、颜色协调仍以可解释规则为主，尚未经过人工标注集校准。
- Mytheresa 只有商品目录，没有本地搭配关系；不能把目录顺序解释为搭配监督信号。
- Polyvore 严格子集只覆盖引用完整的搭配，存在数据选择偏差。
- 个人订单商品通常无原始图片，初始语义能力依赖商品标题和规格文本。

## 7. 下一验收门槛

P0 回归与订单表预览已于 2026-08-04 通过：`compileall` 退出码 0、Pytest 47 passed、Ruff 通过；一次性数据库预览 `eligible = 374 / ineligible = 63 / unknown = 0`、`candidate = 111`。按顺序执行下一步，前一步失败时停止。

### P0：订单提交与自动嵌入验收（尚未执行）

使用专用测试用户选择 3～5 件衣物：

1. 提交一件无图商品，确认生成文字嵌入。
2. 上传一张实拍图，确认图片保存且嵌入类型切换为图像。
3. 尝试手工选择一条`交易关闭`记录，确认 API 返回 422 且数据库没有新增衣物。
4. 重复上传同一 Excel，确认复用同一批次，不重复创建衣物。
5. 在推荐中确认只出现当前用户衣柜商品。
6. 订单候选品类人工抽查：衬衫、半身裙、裤装、鞋、外套、配饰、汉服套装、内衣/背心、非服饰各至少 3 条，确认无新的误分类后再提交。

### P1：Mytheresa 安全接入（尚未执行）

1. 对当前主 SQLite 建立基线备份。
2. 补充适配 Mytheresa 多图片结构的像素解码审计 CLI；现有`audit_images`只适配 Garments2Look 单图路径。
3. 新 CLI 完成后重新执行 `compileall → Pytest → Ruff`，再检查 328,754 张图片。
4. 一次性数据库的故障注入、断点重跑、幂等和备份恢复演练。
5. 导入 62,457 件 Mytheresa 商品到同一一次性数据库并验收计数。
6. API 验收 women、men、girls、boys、baby、life 多受众查询及多图片接口。
7. 紧邻正式导入前再次备份主 SQLite，然后导入主库。
8. 在新目录构建 189,385 件 Polyvore + Mytheresa 合并嵌入和 FAISS 索引。

### P2：语义三 Agent 真实 API 验收（✅ 2026-08-06 已完成）

1. `DEEPSEEK_API_KEY` 配置于项目根 `.env`（已被 `.gitignore` 排除），工作流自动启用语义链路。
2. 确认 `mode=llm_semantic_multi_agent`，`request_signature`/`retrieval_plans`/`pool`/`critic` 字段完整，`llm_call_count=3`。
3. 8类代表性请求（面试/悲惨世界/周杰伦演唱会/美拉德/小雨通勤/高考/打篮球/海边度假）全部`accept`、推荐100%衣柜归属；7类无回退，“高考”请求Critic瞬时降级一次。
4. 无 key 时同一命令返回确定性链路，行为与改造前一致（CLI `--no-llm` 冒烟通过）。
5. 备选方案评分已补确定性 `score_outfit`（此前为 0）。
6. 证据：`artifacts/llm_acceptance.json`（8 请求原始 JSON）、`artifacts/llm_acceptance_summary.txt`（可读摘要）。
7. 剩余：Streamlit UI 展示语义输出字段（未做）。

详细命令见[后续工作](NEXT_STEPS.md)与[Mytheresa 数据接入说明](MYTHERESA_INTEGRATION.md)。

## 8. 作品集表述

推荐表述：

> 构建了一个本地优先的个人衣柜多 Agent 穿搭系统。项目使用 FashionCLIP 生成多模态表示，通过 LangGraph 编排 Planner、Stylist 和 Reviewer，并使用确定性约束引擎保证推荐只引用用户衣柜中的可追溯商品。已对 126,928 张商品图完成全量 GPU 向量化和检索评估，并实现多受众商品目录与购物订单衣柜的可审计接入流程。

不要表述：

- “训练了 FashionCLIP”——当前使用预训练模型，没有微调。
- “LLM 自动完成了全部搭配决策”——当前 Agent 后端是确定性实现。
- “Mytheresa 已完成全量接入”——目前只完成数据审计和代码实现。
- “订单已成功导入个人衣柜”——目前只有旧文件预览记录，最新严格规则尚未实际提交。
- “搭配质量达到 95.5%”——95.5% 是文本检索品类 Precision@10。

当前验证产物位于不提交 Git 的`artifacts/`。发布作品集前应导出不含本地绝对路径和个人订单信息的脱敏摘要，确保仓库读者可以核对关键数字。
