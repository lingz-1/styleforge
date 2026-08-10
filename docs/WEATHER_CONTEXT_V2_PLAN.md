# 天气与时空上下文 V2 详细方案

> 方案日期：2026-08-10  
> 状态：设计完成，尚未实现  
> 当前实现说明见[天气上下文工具](WEATHER_CONTEXT.md)。本文只描述下一阶段目标，不把规划能力写成已完成能力。

## 1. 目标

用户不需要显式说“请根据天气推荐”。系统应由 Agent 1 理解请求中隐含的时间、地点、活动和环境依赖，再由 Context Router 获取可追溯事实，最终仍由现有三个主 Agent 完成检索、搭配和审校。

目标体验：

- “明天穿什么”隐含“查询明天、用户当前定位的天气”；设备定位不可用时再使用用户默认城市。
- “去北京旅游该怎么穿”隐含“查询北京近几天的天气”；未给日期时，明确采用近 3 天默认窗口。
- “下周去看《悲惨世界》”隐含“查询演出场次、城市、场馆、开始和结束时间，再查询演出前后时段天气”。
- 最终答案不仅给衣服，还要解释天气如何改变了方案，并可提醒雨伞、水、墨镜、遮阳帽、花露水、发圈等随身物品。
- 这种能力适用于所有合理依赖天气或户外环境的请求，不局限于“明天、演出、旅游”三个示例。

## 2. 不变的架构原则

1. **仍然只有三个 Agent。** Agent 1 负责理解和声明上下文需求，Agent 2 负责组合与建议，Agent 3 负责事实和实穿性审校。
2. **工具只提供事实。** Weather、Calendar、Event Lookup、Location Resolver 不直接输出“穿外套”或“带雨伞”。
3. **程序执行、LLM 决策。** Agent 1 产出 typed requirements；Context Router 校验并按依赖顺序调用工具。
4. **不新增确定性结果 Service。** 环境影响说明和随身物品建议由 Agent 2 生成、Agent 3 审核，不由规则模板替代。
5. **衣橱归属严格。** 搭配中的 `item_id` 必须属于当前用户衣橱和 Agent 1 候选池；雨伞、水等属于 `external_carry_items`，不能伪装成衣橱单品。
6. **无事实不主张。** 天气不可用时继续推荐，但不得声称“会下雨”“紫外线很强”等。
7. **远期不伪装成预报。** 超出可靠预报窗口时只能使用有标签的历史气候/季节参考，并说明不确定性。

## 3. 当前已经实现的能力

| 能力 | 当前状态 | 实现边界 |
|---|---|---|
| Agent 1 声明天气需求 | 已实现 | `context_requirements.weather` 仅含 `needed/location/date/reason` |
| Context Router + Tool Registry | 已实现 | 校验请求并调用一次 `weather.get_weather` |
| 地点解析 | 已实现 | 请求显式地点或进程级 `STYLEFORGE_DEFAULT_LOCATION` |
| 日期解析 | 已实现 | 仅今天、明天、`YYYY-MM-DD` |
| 天气窗口 | 已实现 | 当前日起 16 天内的单日预报 |
| 天气事实 | 已实现 | 日最低/最高温、平均体感、最大降水概率、平均湿度、最大风速、天气代码 |
| 三 Agent 共享事实 | 已实现 | 天气成功后同一个 Agent 1 再执行一次；Agent 2、Agent 3 收到相同事实 |
| Trace、持久化和 Web 展示 | 已实现 | 事实、工具轨迹、不可用原因可追溯 |
| 失败语义 | 已实现 | `status=unavailable` 时继续主推荐且禁止无依据天气建议 |
| 离线合同测试 | 已实现 | 固定 Transport / Fake Tool，不访问实时网络 |

当前需要天气且天气可用的标准推荐链路是 4 次 LLM 调用：Agent 1 初判、Agent 1 基于事实细化、Agent 2、Agent 3。它仍是三个 Agent，不是四个 Agent。

## 4. 当前缺口

当前实现无法稳定满足目标体验，原因包括：

- 提示词偏向“请求明确受天气影响”，没有把“明天穿什么”等隐含天气意图固化为合同和评测。
- 无日期时会按“今天”查询单日，而不是采用明确的“近几天”窗口。
- 没有设备定位传入、用户级默认城市和时区，只有全局环境变量。
- 不支持后天、周末、下周、下个月、中秋节、圣诞节等时间表达和时间范围。
- 不支持行程跨天、跨城市、去程/返程或昼夜不同时间段。
- 没有演出、赛事、婚礼等活动的场次、场馆和起止时间查询能力。
- 当前天气结果以日级摘要为主，不能判断中午暴晒、晚场散场降温、阵雨时段等。
- 缺少 UV、能见度、空气质量、花粉、雷暴风险、积雪/结冰等扩展环境事实。
- Agent 2 没有强制输出“事实 → 影响 → 采取的穿搭调整”和结构化随身物品清单。
- Agent 3 没有逐条校验天气说明、随身物品与事实引用。
- 超出 16 天只会失败，没有“气候参考”和“临近日期重新查询”的产品语义。

## 5. 全面场景矩阵

是否调用上下文工具不能靠固定关键词，而要判断：**天气或环境是否会实质影响穿着安全、舒适度、活动完成度或随身准备。** 下表用于提示词、评测和产品验收，不是穷举式规则代码。

