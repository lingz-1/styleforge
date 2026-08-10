# StyleForge 问题与解决记录

> 每次排查和修复按日期记录，每条包含现象、根因、修复与验证。验收证据和状态总览见[项目状态](PROJECT_STATUS.md)。

## 2026-08-10

### 16. 从仓库根目录启动 API 时无法导入`styleforge`

- **现象**：按旧文档执行`D:\anaconda\envs\style\python.exe -m uvicorn styleforge.api:app`，Importlib在加载应用时报告`ModuleNotFoundError: No module named 'styleforge'`。
- **根因**：后端已经移动到`apps/api/styleforge/`，Pytest通过项目配置识别包路径，但普通Python/Uvicorn进程从仓库根目录启动时不会自动把`apps/api`加入`sys.path`；启动文档仍保留迁移前命令。
- **修复**：根目录Uvicorn命令统一增加`--app-dir apps\api`；模块CLI先设置`$env:PYTHONPATH=(Resolve-Path ".\apps\api")`；Streamlit改用真实文件路径`apps\api\styleforge\ui.py`。同步修正README、本地部署和Mytheresa验收命令，并将原因写入开发过程记录。
- **验证**：本问题是运行入口文档错误，业务代码的最终回归仍为Pytest 189 passed、compileall和Ruff通过；修正文档后所有Uvicorn命令均显式包含`--app-dir apps\api`。

### 15. 主推荐缺少实时天气上下文，三个 Agent 无法依据环境事实工作

- **现象**：请求可以包含“明天、地点、户外”，但 Agent 1 没有结构化上下文需求，Agent 2/3也收不到温度、降水、湿度和风等事实。
- **根因**：`ContextPack.environment_context`只有预留字段，标准推荐图中没有 Context Router、外部 Tool Registry、天气 Provider 和工具调用轨迹。
- **修复**：Agent 1 契约新增`context_requirements.weather`；加入 typed Weather Tool、Open-Meteo Provider、Tool Registry和Context Router。天气成功后复用同一个 Agent 1 细化检索，再把相同事实传给 Agent 2/3；响应、Context Pack、trace与语义运行明细均保留事实。天气工具只返回事实，不产出确定性穿搭建议；当前实现不是MCP Server。
- **验证**：离线专项19 passed；全量 Pytest 189 passed；compileall、全项目 Ruff 和 Vue 生产构建通过。Pytest 仅保留 FastAPI TestClient 第三方弃用提示，Vite 仅保留现有大 chunk 与第三方 PURE 注释提示。

### 14. 旧 API 进程继续执行宽松契约，Agent 3 反复拒绝扩展任务

- **现象**：单品搭配仍返回 `needs_clarification` 和空 `sample_outfits`；中世纪衣橱缺口把零缺口当成必须澄清，并且 Agent 3 要求使用与当前请求无关的历史偏好。
- **根因**：失败进程启动于 00:17，而 Agent 2 完成契约与复审闭环在 00:22–00:23 才写入，00:27–00:29 的失败任务实际一直运行旧代码。与此同时，Agent 3 提示词没有明确阶段职责和零缺口边界，缺口硬校验也只验证 ID 边界，没有校验 `gaps` 是否等于 Agent 1 的 `missing_elements`。
- **修复**：契约升级为 `extension-three-agent-v3.1`；`/health` 返回 API 启动时间和扩展契约版本。完整搭配的 `reasoning` 改为必填；缺口硬校验要求 Agent 2 的覆盖证据只能来自 Agent 1，并让 `gaps` 与 Agent 1 `missing_elements` 一致。覆盖证据允许只展示相关子集，不能因未抄回全部已覆盖元素而失败。Agent 3 明确不得要求 Agent 1 直接给建议、不得强制使用无关历史、不得因用户询问缺口而虚构缺口。
- **验证**：当前默认衣橱的“黑色马甲怎么搭？”解析出唯一真实锚点 `P01034343`；“中世纪风格衣橱缺口”解析为 targeted、无需澄清。专项 43 passed；全量 Pytest 183 passed；compileall 与 Ruff 通过。

## 2026-08-09

### 13. 单品任务通过外层 Schema，却没有回答“怎么搭”

