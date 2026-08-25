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

v3.2 的真实结果记录在 `evidence/a32-v3-2-live-canary/`：公共来源 materializer 生效，Agent 生成了 `quality_audit.json`，但四个集合全部为空；浅层 shape gate 接受了这个 scaffold，同时 `Tool call limit reached` 被非空文本策略接受。最终只有 1/5、Capacity 0.2、231,897 Token、47 Tool，v2.6 仍是历史最优 Shadow。

v3.3 的 A33/A34 零模型门禁关闭上述两个伪完成路径，并允许任务提供的直接 Python synthesis transform 穿过 delivery recovery；文本形式的工具错误不会再推进 mutation epoch。A33 使用 v3.2 公共产物反事实证明全空集合被拒绝，A34 在 pinned DeerFlow 容器中验证接线与零模型行为。当前仍只允许同一 Development 任务的一次替代 canary。

v3.3 的真实结果记录在 `evidence/a35-v3-3-live-canary/`：Final 错误文本被正确拒绝，但 Response Completion 的早返回用空 assessments 覆盖了 Evidence Gate，导致 Phase 从 `synthesizing` 回退为 `contracted`，Recovery 只修“acceptable final response”而看不到缺失 Artifact；结果 0/5、无产物、277,043 Token、46 Tool。

v3.4 修复 Completion 合取语义，并按 DeepSeek 官方 OpenAI-compatible 接口同时发送 `extra_body.thinking.type=enabled` 与顶层 `reasoning_effort=max`。`evidence/a36-v3-4-thinking-max-gate/` 锁定公共反事实与 137 项核心测试；`evidence/a37-v3-4-thinking-max-container/` 通过实际 DeerFlow model factory 验证 `PatchedChatDeepSeek.reasoning_effort == max`、thinking enabled、reasoning-content replay 和 assessments 保留。此次同时改变 Harness bug 与模型思考配置，因此只用于继续优化，不作为“max thinking 单变量提升”的因果实验。

v3.4 的真实结果记录在 `evidence/a38-v3-4-thinking-max-live/`：思考模式确实输出 `reasoning_content`，但模型在三个 turn 内生成大量中间脚本并反复触发工具 schema/交付门禁，最终将产物回退为空集合；Capacity 仅 0.2，Token 719,574、Tool 64、耗时 1,043.6 秒，并因 `mutation epoch cannot move backwards` 结束。相对 Vanilla Token 增加 170.3%，因此 v2.6 继续作为历史最优 Shadow，thinking=max 不得扩跑。

A39 离线定位并修复 mutation epoch：真实 ToolCallRequest 的 runtime context 为对象，原 `_run_key` 只识别 Mapping，跨 Turn 退化为不同 `id(runtime)`。修复后 PolicySession 从 SessionLedger 持有 durable run id，中间件优先使用该身份；单测和 pinned-container 对象 context 均验证 epoch `[1,1]`。A40/A41 将 v3.5 冻结为 `thinking.type=enabled + reasoning_effort=high`，同时确认 high request 与 epoch 单调，只授权同一 Development 任务一次运行。

v3.5 的真实结果记录在 `evidence/a42-v3-5-thinking-high-live/`：mutation epoch 回退已消失，Capacity 回到 0.4（2/5），但 Token 765,768、Tool 62、耗时 298 秒；产物 SHA 与公开 `workspace/analysis/results.json` 相同，说明模型最终复制了任务明确要求复核而非照抄的中间结果。相同质量下 v2.6 仅用 236,570 Token 和 75.2 秒，因此 v3.5 保持 Shadow 并停止扩跑。

v3.6 引入 source-grounded synthesis 基础层与 provisional-copy guard。核心 LineageGraph 只保存公共 source/claim/content hash 与 derivation kind；Runtime 仅在公共任务同时出现“provisional/not truth”和“对 raw source 复核”时派生 grounding criterion，并以 512 文件、单文件 2 MiB 上限扫描公共 workspace。A43 使用 A42 公共产物证明 exact copy 会被拒绝并返回可操作 diagnostics；A44 在 pinned container 中验证同一行为。下一阶段仍需加入 authoritative source coverage 与 claim-level support，copy guard 不能替代事实 verifier。

