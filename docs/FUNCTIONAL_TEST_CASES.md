# StyleForge 功能测试用例手册

> 黑盒功能测试用例，覆盖 `docs/TESTING_AND_EVALUATION.md`（评估计划总文档）与
> p-outfit 独立 LLM 评估计划中的各项评估计划与功能测试。每个用例固定四要素：
> **输入 / 动作 / 测试范围 / 判定方法**。判定方法均为可执行验证项（命令或可观察行为）。
>
> 执行方式：单测/门禁见各用例引用的 pytest 文件；涉及真实 LLM 的用例走
> `D:\anaconda\envs\style\python.exe -m evals.runners.evaluate_*`。

---

## 一、穿搭推荐主功能

### 用例一：基础搭配推荐
- **输入**：请帮我搭配一套休闲度假风的衣服
- **动作**：用户一次询问
- **测试范围**：系统主功能——根据用户输入输出合适的搭配
- **判定方法**：系统输出符合用户需求（休闲度假）的完整搭配（至少含核心槽位与鞋履）；
  若衣柜中没有此类衣物，则正常给用户对应的提示反馈（缺品类清单 / 兜底推荐），而不是
  报错或无输出。验证：`pytest -q`，主推荐链路输出 `recommendations` 与 `decision`。

### 用例二：空衣柜
- **输入**：（新建用户，衣柜为空）请帮我搭配一套通勤穿搭
- **动作**：用户一次询问
- **测试范围**：主功能的边界——衣柜无任何单品
- **判定方法**：系统返回 `infeasible`，列出缺失槽位 `top/bottom/footwear`，
  且**不加载 FashionCLIP**（无嵌入计算）。见 `docs/TESTING_AND_EVALUATION.md §3 空衣柜`。

### 用例三：衣柜缺单个品类
- **输入**：请推荐一套上班穿搭，衬衫配西裤（衣柜有上衣长裤但无鞋履）
- **动作**：用户一次询问
- **测试范围**：缺品类时的提示与兜底反馈
- **判定方法**：系统识别 `footwear` 缺失并给出明确提示（缺口清单 / `wardrobe_gap`
  反馈），或走 best_effort 兜底仍返回可选方案，不静默失败。门禁：
  `tests/test_p_outfit_eval.py::test_pipeline_wardrobe_gap_best_effort`（E 组）。

### 用例四：未配置 LLM
- **输入**：（无 API key / LLM 不可用）请帮我搭一套衣服
- **动作**：用户一次询问
- **测试范围**：降级路径——LLM 不可用时不得编造结果
- **判定方法**：系统返回**失败**而非确定性结果，且不崩溃；记忆提炼等依赖 LLM 的
  环节优雅跳过、不落任何证据。门禁：`tests/test_session_multiturn.py::test_no_llm_skips_memory_extraction_gracefully`。

### 用例五：结果集互不重复
- **输入**：请推荐一套日常通勤搭配
- **动作**：用户一次询问，查看全部返回方案
- **测试范围**：结果质量——方案间的区分度
- **判定方法**：返回的多个方案间优先不重复单品；重复率低于既定阈值。见
  `docs/TESTING_AND_EVALUATION.md §1`（结果集优先选择互不重复的方案）。

---

## 二、解析与约束

### 用例六：面试＋硬子类
- **输入**：需要正式但有年轻感的上班穿搭，衬衫配半身裙和乐福鞋，偏灰色或蓝色，不要红色，不要高跟鞋
- **动作**：用户一次询问
- **测试范围**：核心槽位解析、硬子类匹配、否定约束、多偏好合成
- **判定方法**：`top→shirt`、`bottom→skirt`、`footwear→loafers`；
  排除 `high_heels` 与红色；方案间优先不重复单品。见
  `docs/TESTING_AND_EVALUATION.md §3 面试＋硬子类`。

