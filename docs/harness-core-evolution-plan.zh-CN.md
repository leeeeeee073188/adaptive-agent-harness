# Agent Harness Core 与 Offline Experience Evolution 实施计划

状态：执行中；P0、P1完成，P2完成真实 DeerFlow No-progress 接线，P3/P4完成治理与检索基础  
决策基线：`CONTEXT.md`、`docs/adr/0001-*.md` 至 `0003-*.md`  
主要目标：在不把 RealReplicaBench 业务规则写入 Core 的前提下，提高固定分层子集上的 Task Execution Success Rate。

## 行为基线与约束

- 改造前 93 个确定性单元测试通过；重构必须先补回归测试再修改实现。
- DeerFlow 是首个 Runtime Foundation，不是 Harness 产品边界。
- RealReplicaBench verifier、Judge 私有反馈和 expected answer 不得进入在线任务上下文或 Experience。
- 不运行完整 107 任务；使用按任务类型、难度、能力与基线结果分层的固定 MiniBench16。
- MiniBench16 固定划分为 8 Development、4 Transfer Validation、4 Held-out Evaluation。
- Task Execution Success Rate 是第一指标；Token 只作诊断，不设置统一增长比例上限。
- 成本控制依赖 No-progress Event、重复 Action Scope 和失控执行的 Resource Guardrail。
- 主模型统一为 `deepseek-v4-flash-vision-exp`，不再要求独立 MiMo 视觉模型。

## 清理策略

1. 先锁定现有行为和新决策的失败测试。
2. 一次只修复一个架构异味，优先删除双轨语义与脚本硬编码。
3. 复用现有 Kernel、ServiceKey、Ledger、Progress 和 Context seams，不引入新依赖。
4. 历史 evidence 保持不可变；旧成本门禁结果标记为历史策略，不重写实验事实。
5. 每阶段通过单元测试、静态编译和仓库边界检查后再进入下一阶段。

## 阶段

### P0：决策与模型路由固化（已完成，未运行付费任务）

- 更新领域术语和 ADR。
- 将 Profile Promotion 的 Token 硬阈值降级为诊断指标。
- 将 Adaptive Harness 和 RealReplicaBench 的真实运行路由切换为统一多模态主模型。
- 保留 provider/token/tool/latency/视觉调用统计。

完成条件：高 Token 增长不会单独拒绝成功 Candidate；视觉任务默认复用主模型、provider、base URL 和 API key。

### P1：Executable Profile Assembly（已完成）

- 建立 Plugin Registry/Factory。
- Profile 必须确定性装配并挂载 Kernel；未知插件和部分挂载失败必须 fail closed 并完成逆序清理。
- 实验脚本不得在 Profile 外硬编码 Candidate 行为。

完成条件：相同 Profile 指纹产生相同插件顺序与服务面；Overlay 同时改变指纹和运行行为。

### P2：统一 Harness Runtime 生命周期（部分完成）

- 定义 Runtime Adapter Conformance suite，并同时用于 Fake Runtime 与 DeerFlow Runtime Adapter。
- Kernel 统一拥有 Completion、Progress、Recovery、Experience Retrieval 和 Resource Guardrail 生命周期。
- 收敛 `AgentDriver` 与 DeerFlow Policy Bridge 的重复策略语义；保留 Runtime Foundation 特有的事件翻译。

完成条件：相同 Policy Chain 在两个 Adapter 上产生等价的规范事件和决策；真实 RealReplica 路径由 executable Profile 装配。

当前进展：DeerFlow 真实候选路径已启用 Resource Guardrail 并把 No-progress 决策写入 Ledger；Fake/DeerFlow 共用 Conformance suite、完整 Profile 装配以及 `AgentDriver`/Policy Bridge 语义收敛仍待完成。

### P3：Offline Experience Evolution（治理基础已完成）

- 将 Experience 定义为结构化 Trigger、Situation、Strategy、Anti-pattern、Progress Signal、Stop Condition 与 Provenance。
- 按可迁移的任务状态和 Failure Pattern 对 Development Rollout 分组，比较成功、部分成功和失败轨迹。
- 执行去泄漏、泛化、去重和 provenance 检查。
- Profile Promotion 与 Experience Promotion 使用独立生命周期和 append-only 事件。

完成条件：Candidate 至少来自 3 个不同 Development Task，并在至少 2 个未参与蒸馏的 Transfer Task 上通过验证后才可晋升。

当前进展：结构化 Experience、去泄漏、精确内容去重、Transfer Gate 和 append-only 生命周期已实现；从 Rollout Group 自动比较并蒸馏 Candidate 的生成器仍待实现。

### P4：Experience Retrieval 与结果归因（基础已完成）

- 先按任务状态、失败类型、Runtime Surface 结构化过滤，再进行可选语义排序。
- 每次最多注入 1–3 条 Experience；每条必须携带触发原因、建议动作、Progress Signal 和 Stop Condition。
- 记录 adoption、progress、success、ineffective 和 harm 等 Retrieval Outcome。
- 支持 `candidate → shadow → promoted → quarantined → retired` 生命周期；运行时不能自我晋升。

完成条件：无关 Experience 不进入工作集；有害或持续无效 Experience 可确定性隔离并通过 Ledger 重放。

当前进展：结构化检索、最多三条注入、model-visible provenance 隔离和 Retrieval Outcome 已实现；基于累计负面结果自动提出 Quarantine Candidate 仍待实现。

### P5：分层 MiniBench16 验证（数据隔离完成，付费验证未开始）

- 固定 8/4/4 数据隔离；Development 可蒸馏，Transfer 只验证 Experience，Held-out 只评估最终 Profile。
- 保证任务类型、官方难度、Runtime Surface、视觉能力与基线成败均有覆盖。
- 仅在通过确定性预检后运行付费任务；先跑 Development canary，再跑 Transfer，最终 Candidate 才运行 Held-out。

完成条件：报告成功率、Stable-pass Regression、总 Token、成功任务平均 Token、无效动作、连续 No-progress、工具与视觉调用；不运行完整 107 任务。

## 当前停止条件

若任一阶段出现 verifier 泄漏、Held-out 污染、Stable-pass Regression、无法重放的 Profile/Experience 版本，或 Runtime Adapter 语义不一致，则停止付费评测并回到确定性测试修复。