v3.6 live（A45）因 recovery 优先级回退到 0/5：Artifact 不存在时 grounding assessment 是 `BLOCKED`，但 failure classifier 提前选择了 lineage gap，未请求首次交付。v3.7 将 lineage failure 限定为 grounding `UNSATISFIED`，缺失 Artifact 继续走 `ARTIFACT_ERROR + WRITE_PARTIAL`。A46/A47 通过 311 项单测和容器 probe 验证 copy guard、thinking=high 与交付恢复共存；尚未执行付费 canary。

v3.7 live（A48）虽进入正确 recovery，却在 Turn 2 写入多个非输出 Python helper，未生成最终 Artifact。v3.8 的 delivery gate 只允许 `_is_delivery_write`、直接 `TRANSFORM` 和 `NAVIGATE/INTERACT`，明确阻止非输出 `WRITE`；这既约束 CLI 文件任务的临时脚本风暴，也保留 Browser/API 状态转换。A49/A50 零模型与容器门禁通过。

v3.8 live（A51）证明“首次交付”和“校验失败后的修复”不能共享一个永久 satisfied bit。模型在门禁压力下先写了四个全空集合，公共 non-vacuity gate 正确拒绝；但 failure classifier 将 `artifact.json_shape:* = false` 归为 `STATE_INCONSISTENCY`，Recovery 转去 refresh/replan，原 delivery-satisfied 状态又让后续读取恢复，最终形成 65 Tool、858,909 Token 的新循环且只通过 1/5。v3.9 将 missing 与 invalid Artifact 分成 `ARTIFACT_ERROR/WRITE_PARTIAL` 和 `ARTIFACT_INVALID/REPAIR_ARTIFACT` 两条一次性预算，并以用户侧 delivery directive generation 重新武装门禁；工具返回中的 marker 不会伪造新 generation。A52/A53 已用 A51 全空产物反事实、160 项核心 gate、71 项 Runtime/Offline Evolution gate 和 pinned container 证明修复接线，模型调用与新增 Token 均为 0；考虑 A51 的高成本回退，暂不执行 v3.9 live。

连续单题 canary 只用于关闭已知失败链，不是综合能力评测。下一轮改用与 MiniBench16 完全互斥的 Development Diagnostic4：File/easy/text、CLI/easy/vision、Browser/medium/text、API/medium/text 各一项；一次只跑一个 index，但普通单题失败不阻断剩余类型的首次采样，收齐四种类型后才允许修改架构。A54 已用零模型 preflight 锁定四类、easy/medium、text/browser/vision、`max_actions=60`、Dev-only、MiniBench16-disjoint 和 thinking=enabled/high。优化必须同时评估规划、上下文、工具、环境、Artifact、Verification/Grounding、Recovery、多模态、离线 Experience 和成本层，优先修复跨类型失败簇。通过后再进入 MiniBench16 Development，冻结 Profile/Experience Store，最后分别运行 4 项 Transfer 与 4 项 Held-out，禁止根据后两者在线调参。LLM Judge 只在任务 rubric 实际声明主观检查时调用。完整规则见 `docs/diagnostic4-evolution-loop.zh-CN.md`。

Diagnostic4 live（A55）已完成且没有触发任务级即时优化：File 0/5、CLI vision 0/8、Browser 0/7、API 1/13，合计 1,206,127 Token、129 Tool。共同失败面不是某个业务答案，而是 Required Artifact lifecycle：错误输出路径也会满足 delivery、目录 Artifact 被 file-only Provider 判空、Observation HTTP 403 逸出边界，以及 delivery 违规仍可消耗十余次工具调用。v4 以 Contract 中的 required paths 约束交付写入，允许 required directory 的子文件，目录 Evidence 包含 visible file count，Observation `OSError` 转为失败事实，同一 generation 第二次错误交付直接结束 turn。A56/A57 已完成零模型与容器验证；尚未执行 v4 付费复验。

