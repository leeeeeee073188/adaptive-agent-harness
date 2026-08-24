# 下一阶段：Runtime Conformance 与 Offline Evolution 接线方案

状态：v3/v3.1 真实 canary 均回退并保持 Shadow；v3.2 零模型门禁通过，仅允许同任务一个 canary
前置版本：`3f589aa`  
约束：禁止完整 107 任务；确定性门禁通过前禁止付费模型调用。

## 目标结果

下一阶段不继续增加 Bench 特判，而是补齐 Harness 的两个关键闭环：

1. **Runtime 闭环**：同一套 Kernel Policy Session 同时驱动 Fake Runtime 和 DeerFlow Runtime Adapter，消除 `AgentDriver` 与 `DeerFlowPolicyBridge` 对完成、进展、恢复、资源约束和 Experience 检索的双轨语义。
2. **Offline Evolution 闭环**：历史 SessionLedger 可被安全转换为 Development Rollout，经去泄漏、组间相对蒸馏生成 Candidate Experience，但不允许在线晋升或污染 Transfer/Held-out。

## 非目标

- 不替换 DeerFlow 的 Sandbox、Browser、MCP 或模型客户端。
- 不让 Core 导入 DeerFlow 或 RealReplicaBench。
- 不在本阶段运行完整 107 任务。
- 不以 Token 增长比例作为成功 Candidate 的硬拒绝条件。
- 不将 Judge reasoning、verifier、rubric、expected answer 或任务身份写入模型上下文。
- 不在单次任务执行中修改全局 Prompt、Profile 或 Experience Store 生命周期。

## 目标架构

```text
Executable Profile
  -> Plugin Registry / Factory
  -> Kernel
  -> Policy Session
       Contract
       Context + Experience Retrieval
       Completion
       Progress
       Recovery
       Resource Guardrail
       Canonical Ledger Events
  -> Runtime Adapter
       Fake Runtime | DeerFlow Runtime | future runtime

SessionLedger
  -> trusted Development partition check
  -> online/evaluation event boundary filter
  -> DevelopmentRollout
  -> group-relative OfflineExperienceEvolution
  -> Candidate Experience
  -> offline Transfer Validation
  -> Experience Promotion
```

## 执行顺序

### S1：锁定 Runtime Conformance 行为

先新增共享 Conformance 契约，不修改生产实现：

- 成功和异常时 Environment 均被清理；
- request/header 不包含凭据；
- Runtime 事件被规范化并可回放；
- Completion 只能由 Evidence 支持；
- Progress 忽略工具活跃度和控制字段；
- Recovery 决策与结果归因写入 Ledger；
- 连续 No-progress 按 `record → replan → block_scope` 升级；
- Experience 仅按结构化 TaskState、Failure Type、Runtime Surface 检索；
- Runtime 不能执行 Profile Promotion 或 Experience Promotion。

完成标准：同一套测试向量可同时运行于 Fake Adapter 与 DeerFlow Adapter，差异仅允许出现在 provider-specific 原始事件翻译层。

完成证据：`runtime_contract.py`、`tests/test_runtime_conformance.py`；两个 Adapter 共享 canonical lifecycle、凭据前置拒绝、异常 Ledger 保留和 No-progress 序列。

### S2：提取 Kernel Policy Session

建立 Core-owned Policy Session：

- 输入：Canonical Runtime Facts、Task Contract、Profile services；
- 输出：Continue、Complete、Recover、Block Scope 或 Stop；
- 所有判断先写 Ledger，再影响下一步执行；
- Runtime Adapter 只执行动作和翻译事件，不拥有策略语义。

迁移顺序：

1. 从 DeerFlow Policy Bridge 提取纯策略决策；
2. 让 DeerFlow Bridge 变成兼容 Facade；
3. 让 `AgentDriver` 使用同一 Policy Session；
4. 通过 Conformance Suite 后删除重复逻辑；
5. 真实 RealReplica candidate 由 executable Profile 装配该 Session。

完成标准：Completion/Progress/Recovery/Resource/Experience 不再存在两套条件分支；Fake 与 DeerFlow 的规范决策序列一致。

完成证据：`policy_session.py`；`AgentDriver` 直接使用 `KernelPolicySession`，`DeerFlowPolicyBridge` 仅保留 Observation Provider/Facade 并委托相同 Session。

### S3：SessionLedger → DevelopmentRollout

新增离线转换器：

- 必须接收可信 MiniBench Development Task ID 集；
- 拒绝 Transfer/Held-out Task ID，即使调用方错误标记为 Development；
- 只读取允许的 Runtime/Policy 事件；
- 删除或拒绝 Judge、verifier、rubric、expected answer 和 evaluation-only 字段；
- 输出仅含可迁移 TaskState、Failure Type、Runtime Surface、结果分数与脱敏事件；
- 同一 Ledger 重放必须得到相同 Rollout fingerprint。

完成标准：历史 Ledger 可自动进入现有 `OfflineExperienceEvolution`，且任务身份和评测私有内容不会到达 Distiller。