### 用例七：泛称多配饰
- **输入**：晚上约会，想穿中长连衣裙和平底鞋，再搭配一些配饰
- **动作**：用户一次询问
- **测试范围**：泛称配饰的展开与多槽位补全
- **判定方法**：必需槽位包含 `accessory_1`、`accessory_2`；两个配饰 ID 不相同；
  UI 展示连衣裙、鞋与两件配饰图片。见 `docs/TESTING_AND_EVALUATION.md §3 泛称多配饰`。

### 用例八：指定配饰子类
- **输入**：衬衫配西裤和乐福鞋，搭配金属腕表和托特包，不要运动鞋
- **动作**：用户一次询问
- **测试范围**：配饰子类（`metal_watch`/`tote_bag`）解析与否定排除
- **判定方法**：腕表解析为 `metal_watch`、包解析为 `tote_bag`，排除 `sneakers`。
  见 `docs/TESTING_AND_EVALUATION.md §3 指定配饰子类`。

### 用例九：中英文混合子类
- **输入**：黑色 blazer 配牛仔裤和白色 sneakers
- **动作**：用户一次询问
- **测试范围**：中英文混排输入的子类解析
- **判定方法**：`blazer→西装外套/外衣`、`sneakers→运动鞋`、`jeans→长裤` 正确落槽；
  不产生 `other` 槽位。评估集缺口见 `docs/TESTING_AND_EVALUATION.md §5`。

### 用例十：否定约束排除
- **输入**：（搭配请求内）不要 X / 避免 Y / 不要红色
- **动作**：用户一次询问
- **测试范围**：否定词对候选集合的过滤
- **判定方法**：被否定品类/颜色/单品不出现在任何方案中；硬约束失败候选不被 Stylist
  选中。见 `docs/TESTING_AND_EVALUATION.md §1`。

### 用例十一：半身裙与裤子一级品类
- **输入**：我要一条半身裙 ／ 我要一条裤子
- **动作**：用户一次询问
- **测试范围**：一级品类（半身裙 vs 裤子）的槽位归属
- **判定方法**：半身裙解析为 `bottom→skirt`、裤子解析为 `bottom→pants`，槽位一致、
  子类正确。见 `docs/TESTING_AND_EVALUATION.md §1`。

---

## 三、任务路由

### 用例十二：常规推荐路由
- **输入**：请帮我搭配一套日常穿搭
- **动作**：用户一次询问
- **测试范围**：Task Router 六类分类之一——新需求推荐
- **判定方法**：路由为 `outfit_recommend`，走三 Agent 完整推荐链（3 次 LLM 调用）。

### 用例十三：修改类路由
- **输入**：把外套换成风衣
- **动作**：多轮对话中一轮修改
- **测试范围**：修改意图识别（`换` + 单品替换）
- **判定方法**：路由为 `modify`（换单品），槽位锁定、仅替换指定品类。
  见 `docs/TESTING_AND_EVALUATION.md §6`（`换成/换到/换为` 已修复的盲区）。

### 用例十四：槽位替换路由
- **输入**：换成乐福鞋
- **动作**：一轮修改
- **测试范围**：槽位替换识别
- **判定方法**：路由为 `modify`，锁定当前搭配，仅替换 `footwear` 槽位。

### 用例十五：缺补类路由
- **输入**：帮我看看该补点什么颜色
- **动作**：用户一次询问
- **测试范围**：缺口/补全意图识别
- **判定方法**：路由为缺口类（`gap`），返回缺失品类/颜色清单。见
  `docs/TESTING_AND_EVALUATION.md §6`（`该补(?:点)?什么` 已修复的盲区）。

### 用例十六：负反馈路由
- **输入**：这件衬衫不好看
- **动作**：用户一次询问
- **测试范围**：单品级负反馈识别
- **判定方法**：路由为负反馈/调整类，识别 `衬衫` 为待调整目标。见
  `docs/TESTING_AND_EVALUATION.md §6`。