- **现象**：Agent 2 返回 `needs_clarification`、可搭配单品列表和空 `sample_outfits`；外层 JSON 校验通过，直到 Agent 3 才发现它没有回答“黑色马甲怎么搭”，随后工作流直接失败。
- **根因**：阶段输入输出只冻结了字段类型，没有冻结任务完成条件；Agent 1 的模型补充信息还能把缺少场合误升为阻塞条件；Agent 3 拒绝后没有重做闭环。
- **修复**：提示词升级为 `extension-three-agent-v2`；事实工具的 `needs_clarification` 成为唯一阻塞依据；单品 `completed` 强制要求锚点、兼容分组和至少一套包含锚点的完整搭配。Agent 2 Schema/完成条件失败可自修复一次，Agent 3 首次拒绝会触发一次 Agent 2 重做和 Agent 3 复审，全程没有确定性结果降级。
- **验证**：新增“缺场合误澄清 + 空 sample_outfits”修复用例和“Critic 拒绝后重做”用例；专项 13 passed、全量 Pytest 182 passed、编译和 Ruff 通过。真实 DeepSeek 验收因需要把 demo-user 衣橱上下文发送给外部服务，等待用户明确授权。

### 11. 扩展任务绕过三 Agent，并用确定性 Service 直接产出结果

- **现象**：主推荐由三 Agent 执行，但五类扩展业务被独立 Service 直接算出结果；前端还要求用户手选任务类型。未知知识会直接报“本地知识库未覆盖”，单品搭配和目标风格衣橱缺口没有按用户语义执行。
- **根因**：首次扩展实现把“不同输入输出”误解成“独立决策服务”，事实检索、业务判断和失败降级没有分层。
- **修复**：删除扩展 `TaskExecutionService` 和五类结果 Service；新增 `MultiTaskWorkflow`，所有扩展严格执行 `SemanticRetrieverAgent → ComposerAgent → CriticAgent`。确定性代码仅保留事实读取和硬校验，模型不可用或输出非法时直接失败并持久化。补充黑色马甲锚点、中世纪目标缺口知识和路由词；Web/小程序合并到主推荐自然语言入口。
- **验证**：编译通过、Ruff clean、Pytest 178 passed；五类扩展专项断言每次成功任务恰好三次模型调用且 trace 无 degraded，未配置 LLM 明确失败并持久化；Vue 生产构建和小程序脚本/JSON 检查通过。当时尚未清理的Schema v8快照中`demo-user`为2062件活跃衣橱；2026-08-09重建后的当前干净快照为2058件，两者不是同一数据库状态。

### 12. Agent 1 事实单品被误判越界，待补充状态无法落库

- **现象**：黑色马甲任务中，Agent 2 引用 `facts.wardrobe_matches` 内的 5 个衣橱 ID，却报“Agent 1 候选范围外”；衣橱缺口返回 `needs_clarification` 时又触发 `task_runs.status` CHECK 约束错误。
- **根因**：Agent 1 同时向提示词暴露 `wardrobe_matches` 和 `compatible_items_by_slot`，但 `candidate_item_ids` 只汇总后者；Schema v7 的任务状态约束也遗漏了合法终态 `needs_clarification`。
- **修复**：候选 ID 统一汇总 Agent 1 暴露的知识匹配、兼容分组和锚点事实，仍受当前衣橱白名单限制；数据库升级到 Schema v8，自动重建 `task_runs` 约束并保留原记录。
- **验证**：用户报出的5个ID全部进入Agent 1的25个候选集合；v7→v8迁移前后均为11条运行记录、2062件活跃衣橱。2062是迁移演练快照；后续2026-08-09干净重建快照为2058件。编译、Ruff和Pytest 180 passed。

### 10. 五类扩展任务从“只路由”补齐为完整业务（已被第 11 项架构替换）

- **现象**：Task Router 能区分六类请求，但除标准推荐外只返回占位子图，局部修改、知识建议、新品兼容和衣橱缺口没有统一输入、上下文、持久化、API 与双端界面。
- **根因**：P2 的目标只是建立安全路由骨架；P3/P4/P8 的共享领域结构和业务服务尚未落地。
- **修复**：新增 Context Pack、共享 Pydantic 输入、Markdown 知识检索、五类独立服务、`TaskExecutionService`、Schema v7 `task_runs`、`/tasks/execute` 与运行查询接口；Web 和小程序新增智能造型页。
- **细节修复**：实际被编辑的方案 ID 写入 Context Pack；新品完整搭配数不再等于仅展示的样例数；日常/商务缺口改为完整槽位判断；未知知识明确失败且保留可审计记录。
- **验证**：`compileall` 通过、Ruff clean、Pytest 175 passed、42 条路由评估 42/42；小程序新增脚本语法检查和 Web Vite 生产构建通过；真实 API 进程的健康检查、风格知识、衣橱缺口和运行记录查询烟测通过。

