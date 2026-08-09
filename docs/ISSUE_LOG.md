# StyleForge 问题与解决记录

> 每次排查和修复按日期记录，每条包含现象、根因、修复与验证。验收证据和状态总览见[项目状态](PROJECT_STATUS.md)。最新代码回归：Pytest 134 passed，Ruff 通过。

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
  1. 新建 [core/rubric.py](styleforge/core/rubric.py)：五维定义（需求还原/请求特异/搭配协调/实穿/新鲜感）+ 默认权重 25/25/20/15/15 + `normalize_weights`（迭代式下限保护 0.05，sum=1）+ 统一 `rubric_text`。
  2. [llm/schema.py](styleforge/llm/schema.py)：`RequestSignature` 新增 `explicit_style`（单独保留明确风格词）；`DimensionScores` 第三维 `coordination` → `outfit_coordination`（单品协调→搭配协调）。
  3. 三个 Agent 的 Prompt 注入统一 Rubric（Agent 1 为五维准备信息、Agent 2 作为组合决策目标、Agent 3 正式评分）；few-shot 补 `explicit_style` 字段后真实链路正确输出 `['美拉德']`。
  4. 用户权重配置：`user_preferences` 表启用（新增 [user_preferences_repository.py](styleforge/repositories/user_preferences_repository.py)），`evaluation_profile.weights` 读写；workflow 读取权重并贯穿三个节点与 `_critic_score`。
  5. API `GET/PUT /preferences/{user_id}/evaluation`；UI 侧边栏"我的偏好"五维滑杆；`_render_critic` 更新维度键/标签。
- **验证**：134 passed（新增 rubric/user_preferences 测试）；真实链路"悲惨世界"首选 score 84.0 = 用户权重加权结果（9×0.2+8×0.15+9×0.3+8×0.25+7×0.1=8.4×10），证明权重贯穿；"美拉德"请求 `explicit_style=['美拉德']`。
- **修复**：`--no-llm` 在 `.env` 有 key 时仍重建 LLM 客户端（CLI 冒烟暴露）——`main()` 用 `dataclasses.replace(settings, llm_enabled=False)` 强制禁用。

## 2026-08-06

### 6. v3.2.1 语义三 Agent 真实 DeepSeek API 批量验收

- **现象**：需要确认语义链路在真实 API 下对多种输入的输出是否符合 v3.2.1 方案第八节的场景预期。
- **过程**：在项目根 `.env` 配置 `DEEPSEEK_API_KEY`（`.gitignore` 已排除），用 `artifacts/llm_acceptance_probe.py` 对 demo-user 衣柜批量跑 8 类代表性请求（面试 / 悲惨世界 / 周杰伦演唱会 / 美拉德 / 小雨通勤 / 高考 / 打篮球 / 海边度假），每个请求 3 次真实 LLM 调用 + 真实 FashionCLIP 检索。
- **结果**：8 请求全部 `accept`、正常 3 次 LLM 调用、0 回退、推荐单品 100% 来自 demo-user 衣柜。`request_signature` 语义准确（"悲惨世界"→悲壮/克制/复古文学感，与方案示例几乎一致；"美拉德"→温暖/复古/秋日 + 大地色系检索计划，证明无映射表也能理解风格词）；"小雨"→防雨/防滑鞋检索；"打篮球"→运动硬约束且 critic 指出"帆布鞋不适合篮球"。
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
  1. [request_parser.py](styleforge/core/request_parser.py) 新增 `OCCASION_HARD_CONSTRAINTS`：sport 必选 top=`t_shirt/tank_top/hoodie`、footwear=`sneakers`；排除 top=`shirt`、bottom 全部裙型、footwear 非运动鞋。场合注入的约束不扩充 `required_slots`（避免污染"运动内衣"等特殊槽位请求）。
  2. [garment_attributes.py](styleforge/core/garment_attributes.py)：给 `SUBTYPE_RULES` 补中文关键词（衬衫/对襟衫、T恤、毛衣、背心、短裤、牛仔裤、百褶裙、运动鞋、高跟鞋、平底鞋等），子类约束对中文商品生效。
  3. [schemas.py](styleforge/core/schemas.py) 的 `TaskSpec` 新增 `excluded_name_keywords`；[candidate_generation.py](styleforge/tools/candidate_generation.py) 按商品名过滤；sport 注入汉服特征词（汉服/汉元素/国风/宋制/唐制/明制/褙子/比甲/齐胸/马面/汉家）。
  4. 扩充 sport 触发词：打球/篮球/足球/羽毛球/乒乓球/网球/排球/锻炼/workout/play ball。