### 用例十七：路由分类准确性（批量评估）
- **输入**：`evals/cases/task_routing.json`（60 条，六类各 10 条）
- **动作**：批量运行路由评估
- **测试范围**：Task Router 整体与逐类准确率
- **判定方法**：`evaluate_task_routing` 输出准确率 ≥ 基线（2026-08-12 基线 100%）；
  门禁 `tests/test_task_routing_eval.py` 锁 60 例计数与逐类 100%。见
  `docs/TESTING_AND_EVALUATION.md §6`。

---

## 四、多轮对话

### 用例十八：一轮推荐后的整体调整
- **输入**：（首轮推荐后）这套太严肃了
- **动作**：follow_up 一轮
- **测试范围**：整体性 follow_up 识别与重做
- **判定方法**：路由为整体调整（session_follow_up），在**当前推荐基础上**重做，不
  丢失会话上下文。门禁：`tests/test_session_multiturn.py::test_negative_feedback_then_footwear_swap`。

### 用例十九：一轮推荐后的换单品
- **输入**：（首轮推荐后）换成乐福鞋
- **动作**：follow_up 一轮
- **测试范围**：槽位锁定替换
- **判定方法**：仅替换 `footwear`，上一轮搭配的其余单品（如连衣裙）保持锁定。

### 用例二十：新场景不被 follow_up 吞掉
- **输入**：推荐 → 换成外套 →（新请求）帮我搭一套婚礼穿搭
- **动作**：多轮混合链
- **测试范围**：fresh 场景在活跃会话内的路由保留
- **判定方法**：婚礼请求路由为 `recommend`（新计划），不被上一轮 follow_up 改写吞掉；
  新计划成为后续修改的基线，上一轮的 blazer 不泄漏进婚礼计划。门禁：
  `tests/test_session_multiturn.py::test_mixed_chain_fresh_scene_does_not_rewrite`。

### 用例二十一：负反馈再换鞋的链式修改
- **输入**：推荐 → 这套太严肃了 → 换成乐福鞋
- **动作**：两轮 follow_up
- **测试范围**：修改链逐轮携带最新搭配
- **判定方法**：否定反馈走整体调整，下一轮换鞋锁定连衣裙，每轮修改基于上一轮结果。

### 用例二十二：会话持久化
- **输入**：完成一轮多轮对话后重启应用，再次进入
- **动作**：会话中断与恢复
- **测试范围**：会话持久化能力
- **判定方法**：历史多轮上下文（之前的搭配与调整链）可恢复，后续修改基于恢复的会话。
  见 `docs/SESSION_CHAT_MEMORY.md`（已实现，2026-08-12）。

### 用例二十三：多轮上下文用户隔离
- **输入**：用户 A 的会话内容与用户 B 完全隔离
- **动作**：两个用户各自多轮对话
- **测试范围**：会话与记忆的跨用户隔离
- **判定方法**：A 的推荐链/偏好/记忆不出现在 B 的任何请求中；越权访问返回 404。
  门禁：`tests/test_memory_generalization.py::test_preference_memory_is_isolated_between_users`、
  `tests/test_extended_task_api.py`（越权 404）。

---

## 五、记忆系统

### 用例二十四：长期偏好记忆沉淀
- **输入**：（日常对话中）我平时就爱穿牛仔面料
- **动作**：多轮对话自然发生
- **测试范围**：用户长期记忆——从对话提取并沉淀偏好
- **判定方法**：偏好（category=牛仔）进入长期记忆，后续推荐可引用；提取无崩溃、
  无虚构证据。见 `docs/TESTING_AND_EVALUATION.md §9` 与 `docs/SESSION_CHAT_MEMORY.md`。

### 用例二十五：记忆提炼评估（批量）
- **输入**：`evals/cases/memory_extraction.json`（33 例口语化请求）
- **动作**：批量提炼
- **测试范围**：Evidence 级 P/R/F1、极性/scope 一致率
- **判定方法**：F1 ≥ 0.85（基线 0.847~0.921）；空案例零误报；门禁
  `tests/test_memory_extraction_eval.py` 锁 33 例计数与关键规则覆盖。见
  `docs/TESTING_AND_EVALUATION.md §9`。