### 5.1 时间隐含型

| 场景 | 示例 | 应解析的上下文 |
|---|---|---|
| 相对日期 | 明天穿什么、后天面试 | 绝对日期、用户时区、默认地点、天气 |
| 周期范围 | 周末约会、下周出差 | 日期范围、每日差异、主要活动时段 |
| 节日节点 | 中秋去杭州、圣诞节去上海 | 节日对应公历日期、地点、是否超出预报范围 |
| 季节表达 | 秋天露营怎么穿、梅雨季上班 | 季节/气候参考；有明确年份和临近日期时优先实时预报 |
| 时段表达 | 今晚聚餐、中午拍照、凌晨赶飞机 | 小时窗口、体感温度、降水、UV、昼夜温差 |
| 未说明时间 | 去北京旅游怎么穿 | 默认近 3 天，并在结果中声明使用了默认窗口 |

### 5.2 地点与移动型

| 场景 | 示例 | 应解析的上下文 |
|---|---|---|
| 本地日常 | 明天上班穿什么 | 用户授权的当前定位、通勤时段；定位不可用时使用默认城市 |
| 目的地旅行 | 去北京旅游、去三亚度假 | 目的地、近 3 天或指定行程、当地时区 |
| 跨城出差 | 周一上海飞北京，周三回来 | 出发地/目的地、去返程窗口、两地温差 |
| 多城市行程 | 下周去伦敦和巴黎 | 每个城市的停留日期和天气，不用一个城市概括全程 |
| 特殊地理环境 | 去海边、高原、沙漠、山区 | 风、UV、温差、海拔等；天气数据不足时标明需要地理/环境数据 |
| 交通节点 | 早班机、夜间高铁、骑车通勤 | 出门和抵达时段、站外步行、空调环境、降雨/风 |

### 5.3 活动和事件型

| 场景 | 示例 | 关键影响 |
|---|---|---|
| 演出/电影节/展览 | 下周看《某某》演出 | 场次、场馆、室内外、排队和散场时间 |
| 演唱会/音乐节 | 周末音乐节穿什么 | 长时间站立、户外、日晒、降雨、泥地、夜间降温 |
| 体育赛事 | 去看球、参加马拉松 | 开赛/结束、户外暴露、运动强度、风雨和补水 |
| 婚礼/宴会 | 周六参加户外婚礼 | 仪式时间、草地/室内、降水、风、礼仪和鞋履 |
| 面试/考试 | 明早面试、高考穿什么 | 通勤时段、考场空调、降雨对整洁度的影响 |
| 约会/聚会 | 今晚露台约会 | 夜间温度、风、室内外切换 |
| 拍摄/旅拍 | 日出拍照、海边拍婚纱 | 日出日落、风、UV、湿度、妆发稳定性 |
| 节庆/庙会/游园 | 中秋夜游、跨年倒数 | 夜间户外、拥挤、长站、降温、降水 |

### 5.4 户外暴露型

| 场景 | 示例 | 关键事实和建议方向 |
|---|---|---|
| 通勤步行 | 明天走路上班 | 降水、风、路面积水；鞋履和雨具 |
| 骑行/电动车 | 明早骑车 | 风寒、降雨、能见度；防风、防水、反光安全 |
| 徒步/登山 | 周末爬山 | 山区温差、风、雷暴、日晒；分层和应急物品 |
| 露营/野餐 | 明晚露营 | 日落后温度、露水、降雨、虫媒；外层、花露水 |
| 海边/水上活动 | 去海边玩 | UV、风、湿度；遮阳帽、墨镜、补水、发圈 |
| 滑雪/冰雪 | 去滑雪 | 低温、风寒、降雪、UV 反射；保暖、防水和护目 |
| 乐园/亲子 | 带孩子去迪士尼 | 全天户外、排队、补水、防晒、突发降雨 |
| 遛狗/遛娃 | 下午带宝宝出门 | 短时天气窗口、体感、空气环境；用户本人建议需与儿童建议分开 |

### 5.5 室内外切换型

| 场景 | 示例 | 关键影响 |
|---|---|---|
| 商场/办公室 | 夏天坐地铁去公司 | 室外炎热与室内空调温差，轻薄可携带外层 |
| 剧场/影院 | 晚场电影、音乐剧 | 室内久坐、空调、散场后夜间温度 |
| 展会/会议 | 全天参加展会 | 场馆空调、长走、室外排队 |
| 酒店/温泉 | 冬天泡温泉 | 湿身后的体感变化、室内外过渡 |
| 校园/考试 | 教室考试一整天 | 通勤与室内静坐温差、易穿脱分层 |

### 5.6 天气风险型

| 环境信号 | 对穿搭的潜在影响 | 可建议的非衣物准备 |
|---|---|---|
| 高降水概率/阵雨 | 防水外层、耐水鞋、避免拖地裤脚 | 伞、防水袋、替换袜 |
| 高温/高体感温度 | 透气、浅色、减少层次 | 水、遮阳帽、墨镜、便携风扇 |
| 低温/风寒 | 保暖层、防风外层、覆盖暴露部位 | 暖宝宝、保温杯 |
| 大风 | 稳定下摆、避免宽大易扬起单品、发型管理 | 发圈、发夹；必要时不建议普通雨伞 |
| 高 UV | 覆盖、防晒材质、帽檐 | 墨镜、遮阳帽、防晒用品 |
| 高湿/闷热 | 排汗、速干、减少厚重叠穿 | 水、发圈、吸汗用品 |
| 雷暴/极端天气 | 安全优先，必要时建议改变或推迟户外计划 | 应急用品；不能只靠穿搭解决 |
| 雪/结冰 | 防滑、防水、保暖 | 手套、暖贴；提示路面风险 |
| 雾/低能见度 | 提高可见性和通勤安全 | 反光物、照明；骑行场景尤其重要 |

