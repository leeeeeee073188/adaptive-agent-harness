# 下一代 Adaptive Agent Harness 架构

状态：P0 实施中  
输入：真实 v2.6/v2.7 失败分析、论文调研、同类产品技术调研  
目标：提升通用 Agent Harness 的复杂任务成功率，而不是为 RealReplicaBench 写业务特判。

## 1. 架构原则

1. **Harness 拥有状态与控制，模型只选择下一步动作。**
2. **Ledger 是事实源，Context 是可丢弃投影。**任何压缩都必须可恢复、可审计。
3. **阶段由证据推进，不由模型自述推进。**阶段控制器只约束义务和预算，不规定思考路径。
4. **交付是事务，不是一次 write。**draft、validation、commit、rollback 必须分开。
5. **运行时不学习。**Experience 只从 Development Rollout 离线蒸馏、验证、晋升；运行时只读检索。
6. **成本约束无进展，不约束有效思考。**不设置任意 25% Token 增幅上限。
7. **Bench 标签只属于评估层。**任务类型、难度和 partition 不进入 Core runtime policy。

## 2. 从现有架构演进，而不是重写

现有模块继续保留：

- `SessionLedger`：append-only 事实与回放；
- `TaskContract` / `TaskState`：公共契约与状态投影；
- `KernelPolicySession`：Runtime-independent Policy 语义；
- `TaskAwareContextManager`：模型工作集选择；
- `ToolActionLedger` / `ResourceGuardrail`：工具语义与无进展治理；
- `ExperienceStore` / `ExperienceEvolution`：离线经验控制面；
- `RuntimeAdapter`：DeerFlow 与后续 Runtime Provider 接缝。

新增能力应形成 deep module，而不是在各 Adapter 里复制规则。

## 3. 模块与小接口

### 3.1 Visible Evidence Workspace

职责：把 action-observation 历史编译为 typed、addressable、content-addressed evidence blocks。

```python
snapshot = EvidenceWorkspace.compile(messages, task_text=task_prompt)
snapshot.dashboard
snapshot.blocks
```

接口保证：

- 按 canonical resource 跨工具聚合访问；
- 每个 block 带 payload hash、大小、访问次数、工具来源、归档状态；
- 大结果保留任务相关和约束相关 excerpt，全文仍留在原始事件/外部归档；
- 同一 payload 不重复占用模型工作集；
- credential/secret 在进入 block 前清除；
- 编译确定性、无模型调用。

接缝：`context.py` 调用；DeerFlow middleware 只负责消息类型转换。

### 3.2 Durable Runtime State Handoff

职责：把 Ledger 投影出的当前 `TaskState` 在一次实际 model call 范围内绑定到 Policy Context。

```python
with bind_policy_session(session, task_state=frozen_task_state):
    runtime.stream(...)
```

接口保证：

- snapshot 深度冻结，调用方后续修改不会污染；
- Context middleware 能看到 failures、latest completion、recovery、evidence；
- ContextVar 生命周期只覆盖当前 runtime call；
- Core 不导入 LangChain/DeerFlow。

### 3.3 Evidence-driven Soft Phase Controller

职责：根据 Contract、Evidence Workspace、Artifact State 和 Failure State 输出阶段建议。

```python
decision = PhaseController.evaluate(task_state, evidence_workspace, budget_state)
```

建议阶段：

```text
contracted → acquiring → synthesizing → drafted → validating → ready
                     ↘ repairing ↗
```

阶段不是硬编码工作流：模型仍可选择工具和动作顺序；控制器只输出：

- 尚未满足的公共义务；
- 当前允许/保留的 action budget；
- 必须保留的 evidence blocks；
- replan、repair、stop 条件。

这吸收 Agentless 的阶段确定性，但避免把更强模型锁进僵硬状态机。

### 3.4 Public Artifact Contract

职责：只从 model-visible prompt、public schema 和 Runtime observation 推导 artifact 要求。

多维结果：

```text
present
shape_valid
consistency_valid
source_coverage_satisfied
public_self_check_passed
ready_to_commit
```

禁止输入：rubric、expected answer、verifier reason、Held-out 数据。

首批通用检查：

- 文件存在、格式可解析；
- public JSON example/schema 的 required keys 与类型；
- 明确声明的数量、唯一性和 dependency；
- model-visible source-of-truth 是否有访问证据；
- artifact 与明确声明的 source/constraint 是否建立 provenance。

### 3.5 Transactional Artifact Lifecycle

职责：避免 review 用更差结果覆盖更好 artifact。

```text
draft(path, hash)
  → stage(validation report)
  → commit(version)
  → supersede(previous version)
  ↘ rollback(previous version)
```

规则：

- review 写 staging path，不直接覆盖 committed artifact；
- public checks 不通过则进入 repair；
- 任何新版本质量证据弱于 committed version 时自动 rollback；
- 生命周期事件全部写 Ledger，可删除投影后重建。

### 3.6 Hierarchical Budget Governor

职责：统一 task、turn、phase、resource 四层预算。

```python
decision = BudgetGovernor.admit(action, phase, progress, state)
```

原则：