### 用例二十六：无 LLM 优雅跳过记忆
- **输入**：（无 LLM）进行一轮对话
- **动作**：用户询问
- **测试范围**：记忆系统的降级健壮性
- **判定方法**：任务正常完成，记忆提炼优雅跳过、不落任何证据、不崩溃不虚构。门禁：
  `tests/test_session_multiturn.py::test_no_llm_skips_memory_extraction_gracefully`。

### 用例二十七：记忆合并幂等
- **输入**：对同一批证据连续执行两次 consolidation
- **动作**：两次调用
- **测试范围**：consolidation 幂等性
- **判定方法**：第二次为 no-op（不重复合并）；正负极性矛盾的两条证据**都保留**（矛盾
  是数据不是重复）。门禁：`tests/test_memory_consolidation.py`。

### 用例二十八：记忆用户隔离
- **输入**：u1 的事件/证据/偏好 与 u2 完全隔离
- **动作**：两用户分别产生偏好
- **测试范围**：跨用户不串数据
- **判定方法**：u1 的记忆不出现在 u2；跨用户矛盾不打 `conflict_with` 标记。门禁：
  `tests/test_memory_generalization.py::test_preference_memory_is_isolated_between_users`。

### 用例二十九：偏好管理页
- **输入**：进入偏好管理页，查看/编辑/删除某条偏好
- **动作**：用户在管理页操作
- **测试范围**：长期记忆的可管理性（用户可修正系统沉淀的错误偏好）
- **判定方法**：页面展示全部已沉淀偏好（维度/属性/值/极性）；编辑与删除后，后续推荐
  反映变更。见 `docs/SESSION_CHAT_MEMORY.md`（偏好管理页，已实现）。

---

## 六、扩展业务

### 用例三十：共享上下文
- **输入**：一次请求同时携带请求文本、衣橱统计、五维偏好
- **动作**：用户一次询问
- **测试范围**：Context Pack——上下文注入三 Agent
- **判定方法**：请求、衣橱统计、五维偏好进入共享上下文并传递给 Agent。
  门禁：`tests/test_context_pack.py`。见 `docs/TESTING_AND_EVALUATION.md §7`。

### 用例三十一：本地知识检索
- **输入**：请求涉及本地知识库中的概念/规则
- **动作**：用户一次询问
- **测试范围**：知识检索命中与来源标注
- **判定方法**：检索到相关分段，附来源；命中质量稳定。门禁：
  `tests/test_knowledge_retriever.py`。见 `docs/TESTING_AND_EVALUATION.md §7`。

### 用例三十二：单品锚点
- **输入**：（扩展任务）围绕指定单品搭配
- **动作**：用户一次询问
- **测试范围**：五类扩展业务之一——单品锚点
- **判定方法**：生成方案锚定指定单品；验证真实三次模型调用、无 degraded trace。
  门禁：`tests/test_extended_tasks.py`。见 `docs/TESTING_AND_EVALUATION.md §7`。

### 用例三十三：新品不落库
- **输入**：（扩展任务）涉及候选新品
- **动作**：用户一次询问
- **测试范围**：数据边界——候选新品不得写回主库
- **判定方法**：新品不落库；Agent 2 引用衣橱外 ID、锁定槽位被改、证据来源越界时硬失败。
  见 `docs/TESTING_AND_EVALUATION.md §7`。

### 用例三十四：目标风格缺口与失败持久化
- **输入**：（扩展任务）目标风格衣柜缺口 / 任务失败
- **动作**：用户一次询问
- **测试范围**：缺口清单与失败记录
- **判定方法**：输出目标风格缺口清单；失败任务持久化且可重试；Agent 3 拒绝时不发布
  草稿。门禁：`tests/test_extended_tasks.py`。见 `docs/TESTING_AND_EVALUATION.md §7`。

---

## 七、天气上下文