### 5.7 个体敏感型

只有用户资料或本次请求明确提供相关信息时才能个性化，不能由系统猜测健康状况。

| 场景 | 可用上下文 | 边界 |
|---|---|---|
| 怕冷/怕热 | 用户显式偏好 | 调整体感阈值和层次，不做医学判断 |
| 容易出汗 | 用户显式偏好 + 湿度/体感 | 透气、速干和备换建议 |
| 花粉敏感 | 用户显式信息 + 花粉数据源 | Weather Tool 本身不等于花粉工具 |
| 呼吸道敏感 | 用户显式信息 + AQI 数据源 | 不提供诊断；空气质量差时给一般性防护提示 |
| 行动不便/孕期 | 用户显式信息 | 优先安全、稳定鞋履和易穿脱；不替代专业建议 |

### 5.8 通常不应强行调用天气的场景

- “黑色马甲怎么搭”“美拉德风格是什么”等纯风格/单品知识问题，且没有出行、时间、地点或实穿环境。
- 用户明确说只做灵感板、虚拟造型或室内静态拍摄，天气不影响决策。
- 局部修改只要求“把鞋换成白色”，且当前上下文没有新的环境变化。
- 无法获得地点且天气只是可选增强时，可跳过并明确未使用；不得反复阻塞用户。

## 6. 目标决策流程

```text
用户自然语言
    ↓
Task Router：只判断推荐/修改/风格/单品/兼容性/衣橱缺口
    ↓
Agent 1 第一次：理解隐含时间、地点、事件、环境依赖
    ↓ typed ContextRequirements
Context Router 构建工具依赖图
    Bootstrap Timezone（显式地点 / 设备定位 / 用户资料 / 请求接收时区）
        ↓
    Calendar Resolver（第一次解析）
        ↓
    Event Lookup（需要时）
        ↓
    Location Resolver（生成按时段绑定的 location_segments）
        ↓
    Calendar Resolver（地点或事件时区改变时重新解析并检查冲突）
        ↓
    Weather Forecast / Climate Reference / 其他环境工具
    ↓ typed facts + evidence + trace
Agent 1 第二次：把事实转成检索条件和 practical_context
    ↓
Wardrobe Retriever：只从用户衣橱取候选
    ↓
Agent 2：完整搭配 + 环境调整说明 + 随身物品
    ↓
Agent 3：穿搭、天气主张、事实引用和安全边界审校
    ↓
Renderer：展示“何时/何地/事实 → 影响 → 采取的行动”
```

工具之间是有向依赖，不应并行乱调。例如“下周去看《某演出》”先用 bootstrap 时区得到候选日期范围，再确定演出场次和场馆；如果场馆时区不同，Calendar Resolver 必须按场馆时区重算并检查场次是否仍落在用户表达的“下周”内，最后才能查询天气。发生日期边界冲突时返回 `ambiguous`，不能静默选一个解释。

## 7. 时间解析设计

新增 `TemporalContextRequirement` 和 `ResolvedTimeContext`：

```json
{
  "temporal": {
    "needed": true,
    "expression": "下周",
    "activity_window_hint": "晚场演出",
    "reason": "需要确定演出场次和散场天气"
  }
}
```

解析结果至少包含：

```json
{
  "status": "resolved",
  "original_expression": "下周",
  "start_at": "2026-08-17T00:00:00+08:00",
  "end_at": "2026-08-23T23:59:59+08:00",
  "timezone": "Asia/Shanghai",
  "precision": "week",
  "default_applied": false,
  "resolution_basis": "relative_to_request_time"
}
```

规则：

1. 所有相对时间以请求接收时间和目标地点时区解析，不能只用服务器本地日期。目标地点尚未知时，bootstrap 时区依次取显式地点、设备定位、用户资料、客户端请求时区；事件或行程解析出新时区后必须二次解析。
2. 明确日期优先；事件官方时间可进一步收窄用户的“下周”。
3. 用户未给时间但请求包含目的地、出行或户外活动时，默认查询**近 3 天（今天起连续 3 个自然日）**，并输出 `default_applied=true`。
4. 纯“穿什么”且无地点/时间时，使用本次请求携带的设备定位 + 近 3 天；定位不可用时使用用户默认城市。两者都缺失时，天气为必要条件才澄清，否则跳过天气。
5. 节日需解析年份。若今年节日已过且用户说“中秋节”而未给年份，默认下一个尚未发生的同名节日，并明确年份。
6. “下个月”“圣诞节”等超出预报窗口的时间不能伪造成精准天气预报。

## 8. 地点解析设计

单地点请求按以下优先级解析；多城市、去返程或跨时区请求不得生成一个全局地点，而要生成 `location_segments[]`，每段地点必须绑定自己的 `start_at/end_at/timezone/purpose/source`：

