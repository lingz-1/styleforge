# Prompt 注入防护

## 1. 适用范围

本项目把 Prompt 注入视为“不可信数据试图改变 Agent 控制面”。不可信数据包括用户输入、网页与
MCP 返回、RAG 文本、衣物名称和描述、会话历史、偏好记忆、工具观察及其他 Agent 生成的证据。
这些内容可以提供业务事实，但不能改变 system 规则、Agent 路由、可见工具、工具前置条件或输出
契约。

本防护采用纵深设计，目标是降低注入成功概率、阻止越权和外传，并留下低基数诊断；不宣称能够
仅靠关键词检测消除所有未知攻击。

## 2. 调用链

```text
ContextVisibilityPolicy
  -> ContextAssembler
  -> PromptAssembler（可信 system / 不可信 user 分离、边界转义、信号扫描）
  -> ContextGuard（按真实包装后长度计预算）
  -> LLM（仅收到当前 Agent 的工具目录）
  -> AgentRuntime（再次校验工具是否在当前目录）
  -> ToolRuntime（Schema -> 出站安全 -> Hook -> 前置条件 -> Handler）
```

## 3. 已实现的控制

- `system` 只包含版本化 Harness、Prompt Security、工具协议、Agent 专属指令及能力清单。
- 所有动态内容只放入 user role，并分别包在 `STYLEFORGE_RUNTIME_DATA` 和
  `STYLEFORGE_USER_REQUEST` 区块；动态文本中的伪造边界会转义，不能提前闭合区块。
- 中英文规则覆盖层级覆盖、角色伪造、系统提示/凭据索取、强制工具调用、边界伪造和常见密钥
  形态。诊断只保存信号数、类别和来源类型，不保存命中文本。
- AgentRuntime 对模型返回的每一个工具名做当前可见目录复核；隐藏或其他 Agent 的工具返回
  `TOOL_NOT_AUTHORIZED`，不会执行。
- `search_web`、`get_weather` 的外发文本有长度 Schema 和出站校验；疑似注入、敏感信息或多行
  指令返回 `PROMPT_INJECTION_BLOCKED`。被拒参数不会进入 `PreToolUse` 钩子。
- 工具参数 Schema、运行时前置条件、决策/工具数量契约、ContextGuard 和确定性 Environment Gate
  继续作为独立边界；Prompt 文本不能解除这些程序约束。
- `/health.observability` 仅聚合 `prompt_security` 信号，不记录原始用户内容、工具参数或密钥。
- 任务后的长期偏好提炼属于可选副作用；原请求命中注入信号时整次跳过，防止恶意文本被蒸馏为
  持久记忆，已完成的穿搭任务不受影响。

## 4. 行为语义

检测到疑似注入时不会默认拒绝整个请求。系统忽略试图改变控制面的部分，继续处理可识别的合法
穿搭需求；合法目标无法区分时才请求澄清。对搜索和天气等会离开进程的参数采用更严格策略：命中
即阻止该次工具调用，Agent 可在有界循环中改写为正常业务查询。

## 5. 验证与剩余风险

`tests/test_prompt_security.py` 覆盖信号扫描、system/user 隔离、边界伪造、隐藏工具越权、密钥与
注入出站拦截、Hook 不接触被拒参数及正常查询不受影响。既有 Prompt、记忆、RAG、Research、
Stylist、Critic 和端到端测试同时断言动态业务事实仍可用，但不再进入 system role。

剩余风险主要是未命中特征的新型间接注入、模型对复杂自然语言的误判、未来新增外部工具未登记
出站字段，以及第三方模型自身缺陷。新增外部工具时必须同时定义：最小参数 Schema、外发字段、
权限归属、前置条件、安全 observation 和对抗测试；不能只把工具加入目录。
