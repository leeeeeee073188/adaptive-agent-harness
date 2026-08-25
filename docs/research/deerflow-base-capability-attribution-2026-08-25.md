# DeerFlow 基座能力归因：结论、证据与架构决策

## 结论

**DeerFlow 基座是当前低成功率的重要限制因素，但不是唯一原因，也不是“完全不可用”。**

证据分成两组：

1. 历史 Vanilla DeerFlow 在 MiniBench16 上通过 6/16，说明它能完成部分 File/API/CLI 任务；
2. fresh 当前模型、高思考、同 File/easy 任务中，Vanilla 与 Adaptive 都是 0/5、无产物，说明该任务的质量上限不是 Harness 单独造成。

因此项目不应继续把 DeerFlow 当不可替换“底座”，也不应直接删除它。正确架构是：**Adaptive Harness 为独立控制平面，DeerFlow 只是一个 Runtime Backend。**

## 历史能力分布

旧 Vanilla MiniBench16：

| 类型 | 通过/总数 |
|---|---:|
| File | 3/3 |
| API | 2/3 |
| CLI | 1/5 |
| Browser | 0/5 |
| 总计 | 6/16 |

这显示 DeerFlow 并非全面失效，但 Browser 和复杂 CLI 明显偏弱。

这些结果不能作为当前因果对照：旧实验使用 `deepseek-v4-flash`、关闭思考，并使用 `mimo-v2.5` 视觉路由；当前使用 `deepseek-v4-flash-vision-exp + thinking=enabled/high`。

## Fresh current control

A65 固定除 `adaptive_policy_enabled` 和 variant/run-id 外的全部关键字段：任务、Prompt、模型、thinking、镜像、seed、split、phase、judge、Tool Runtime、token/recursion 配置完全一致。

A66 结果：

| 指标 | Vanilla DeerFlow | Adaptive Harness v4.1 |
|---|---:|---:|
| Capacity | 0.0 | 0.0 |
| 产物 | 0 | 0 |
| Token | 321,950 | 220,496 |
| Tool | 15 | 31 |
| 耗时 | 250.4s | 116.6s |

Vanilla 轨迹已经读取 raw snapshot、识别脚本路径错误并生成分析 helper，但最终没有写 required artifact，也没有 final response；自动 retry 后仍返回 “no final response”。它还遇到两次 Sandbox path false positive：正常 `find ... -not -path '*/node_modules/*'` 被识别为绝对路径，Python 表达式中的除法附近文本也被识别成 `/len` 路径。

Adaptive Harness 没有提升质量，但降低 Token 31.5%、耗时 53.5%；Tool 增加 16 次。因此可声明 Harness 改善了部分运行效率，不能声明提升成功率。

## 根因拆分

### DeerFlow 基座

- Tool schema 存在非语义 required 字段；
- Sandbox 命令路径检查对嵌入脚本产生 false positive；
- 模型完成分析后没有稳定 Artifact/Final 交付；
- 自动 retry 不保证恢复最终响应；
- 历史 Browser 0/5，表明 Browser ACI/状态观察也可能是系统性短板。

### 模型

- 高思考并未自动转化为终止和交付；
- 会生成 helper/inspection，却未完成 required artifact；
- Tool call 批次和长 reasoning 仍可能放大成本。

### Adaptive Harness

- 已改善 required artifact、批次早停和上下文证据；
- 当前仍未把 Transform Manifest 转化为安全可执行计划；
- 在 fresh 对照中 Tool 次数高于 Vanilla；
- 还没有第二个真实 Runtime Backend，无法做 Runtime ranking。

## 架构决策

1. DeerFlow 从“项目底座”降为 `RuntimeBackend(deerflow)`；
2. Core Policy、Contract、Evidence、Experience、Recovery 不依赖 DeerFlow；
3. 新增 Runtime Capability Profile 和 Runtime Selection/Conformance；
4. 下一真实后端优先选择已有强 Sandbox/Action observation 的实现，例如 OpenHands Runtime；在没有明确依赖审批前先做接口和 deterministic fake，不直接引入包；
5. 保留 DeerFlow 作为 compatibility/backward control；
6. 不再为 DeerFlow 单独新增任务特判或无限 middleware；
7. 只有同任务、同模型、同配置的多 Runtime paired evidence 才能决定替换。

该方向与 [SWE-agent ACI](https://arxiv.org/abs/2405.15793) 强调接口设计、[OpenHands Runtime](https://docs.openhands.dev/openhands/usage/architecture/runtime) 强调执行/观察分层，以及 [OpenHands SDK](https://arxiv.org/abs/2511.03690) 的 Agent/Tool/Workspace 解耦一致。

## 下一阶段

- P0：定义 Runtime Capability Profile 与 required capability negotiation；
- P1：Deterministic Fake Runtime + DeerFlow capability snapshot；
- P2：相同 Ledger/Contract 在两个 Runtime 上做 conformance；
- P3：获得依赖授权后接 OpenHands 或另一独立 Runtime；
- 在第二真实 Runtime 出现前，不再运行更多 paid DeerFlow 优化任务。

## 声明边界

- 可以说“fresh control 支持 DeerFlow 是该任务的能力上限因素之一”；
- 不能说“DeerFlow 导致全部失败”；
- 不能说“Adaptive Harness 已优于 DeerFlow 质量”；
- 不能说“OpenHands/Youtu/其他 Runtime 会更好”，因为尚无同配置实测。
