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
        ├→ immutable Profile candidate → paired Shadow evaluation → Profile Promotion
        └→ Candidate Experience → leakage / generalisation / dedup / transfer validation
                                → Experience Store → bounded runtime retrieval

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
9. **Success first, waste bounded**：Token 是诊断指标；Resource Guardrail 按 No-progress Event 升级干预。
10. **One multimodal primary model**：语言与视觉任务统一使用 `deepseek-v4-flash-vision-exp`，不要求独立视觉模型。

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
| `assembly.py` | Profile → Plugin Factory → Kernel 的可执行、失败回收装配 |
| `runtime_contract.py` / `policy_session.py` | Runtime 无关请求/结果契约，以及 Completion/Progress/Recovery/Resource/Context 统一策略会话 |
| `resource_guardrail.py` | No-progress 的记录、重规划与 Action Scope 阻断决策 |
| `evolution.py` | Profile 版本、Shadow 评估、晋升/拒绝/回滚治理 |
| `experience_evolution.py` | Development Rollout 分组、轨迹去泄漏与组间相对 Candidate 蒸馏 |
| `ledger_rollout.py` / `distiller.py` | 可信 Ledger 转换、稳定 fingerprint、默认关闭的模型 Distiller 与零模型 Fake |
| `experience_store.py` | Candidate Experience、Transfer Validation、晋升/隔离/退休、检索与结果归因 |
| `integrations/deerflow*.py` | DeerFlow stream/event/runtime 桥接 |
| `integrations/realreplica*.py` | Bench 专属 Contract 语义、Observation Provider 与评测适配 |

## 自进化不是在线自改 Prompt

`EvolutionManager` 管理不可变 Profile 版本，并把每次候选、评估、晋升、拒绝与回滚写入 Ledger。Profile 默认门禁要求：

- paired matched samples ≥ 5；
- 质量提升置信下界 ≥ 0；
- 回归数 = 0；
- integrity 与 leakage 检查均通过。

Token、工具调用、延迟和视觉调用始终记录，但固定增长比例不会单独 Reject 成功 Candidate；同 Scope、同策略且无新证据或状态变化时，由 Resource Guardrail 依次记录、强制重规划并阻断继续重复。`ExperienceStore` 使用独立的 Candidate/Shadow/Promoted/Quarantined/Retired 生命周期，运行时检索不能执行晋升或修改全局 Prompt/Policy。

## Task-aware Context Working Set

上下文被拆为 Immutable Task、Active State、Evidence、Failure、Experience 五层，并按可配置比例与相关性/新近性/状态重要度/失败重要度/证据价值评分。首个任务和工具调用/结果组保持原子；被淘汰的大结果留下脱敏的参数、结果哈希、有限预览和重复次数。动态工具数据保持 user authority，静态防注入规则保持 system authority；每次选择以 `context/selected` 写入 Ledger，但审计事件不复制敏感正文。

该模块的 Shadow 迭代也展示了 Evolution 门禁的作用：`v1` 因过度压缩导致重复读取并失败；`v1.1` 恢复到 1.0，但相对探索性 Vanilla 对照 Token +111.1%、工具调用 +23，因此 Reject。`v1.2` 修复“不同 description 被误判为不同调用”后，同题保持1.0和字节级相同产物，Token降至166,070（方向性+9.23%），但工具调用仍+7、耗时+76.8%，且只有一个非fresh-pair样本，因此继续Shadow。

## Tool Action Ledger / Verification Budget

P19把工具轨迹投影为可回放的Intent、Resource、Data field、Mutation epoch和结果哈希，而不是只统计Tool call数量。Verification Budget在成功写入后重置；同范围或连续验证超预算时生成Warn事实。DeerFlow中间件默认`observe`，不会阻断或改写工具结果。

扩展Browser/API语义后，7条轨迹的200个Action达到100%分类；5条确定性Browser和2条stable-pass控制均0告警，2条历史失败控制均被覆盖。Wilson门禁只允许非阻断Advice，不允许Enforcement。

历史 Shadow 当时仍按已废止的固定成本门禁处理：v1.4只在最后一次验证触发1次 Advice，却达到189,696 Token、23 Tool；v1.5加入“第4次同scope且结果不变的Read”Advice后，同题1.0、154,036 Token（方向性+1.31%），但实际0次 Advice 触发、Tool仍+7、耗时+128%。这些 evidence 保持原样，不追溯改写为新策略结果；v1.5仍因单样本且改善不可归因而保持 Shadow。

## 验证

```bash
uvx ruff check src tests scripts
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m compileall -q src tests scripts
```

