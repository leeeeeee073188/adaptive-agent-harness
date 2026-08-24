# Adaptive Agent Harness

面向长链路真实任务的架构优先 Agent Harness。目标不是针对某个 Bench 追加规则，而是把模型执行增强为一套可组合、可观测、可恢复、可离线演进的系统；Benchmark 只负责从外部验证通用能力是否提升。

## 设计来源与取舍

- **DeepSeek Harness**：采用 Plugin/ServiceKey、作用域服务、可逆 effect、事件溯源和 Turn/Step 分层；不绑定 Cordis/TypeScript 实现。
- **Tencent Youtu-Agent**：采用 Agent/Environment/Toolkit/Context 分离，以及 rollout/judgement/practice 分层；不让 ground truth 或 task id 进入在线学习。
- **DeerFlow**：作为首个 Runtime Adapter，复用模型、Sandbox、Browser 与 MCP 能力，但不拥有核心状态和策略。
- **RealReplicaBench**：作为外部 integration/evaluation adapter，业务 ontology 与 mock 协议不得进入 Core。

## 总体架构

```text
Composition Plane
  Profile / Bundle / Overlay / immutable fingerprint
        │
        ▼
Plugin Kernel ── Scoped Service Registry ── reversible lifecycle
        │
        ├── Capability Plane: Model / Environment / Toolkit / Context / Tools
        │     └── Task-aware Working Set: Task / State / Evidence / Failure / Experience
        ├── Runtime Plane: Inbox / Turn / Step / RuntimeAdapter
        ├── State Plane: append-only SessionLedger → disposable projections
        └── Policy Plane: Contract / Evidence / Progress / Recovery / Completion

Offline Evolution Plane
  Outcomes / Rollouts
        → admissibility & leakage checks
        → immutable Profile candidate
        → paired Shadow evaluation
        → integrity / sample / quality / cost / regression gates
        → Promote | Keep Shadow | Reject
        → append-only decision history and deterministic Rollback

External adapters
  DeerFlow Runtime       RealReplica contract/provider/evaluation
```

## 核心不变量

1. **Model-visible means Ledger-backed**：模型上下文只能由可回放事实投影产生。
2. **Capability is a service**：消费者依赖稳定 `ServiceKey`，而不是具体 provider。
3. **Core does not know the benchmark ontology**：邮件、日历、文档、Workbench 等规则由 integration 注入。
4. **Completion requires evidence**：工具返回的自然语言“完成”不能作为证明。
5. **Recovery execution is not recovery success**：只有后续语义进展或完成证据才能记为有效。
6. **Evolution is offline and governed**：运行中的生产 Profile 不会自主改写；候选必须先 Shadow、通过门禁并可回滚。
7. **Evaluation stays external**：verifier、rubric、ground truth、expected answer 不进入在线 Runtime。
8. **Context is a decision working set**：不是无限对话回放；任务、工具协议和高价值事实必须在预算内保持可审计。

## 模块边界

| 模块 | 职责 |
|---|---|
| `kernel.py` / `events.py` | Plugin 生命周期、作用域服务、waterfall/serial/parallel 事件 |
| `ledger.py` / `task_state.py` | append-only 事实和可删除、可重建投影 |
| `task_contract.py` | 通用 artifact/count/schema 解析与 `CriterionExtractor` 扩展点 |
| `tool_runtime.py` / `tool_reliability.py` | 工具生命周期、瞬态错误分类和有界重试 |
| `progress.py` / `recovery.py` | 语义进展、结构化恢复决策、执行与结果归因 |
| `context.py` | 五层上下文评分、Token预算、工具协议原子性、紧凑工具事实和泄漏过滤 |
| `action_ledger.py` | Tool Intent/Resource投影、Mutation epoch、重复验证预算与可回放决策 |
| `evolution.py` | Profile 版本、Shadow 评估、晋升/拒绝/回滚治理 |
| `integrations/deerflow*.py` | DeerFlow stream/event/runtime 桥接 |
| `integrations/realreplica*.py` | Bench 专属 Contract 语义、Observation Provider 与评测适配 |