- 对有新 resource coverage 或 state mutation 的动作放行；
- 对跨工具、跨 turn 的同资源无变化访问累计；
- 为 synthesis、artifact write、validation 保留额度；
- tool/token/latency 是联合诊断量，阻断依据是无进展因果链；
- 预算耗尽返回下一步义务或已有 observation handle，而不是只返回通用错误。

### 3.7 Failure-aware Offline Experience

Experience key 从单一 task state 扩展为：

```text
(phase, failure_type, runtime_surface, obligation_kind, evidence_gap)
```

经验内容必须包含：适用条件、策略、anti-pattern、progress signal、stop condition、失效条件。

晋升链：

```text
Development Rollout
→ Candidate Experience
→ leakage/generalization/dedup
→ historical replay
→ Development shadow
→ Transfer validation
→ Experience Store
```

Held-out 不参与蒸馏、筛选或在线检索调优。

## 4. 状态生命周期所有权

| 状态 | 生命周期 | 所有者 |
|---|---|---|
| Session Ledger | task run | Runtime Adapter/Core |
| ToolActionLedger mutation epoch | task run | Tool governance |
| delivery/artifact version | task run | Artifact lifecycle |
| non-mutating admission count | policy turn | Budget Governor |
| phase | task run，evidence-derived | Phase Controller |
| Context selection | model call | Context Manager |
| Experience Store | cross-run, immutable versions | Offline Evolution Plane |

禁止用同一个 key 同时承载不同生命周期。v2.7 的 mutation epoch 回退和 delivery reset 就是该
不变量被破坏的直接结果。

## 5. 创新点

这些设计不是简单复制 DeepSeek Harness 或 Youtu-Agent：

1. **可逆的 Evidence Workspace**：Context 中显示 block dashboard，原始 observation 可寻址恢复；
2. **Evidence Obligation Graph**：阶段由公共义务覆盖率推进，而不是固定 planner 文本；
3. **Monotonic Artifact Quality**：事务化版本和 rollback 保证 review 不降低已交付质量；
4. **Causal Budgeting**：按 progress/resource/phase 判断无效成本，而不是固定 Token 比率；
5. **Failure-conditioned Workflow Memory**：经验不仅按任务相似度，还按阶段和证据缺口检索；
6. **Counterfactual Promotion**：候选 Policy/Experience 必须在相同历史轨迹上证明不会增加误阻断，
   再进入付费 Shadow。

## 6. 实施切片

### Slice A：修复已证实的 P0 生命周期问题

- Durable TaskState 进入实际 model middleware；
- ToolActionLedger/delivery 使用 task-run key；
- non-mutating budget 单独使用 turn key；
- v2.7-style 跨 turn replay 不再出现 epoch 回退或写后阻读。

### Slice B：Visible Evidence Workspace

- 地址化 observation blocks；
- 跨工具资源聚合；
- constraint-aware excerpt；
- Context audit 报 block/archived/repeat 指标；
- v2.6 历史消息做零模型 context replay。

### Slice C：Soft Phase + Public Artifact Contract

- 从公共输入提取 source/schema/uniqueness obligations；
- phase decision 写 Ledger；
- artifact exists 不再等于 ready；
- 合成任务验证“不可信草稿不能单独完成任务”。

### Slice D：Transactional Artifact + Hierarchical Budget

- staging/commit/rollback；
- phase reserve；
- 跨 turn/resource no-progress；
- v2.7 空 artifact 覆盖场景回放。

### Slice E：Offline Experience

- 从 v2.6/v2.7 只生成去泄漏 Candidate；
- 用其他 Development/Transfer 合成或历史轨迹验证；
- 未通过前不进入 runtime Experience Store。

## 7. Acceptance Criteria

### 确定性门禁

1. 全量现有测试保持通过；
2. v2.6/v2.7 历史轨迹 0 模型调用 replay；
3. actual model middleware 在第二 turn 看得到第一 turn failure/recovery；
4. mutation epoch 在 task run 内单调，delivery write 跨 turn 有效；
5. large tool results 被丢出 history 后，关键约束仍存在于 Evidence Workspace；
6. 相同资源跨 `read_file`/`bash` 聚合，secret 不进入 working set；
7. artifact public self-check 不使用任何 evaluator-private 字段；
8. review 失败不能覆盖已 commit artifact；
9. Experience retrieval 不包含 source task id 或 private verifier 内容。

### 真实模型门禁

只有完成对应 Slice 的零模型门禁后，才运行一个 Development canary。扩跑必须同时满足：

- task passed；
- integrity passed；
- 无 uncontrolled no-progress loop；
- 相对基线没有明显无意义动作回归；
- artifact public checks 全部通过；
- 独立 review 无 High/Medium blocker。

任何单任务结果都不能直接授权 Transfer、Held-out 或完整 107。

## 8. 明确拒绝

- RealReplica 任务 ID、grader 文案、expected answer 进入 Core 或 Experience；
- 在线修改全局 Prompt/Policy；
- 默认开启 tree search 或多 Agent 以换取表面成功率；
- 只增加 turn/tool/token 上限；
- 单模型自由文本 review 直接覆盖 artifact；
- 只凭单个 Development 任务宣称架构成功。
