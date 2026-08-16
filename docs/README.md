# StyleForge 文档索引

当前入口：[项目状态](PROJECT_STATUS.md)。其中严格区分已验证、已实现未验证和未完成能力，并记录完整订单表及 Mytheresa 全量路径审计结果。

这组文档面向三类读者：项目维护者、技术面试官和希望在本地复现演示的人。

| 文档 | 内容 |
|---|---|
| [项目状态](PROJECT_STATUS.md) | 当前完成度、验证证据、未验证改动与已知限制 |
| [问题与解决记录](ISSUE_LOG.md) | 按日期记录每次排查与修复（现象、根因、修复、验证） |
| [开发过程记录](DEVELOPMENT_LOG.md) | 按批次记录需求边界、设计、实现顺序、开发故障与验证证据 |
| [系统架构](ARCHITECTURE.md) | 多 Agent 边界、数据流、约束与降级路径 |
| [扩展任务业务与 API](EXTENDED_TASKS.md) | Context Pack、五类扩展业务、统一执行接口、输入输出与边界 |
| [会话与记忆契约](SESSION_CHAT_MEMORY.md) | 多轮对话持久化 + Context-Aware 偏好记忆闭环的接口与 Schema 契约 |
| [小程序衣柜接口](WARDROBE_MOBILE_API.md) | 微信小程序衣柜管理 API 契约（上传/编辑/订单导入/实拍图） |
| [天气上下文工具](WEATHER_CONTEXT.md) | Agent 1 上下文需求、Open-Meteo 事实、Context Router、失败语义与配置 |
| [天气与时空上下文 V2 方案](WEATHER_CONTEXT_V2_PLAN.md) | 隐含天气识别、设备定位、时间/事件解析、小时预报、三 Agent 契约、场景矩阵与开发分期（设计完成，尚未实现） |
| [数据与模型](DATA_AND_MODELS.md) | 数据审计、严格搭配子集、FashionCLIP 与 FAISS 产物 |
| [中英分类结构](category_taxonomy.md) | 27 大类 + 70 细分类中英标签、上传表单大类必选/细分类可选联动 |
| [本地部署](LOCAL_DEPLOYMENT.md) | 环境、初始化、API、Streamlit 和故障排查 |
| [测试与评估](TESTING_AND_EVALUATION.md) | 评估方案总纲：单元/集成测试与离线质量指标 |
| [功能测试用例手册](FUNCTIONAL_TEST_CASES.md) | 60 个黑盒功能用例（输入/动作/测试范围/判定方法） |
| [功能覆盖评估报告](EVALUATION_REPORT.md) | 60 用例↔门禁↔实测对照、独立裁判质量级证据、盲区与缺陷记录 |
| [p-outfit 门禁覆盖设计稿](P_OUTFIT_EVAL_TEST_COVERAGE.md) | p-outfit 独立 LLM 评估的门禁测试覆盖表（A-F 组设计） |
| [p-outfit 门禁完成报告](P_OUTFIT_EVAL_TEST_REPORT.md) | p-outfit 门禁设计与实际完成对照、真实运行闭环 |
| [后续工作](NEXT_STEPS.md) | 下一验收顺序、MVP 缺口和作品集完善路线 |
| [复现边界](REPRODUCIBILITY.md) | 当前机器复用路径、干净环境缺口和发布清单 |
| [订单衣柜导入](ORDER_WARDROBE_IMPORT.md) | 收货/退款硬过滤、购物订单预览、确认、数据库结构和自动嵌入 |
| [Mytheresa 接入](MYTHERESA_INTEGRATION.md) | 多受众目录、全量路径审计、安全导入和合并嵌入步骤 |

## 阅读建议

- 想快速了解项目：先读“项目状态”和“系统架构”。
- 想运行演示：直接读“本地部署”。
- 想判断技术可信度：重点看”功能覆盖评估报告”（覆盖矩阵 + 独立裁判质量级证据 + 缺陷记录）、”数据与模型”和”测试与评估”。
- 继续开发：先读“开发过程记录”了解最近的架构决定和踩坑，再按“后续工作”的 P0 清单执行；测试通过前不要提交真实订单或导入主库。