### 9. v3.3 Task Router 测试在 Pytest 收集阶段失败

- **现象**：P2 定向测试和全量测试均在收集 `tests/test_task_router.py` 时停止，报错：`'request' is a reserved name and cannot be used in @pytest.mark.parametrize`。
- **根因**：参数化测试把参数命名为 `request`，与 Pytest 内置 `request` fixture 的保留名称冲突；业务代码和 Task Router 尚未进入执行阶段。
- **修复**：把参数化字段及测试函数形参统一改为 `user_query`，调用逻辑不变。
- **后续发现**：42 条路由基线中“保留上衣和裤子，只换外套”首次被默认路由为推荐；局部修改规则只接受“换一/换双/换件/换掉”，未覆盖“换+目标槽位”。同时，评估 runner 从仓库根目录执行时因 `apps/api` 不在模块路径而无法导入 `styleforge`。
- **补充修复**：局部修改规则增加“换鞋/靴/外套/上衣/下装/裤子/裙子/包/配饰”；runner 从自身路径解析工作区并添加 `apps/api`，报告固定写入工作区 `artifacts/evaluation/`。
- **验证**：`compileall` 通过；P2/P2.5 定向测试 31 passed；全量 Pytest 165 passed；Ruff `All checks passed`；42 条路由基线准确率 100%，六类逐类准确率均为 100%，失败 0。

## 2026-08-06

### 8. 架构 v3.3 规划 + 后端移动 + 双端前端（Vue Web + 小程序）

- **背景**：用户要求参考《项目文档/参考目录.txt》与《扩展版方案.txt（v3.3-extension）》重排项目架构，并以"web 端和小程序端能正常运行"为目标。
- **实现**：
  1. 架构收口为 v3.3-plan.1（[architecture/ARCHITECTURE_PLAN.md](architecture/ARCHITECTURE_PLAN.md)）：Core + Extension 分层、Task Router / Context Router / Retriever / Validator / Memory / RAG 均为系统能力（不新增 Agent）；技术栈定稿（Vue 3 + Element Plus / 小程序 / PostgreSQL 后期 / Chroma RAG / Nginx，不用 Docker）。
  2. 后端移动：`styleforge/` → `apps/api/styleforge/`，保持 `from styleforge.*` import 不变（pyproject package path 指向 apps/api，pytest pythonpath）；修复 `config.py` 的 `WORKSPACE_ROOT`（向上查找 pyproject.toml，避免移到 apps/api 后路径错乱）。
  3. 新增接口：`POST /wardrobes/{user_id}/items/photo`（拍照/上传创建个人衣物 + 图像嵌入）、`PUT /wardrobes/{user_id}/items/{item_id}`（改信息重嵌入）；`services/personal_images.py` 提取图片保存 helper，新增 `services/wardrobe_item_service.py`。
  4. Vue Web（`apps/web/`）：Vite + Element Plus，衣柜（上传/编辑/补图/移出）、推荐（语义决策/评审）、订单导入、五维偏好；Vite 代理 `/api` → FastAPI。
  5. 小程序（`apps/miniprogram/`）：衣柜（点击补图/长按操作）、上传新衣物、订单导入、推荐、偏好；user_id 用 `wx.setStorageSync` 持久化保持登录；AppID 已填。
- **验证**：134 passed、ruff clean；Vite 运行于 5173、代理连通；后端新接口实测（创建 201 + embedding completed + 修改 200）。
- **修复**：小程序 WXML 不支持模板字符串（`${}`）与函数调用（`.join/.toFixed`）——import 提交按钮与 recommend 展示改为预格式化字段（`score_display`/`unique_mood_text` 等）。
- **待办**：小程序真机预览因校园网 AP 隔离未能连通（开发用模拟器或 USB 真机调试；公网部署后无需调网络）；P1b Repository Protocol、P1c 三层 Schema 未做。

### 7. 落地五维统一评估框架（评估方案.txt v1.1）

