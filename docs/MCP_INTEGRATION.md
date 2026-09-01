# StyleForge MCP 集成

## 1. 完成范围

当前实现同时覆盖 MCP 的两个方向，而不是只在项目中安装一个 SDK：

1. **StyleForge 作为 MCP Client**：Agent 的天气事实链通过官方 Time MCP 与官方 Fetch MCP
   完成协议握手、工具发现和工具调用；Fetch 只允许访问 Open-Meteo 的两个 HTTPS 域名。
2. **StyleForge 作为 MCP Server**：向其他支持 MCP 的 Agent/IDE 暴露衣柜摘要、衣柜检索、六任务
   执行和任务结果查询能力，同时提供能力资源和穿搭规划提示词。

本项目没有接入 Hugging Face MCP。FashionCLIP、个人嵌入和向量检索仍是项目内 RAG 能力，避免把
本地衣物图像、向量和用户数据交给无关公共服务。

## 2. 架构与数据流

```text
用户 / 外部 Agent
        │
        ├─ Web / REST ───────────────┐
        └─ MCP Client ─ /mcp/ ──────┤
                                     ▼
                         StyleForge FastAPI + MCP Server
                                     │
                         Task Router（六类任务）
                                     │
                         Multi-Agent Harness
                    ┌────────────────┼──────────────────┐
                    │                │                  │
               衣柜 RAG          PostgreSQL        环境事实工具
          FashionCLIP/keyword      用户隔离         get_weather
                                                        │
                                               MCP Client Adapter
                                     ┌──────────────────┴──────────────┐
                                     ▼                                 ▼
                           official-time MCP                  official-fetch MCP
                           get_current_time                   fetch(Open-Meteo only)
                                                                        │
                                     MCP 失败 ──► 现有 Open-Meteo 直连降级
                                     双路径失败 ─► structured unavailable
```

MCP 是工具协议边界，不取代任务路由、LangGraph、多 Agent、RAG 或数据库。内部 Agent 仍调用稳定的
`get_weather` typed tool；Provider 适配层决定底层通过 MCP 还是直连，因此 Agent prompt 和领域契约
不与传输实现耦合。

## 3. 用户请求进入后的 MCP 行为

| 情况 | MCP 行为 | 结果语义 |
|---|---|---|
| 普通穿搭推荐，包含城市/日期或 Agent 判断需要天气 | Time MCP 解析目标时区日期；Fetch MCP 请求地理编码和预报 | 天气事实进入 Context Pack、三个 Agent 和任务诊断 |
| 穿搭推荐不依赖天气 | 不调用外部 MCP | 不为展示技术而制造无用调用 |
| 局部修改涉及天气变化 | 与推荐任务相同，按需调用 | 仅将事实用于判断修改，不由天气工具生成搭配 |
| 风格建议、单品建议 | 通常不调用；确有时空事实缺口时 Research/Stylish Agent 可请求 | 保持知识任务与外部天气解耦 |
| 衣柜兼容性、衣柜缺口 | 默认依赖衣柜 RAG/偏好，不调用天气 | MCP 调用数可以为 0，这是正确行为 |
| Time MCP 失败 | 使用本机日期继续，记录失败 trace | 任务可降级继续 |
| Fetch MCP 失败 | 尝试现有 Open-Meteo 直连 Provider | `source=open-meteo-direct-fallback` |
| MCP 与直连都失败 | 不把网络异常抛进 Agent | 返回 `status=unavailable` 和稳定错误码 |

每个任务的 `diagnostics.mcp` 包含调用数、成功/失败数、server、tool、耗时、稳定错误码和安全的
逐调用 trace。它不保存工具参数、城市原文、完整上游响应、API Key 或数据库凭据。

## 4. 接入的公共 MCP Server

### official-time

- 包：`mcp-server-time`
- 工具：`get_current_time`
- 用途：用目标地点时区确定“今天/明天”的本地日期，避免服务器时区影响语义。
- 默认传输：stdio；可通过 `STYLEFORGE_MCP_TIME_URL` 切换到 Streamable HTTP。

### official-fetch

- 包：`mcp-server-fetch`
- 工具：`fetch`
- 用途：调用 Open-Meteo 地理编码、反向地理编码与天气预报 JSON API。
- 默认传输：stdio；可通过 `STYLEFORGE_MCP_FETCH_URL` 切换到 Streamable HTTP。
- 安全边界：仅 HTTPS；默认只允许
  `api.open-meteo.com` 与 `geocoding-api.open-meteo.com`；拒绝 URL 凭据和任意主机。