1. 用户本次请求明确的活动地点或目的地。
2. Event Lookup 返回且与用户选择的场次一致的场馆城市。
3. 行程中当前时间段对应的城市。
4. 用户为本次请求明确授权的设备定位。
5. 用户资料中的默认城市和时区。
6. 全局开发环境默认城市，仅用于 demo，不应覆盖真实用户资料。
7. 必要上下文仍缺失时澄清；可选增强则跳过。

多段结果示例：

```json
{
  "location_segments": [
    {
      "location_id": "loc_departure",
      "display_name": "上海",
      "start_at": "2026-08-17T06:00:00+08:00",
      "end_at": "2026-08-17T09:00:00+08:00",
      "timezone": "Asia/Shanghai",
      "purpose": "departure",
      "source": "device"
    },
    {
      "location_id": "loc_destination",
      "display_name": "北京",
      "start_at": "2026-08-17T10:00:00+08:00",
      "end_at": "2026-08-20T18:00:00+08:00",
      "timezone": "Asia/Shanghai",
      "purpose": "stay_and_return",
      "source": "explicit_destination"
    }
  ]
}
```

优先级在每个时间段内单独应用：明确行程地点优先于该时间段的事件场馆冲突项；事件场馆用于没有更明确行程地点的活动时间段；设备定位只适用于“现在所在位置/本地活动”时间段。

这里的“直接根据用户定位获取天气”采用客户端授权和可续跑模式：

```text
首次提交自然语言（可同时带已有且仍有效的授权定位）
    ↓
Agent 1 判断本次是否需要位置
    ↓ 需要但请求没有有效位置
API 返回 status=needs_context、required_context=[device_location]、resume_token
    ↓
Web / 小程序说明用途并请求系统定位权限
    ↓ 用户同意
取得本次经纬度、精度和采集时间
    ↓ POST /tasks/{run_id}/context 后携带 resume_token 续跑同一 run
后端校验坐标、新鲜度和授权声明
    ↓ Location Resolver 解析城市/时区
Weather Tool 按坐标查询，不再做同名城市猜测
```

若客户端在用户已授予“使用期间定位”且定位仍满足新鲜度/精度阈值时，可以在首次请求直接携带坐标，省去一次往返。用户明确目的地且无需当前位置时，Agent 1 不应要求设备定位。

建议请求字段：

```json
{
  "location_context": {
    "latitude": 31.2304,
    "longitude": 121.4737,
    "accuracy_m": 120,
    "captured_at": "2026-08-10T10:15:00+08:00",
    "source": "device",
    "consent_granted": true
  }
}
```

定位使用规则：

- 用户说“去北京旅游”时必须查北京，不得用当前定位覆盖目的地。
- 用户说“明天穿什么”“今天上班怎么穿”等本地请求，优先使用本次设备定位。
- 默认认为定位有效需同时满足：`captured_at` 距请求不超过 30 分钟、`accuracy_m <= 5000`、经纬度合法。阈值配置为 `STYLEFORGE_LOCATION_MAX_AGE_SECONDS=1800` 和 `STYLEFORGE_LOCATION_MAX_ACCURACY_M=5000`，不得散落在提示词里。
- 坐标过旧、精度过低、越界或权限被拒绝时，分别返回 `location_stale/location_inaccurate/location_invalid/location_denied`，再按用户默认城市策略处理。
- 前端必须展示“本次使用当前位置查询天气”，并允许用户改选城市。
- 后端不能通过 IP 静默推断精确位置；如未来增加 IP 粗定位，必须单独标注来源和低置信度。
- 原始精确经纬度只存在于当前 run 的内存状态，run 完成、失败或取消即删除；不进入 API 响应、业务日志、trace、`ContextPack`持久化或`styling_runs`。
- Weather Provider 缓存使用四舍五入到小数点后 2 位的坐标键，默认 TTL 15 分钟（`STYLEFORGE_LOCATION_CACHE_TTL_SECONDS=900`），缓存值只含天气事实和规范化显示地点，不保留原始坐标。
- API响应、`resolved_location_context`、trace和持久化只保留`display_name/timezone/source/accuracy_bucket`；其中`accuracy_bucket`固定为`high(<=100m) | medium(<=1000m) | low(<=5000m)`。Provider内部参数记录必须经过字段白名单脱敏，不记录`latitude/longitude`。
- 用户撤回授权时，客户端删除本地授权状态和缓存定位；后端立即清除该用户尚未完成run中的原始坐标以及可关联的设备定位缓存键。已持久化的城市级历史不反推精确位置。

新增用户偏好字段建议：

```json
{
  "environment_profile": {
    "default_city": "上海",
    "timezone": "Asia/Shanghai",
    "location_source": "user_provided",
    "device_location_consent": "ask_each_time",
    "temperature_sensitivity": "neutral"
  }
}
```

默认只保存城市、时区和授权偏好，不保存精确经纬度或持续位置历史。

## 9. Event Lookup 设计

Event Lookup 是待新增的外部事实工具，不是新 Agent，也不是天气 Provider 的职责。

### 9.1 输入

```json
{
  "event_query": "《悲惨世界》",
  "date_range": {
    "start": "2026-08-17",
    "end": "2026-08-23"
  },
  "location_hint": "",
  "event_type_hint": "演出"
}
```

### 9.2 输出