v4 File/easy 复验（A58）仍为 0/5：Token 从 330,962 小幅升至 337,994，Tool 从 43 增至 45，且无最终产物。正向信号是模型已准确命中 required path；新失败是 DeerFlow 的 `write_file` 把非语义 `description` 设为 required，模型只提供 path/content，导致正确写入被 schema 拒绝，之后一个模型批次留下 15 个 delivery 终止结果。v4.1 只在 Tool Adapter 层补缺失 description，保留显式值和所有语义参数；after-model delivery batch guard 在整批无任何交付进展时直接结束本 turn。A59/A60 零模型门禁通过，尚未付费运行。

v4.1 File/easy 复验（A61）仍为 0/5、无产物，因此不晋升也不扩跑；但 Token 220,496（较 v4 -34.8%）、Tool 31（-14）、耗时 116.6 秒（-65.1%），并把 delivery batch 终止结果从 15 个降为 2 个。description repair 本次未触发，因为模型改走 Bash。当前失败转移到通用 Transform/Workspace seam：任务提供脚本按自身位置把 snapshots 解析为不存在的 `/task/snapshots`，模型随后尝试的 `/tmp` 与路径穿越被 Sandbox 正确阻止。下一阶段只做离线安全 Transform Capsule/Workspace Manifest 设计，不再继续付费试错。

A62 完成 P0 Static Transform Manifest：零执行扫描公共 workspace 内最多 32 个、单个 256 KiB 的 Python 脚本，拒绝 hidden/symlink/outside-root，保守解析 `Path(__file__)`、parent、字面量 `/` 和 file read/write。对 A61 的离线回放准确输出一个 missing read `snapshots/manifest.json`、一个已存在 write target `workspace/analysis/results.json` 和一个 dynamic unresolved access；模型调用/新增 Token 为 0。P1 才考虑把 bounded manifest 接入 Evidence layer，P2 Capsule 默认关闭。

v4.2 完成 P1 bounded Evidence Provider：只选 Prompt 点名或直接生产 Required Artifact 的 Transform，最多 8 条、每类 4 个 access、总 payload 4,000 字符；unsafe target 仅暴露计数，动态表达式保持 unresolved。Provider 只在 before-run 扫描一次，A63/A64 已通过单测和 pinned-container wiring，模型调用/新增 Token 为 0。P2 Capsule 仍 disabled，本阶段不执行付费任务。

A65/A66 对 DeerFlow 基座做 fresh attribution：旧 Vanilla 16 题通过 6 题，但配置不可与当前比较；当前同模型/high-thinking File/easy 中 Vanilla 与 Adaptive 都是 0/5、无产物。Vanilla 仍完成了大量数据读取和 helper 计算，却没有 final response，并触发两次 Sandbox path false positive；Adaptive Token/耗时更低但 Tool 更多，质量无提升。架构决策是把 DeerFlow 降为一个 Runtime Backend，下一阶段做 Runtime Capability Profile/Conformance，不再继续 DeerFlow-only paid tuning。

A67 按用户明确要求完成一次当前 v4.2 MiniBench16 全量隔离评测：Development 0/8、Transfer 0/4、Held-out 0/4，总计 0/16；Token 5,152,350、Tool 510、输出文件 6、integrity 16/16。5 个任务有部分 Capacity，但无 task-level pass；10 个任务缺失 Required Artifact，14 个任务出现 Tool/No-progress 信号。该结果否定 v4.2 Candidate，后续 paid expansion 全部停止；下一阶段必须先完成 Runtime Capability/替代基座，而不是继续在 DeerFlow 上追加 Middleware。

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