### 用例三十五：天气事实注入
- **输入**：帮我搭一套明天上海的穿搭（携带日期/地点）
- **动作**：用户一次询问
- **测试范围**：地点/日期解析 + 逐日天气事实映射
- **判定方法**：解析地点与日期，按日映射天气事实（含湿度聚合）供三 Agent 共享；天气
  工具只调用一次。门禁：`tests/test_weather_tool.py`、
  `tests/test_workflow_llm.py::test_workflow_weather_context_is_shared_with_three_agents`。

### 用例三十六：天气 Provider 失败
- **输入**：（天气 Provider 返回错误）带地点搭配请求
- **动作**：用户一次询问
- **测试范围**：外部服务失败降级
- **判定方法**：Provider 失败时优雅降级（默认/兜底事实），不崩溃、不影响推荐主流程。
  门禁：`tests/test_weather_tool.py`（Provider 失败分支）。

### 用例三十七：默认地点
- **输入**：帮我搭一套明天的穿搭（无地点）
- **动作**：用户一次询问
- **测试范围**：默认地点解析
- **判定方法**：使用默认地点解析天气；不要求用户提供地点。门禁：
  `tests/test_weather_tool.py`（默认地点）。

### 用例三十八：普通请求不增 LLM 成本
- **输入**：无地点无日期的普通搭配请求
- **动作**：用户一次询问
- **测试范围**：天气上下文不得无条件加成本
- **判定方法**：普通请求仍为**三次 LLM 调用**，Context Router 不因天气存在而增加调用。
  门禁：`tests/test_workflow_llm.py`。见 `docs/TESTING_AND_EVALUATION.md §8`。

---

## 八、衣橱与订单

### 用例三十九：订单导入式衣橱
- **输入**：导入购物订单（含多商品子行）到衣橱
- **动作**：一次导入操作
- **测试范围**：订单 → 衣橱数据形态（弱数据，无实拍图）
- **判定方法**：订单商品进入衣橱，`image_status=unbound`（无图）；多商品子行继承订单
  状态；导入接口不能通过手工属性覆盖绕过准入门槛。见
  `docs/TESTING_AND_EVALUATION.md §2` 与 p-outfit 计划实验 A。

### 用例四十：图片导入式衣橱
- **输入**：导入含实拍图的单品
- **动作**：一次导入操作
- **测试范围**：图片 → 衣橱数据形态（强数据，含图像嵌入）
- **判定方法**：商品带 `image_status=available`，可走图像嵌入与 Gemini 识别增强。见
  p-outfit 计划实验 B。

### 用例四十一：订单状态继承
- **输入**：导入含未收货 / 已关闭交易的订单
- **动作**：一次导入操作
- **测试范围**：交易状态对衣橱准入的过滤
- **判定方法**：交易关闭/未收货的订单商品被排除；成功订单出现退款信号时排除。
  见 `docs/TESTING_AND_EVALUATION.md §2`。

### 用例四十二：退款信号排除
- **输入**：一笔已确认订单随后出现退款记录
- **动作**：订单状态变更
- **测试范围**：退款对衣橱归属的动态影响
- **判定方法**：退款商品从可用衣橱中移除，不影响其他订单。见
  `docs/TESTING_AND_EVALUATION.md §2`。

### 用例四十三：准入校验不可绕过
- **输入**：通过手工属性覆盖（跳过接口校验）提交商品
- **动作**：恶意/异常调用
- **测试范围**：提交接口准入门槛
- **判定方法**：手工属性覆盖被拒绝，不能绕过准入门槛。见
  `docs/TESTING_AND_EVALUATION.md §2`。

### 用例四十四：偏好管理数据正确性
- **输入**：偏好管理页查看/编辑/删除偏好
- **动作**：用户操作
- **测试范围**：管理页与记忆存储的一致性
- **判定方法**：页面操作实时反映到记忆存储；删除后推荐不再引用该偏好。见
  `docs/SESSION_CHAT_MEMORY.md`。

---

## 九、p-outfit 独立 LLM 评估

> 计划：`C:\Users\32369\.claude\plans\encapsulated-wibbling-planet.md`；
> 评估总文档 `docs/TESTING_AND_EVALUATION.md`；case 文件 `evals/cases/p_outfit.json`。