- **现象**：按《评估方案.txt》实现五维统一评估框架——让三个 Agent 共享同一评价口径，而非各自按自己的标准理解"什么是好的推荐"。
- **实现**：
  1. 新建 [core/rubric.py](../apps/api/styleforge/core/rubric.py)：五维定义（需求还原/请求特异/搭配协调/实穿/新鲜感）+ 默认权重 25/25/20/15/15 + `normalize_weights`（迭代式下限保护 0.05，sum=1）+ 统一 `rubric_text`。
  2. [llm/schema.py](../apps/api/styleforge/llm/schema.py)：`RequestSignature` 新增 `explicit_style`（单独保留明确风格词）；`DimensionScores` 第三维 `coordination` → `outfit_coordination`（单品协调→搭配协调）。
  3. 三个 Agent 的 Prompt 注入统一 Rubric（Agent 1 为五维准备信息、Agent 2 作为组合决策目标、Agent 3 正式评分）；few-shot 补 `explicit_style` 字段后真实链路正确输出 `['美拉德']`。
  4. 用户权重配置：`user_preferences` 表启用（新增 [user_preferences_repository.py](../apps/api/styleforge/repositories/user_preferences_repository.py)），`evaluation_profile.weights` 读写；workflow 读取权重并贯穿三个节点与 `_critic_score`。
  5. API `GET/PUT /preferences/{user_id}/evaluation`；UI 侧边栏"我的偏好"五维滑杆；`_render_critic` 更新维度键/标签。
- **验证**：134 passed（新增 rubric/user_preferences 测试）；真实链路"悲惨世界"首选 score 84.0 = 用户权重加权结果（9×0.2+8×0.15+9×0.3+8×0.25+7×0.1=8.4×10），证明权重贯穿；"美拉德"请求 `explicit_style=['美拉德']`。
- **修复**：`--no-llm` 在 `.env` 有 key 时仍重建 LLM 客户端（CLI 冒烟暴露）——`main()` 用 `dataclasses.replace(settings, llm_enabled=False)` 强制禁用。

## 2026-08-06

### 6. v3.2.1 语义三 Agent 真实 DeepSeek API 批量验收

- **现象**：需要确认语义链路在真实 API 下对多种输入的输出是否符合 v3.2.1 方案第八节的场景预期。
- **过程**：在项目根 `.env` 配置 `DEEPSEEK_API_KEY`（`.gitignore` 已排除），用 `artifacts/llm_acceptance_probe.py` 对 demo-user 衣柜批量跑 8 类代表性请求（面试 / 悲惨世界 / 周杰伦演唱会 / 美拉德 / 小雨通勤 / 高考 / 打篮球 / 海边度假），每个请求 3 次真实 LLM 调用 + 真实 FashionCLIP 检索。
- **结果**：8请求全部`accept`且推荐单品100%来自demo-user衣柜；其中7请求正常3次LLM调用且无回退，“高考”请求Critic一次瞬时API失败并降级。`request_signature`语义准确（"悲惨世界"→悲壮/克制/复古文学感；"美拉德"→温暖/复古/秋日+大地色系检索计划）；"小雨"→防雨/防滑鞋检索；"打篮球"→运动硬约束。
- **修复 1（备选方案 0 分）**：critic 的冻结结构只对 `outfit_assessment.outfit_id` 给五维分，其余 4 套备选推荐在 UI 显示 0.0 分。修复：`workflow/graph.py` 新增 `_proposal_score`——首选保留 LLM 五维加权分，备选补确定性 `score_outfit`，保证每套推荐都有可解释分数。
- **观察（非修复）**：请求 6（高考）出现一次 critic 瞬时降级（`llm_call_count=2`，评审理由回退为"确定性评审"）——API 瞬时失败时降级机制按设计工作，结果仍正确；request_signature 识别了"幸运红"心理仪式但未落到单品。
- **验证**：122 passed（新增备选分断言）、ruff clean。证据见 `artifacts/llm_acceptance.json` 与 `artifacts/llm_acceptance_summary.txt`。

## 2026-08-05

### 5. 运动请求推荐不合理（"打球/运动搭汉服"）

- **现象**：请求"去运动/打球"，系统搭配汉服对襟衫、马面裙/百迭裙、针织衫、高跟鞋、小皮鞋，场景匹配分低。
- **根因**（4 层）：
  1. `_find_occasion` 只把场合存为标签，未转成硬约束；sport 的 `required/excluded_subtypes` 全空，裙子、衬衫照常进候选。
  2. `SUBTYPE_RULES` 匹配词全英文，中文商品（百迭裙/马面裙/对襟衫/运动鞋）匹配不到任何子类，排除约束对中文商品失效。
  3. 汉服吊带含"吊带"被归为 `tank_top`，与运动背心同子类，无法用子类排除。
  4. "打球/篮球/羽毛球"等词不在 sport 触发别名里，"打球穿什么"被当成 daily，所有运动约束完全不生效。