```json
{
  "status": "resolved",
  "event_name": "悲惨世界",
  "venue_name": "示例剧院",
  "city": "上海",
  "timezone": "Asia/Shanghai",
  "starts_at": "2026-08-22T19:30:00+08:00",
  "ends_at": "2026-08-22T22:35:00+08:00",
  "end_time_estimated": false,
  "indoor_outdoor": "indoor",
  "source_refs": ["event:official:..."],
  "confidence": 0.96
}
```

### 9.3 解析政策

- 来源优先级：活动主办方/场馆官方页面 → 官方票务页面 → 可信票务或城市活动平台。
- 多个城市、巡演版本或场次都可能匹配时，返回 `ambiguous` 和候选列表，要求用户选择，不能猜。
- 官方只给开始时间时，可根据公开时长估算结束时间，但必须标记 `end_time_estimated=true`。
- 查询天气的窗口应覆盖出发、排队、活动和散场返程。默认可用活动开始前 2 小时至结束后 2 小时，但要在实现时做成配置，并以用户行程覆盖默认值。
- 传给 LLM 的是经过 Schema 校验的字段和短摘要，不是原始网页正文，防止网页内容污染 Agent 指令。
- Provider 选型、授权和实时数据源尚未确定，开发时需要单独决策，不在本文假装已有实现。

## 10. Weather / Environment Tool V2

### 10.1 输入从单日改成时间窗口

```json
{
  "location": {
    "name": "上海",
    "timezone": "Asia/Shanghai"
  },
  "start_at": "2026-08-22T17:30:00+08:00",
  "end_at": "2026-08-23T00:35:00+08:00",
  "granularity": "hourly",
  "purpose": "event_window"
}
```

### 10.2 事实模型

保留原日级字段，同时增加：

- `context_type`: `forecast | climate_reference | current_observation`。
- `valid_from/valid_to/timezone`。
- 小时级温度、体感、降水概率/量、风速/阵风、湿度、天气代码。
- `uv_index_max`、日出日落；Provider 支持时再加入能见度、积雪/结冰等。
- `key_periods`：出发、活动开始、活动结束、返程等时段摘要。
- `source/retrieved_at/source_refs`。
- `confidence/limitations`，明确预报还是气候参考。

### 10.3 预报与远期气候语义

| 时间距离 | 行为 |
|---|---|
| Provider 可靠预报窗口内 | 查询 forecast，可按活动窗口给小时级结果 |
| 超出预报窗口但可取得气候数据 | 返回 `context_type=climate_reference`，只描述典型温度/降水倾向和不确定性 |
| 无可靠数据 | `status=unavailable`，不生成天气主张 |

例如“圣诞节去北京”在 8 月提出时，可以说“只能按历史气候做准备，临近出发需重新确认”，不能说“12 月 25 日有雪、降水概率 70%”。

### 10.4 天气之外的环境能力

为保持事实边界，以下能力应按独立 Provider 或 Tool 接入，而不是伪造进 Weather Tool：

- Air Quality：AQI、PM2.5、污染提示。
- Pollen：花粉种类和等级。
- Geographical Context：海拔、海边、山地等静态环境。
- Severe Weather Alerts：官方极端天气预警。
- Route/Commute：实际出发和到达窗口、步行/骑行暴露时长。

Agent 1 可以声明统一的 `environment_requirements`，Context Router 只调用当前已注册且确实需要的工具。

## 11. 三 Agent 输入输出合同

### 11.1 Agent 1：语义检索师

首次输出由单一 `weather` 扩成：

```json
{
  "context_requirements": {
    "temporal": {
      "needed": true,
      "expression": "下周",
      "reason": "需要确定活动时间"
    },
    "event": {
      "needed": true,
      "query": "《悲惨世界》演出",
      "reason": "需要场馆和开散场时间"
    },
    "location": {
      "needed": true,
      "query": "",
      "allow_profile_default": true,
      "reason": "天气查询需要地点"
    },
    "weather": {
      "needed": true,
      "granularity": "hourly",
      "reason": "排队和散场时段受温度、降水影响"
    }
  }
}
```

Agent 1 还应输出：

- `implicit_context_signals`：为什么用户虽未提天气仍需要环境上下文。
- `context_criticality`: `required | helpful | not_needed`。
- `uncertainties`：地点、场次、日期冲突或歧义。
- `default_policy_allowed`：是否可安全应用近 3 天，以及设备定位不可用时是否可使用默认城市。

工具完成后，同一个 Agent 1 第二次执行，把事实写入：

- `request_signature.practical_context`：如晚间散场、31°C、高湿、阵雨。
- `retrieval_plans`：如 lightweight breathable、water-resistant walking shoes、packable outer layer。
- `candidate_requirements`：需要时提高外套、鞋履或配饰候选配额。

### 11.2 Agent 2：搭配组合师

每套方案新增结构：

```json
{
  "outfit_id": "outfit_001",
  "item_ids": [
    "wardrobe_top_1",
    "wardrobe_bottom_2",
    "wardrobe_outerwear_4",
    "wardrobe_shoes_3"
  ],
  "environment_adjustments": [
    {
      "fact_refs": ["weather:key_period:return"],
      "impact": "22:30 散场时体感降至 12°C",
      "action": "加入可脱卸外层",
      "wardrobe_item_ids": ["wardrobe_outerwear_4"]
    }
  ],
  "carry_recommendations": [
    {
      "name": "折叠伞",
      "reason": "返程时段降水概率较高",
      "fact_refs": ["weather:key_period:return"],
      "category": "external_carry_item"
    }
  ],
  "limitations": []
}
```