### 用例四十五：实验 A —— 订单导入式（弱数据）
- **输入**：`evals/cases/p_outfit.json`（100 例）按实验 A 构建衣橱快照（仅官方文本，
  `image_status=unbound`、文字嵌入），`IndependentJudge` 逐例打分
- **动作**：`evaluate_p_outfit.py --mode order`
- **测试范围**：弱数据衣橱下系统生成穿搭的整体质量（五维裁判分）
- **判定方法**：报告 `modes.order` 含 `mean_judge_overall`（top-1 生成套）、
  `mean_judge_overall_golden`（真实套锚点）、`delta`、`pass_rate`、`gap_rate`、
  `judge_failure_rate`；临时 schema 已 DROP，主库零新表。

### 用例四十六：实验 B —— 图片导入式（强数据）
- **输入**：同一批 case 按实验 B 构建（`image_status=available`、图像嵌入，
  Gemini 识别增强文本），同一裁判打分
- **动作**：`evaluate_p_outfit.py --mode image`
- **测试范围**：强数据衣橱（含图像嵌入）下生成质量的提升效应
- **判定方法**：报告 `modes.image` 指标齐全；VLM 增强失败 item 保持官方文本不阻塞。

### 用例四十七：独立裁判打分
- **输入**：`build_judge_prompt(请求, 单品文本)` → `IndependentJudge.score_outfit`
- **动作**：一次离线打分
- **测试范围**：独立裁判的五维打分与**独立性**（与 Runtime Critic 分离）
- **判定方法**：输出五维分（R/S/C/W/F）+ reasoning；失败返回 `(None, degraded)` 不产生
  中性分；prompt 不含 critic 决策词汇。门禁：`tests/test_p_outfit_eval.py` C 组。

### 用例四十八：golden 真实套锚点
- **输入**：对同一 case 的 golden 真实套与 top-1 生成套分别打分
- **动作**：配对打分
- **测试范围**：真实搭配 vs 生成搭配的相对质量
- **判定方法**：`mean_judge_overall_golden` 作为锚点；`delta_golden_vs_generated` 如实
  报告差距，不因 golden 得分高低而改变判定口径。

### 用例四十九：A/B 对照与统计检验
- **输入**：order 与 image 两组各 100 例逐 case 配对
- **动作**：配对比较
- **测试范围**：弱数据 vs 强数据衣橱的生成质量差
- **判定方法**：`modes.comparison` 输出配对检验（paired t-test / Wilcoxon）、效应量
  Cohen's d、95% CI；pass_rate 差异用 McNemar。统计口径如实声明，不夸大。

### 用例五十：评估门禁覆盖
- **输入**：`tests/test_p_outfit_eval.py`
- **动作**：离线运行
- **测试范围**：case 文件契约、映射/裁判/聚合纯函数、accept/recompose 两条执行路径、
  runner 集成
- **判定方法**：全部通过（FakeLlm 锁确定性，不锁真实分数）；accept 路径 3 次 LLM 调用、
  recompose 路径 5 次；case 文件 100 例、`_reviewed` 标记齐全、request 长度 25-120
  （实际 28-45，均值≈38）。见 `docs/P_OUTFIT_EVAL_TEST_COVERAGE.md`。

---

## 十、评估质量检查（Meta-Evaluation）

> 这 5 个用例**不测系统功能**，而测**评估本身是否可信**。它们是 p-outfit 评估计划的
> 质量门禁，与功能用例（一~五十）分开。判定的都是"评估是否可靠"，不是"系统是否好"。