- **修复**：
  1. [request_parser.py](../apps/api/styleforge/core/request_parser.py) 新增 `OCCASION_HARD_CONSTRAINTS`：sport 必选 top=`t_shirt/tank_top/hoodie`、footwear=`sneakers`；排除 top=`shirt`、bottom 全部裙型、footwear 非运动鞋。场合注入的约束不扩充 `required_slots`（避免污染"运动内衣"等特殊槽位请求）。
  2. [garment_attributes.py](../apps/api/styleforge/core/garment_attributes.py)：给 `SUBTYPE_RULES` 补中文关键词（衬衫/对襟衫、T恤、毛衣、背心、短裤、牛仔裤、百褶裙、运动鞋、高跟鞋、平底鞋等），子类约束对中文商品生效。
  3. [schemas.py](../apps/api/styleforge/core/schemas.py) 的 `TaskSpec` 新增 `excluded_name_keywords`；[candidate_generation.py](../apps/api/styleforge/tools/candidate_generation.py) 按商品名过滤；sport 注入汉服特征词（汉服/汉元素/国风/宋制/唐制/明制/褙子/比甲/齐胸/马面/汉家）。
  4. 扩充 sport 触发词：打球/篮球/足球/羽毛球/乒乓球/网球/排球/锻炼/workout/play ball。
- **验证**：demo-user "去运动/打球" 推荐为 T恤 + 裤/短裤 + 运动鞋，无汉服/裙子/针织衫/非运动鞋；候选排除 107 件不合适单品。新增 9 个回归测试。

### 4. UI 换问题后搭配不更新

- **现象**：修改"今天想怎么穿"后点生成，界面仍显示旧结果或无明显更新。
- **根因**：推荐结果只渲染在按钮点击的那一次 rerun，未持久化；改问题不点按钮时旧结果继续显示且无任何提示。
- **修复**：[ui.py](../apps/api/styleforge/ui.py) 将结果存入 `st.session_state["recommend_result"]` 并绑定请求文本；显示"当前搭配基于：×××"；改问题未重新生成时提示"点击「生成搭配」更新结果"。
- **验证**：56 passed；Streamlit 刷新生效。

### 3. API 422 Audience must be confirmed

- **现象**：提交订单时接口返回 `422 Audience must be confirmed for row <row_id>`。
- **根因**：商品名不含性别词时 `predicted_audience` 为空；提交时用户未选性别 → `audience` 为空，仓库层 `_selected_attributes` 拒绝。
- **修复**：[ui.py](../apps/api/styleforge/ui.py) 提交前先拦截：勾选记录中人群为空且未选性别时提示并列出商品名；选了性别则用性别兜底。
- **验证**：56 passed。

### 2. 订单提交强制逐条填品类/目标人群

- **现象**：提交前提示"所选记录必须填写品类和目标人群"，需要逐条填写。
- **修复**：[ui.py](../apps/api/styleforge/ui.py) 品类完全用系统预测值，未识别品类的记录自动跳过并提示；"目标人群"改为"你的性别"（可选），与预测值兜底（默认 women）；报错改为列出具体商品名。
- **验证**：56 passed。

## 2026-08-04

### 1. 完整订单表验收与误分类修复

- **现象**：订单预览候选 126 条中含 15 条明确非服饰（卫生巾、电脑 CPU 套装、棉花娃娃娃衣、修眉刀、睡袋床单、拼豆 DIY、刻刀、美甲工具、消毒液、首饰盒、毛绒挂件钥匙扣、cos 眼镜框道具）；内裤误归 `pants`，马面裙西装半身裙误归 `blazer`。
- **根因**：`NON_FASHION_PATTERN` 词表不全；`outfit_set` 的"套装"过宽；`blazer` 的"西装"过宽；内裤无 `underwear` 分类。
- **修复**：[order_import.py](../apps/api/styleforge/services/order_import.py) 扩充 `NON_FASHION_PATTERN`；新增 `underwear/underpants`（内裤/三角裤/平角裤/安全裤）；收紧 `blazer` 为"西装外套/西服外套/西服"；新增 12 个回归测试。
- **验证**：候选 126→111；一次性数据库验收 `eligible=374/ineligible=63/unknown=0`、金额逐行不继承、原始订单号 SHA-256 无明文、51 个多商品订单状态继承 0 异常。

## 未解决 / 待排查

- **personal-user 衣柜为空**：`catalog_items` 中已有 111 件 `personal-*` 来源商品，但 `wardrobe_items` 中 `personal-user` 没有 `active=1` 记录，该用户推荐一直无解。疑似 UI 提交的衣物未挂到预期用户 ID 下，或使用了其他 user_id，待排查。