要求：

- `environment_adjustments` 必须写清事实、影响和具体方案动作。
- 衣物动作必须引用本方案中真实的衣橱 ID。
- 随身物品可以不是衣橱单品，但必须标为 `external_carry_item`。
- “发圈、花露水、墨镜、遮阳帽”等只能在相关事实和活动支持时建议，不能机械地每次全列。
- 极端天气下，Agent 2 应允许输出安全提示或建议调整活动，不能把一切风险都包装成“换套衣服即可”。

### 11.3 Agent 3：评审与决策官

Agent 3 除现有五维评审外，新增检查：

1. 每条天气/环境主张是否有 `fact_refs`。
2. `fact_refs` 是否存在且状态可用，时间和地点是否对应本次活动。
3. 穿搭调整引用的衣物是否属于本方案和候选池。
4. 随身物品是否标成外部物品，是否由事实和活动合理支持。
5. `climate_reference` 是否被错误写成精确预报。
6. `status=unavailable` 时是否仍出现下雨、高温、UV 等断言。
7. 多城市/多日请求是否遗漏关键时段。
8. 极端天气是否缺少安全优先提示。

新增输出建议：

```json
{
  "environment_assessment": {
    "grounded": true,
    "coverage_complete": true,
    "unsupported_claims": [],
    "missing_adjustments": [],
    "carry_advice_grounded": true
  }
}
```

环境审校只是现有 Critic 决策的一部分，不新增另一套互相冲突的状态机：

- `grounded=false` 且候选池内可修正 → `decision=recompose`，最多沿用现有重组次数上限。
- 缺少必要环境事实、地点或事件选择 → 工作流返回 `needs_context`/`needs_clarification`，保存 run 和 `resume_token`；用户补充后从 Context Router 续跑，不消耗 Composer 重试。
- Provider 明确 `unavailable` 且天气只是增强 → 移除天气主张后可继续 `accept`。
- 极端天气安全要求无法满足 → 返回 `infeasible` 或带安全警示的 best effort，不能循环重组。
- 达到既有重试上限仍不 grounded → `failed`，记录具体 unsupported claim；不得确定性拼接一个结果。

### 11.4 工具合同冻结要求

V2.1编码前必须先冻结公共状态模型：

```json
{
  "status": "resolved | available | partial | unavailable | ambiguous | needs_context",
  "source": "provider-name",
  "source_refs": [],
  "retrieved_at": "ISO-8601",
  "confidence": 0.0,
  "limitations": [],
  "error_code": "",
  "error_message": ""
}
```

各工具最小合同：

| 工具 | 输入 | 成功输出 | 主要错误码 |
|---|---|---|---|
| Calendar Resolver | expression、request_at、bootstrap_timezone、可选event/location timezone | start/end、timezone、precision、default_applied、resolution_basis | `invalid_expression/timezone_required/timezone_conflict/out_of_range` |
| Location Resolver | 显式地点、事件地点、本次设备坐标、用户默认城市、时间段 | `location_segments[]`、display_name、timezone、source、confidence | `location_required/location_invalid/location_stale/location_inaccurate/location_denied/location_ambiguous` |
| Event Lookup | event_query、date_range、location_hint、type_hint | 候选或已选事件、venue、start/end、timezone、source refs | `event_not_found/event_ambiguous/source_unavailable/time_unconfirmed` |
| Weather Forecast | location segment、start/end、granularity | daily/hourly facts、key periods、forecast type | `forecast_out_of_range/provider_unavailable/partial_window/location_unsupported` |
| Climate Reference | location segment、calendar period、requested variables | 历史分布/典型范围、reference years、limitations | `climate_unavailable/location_unsupported/insufficient_history` |

`partial`必须列出可用与缺失的时间段；`ambiguous`必须携带安全可展示的候选；`needs_context`必须列出所需字段和可续跑token。Provider选型可以后定，但Provider适配器必须实现冻结后的公共合同。

`resume_token`必须是单次使用、与`run_id/user_id/required_context`绑定且服务端可校验的短期令牌，默认10分钟失效；不得把坐标编码进可读token。超时或已使用时返回`resume_token_expired/resume_token_used`。`task_runs`需要Schema迁移，将`needs_context`加入CHECK约束并增加可恢复节点字段；迁移测试必须覆盖旧数据库，避免再次出现状态值与CHECK约束不一致。

## 12. 最终 API 与界面输出

推荐响应新增：

- `resolved_time_context`
- `resolved_location_context`
- `event_context`
- `environment_context`
- 每套方案的 `environment_adjustments`
- 每套方案或本次出行共用的 `carry_recommendations`
- `evidence_refs`
- `confidence` 和 `limitations`

前端不只显示天气卡片，还要显示：

1. **按什么时间和地点查的**：例如“上海，8 月 22 日 17:30—23 日 00:35”。
2. **数据是什么性质**：实时预报、小时预报，还是远期气候参考。
3. **这套搭配因环境做了什么调整**：例如“散场体感 12°C，因此使用衣橱中的黑色短外套”。
4. **出门还要带什么**：例如“返程有阵雨，建议带折叠伞”。
5. **不确定性**：例如“结束时间按演出时长估算”“远期天气需临近确认”。