### 用例五十一：裁判独立性（Critic 去相关）
- **输入**：一次评估的 per-case 记录（top-1 生成套的 `judge_overall` 与 runtime `critic_score`）
- **动作**：计算 Pearson r 与平均绝对差
- **测试范围**：评估可信度——独立裁判不得退化为 Critic 自身的回声
- **判定方法**：
  - **硬门禁**：`build_judge_prompt` 不含 critic 决策词汇（决策枚举/阶段二/recompose/request_signature 等）——`tests/test_p_outfit_eval.py` C 组锁死；
  - **软告警**：报告输出 `critic_judge_consistency = {pearson_r, mean_abs_delta}`；当 `r ≥ 0.9` 时报告标记"裁判独立性可疑"，需审查 prompt 是否混入 critic 语义后重跑。
  - 如实说明：两者都度量搭配质量，**天然存在正相关**，所以一致性是告警信号而非硬门禁；词汇级独立性才是硬门禁。

### 用例五十二：及格线预注册
- **输入**：`evals/cases/p_outfit.json`（含 `pass_criteria`）
- **动作**：核对及格标准何时定义
- **测试范围**：评估有效性——及格线必须在看结果前定好，禁止事后调线
- **判定方法**：每个 case 的 `pass_criteria.min_judge_overall` 在 case 文件中固化（当前 60）；
  报告记录 pass 标准的定义时间与 case 文件版本，且该定义存在于评估运行 commit **之前**
  （git 历史可证）；事后按结果调整标准视为评估无效。

### 用例五十三：衣柜缺口路径覆盖
- **输入**：`tests/test_p_outfit_eval.py`
- **动作**：检查门禁是否覆盖缺口路径
- **测试范围**：执行路径覆盖完备性——accept / recompose / wardrobe_gap 三条路径缺一不可
- **判定方法**：门禁含 `test_pipeline_wardrobe_gap_best_effort`（a3 决策=wardrobe_gap →
  best_effort 仍返回推荐，不静默失败）；含 `test_pipeline_accept_path`（3 次 LLM 调用）
  与 `test_pipeline_recompose_path`（5 次 LLM 调用）；**三条全绿** CI 才算通过。
  （该用例对应 `docs/P_OUTFIT_EVAL_TEST_COVERAGE.md` E 组。）

### 用例五十四：评估可复现性记录（provenance）
- **输入**：`artifacts/evaluation/p_outfit.json`
- **动作**：检查报告元数据完整性
- **测试范围**：评估可信度——每次运行必须留下可追溯环境快照
- **判定方法**：报告含 model id、`judge_prompt_version`、case 文件版本/hash、抽样 `seed`、
  runner 版本、模式（order/image）、运行 UTC 时间；任一缺失记"provenance 不完整"，
  分数变更无法溯源即视为评估无效。

### 用例五十五：请求无答案泄漏（no-leakage）
- **输入**：`evals/cases/p_outfit.json` 的 `user_request` 与 `items` 文本
- **动作**：逐 case 计算 request 与 golden 文本重合度
- **测试范围**：评估有效性——人工请求不得把 golden 答案泄漏给系统
- **判定方法**：request **不含**任何 golden item 的 `url_name`/`title` 逐字文本片段；
  request 中的品类词（如"连衣裙""连体裤"）属需求级骨架，**允许**（否则无法路由到正确
  槽位），但 golden 特有命名（url_name、title、独特描述词）不得出现；门禁
  `test_case_no_answer_leakage` 锁 0 泄漏。

---

## 十一、评估方案对齐（补充缺口）

> 对应评估方案的 §4.5 / §5.2 / §5.3 / §6.3 / §3.4 五个缺口。判定均基于可观测
> 输出（trace / 报告字段），不锁真实分数。

### 用例五十六：Handoff 信息保全（§4.5）
- **输入**：带复杂约束的请求（如"不要高跟鞋，但可以乐福鞋"）
- **动作**：捕获 Agent1→Agent2、Agent2→Agent3 的交接 payload
- **测试范围**：多智能体协作——约束在 Agent 链中完整传递
- **判定方法**：交接 payload 中 `hard_constraints` 列表长度未缩短、关键词（如
  "no heels"）未丢失；信息保全无损失。落地：runner 在 trace 中记录交接 payload，
  门禁 E 组 `test_pipeline_handoff_preservation` 断言。