## 自进化不是在线自改 Prompt

`EvolutionManager` 管理不可变 Profile 版本，并把每次候选、评估、晋升、拒绝与回滚写入 Ledger。默认门禁要求：

- paired matched samples ≥ 5；
- 质量提升置信下界 ≥ 0；
- token 增幅 ≤ 10%；
- 回归数 = 0；
- integrity 与 leakage 检查均通过。

样本或成本估计不足时只保持 Shadow；泄漏、完整性、质量、成本或回归失败时直接 Reject。该模块不挂接在线 Runtime 的写路径，因此模型无法在一次任务中修改生产 Profile。

## Task-aware Context Working Set

上下文被拆为 Immutable Task、Active State、Evidence、Failure、Experience 五层，并按可配置比例与相关性/新近性/状态重要度/失败重要度/证据价值评分。首个任务和工具调用/结果组保持原子；被淘汰的大结果留下脱敏的参数、结果哈希、有限预览和重复次数。动态工具数据保持 user authority，静态防注入规则保持 system authority；每次选择以 `context/selected` 写入 Ledger，但审计事件不复制敏感正文。

该模块的 Shadow 迭代也展示了 Evolution 门禁的作用：`v1` 因过度压缩导致重复读取并失败；`v1.1` 恢复到 1.0，但相对探索性 Vanilla 对照 Token +111.1%、工具调用 +23，因此 Reject。`v1.2` 修复“不同 description 被误判为不同调用”后，同题保持1.0和字节级相同产物，Token降至166,070（方向性+9.23%），但工具调用仍+7、耗时+76.8%，且只有一个非fresh-pair样本，因此继续Shadow。

## Tool Action Ledger / Verification Budget

P19把工具轨迹投影为可回放的Intent、Resource、Data field、Mutation epoch和结果哈希，而不是只统计Tool call数量。Verification Budget在成功写入后重置；同范围或连续验证超预算时生成Warn事实。DeerFlow中间件默认`observe`，不会阻断或改写工具结果。

扩展Browser/API语义后，7条轨迹的200个Action达到100%分类；5条确定性Browser和2条stable-pass控制均0告警，2条历史失败控制均被覆盖。Wilson门禁只允许非阻断Advice，不允许Enforcement。

实际Shadow仍按成本门禁处理：v1.4只在最后一次验证触发1次Advice，却达到189,696 Token、23 Tool，Reject；v1.5加入“第4次同scope且结果不变的Read”Advice后，同题1.0、154,036 Token（方向性+1.31%），但实际0次Advice触发、Tool仍+7、耗时+128%，因此改善不可归因于策略并继续Shadow。

## 验证

```bash
uvx ruff check src tests scripts
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m compileall -q src tests scripts
```

当前 93 项零模型测试覆盖 Core/integration 边界、Plugin/Ledger/Runtime、Contract/Evidence、Tool Reliability、Task-aware Context、Tool Action Ledger/Advice Gate、Progress/Recovery，以及 Evolution 的 Shadow/Promote/Reject/Rollback 与 JSONL replay。

RealReplicaBench 仅作为外部验证：冻结 MiniBench16 覆盖类型、能力与难度，未运行完整 107 任务。本阶段只执行同一 Development 任务的两个探索性 Context Shadow，不能声称总体通过率提升。历史证据、成本停止规则和可声明边界见 [`evidence/index.json`](evidence/index.json)。

详细架构见 [`docs/architecture.md`](docs/architecture.md)，重构决策见 [`docs/architecture-refactor-plan.zh-CN.md`](docs/architecture-refactor-plan.zh-CN.md)，简历案例见 [`docs/resume-case-study.zh-CN.md`](docs/resume-case-study.zh-CN.md)。
