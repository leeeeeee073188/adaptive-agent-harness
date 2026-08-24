# Diagnostic4：高效率跨类型“测试—优化—测试”流程

## 目标

旧流程连续在同一个 CLI 数据审计任务上迭代，能定位深层生命周期错误，但反馈面过窄，且一次失败可能消耗数十万 Token。新流程先用四个互斥的简单 Development 任务获得跨类型信号，再按失败簇优化通用 Harness 模块，避免围绕单题打补丁。

所有真实模型测试继续固定：

- 主模型：`deepseek-v4-flash-vision-exp`；
- `thinking=enabled`；
- `reasoning_effort=high`；
- 一次只运行一个任务，`parallelism=1, limit=1`；
- LLM Judge 仅在 rubric 声明主观检查时调用。

## 初筛集合

`splits/diagnostic4.development.collection.json` 从官方 Dev70 中按类型、简单难度、`max_actions`、timeout 和固定 seed 确定性选择，并排除 MiniBench16 的全部 Development/Transfer/Held-out 内部角色。

| 顺序 | 类型 | 难度 | 能力 | 任务 |
|---|---|---|---|---|
| 1 | File | easy | text-only | `file-google-trends-data-quality-audit` |
| 2 | CLI | easy | vision | `cli-alibaba-silicone-kitchenware-publish` |
| 3 | Browser | medium | browser-text | `browser-reddit-auth-browse` |
| 4 | API | medium | text-only | `api-slack-contacts-sourcing-project-status-brief` |

四项均为 `max_actions=60, timeout=1800s`。选择算法和候选数量记录在 `splits/diagnostic4.selection.json`，不是根据 Candidate 成绩事后挑题。

## 分层执行

### D0：零模型门禁

每轮先运行 Unit、Runtime Conformance、历史轨迹 replay、公共 Contract 反事实、容器 wiring、泄漏扫描和配置 fingerprint。任何失败都不得调用真实模型。

### D1：Diagnostic4 breadth-first

按 collection 顺序逐项运行，每项结束立即冻结 Ledger、Token、Tool、Latency、Artifact 和公开 verifier 结果。普通任务失败不会阻止剩余类型的首次采样，否则又会退化为单类型优化；只有以下全局故障立即停止：

- integrity / split / credential 泄漏；
- Runtime crash 或状态不可重放；
- 未受控 no-progress loop；
- 同一 fingerprint 已存在等价付费结果；
- 产物或证据包含 evaluator-private 数据。

完成四个首次样本后才允许修改架构。不得在每个任务后立刻针对任务文本加规则。

### D2：失败簇驱动优化

把四条轨迹投影到下列模块维度，而不是只看最终 pass：

1. **理解与规划**：Contract 提取、子目标、依赖顺序、终止条件；
2. **上下文与证据**：Working Set、来源覆盖、claim lineage、压缩后可恢复性；
3. **工具选择与参数**：Tool intent、schema、路径/selector、MCP/API 能力协商；
4. **环境交互**：Browser 状态、文件系统、网络/API、副作用确认；
5. **产物合成**：结构、非空性、完整性、跨来源聚合、格式转换；
6. **验证与 Grounding**：公共 Contract、source coverage、provisional copy、事实复核；
7. **Recovery 与循环控制**：failure classification、artifact lifecycle、bounded retry；
8. **多模态**：视觉输入是否真正进入主模型、视觉证据是否进入 lineage；
9. **Experience Evolution**：只从 Development rollout 离线蒸馏，跨任务泛化、去重、去泄漏；
10. **成本与可观测性**：Input/Output/Reasoning Token、Tool、Latency、重复资源、Judge 调用。

优化候选必须说明影响哪些模块、预期覆盖哪些任务类型，并用至少一个正例和一个反例锁定行为。优先修复跨两个以上类型复现的失败；单类型问题必须落在通用接口或能力适配层，不能写 task id/答案特判。

### D3：定向复验

只复验受改动模块影响的 Diagnostic4 任务；未受影响且 fingerprint/环境相同的结果复用。若通用模块变化可能影响全部类型，再运行四项完整 paired 复验。

### D4：难度递进

Diagnostic4 无明显 Regression 后，才进入：

1. MiniBench16 Development 8；
2. 冻结 Profile 与 Experience Store；
3. Transfer 4，仅验证，不蒸馏；
4. Held-out 4，仅最终评估，不调参；
5. 不默认运行完整 107 任务。

## 晋级与停止

晋级不要求固定 Token 增长比例，但要求：

- 不出现无意义重复读取、重复脚本或空占位产物循环；
- 至少两个任务类型的过程指标或质量改善，且没有类型级 Regression；
- 所有行为变化能由 Harness 事件、Profile fingerprint 和公共证据解释；
- Candidate 仍未通过时只能保持 Shadow，不能声明成功率提升。

若四类失败分散在不同层，先修共享底层（状态、工具语义、Artifact/Verification 生命周期）；若只在某个能力边界出现，则改能力协商或 Adapter，不修改全局 Prompt。这样每轮真实模型调用提供的是“跨类型架构信息”，而不是单题试错。

## 执行入口

```bash
# 每次只运行一个 index；默认 thinking=enabled/high
python -m real_replica_bench.cli run \
  --config configs/realreplicabench_adaptive_diagnostic4.yaml \
  --run-id adaptive-harness-diagnostic4-v4-0-file \
  --start-index 1 --limit 1

# 后续依次改为 2、3、4，并为 CLI/Browser/API 使用独立 run-id；
# 每项完成后先冻结证据，再启动下一项。
```

在 D0/D1 完成前，不运行 MiniBench Transfer、Held-out 或 full107。