- **验证**：demo-user "去运动/打球" 推荐为 T恤 + 裤/短裤 + 运动鞋，无汉服/裙子/针织衫/非运动鞋；候选排除 107 件不合适单品。新增 9 个回归测试。

### 4. UI 换问题后搭配不更新

- **现象**：修改"今天想怎么穿"后点生成，界面仍显示旧结果或无明显更新。
- **根因**：推荐结果只渲染在按钮点击的那一次 rerun，未持久化；改问题不点按钮时旧结果继续显示且无任何提示。
- **修复**：[ui.py](styleforge/ui.py) 将结果存入 `st.session_state["recommend_result"]` 并绑定请求文本；显示"当前搭配基于：×××"；改问题未重新生成时提示"点击「生成搭配」更新结果"。
- **验证**：56 passed；Streamlit 刷新生效。

### 3. API 422 Audience must be confirmed

- **现象**：提交订单时接口返回 `422 Audience must be confirmed for row <row_id>`。
- **根因**：商品名不含性别词时 `predicted_audience` 为空；提交时用户未选性别 → `audience` 为空，仓库层 `_selected_attributes` 拒绝。
- **修复**：[ui.py](styleforge/ui.py) 提交前先拦截：勾选记录中人群为空且未选性别时提示并列出商品名；选了性别则用性别兜底。
- **验证**：56 passed。

### 2. 订单提交强制逐条填品类/目标人群

- **现象**：提交前提示"所选记录必须填写品类和目标人群"，需要逐条填写。
- **修复**：[ui.py](styleforge/ui.py) 品类完全用系统预测值，未识别品类的记录自动跳过并提示；"目标人群"改为"你的性别"（可选），与预测值兜底（默认 women）；报错改为列出具体商品名。
- **验证**：56 passed。

## 2026-08-04

### 1. 完整订单表验收与误分类修复

- **现象**：订单预览候选 126 条中含 15 条明确非服饰（卫生巾、电脑 CPU 套装、棉花娃娃娃衣、修眉刀、睡袋床单、拼豆 DIY、刻刀、美甲工具、消毒液、首饰盒、毛绒挂件钥匙扣、cos 眼镜框道具）；内裤误归 `pants`，马面裙西装半身裙误归 `blazer`。
- **根因**：`NON_FASHION_PATTERN` 词表不全；`outfit_set` 的"套装"过宽；`blazer` 的"西装"过宽；内裤无 `underwear` 分类。
- **修复**：[order_import.py](styleforge/services/order_import.py) 扩充 `NON_FASHION_PATTERN`；新增 `underwear/underpants`（内裤/三角裤/平角裤/安全裤）；收紧 `blazer` 为"西装外套/西服外套/西服"；新增 12 个回归测试。
- **验证**：候选 126→111；一次性数据库验收 `eligible=374/ineligible=63/unknown=0`、金额逐行不继承、原始订单号 SHA-256 无明文、51 个多商品订单状态继承 0 异常。

## 未解决 / 待排查

- **personal-user 衣柜为空**：`catalog_items` 中已有 111 件 `personal-*` 来源商品，但 `wardrobe_items` 中 `personal-user` 没有 `active=1` 记录，该用户推荐一直无解。疑似 UI 提交的衣物未挂到预期用户 ID 下，或使用了其他 user_id，待排查。
