# Harness 架构去耦与自进化重构计划

状态：已完成（2026-08-21）。完成证据见 `evidence/a16-architecture-evolution/summary.json`。

## 目标

把 RealReplicaBench 恢复为外部验证器，而不是核心架构的设计来源。核心 Harness 只提供可组合、可观测、可恢复、可演进的通用机制；具体任务语义、状态读取方式和测评覆盖统计由 integration/profile 注入。

## 行为基线

- 保留现有 Contract、Ledger、Evidence、Completion、Recovery、Progress 的公开行为。
- 保留 MiniBench16 作为外部适配层回归，不把其 task id、业务实体或 mock 协议放入核心模块。
- 不运行新的付费模型任务；本阶段使用确定性单元测试和已有证据完成架构验证。

## 小步重构顺序

1. **Contract 提取器解耦**
   - 核心保留通用 artifact、exact-count 和 public-schema 解析。
   - 引入 `CriterionExtractor` 扩展点；外部提取器只返回候选 Criterion，由核心统一完成 ID、参数和依赖校验。
   - 将 listing、mail、calendar、document 的自然语言规则迁入 RealReplica integration。
2. **Observation Provider 解耦**
   - DeerFlow bridge 保留 provider 协议、文件、结构化证据、通用 HTTP JSON provider。
   - Gmail、Google Docs、Workbench 的协议实现迁入 RealReplica integration。
3. **通用 Evolution Plane**
   - 候选由不可变 Profile 版本及变更假设表示。
   - 只允许离线/Shadow 成对评估；默认禁止在线改写 prompt、策略或工具。
   - 以数据完整性、泄漏、样本数、质量置信下界、成本和回归为晋升门禁。
   - 所有 candidate/evaluate/promote/reject/rollback 决策写入 append-only Ledger，并可重放恢复状态。
4. **边界与回归验证**
   - 添加核心源文件禁用 Bench 业务 token 的边界测试。
   - 原有业务解析/provider 测试迁到 integration 测试，确保行为未丢失。
   - 添加 Shadow、Promote、Reject、Rollback、Ledger replay 的确定性测试。

## 保留、迁移、删除

| 处理 | 内容 | 原因 |
|---|---|---|
| 保留 | Kernel / ServiceKey / scoped plugin lifecycle | 通用模块化底座 |
| 保留 | Ledger / TaskState / Evidence / Recovery / Progress | 通用事实与控制平面 |
| 保留 | Profile / Bundle / PluginSpec | 可复现实验配置基础 |
| 迁移 | listing/mail/calendar/document prompt 规则 | Bench/业务语义不应进入 Core |
| 迁移 | Gmail/Docs/Workbench observation provider | 外部系统协议属于 integration |
| 删除 | Core 内固定 `_STATE_RULES` 与目标字段特判 | 阻止按 Bench 失败继续膨胀核心 |
| 不实现 | 在线自主修改生产 Profile | 当前证据不足且不可控 |

## 完成条件

- `task_contract.py` 与通用 DeerFlow policy bridge 不包含 RealReplica 业务 subject、mock 名称或样例实体。
- RealReplica adapter 显式组装其 contract extractor 和 observation providers。
- Profile 候选未通过全部门禁时不能晋升；晋升版本可以确定性回滚。
- lint、静态编译、全部测试通过；MiniBench16 contract/provider coverage 回归不下降。
