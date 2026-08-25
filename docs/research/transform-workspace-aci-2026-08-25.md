# Transform / Workspace ACI：从 A61 失败到安全执行能力

## 问题证据

A61 的 File/easy 复验表明，Harness 已经做到：

- 从公共 Contract 指向正确产物 `outputs/quality_audit.json`；
- 阻止错误输出路径、`/tmp` 和路径穿越；
- 把无交付进展的模型批次提前结束，将 Token 降低 34.8%、Tool 减少 14 次；
- 仍未完成任务，因为任务提供的 `workspace/analysis/audit.py` 通过 `__file__` 解析到不存在的 `/task/snapshots/manifest.json`，而真实公共数据位于 `/task/workspace/snapshots/`。

这不是继续放宽 Sandbox 或修改任务答案的理由。它暴露的是 Agent-Computer Interface（ACI）缺少“执行前依赖可见性”和“安全派生 Transform”能力。

## 一手资料

1. [SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering](https://arxiv.org/abs/2405.15793) 将工具接口本身视为影响 Agent 表现的关键变量，而不是把所有失败归因于基础模型。其 [ACI 设计说明](https://github.com/SWE-agent/SWE-agent/blob/main/docs/background/aci.md) 强调短、结构化、可操作的观察，以及在编辑阶段先做确定性校验。
2. [Agentless](https://arxiv.org/abs/2407.01489) 使用 localization → repair → validation 的有限阶段，说明很多工程任务不需要开放式无限 Agent loop；先定位和验证候选修改可以显著降低成本。
3. [OpenHands](https://arxiv.org/abs/2407.16741) 与其 [Runtime Architecture](https://docs.openhands.dev/openhands/usage/architecture/runtime) 把 Action execution、Sandbox、Observation formatting 和 Agent controller 分层；Runtime 负责安全、可复现地把执行结果格式化为观察，而不是让模型猜环境拓扑。
4. [OpenHands Software Agent SDK](https://arxiv.org/abs/2511.03690) 进一步将 Agent、Tool 与 Workspace 包解耦，支持本地/远端执行可移植性，说明 Workspace 是独立能力边界，而非 Prompt 附件。

这些资料共同支持：下一阶段应改 ACI/Workspace/Validation，而不是继续增加全局 Prompt 或给模型更多盲试 Token。

## 设计：Workspace Affordance Graph

新增只读、零模型的 `WorkspaceManifest`：

```text
Public file/source
  └─ hash / media type / size / role

Transform
  ├─ entrypoint hash
  ├─ static read dependencies
  ├─ static write targets
  ├─ unresolved dependencies
  └─ safety flags

Required Artifact
  ├─ file or directory
  ├─ current existence/version
  └─ produced_by candidate transforms
```

第一版只分析公共 workspace 中有界数量、有界大小的 Python transform。它通过 AST 解析显式的 `Path(__file__)`、`.parent`、字面量 `/` 拼接和 `.open/read_text/write_text`，不执行代码、不读取私有目录、不跟随 symlink。模型上下文只接收相对路径、hash 和缺失依赖，不接收整段源码。

## 设计：Ephemeral Transform Capsule

后续执行面不是“允许模型任意写 helper script”，而是：

1. 输入必须是公共、task-provided transform 的 source hash；
2. 可选修复必须是独立 patch，记录 patch hash，不能覆盖原文件；
3. patched copy 只存在于 `.adaptive/transforms/<derivation-hash>/`；
4. 声明允许读取的公共 roots 与唯一 required output targets；
5. Sandbox 禁止 symlink、私有路径、网络扩权和未声明输出；
6. 执行结果记录 exit、stdout/stderr 摘要、实际输出 hash；
7. 只有 Required Artifact Provider 验证后才算进展；
8. 原始 source hash、patch hash、执行环境 fingerprint 进入 LineageGraph。

这允许模型修复通用的 workspace/transform 兼容问题，同时避免恢复到“写大量临时脚本直到碰巧过 verifier”。

## 与现有模块的关系

- `EvidenceWorkspace`：展示 Manifest 的短摘要；
- `TaskContract`：提供 required artifact nodes；
- `SourceGrounding`：记录 original transform → derived transform → artifact lineage；
- `ActionLedger`：记录 manifest inspect、capsule execute 和 output mutation epoch；
- `Recovery`：缺失依赖属于 `TRANSFORM_PRECONDITION_GAP`，不是盲目 `ARTIFACT_ERROR`；
- `Offline Experience Evolution`：只蒸馏“识别依赖缺口/选择 Capsule”的通用策略，不存任务路径或 patch 正文。

## 安全与成本门禁

- 扫描上限：32 个脚本、每个 256 KiB；
- 只读公共 workspace；拒绝 symlink、隐藏目录和 task root 外路径；
- AST 不支持的表达式标为 unknown，不执行求值；
- Manifest 总模型可见预算不超过 800 estimated tokens；
- Capsule patch/执行在零模型单测、固定容器、历史 A61 replay 通过前默认关闭；
- 不能因 ACI 变更直接声称 Benchmark 成功率提升。

## 实施顺序

1. **P0 Static Manifest**：纯函数 AST 分析、路径归一化、安全边界、A61 反事实；
2. **P1 Context Provider（已完成 A63/A64）**：把相关且有缺口的 Transform 摘要按 4,000 字符总预算加入 before-run Evidence layer；
3. **P2 Capsule Prototype**：Deterministic Fake patch + sandboxed execution，不接真实模型；
4. **P3 Targeted Development**：只在 File/easy 上运行一次；
5. 质量仍无提升则停止，不扩到 API/Browser/MiniBench。

## 创新点边界

相较 DeepSeek Harness/Youtu-Agent 的通用编排和训练外经验，本设计把“可执行环境的静态 affordance、派生 transform lineage、一次性 Capsule 验证”做成独立 Harness 服务。创新声明只能是架构与可重放安全控制；在真实任务通过前不能声明成功率提升。