### 用例五十七：Ablation 消融实验（§5.2）
- **输入**：完整系统 vs 各消融变体
- **动作**：分别运行评估（Full / -Critic / -Retrieval）
- **测试范围**：各模块对 Pass Rate 与 Overall 的独立贡献
- **判定方法**：报告各变体指标差异；某模块移除后指标无变化 → 标记"需审视"。
  **如实说明**：p-outfit 评估链路是单次 `recommend_payload`、无历史上下文，
  **-Memory 消融在该评估中不适用**（无记忆参与），记忆模块的贡献由用例二十四~二十九
  （系统级多轮评估）验证；消融变体聚焦实际参与链路的 Critic 与 Retrieval。

### 用例五十八：性能指标采集（§6.3）
- **输入**：p-outfit 100 例运行
- **动作**：自动记录每例延迟、Token、工具调用次数
- **测试范围**：端到端性能可接受性
- **判定方法**：报告 P50/P95 延迟、平均 Token、Cost/Success；P95 延迟 < 10s 作为
  **观察阈值**（非硬门禁，网络波动下仅供参考）。

### 用例五十九：Public Fashion Benchmark（§5.3）
- **输入**：p-outfit 100 例生成结果
- **动作**：离线计算 Compatibility AUC 与 Complementary Item Retrieval R@K
- **测试范围**：基础能力是否达到学术标准
- **判定方法**：报告 AUC 与 R@K，与 CSA-Net / OutfitTransformer 公开数值对比；
  **如实声明数据不完全可比**（p-outfit 抽样子集、文本生成场景、品类映射粗于原基准），
  对比仅作参考不作裁决。

### 用例六十：Trace Golden Path 节点检查（§3.4）
- **输入**：p-outfit 正常 case 的完整 Trace
- **动作**：检查 Trace 是否包含 Router→Agent1→Agent2→Agent3→Final 全部节点
- **测试范围**：执行路径符合 Golden Path 定义的必要步骤
- **判定方法**：断言必备节点全部存在、无跳过；禁止节点（如生成衣橱外 item）
  未出现。门禁：E 组 `test_trace_golden_path_nodes`。

---

## 覆盖映射（用例 ↔ 评估计划项）

| 用例 | 对应评估计划 / 功能 |
|---|---|
| 一~五 | 主功能、边界、降级（§1/§3/§7） |
| 六~十一 | 解析与约束（§1/§3/§5） |
| 十二~十七 | 任务路由（§6，60 例评估） |
| 十八~二十三 | 多轮对话与 follow_up（§10，D2/D4/D6/D7） |
| 二十四~二十九 | 记忆系统（§9 记忆提炼评估 + SESSION_CHAT_MEMORY） |
| 三十~三十四 | 扩展业务（§7 五类业务验收） |
| 三十五~三十八 | 天气上下文（§8） |
| 三十九~四十四 | 衣橱与订单（§2 + ORDER_WARDROBE_IMPORT） |
| 四十五~五十 | p-outfit 独立 LLM 评估（实验 A/B + 独立裁判 + 门禁） |
| 五十一~五十五 | 评估质量检查（裁判独立 / 预注册 / 缺口覆盖 / 可复现 / 无泄漏） |
| 五十六~六十 | 评估方案对齐（Handoff §4.5 / Ablation §5.2 / 性能 §6.3 / Public Benchmark §5.3 / Golden Path §3.4） |

**补完后的覆盖状态**：

| 评估方案章节 | 覆盖状态 |
|---|---|
| 第一部分（L1-L4 结构） | ✅ 完整 |
| 第二部分（Benchmark 设计） | ✅ 完整 |
| 第三部分（评估方法论） | ✅ 完整 |
| 第四部分（各组件评估） | ✅ 完整（含用例五十六） |
| 第五部分（实验设计） | ✅ 完整（含用例五十七、五十九） |
| 第六部分（工程指标） | ✅ 完整（含用例五十八） |
| Trace 评估 | ✅ 完整（含用例六十） |
