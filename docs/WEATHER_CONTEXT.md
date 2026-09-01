# 天气上下文工具

> 实现日期：2026-08-10  
> 当前形态：Agent 侧保持进程内 typed Tool；Provider 主路径通过官方 Time/Fetch MCP 调用
> Open-Meteo，失败时回退原有直连 Provider。StyleForge 自身另在 `/mcp/` 提供 MCP Server。
> 具体协议边界见 [MCP 集成](MCP_INTEGRATION.md)。
> 下一阶段：隐含天气需求、设备定位、时间/事件上下文和可解释建议见[天气与时空上下文 V2 详细方案](WEATHER_CONTEXT_V2_PLAN.md)，该方案尚未实现。

## 1. 业务边界

天气模块只返回可追溯事实，不返回“穿风衣”“换靴子”等穿搭结论。所有搭配判断仍由三个主 Agent 完成：

1. Agent 1 首次输出 `context_requirements.weather`，决定当前请求是否真的需要天气，并提取地点和日期。
2. Context Router 校验参数，通过 Tool Registry 调用 `weather.get_weather`。
3. 天气可用时，同一个 Agent 1 基于事实细化 `practical_context` 和 FashionCLIP 英文检索短语；这不是新增 Agent。
4. Agent 2 使用事实处理层次、材质、鞋履与实穿平衡，但只能从候选池选衣物。
5. Agent 3 把事实纳入 `wearability` 审校，不得引用未提供的天气信息。

普通请求仍为 3 次 LLM 调用；需要且成功取得天气的请求为 4 次，其中 Agent 1 调用两次。工具调用不计作 LLM 或第四个 Agent。

## 2. 输入输出

Agent 1 的新增输出：

```json
{
  "context_requirements": {
    "weather": {
      "needed": true,
      "location": "上海",
      "date": "明天",
      "reason": "户外活动受降水和温度影响"
    }
  }
}
```

工具事实进入 `environment_context.weather`：

```json
{
  "status": "available",
  "source": "open-meteo",
  "requested_location": "上海",
  "forecast_date": "2026-08-11",
  "temperature_min_c": 25.0,
  "temperature_max_c": 32.0,
  "feels_like_c": 31.0,
  "precipitation_probability_percent": 70.0,
  "humidity_percent": 82.0,
  "wind_speed_kmh": 18.0,
  "weather_code": 61,
  "condition": "小雨"
}
```

完整事实和脱敏工具参数同时写入推荐响应、`ContextPack.environment_context`、工作流 trace 和 `styling_runs.semantic_detail_json`。

## 3. 地点、日期与失败语义

地点优先级是：用户请求中的显式地点 → `STYLEFORGE_DEFAULT_LOCATION` → `location_required`。日期支持`今天`、`明天`和`YYYY-MM-DD`，只允许当前日起 16 天预报窗口。

天气服务不可用、地点无法解析或日期无效时，返回`status=unavailable`及明确`error_code`，主推荐继续执行，但三个 Agent 不得生成天气相关主张。这里是缺失外部事实的显式语义，不是用规则模板替代 Agent 结果。

## 4. 配置

```dotenv
STYLEFORGE_WEATHER_ENABLED=true
STYLEFORGE_WEATHER_PROVIDER=open-meteo
STYLEFORGE_DEFAULT_LOCATION=上海
STYLEFORGE_WEATHER_TIMEOUT=10
```

`GET /health`返回天气工具开关、Provider 名称和默认地点是否已配置。Web 推荐结果会展示本次实际使用的天气事实或不可用原因。

## 5. 测试边界

`tests/test_weather_tool.py`通过注入固定 JSON Transport 测试，不访问网络。`tests/test_workflow_llm.py`通过假 Weather Tool 验证：Agent 1 先声明需求、Context Router 只调用一次、Agent 1 基于事实重检索、Agent 2/3均收到相同事实、响应与持久化保留工具轨迹。

Open-Meteo 实时连通性属于运行时烟测，不作为离线 Pytest 的前置条件。