当前固定 `mcp>=1.29,<2`，原因是上述官方 Time/Fetch Server 的已发布版本显式依赖 MCP SDK 1.x。
升级到 SDK 2.x 前必须先确认两个 Server 的兼容版本并重新跑协议测试。

## 5. StyleForge 对外 MCP Server

公开工具：

| 工具 | 读写 | 说明 |
|---|---|---|
| `styleforge_get_system_status` | 只读 | 返回去敏后的数据库、RAG、天气、LLM、MCP 与观测状态 |
| `styleforge_get_wardrobe_summary` | 只读 | 按 `user_id` 返回衣柜数量、槽位、类型、颜色和偏好权重摘要 |
| `styleforge_search_wardrobe` | 只读 | 复用已部署的 FashionCLIP/文本混合检索，失败时显式 keyword 降级 |
| `styleforge_execute_task` | 非幂等 | 执行六类任务之一，持久化 run，并可能提取偏好证据 |
| `styleforge_get_task_result` | 只读 | 仅允许 `user_id` 读取自己持有的 run |

另有：

- Resource：`styleforge://capabilities`
- Prompt：`styleforge_plan_outfit`
- Streamable HTTP：`http://127.0.0.1:<API_PORT>/mcp/`
- 运行状态：`GET /mcp/status`

所有工具使用扁平、强约束 JSON Schema；字段长度和结果数量有上限；不存在任意 SQL、任意文件读取、
任意 URL 请求或跨用户衣柜访问工具。

## 6. 启动和连接

随 FastAPI 启动的 Streamable HTTP Server：

```powershell
.\scripts\start_styleforge.ps1 -Action Start -ApiPort 18000 -UiPort 15173
```

MCP endpoint 为 `http://127.0.0.1:18000/mcp/`。统一启动器会把 Vite `/api` 代理同步到实际
`ApiPort`，因此使用非默认端口时衣柜不再请求错误的 8000 端口。

独立 stdio Server：

```powershell
$env:PYTHONPATH=(Resolve-Path ".\apps\api")
D:\anaconda\envs\style\python.exe -m styleforge.mcp_server
```

独立 Streamable HTTP Server（仅调试时使用，正常部署直接使用 FastAPI 内挂载端点）：

```powershell
$env:PYTHONPATH=(Resolve-Path ".\apps\api")
D:\anaconda\envs\style\python.exe -m styleforge.mcp_server `
  --transport streamable-http --host 127.0.0.1 --port 18001 --http-path /mcp
```

## 7. 配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `STYLEFORGE_MCP_ENABLED` | `true` | MCP Client 总开关 |
| `STYLEFORGE_MCP_WEATHER_ENABLED` | `true` | 天气 MCP 主路径开关 |
| `STYLEFORGE_MCP_TIMEOUT` | `20` | MCP 初始化、发现和调用超时秒数 |
| `STYLEFORGE_MCP_TIME_URL` | 空 | 非空时用远程 Streamable HTTP Time Server |
| `STYLEFORGE_MCP_FETCH_URL` | 空 | 非空时用远程 Streamable HTTP Fetch Server |
| `STYLEFORGE_MCP_FETCH_ALLOWED_HOSTS` | 两个 Open-Meteo 域名 | Fetch URL 主机白名单 |

生产环境若使用远程 MCP URL，应由可信内网服务提供 TLS、鉴权和访问日志；不要把公网未知 MCP URL
直接写入配置。当前项目默认 stdio 模式在本机按调用创建短生命周期会话，规避跨 FastAPI worker
事件循环共享问题。

## 8. 验证范围

`tests/test_mcp_integration.py` 覆盖：

- MCP 初始化、工具发现、工具调用和错误结果；
- 未知工具拒绝与稳定错误码；
- trace 去敏和有界运行时状态；
- Fetch HTTPS/主机白名单；
- MCP → 直连 → structured unavailable 两级降级；
- StyleForge 五工具 Schema、读写注解；
- 真实 stdio 子进程通过 MCP 协议读取 PostgreSQL 隔离用户衣柜。

端到端验收还应包含：随 FastAPI 启动的 `/mcp/` Streamable HTTP 握手、真实 Time/Fetch/Open-Meteo
调用、一个含天气的六任务请求、任务诊断回读和 Web 健康页展示。