完成证据：`ledger_rollout.py`、`tests/test_ledger_rollout.py`，包含可信 Development allowlist、稳定 fingerprint 和评测字段 fail-closed。

### S4：可替换 Distiller Adapter

建立严格的 Distiller 边界：

- 输入只能是 `ExperienceGroupTrigger + SanitizedRollout[]`；
- 输出必须是结构化 `ExperienceDraft`；
- Adapter 不可直接读取文件系统、verifier 或原始任务目录；
- 支持 Deterministic Fake Distiller，用于零模型测试；
- 实际 LLM Distiller 默认关闭，只能通过显式 Profile 开启；
- 每个 group 至多一次总结/蒸馏调用，低于 3 个来源任务或无结果差异时零调用。

完成标准：Mock 端到端完成 Ledger→Rollout→Candidate；任何泄漏或结构异常均 fail closed 且不部分写入 Store。

完成证据：`distiller.py`、`tests/test_distiller_adapter.py`、`tests/test_offline_evolution_e2e.py`；Model Distiller 默认关闭，Deterministic Fake 完成零模型 E2E。

### S5：MiniBench 8/4/4 门禁

- Development 8：允许生成 Rollout/Candidate；
- Transfer 4：只允许离线验证，不允许蒸馏；
- Held-out 4：仅最终 Candidate 评估，不允许调参或晋升；
- 三组 Task ID、运行目录、Experience provenance 必须互斥；
- 默认每次只运行一个 Development canary；
- 只有零模型 preflight、容器 wiring、历史基线和稳定任务回归均通过时，才产生“允许付费 canary”的显式结果。

付费停止条件：

- Stable-pass Regression > 0；
- verifier/partition 泄漏；
- uncontrolled No-progress loop；
- Profile/Experience fingerprint 不可复现；
- Runtime Conformance 不一致；
- Candidate 成功率无提升且新增无效动作。

零模型门禁见 `evidence/a20-runtime-evolution/summary.json`。真实模型迭代见 `evidence/a22-live-model-evolution/summary.json`：v2.6 在同一 Development 任务上取得部分公开质量提升并降低观察 Token，但仍未通过且有工具调用回归，因此保持 Shadow；Transfer、Held-out 和更多任务继续禁用。

后续因果回放见 `evidence/a23-runtime-failure-analysis/summary.json`，v3 的历史 Context/Contract gate 与容器接线分别见 `evidence/a24-next-generation-gate/summary.json` 和 `evidence/a25-v3-container-conformance/summary.json`。二者绑定相同 source/Profile fingerprint，且只允许配置中的一个 Development canary；任何失败、无进展循环或质量回退都会继续保持 Shadow。

v3 的真实回退记录在 `evidence/a26-v3-live-canary/`：0/5、无产物、350,771 Token、46 Tool，因此未替代 v2.6。v3.1 的 source-first phase、local-resource governor 与三 turn 配置由 `evidence/a27-v3-1-gate/summary.json` 和 `evidence/a28-v3-1-container-conformance/summary.json` 绑定；仍只允许同一 Development 任务的一次替代 canary。

v3.1 的真实结果记录在 `evidence/a29-v3-1-live-canary/`：Token/耗时下降，但仍未访问公共 API、未生成产物。v3.2 使用安全 loopback GET materializer 在首轮前物化显式公共来源，并在第二次本地缓存阻断时提前结束 turn；对应 A30/A31 gate 仍禁止任何任务扩展。

## 测试规格摘要

| 层级 | 必须证明 |
|---|---|
| Unit | Policy Session 状态转换、清理、泄漏过滤、fingerprint、Promotion 边界 |
| Conformance | Fake/DeerFlow 对相同事实产生相同规范决策 |
| Integration | Profile 装配真实 DeerFlow Facade；Ledger 可转换为 DevelopmentRollout |
| Offline E2E | Ledger → Rollout → Candidate → Transfer Shadow，全程零真实模型调用 |
| Preflight | 8/4/4 隔离、模型/镜像/Profile 固定、凭据不落盘、付费门禁 fail closed |

## Token 成本控制

- 架构开发仅运行确定性测试和 Mock Distiller。
- 不重复运行已通过的付费任务；以 fingerprint 判断历史结果是否可复用。
- Distiller 只处理满足来源数量和组间差异门槛的 group。
- Runtime 不设置统一 Token 增长比例，但必须阻断相同 Scope/Strategy 的无进展重复。
- Held-out 只允许 Baseline、最终 Candidate 和必要复验，禁止每轮执行。

## 交付物

- Runtime Adapter Conformance Suite；
- Core Policy Session 与兼容 Facade；
- Ledger Development Rollout Adapter；
- Distiller Adapter + Deterministic Fake；
- MiniBench partition/preflight 报告；
- 更新后的 Architecture、CONTEXT/ADR（仅在出现新硬决策时）；
- 全量测试、静态检查、双代码审查与 Lore Commit。