推荐文本必须采用“事实 → 影响 → 行动”表达：

> 考虑到明天上海 18:00 后降水概率升高，且返程时体感约 17°C，这套选择了衣橱中的防风外套 `outerwear_04` 和耐水鞋 `shoes_11`；建议额外带一把折叠伞。天气事实更新时间为……

避免空泛文本：

> 已根据天气做了适当调整。

## 13. 失败、歧义与澄清政策

| 情况 | 系统行为 |
|---|---|
| 未给时间，但目的地明确 | 使用近 3 天，显示默认已应用 |
| 未给地点，但有用户默认城市 | 使用用户默认城市，显示来源 |
| 未给地点且无用户默认城市 | 天气必要则澄清；仅为增强则跳过 |
| 演出有多个匹配场次/城市 | 展示候选并澄清，禁止猜测 |
| 时间超出预报窗口 | 使用明确标注的气候参考或 unavailable |
| Weather Provider 超时 | 记录 trace，继续无天气推荐，不生成天气建议 |
| 只有部分行程天气可用 | 对可用时段给依据，对缺失时段标明限制 |
| 极端天气预警 | 安全优先，允许建议取消/延期；不以穿搭建议淡化风险 |

澄清只在答案会因不同选择发生实质变化时触发。不能因为用户没写场合细节就拒绝给“明天穿什么”的通用建议。

## 14. 开发分期

### V2.1：隐含天气识别与可解释输出

目标：先完成不依赖事件搜索的高价值闭环。

开发内容：

- 扩展 Agent 1 提示词和 Schema，加入 temporal/location/weather requirements、隐含信号和 criticality。
- 增加两阶段定位协议：Agent 1判断需要位置后，API以`needs_context + resume_token`暂停；Web/小程序获得授权后向同一run补交坐标并续跑。已有有效授权定位可随首次请求直接传入；明确目的地时不请求无关的当前位置。
- Context Router 校验设备定位，Location Resolver 支持坐标反向解析；定位不可用时读取用户默认城市/时区，最后才使用 demo 全局默认城市。
- 实现无日期时近 3 天默认窗口和 `default_applied`。
- Weather Tool 支持日期范围；先提供日级摘要。
- 扩展 Agent 2 的 `environment_adjustments`、`carry_recommendations`。
- 扩展 Agent 3 的引用、不可用状态和随身物品审校。
- API、Context Pack、持久化和 Web 显示事实 → 影响 → 行动。

首批验收：

- 明天穿什么：授权定位后直接查询当前位置的明天天气；拒绝授权时使用默认城市。
- 去北京旅游该怎么穿。
- 周末户外婚礼穿什么。
- 今晚露台约会穿什么。
- 纯“黑色马甲怎么搭”不误触天气。

### V2.2：完整时间语义与小时天气

开发内容：

- 新增 Calendar Resolver：后天、周末、下周、下个月、节日、时段、日期范围和时区。
- Weather Provider 增加小时级温度、体感、降水、风、UV、日出日落。
- 支持多日、多城市 Context Pack 和关键时段摘要。
- 增加 forecast / climate_reference 类型与远期限制。

首批验收：

- 明早骑车上班。
- 周末露营。
- 中秋去杭州。
- 圣诞节去北京（远期不能给伪精确预报）。
- 下周上海飞北京、三天后返程。

### V2.3：事件检索与活动窗口

开发内容：

- 新增 typed Event Lookup Tool 和 Provider 接口。
- 定义官方/场馆/票务来源优先级、证据引用、缓存和超时。
- 处理巡演、多城市、多场次、同名活动和结束时间估算。
- 将场馆时区、室内外、开散场和出行缓冲传给小时天气。
- Web 展示事件来源和歧义选择。

首批验收：

- 下周去看《悲惨世界》演出。
- 周末去某音乐节。
- 明晚看球。
- 同名演出有两个城市时必须澄清。
- 官方无结束时间时必须标记估算。

### V2.4：环境增强、质量评测与运行保障

开发内容：

- 按优先级接入官方天气预警、AQI、花粉、静态地理环境等独立工具。
- 增加缓存、限流、Provider 熔断和事实新鲜度策略。
- 建立隐含上下文 benchmark、事实引用准确率和建议覆盖率。
- 增加真实 Provider 烟测、监控和数据源异常告警。
- 可选：远期行程临近时重新查询的提醒机制；必须由用户明确开启。

## 15. 代码改动地图