零模型测试覆盖 Core/integration 边界、Executable Profile Assembly、Plugin/Ledger/Runtime、Contract/Evidence、Tool Reliability、Task-aware Context、Tool Action Ledger、Resource Guardrail、Progress/Recovery，以及 Profile/Experience 两套独立生命周期与 JSONL replay。

RealReplicaBench 仅作为外部验证：冻结 MiniBench16 覆盖类型、能力与难度，并内部隔离为 8 Development / 4 Transfer Validation / 4 Held-out Evaluation；不会运行完整 107 任务。当前新模型还没有新的总体成功率证据，不能声称总体通过率提升。历史证据和可声明边界见 [`evidence/index.json`](evidence/index.json)。

下一阶段的 Runtime Conformance、共享 Policy Session、Ledger→Rollout、Distiller Adapter 和零模型门禁已经落地。`evidence/a20-runtime-evolution/summary.json` 明确区分“架构检查通过”和“允许付费 Candidate”：前者已通过，后者在新模型 Development baseline 与 Stable-pass Controls 生成前保持 false。

真实 `deepseek-v4-flash-vision-exp` 单任务 Development 实验随后暴露了新的失败链：Vanilla 在 266,169 Token 后未交付；初版 v2 因工具风暴上升到 1,815,707 Token。经过原子工具预算、图执行终止和 delivery-first recovery，v2.6 交付了 JSON，将公开检查从 0/5 提升到 2/5、Capacity 从 0.0 提升到 0.4，同时相对 Vanilla 的观察 Token 降低 11.1%、耗时降低 44.7%。但任务仍未通过、工具调用仍增加，因此 v2.6 只保留为 Shadow，未运行 Transfer、Held-out 或更多任务。机器证据见 `evidence/a22-live-model-evolution/summary.json`。

对 v2.6/v2.7 的零模型因果回放进一步定位到四个通用 Harness 缺口：完整观察被压成短 preview、durable failure state 没进入实际 model middleware、artifact exists 被误当作完成、task/turn 生命周期混用。v3 因此加入 Visible Evidence Workspace、durable state handoff、跨 turn 单调 Action Ledger、public JSON shape/consistency 与 source-access obligations、evidence-gap recovery、soft phase controller 及统一 secret redaction。它的真实 canary 仍回退到 0/5：模型看到了状态，却继续通过路径别名和批量 shell 重读本地材料。v3.1 用 source-first phase、resource budget 和三阶段窗口把 Token 降至 216,688、耗时降至 56.3 秒，但仍未访问公共 API、没有产物。v3.2 增加安全的 loopback public-source materializer，并在第二次 cache block 时提前结束当前 turn；真实结果恢复了来源证据和一个产物，但产物四个集合全空，`Tool call limit reached` 又被误当成最终回复，最终仅 1/5、231,897 Token、47 Tool，仍不晋升。

v3.3 针对这条真实失败链加入四项通用修复：仅由公共任务中的强完整性措辞和 JSON 示例结构派生集合 non-vacuity 约束（不保留示例值，且显式允许空集合时不启用）；拒绝运行时控制/错误文本作为 Final；把直接执行任务提供的 Python 脚本识别为 synthesis transform，使其可穿过 delivery recovery；把文本错误 envelope 记为失败，禁止伪造 mutation epoch。真实 canary 正确阻止了空产物，却暴露了新的组合错误：Response Gate 拒绝后覆盖了 Evidence assessments，Phase 从 synthesis 回退，最终 0/5、无产物、277,043 Token。

v3.4 将 Response 与 Evidence Completion 改为真正的合取，响应拒绝不再丢失 Artifact/Source assessments；同时按 [DeepSeek 官方思考模式](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode) 为 OpenAI-compatible 请求冻结 `thinking.type=enabled` 与 `reasoning_effort=max`。A36/A37 在 137 项核心测试和 pinned-container 实际模型工厂中验证了 max 字段、reasoning-content 兼容层和 completion 合取。真实 A38 canary 中 reasoning tokens 和请求字段均可见，但最终仍仅 1/5、719,574 Token、64 Tool、1,043.6 秒，并以 `mutation epoch cannot move backwards` 结束；相对 Vanilla Token 增加 170.3%，因此禁止扩跑且不能声称 max thinking 提升了能力。

详细架构见 [`docs/architecture.md`](docs/architecture.md)，当前实施顺序见 [`docs/harness-core-evolution-plan.zh-CN.md`](docs/harness-core-evolution-plan.zh-CN.md)，简历案例见 [`docs/resume-case-study.zh-CN.md`](docs/resume-case-study.zh-CN.md)。
