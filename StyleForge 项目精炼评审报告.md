# StyleForge 项目评审报告

> 修复复核（2026-09-11）：GitHub API 确认远端最新一次 CI 是 2026-09-01 提交 `4a282f0`，
> 失败于 Ruff；当前本地 HEAD `162cba4` 更新，工作区执行同一 Ruff 范围已通过，但尚未推送取得
> 新的远端结论。根 README/本地部署文档已同步为 PostgreSQL、Vue 3、
> `MultiTaskWorkflow + StyleForgeHarness` 当前主架构；CI 已补前端单元测试并升级到 Node.js 24
> 运行时的官方 Actions 主版本。2026-09-12 本地验证：Ruff clean、后端 842/842、前端 3/3、
> Vite build、确定性质量基准 42/42、FastAPI health/metrics/MCP smoke 均通过。本地通过不能
> 替代下一次远端 CI。

## 一、总体评价

StyleForge 当前已经具备较完整的多智能体穿搭系统能力，Agent 编排、衣橱管理、多模态理解、记忆、工具调用、规则约束、可观测性和评估框架均已有实际实现。

目前主要问题不在“功能不足”，而在于**工程状态、实验验证和架构收敛度尚未完全达到项目本身设计水平**。

## 二、主要问题

### 1. 远端 CI 需要重新验证

原始评审发现 GitHub Actions 存在 Ruff 失败记录。2026-09-11 当前工作区执行同一 Ruff 范围已经通过，但尚未把当前未提交修改推送并取得新的远端 Actions 结果；因此这里的准确状态是“本地静态门禁已恢复，远端仍待新提交验证”。

这会造成：

- 本地测试结果与公开仓库状态不一致；
- 无法证明主分支始终处于可部署状态；
- 对公开项目的工程可信度影响较大。

这是当前优先级最高的工程问题。

---

### 2. README 与实际架构存在不同步（已修复本轮主要偏差）

README 中原先残留 SQLite、Streamlit、已删除 `workflow.graph` 和旧 Planner/三 Agent 主链描述；2026-09-11 已改为 PostgreSQL、Vue 3、`POST /tasks/execute` 与新的 Agentic Harness，并将 Streamlit 明确为非验收主路径。

问题不在文档美观，而在于：

- 外部读者无法快速判断当前真实架构；
- 新旧方案同时出现，容易产生设计混乱的印象；
- 面试时可能被追问“实际使用的到底是哪一套架构”。

需要保证 README、架构图和实际代码保持一致。

---

### 3. 新评估体系尚未形成完整闭环

项目已经设计了较完整的 Evaluation V2，包括：

- Hard Contract；
- Agent / Tool Trace；
- Quality / Stability / Performance；
- Baseline；
- Ablation；
- Offline Benchmark。

已有 Polyvore 100 例 × 2、真实 DeepSeek 六任务、记忆提炼、路由和历史回归结果；但这些结果不能替代 Evaluation V2 的系统级配对基线、消融、稳定性与最新性能实验。当前最大的缺口是：

**历史质量证据已经存在，Evaluation V2 的统一实验闭环尚未完成。**

因此目前还无法用数据回答几个关键问题：

- Multi-Agent 相比单 Agent 是否确实提升效果；
- Critic 是否真正提高约束满足率；
- Memory 是否真正改善个性化结果；
- Research / Grounding 是否提高事实依据与推荐质量；
- 这些提升是否值得额外的延迟和 Token 成本。

这是当前项目从“架构合理”走向“架构得到验证”最关键的一步。

---

### 4. 新旧架构仍存在并存现象

项目已经形成新的 Agentic Harness / LangGraph 主架构，但仓库中仍保留较重的旧 workflow、orchestration 和历史执行路径。

风险主要是：

- orchestration source of truth 不够唯一；
- 新旧逻辑可能出现重复；
- 修复一条链路时可能遗漏另一条；
- 新开发者难以快速理解哪些模块仍然有效。

当前需要进一步明确：

**主路径、兼容层、Deprecated 模块和待删除模块。**

---

### 5. 部分核心文件体积过大

后端部分 workflow/API 文件以及部分 Vue 页面已经承担过多职责。

长期可能导致：

- 修改影响范围扩大；
- 单元测试困难；
- 状态管理与业务逻辑耦合；
- Merge conflict 和回归风险增加。

这属于典型的项目发展到中后期后的结构性技术债务，目前尚未影响基本功能，但已经值得处理。

---

### 6. Agent 架构复杂度需要实验支撑

当前 Coordinator、Research、Evidence、Stylist、Critic、Gate 等模块设计完整，但 Agent 数量和链路复杂度本身不能作为优势。

如果缺少 Ablation，容易被质疑：

> 是否一个能力更强的单 Agent 就可以完成相同任务？

因此项目目前最重要的不是继续增加 Agent，而是证明现有 Agent 分工分别带来了哪些可测量收益。

---

### 7. RAG / Knowledge 层的效果证据仍偏弱

项目已经具备知识检索和 Evidence Grounding 能力，但当前公开结果中，还缺少对知识检索质量本身的系统验证，例如：

- Retrieval Recall；
- Relevant Evidence Rate；
- Grounded Answer Rate；
- 无 RAG / 有 RAG 的效果差异。

因此现阶段 RAG 属于“已实现能力”，但还没有成为“经过充分验证的核心优势”。

---

### 8. 测试数量较多，但测试证据需要进一步统一

项目已有较多 pytest 和确定性测试，这是明显优势。

但当前存在：

**本地测试通过记录 ≠ GitHub 主分支持续集成通过。**

因此后续应把测试结果统一到可复现的 CI pipeline 中，而不是依赖文档中的人工记录。

## 三、问题优先级

### P0：立即处理

1. 推送当前本地 Ruff clean 的修改并确认 GitHub Actions 全绿；
2. ✅ 同步 README 与当前真实架构，并在后续架构变更时持续维护；
3. 执行正式 Evaluation V2 核心实验。

### P1：随后处理

4. 明确新旧架构边界；
5. 拆分过大的核心文件；
6. 完成 Agent Baseline 与 Ablation。

### P2：进一步完善

7. 增强 RAG / Retrieval 独立评估；
8. 补充性能、成本和稳定性长期数据。

## 四、结论

StyleForge 当前不存在明显的项目方向问题，主要问题集中在：

**工程状态未完全收敛、架构价值缺少实验数据证明、公开文档与真实实现存在部分偏差。**

当前阶段不建议继续扩大功能范围。

更合理的工作重点是：

**CI 收口 → 架构收口 → 评估收口 → 文档收口。**

完成这些工作后，项目的实际工程质量与对外呈现质量才能保持一致。