| 位置 | 预计改动 |
|---|---|
| `tools/weather/schemas.py` | 时间窗口、小时事实、context type、evidence、limitations |
| `tools/weather/provider.py` | 小时预报、UV、日期范围、Provider 能力声明 |
| `tools/weather/client.py` | 时间窗口解析、预报/气候分流、部分可用语义 |
| `tools/calendar/` | 新增相对时间、节日、时区解析 |
| `tools/events/` | 新增活动检索接口、Provider、证据与歧义模型 |
| `orchestration/context_router.py` | 从单工具调用升级为依赖图、两阶段暂停/续跑和按规范化查询键幂等缓存 |
| `llm/schema.py` | Agent 1 requirements、Agent 2 调整/随身物品、Agent 3 环境审校 |
| `llm/prompts.py` | 隐含场景判断、事实 → 影响 → 行动、few-shot 和禁止事项 |
| `models/context.py` | typed temporal/location/event/environment context |
| `repositories/user_preferences_repository.py` | 用户默认城市、时区、定位授权偏好和体感偏好；不保存本次精确坐标 |
| `repositories/database.py` / `task_run_repository.py` | 安全迁移`needs_context`状态、保存可恢复节点和单次续跑令牌元数据；禁止落库原始坐标 |
| `workflow/graph.py` | Agent 1 前后两阶段和工具依赖状态传播 |
| API / Vue / 小程序 | 定位授权与本次坐标传递、城市改选、上下文卡片、调整说明、随身清单、歧义澄清 |
| `evals/` / `tests/` | 场景矩阵、工具合同、端到端 grounding 和失败用例 |

## 16. 测试与评估

### 16.1 单元测试

- 相对日期、跨月、跨年、节日和时区解析。
- 近 3 天默认窗口与 `default_applied`。
- 小时天气字段、空字段、部分时段缺失和 Provider 超时。
- Event Lookup 多匹配、无匹配、估算结束时间和来源优先级。
- 显式目的地、事件场馆、设备定位、默认城市的优先级；过期/低精度/未授权定位。

### 16.2 三 Agent 合同测试

- Agent 1 对“明天穿什么”必须声明天气，而“黑色马甲怎么搭”不得无条件声明天气。
- Agent 2 每条环境调整都必须有事实引用和真实衣橱 ID。
- 随身物品不得进入 `item_ids`。
- Agent 3 拒绝无依据的“带伞”、把气候参考写成精确预报、错用其他城市天气等。
- 同一请求中，每个 `provider + 规范化地点段 + 时间窗 + 粒度` 查询键至多执行一次；多城市或不连续窗口可以产生多个键。Agent 1细化及后续重组复用对应事实快照。

### 16.3 Benchmark 指标

- Implicit Context Recall：真正需要环境上下文的请求被识别比例。
- Unnecessary Tool Rate：纯知识/纯造型请求误调天气比例。
- Time/Location Resolution Accuracy。
- Event Resolution Accuracy 与 Ambiguity Safety Rate。
- Weather Claim Grounding：天气主张具有有效事实引用的比例。
- Adjustment Coverage：关键风险是否落实为穿搭调整或随身建议。
- Long-range Honesty：远期气候参考被正确标注的比例。
- Wardrobe Integrity：搭配衣物 100% 属于用户衣橱和候选范围。

### 16.4 核心端到端验收表

| 输入 | 预期 |
|---|---|
| 明天穿什么 | 本次设备定位 + 明天；拒绝授权则默认城市；天气可用时 4 次 LLM 调用；解释具体调整 |
| 去北京旅游该怎么穿 | 北京 + 近 3 天；显示 `default_applied=true` |
| 下周去看《悲惨世界》 | 先解析场次/城市/开散场，再查活动窗口天气；歧义则澄清 |
| 中秋节去杭州 | 解析具体年份和日期；超出窗口时只用气候参考 |
| 今晚骑车回家 | 查询返程小时窗口；考虑风、雨、能见度，安全优先 |
| 周末户外婚礼 | 时间、地点、降水、风、草地鞋履与礼仪共同作用 |
| 夏天中午海边拍照 | UV、风、湿热；服装调整及水/墨镜/遮阳帽/发圈按事实选择 |
| 秋天晚场演出 | 散场时段体感下降时选择真实衣橱外套并说明 |
| 黑色马甲怎么搭 | 无时空场景时不强行查天气 |
| Weather unavailable | 正常推荐，但无天气断言和带伞等天气建议 |

## 17. 安全、隐私、性能和可观测性

- 定位必须获得用户授权；原始精确坐标只存在于未完成run的内存状态，run终止即删除。15分钟天气缓存只使用取小数点后2位的坐标键；长期只保存城市/时区和授权偏好，不保存持续轨迹。
- 外部事件页面只转成 typed facts，丢弃网页中的指令性文本。
- Trace 保存工具名、脱敏参数、Provider、来源 ID、请求/完成时间、缓存命中、状态和错误码。
- 缓存键至少包含 Provider、规范化地点、时间窗口和粒度；Agent 重组不得重复请求相同事实。
- 设置 Provider 超时、限流、熔断和最大工具调用预算，避免一个推荐因外部查询无限等待。
- 极端天气、空气质量和健康敏感场景使用一般性安全提醒，不给医学诊断。
- 最终界面必须展示数据更新时间，避免用户把旧事实当成当前天气。

## 18. 开发优先级结论

下一步应先实现 **V2.1**，不要一开始就接演出搜索。V2.1 能先解决“明天穿什么”“去某地旅游”等大多数隐含天气需求，并建立 Agent 2/3 的可解释输出合同。合同稳定后，再做小时级时间语义和 Event Lookup，避免外部数据复杂性掩盖三 Agent 输入输出问题。

完成 V2.1 后的可验收结果应是：用户只说自然语言，Agent 1 自动识别环境依赖；天气事实通过 Context Pack 进入 Agent 2/3；最终答案明确说明哪项天气事实改变了哪件衣服或哪项出门准备，同时严格区分衣橱衣物、外部随身物品和不可用事实。
