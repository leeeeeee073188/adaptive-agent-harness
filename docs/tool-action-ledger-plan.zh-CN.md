# P19 Tool Action Ledger / Verification Budget 实施计划

状态：执行中。

## 目标

从通用Harness控制面识别“执行语义相同或验证范围相同、但没有产生新状态”的工具调用，减少长任务中的重复读取和过度验证。该模块不理解MiniBench任务答案，不按task id、文件名或业务字段写特判。

## 行为基线

- v1.2单题质量1.0、产物SHA与探索性Vanilla一致；现有Contract/Evidence/Recovery/Context行为必须保持。
- v1.2共16次工具调用，其中12次Bash；不能通过简单禁止Bash或减少最大步骤伪造优化。
- Tool call / ToolResult协议必须原子完整；写入、转换和最终验证不能被错误判为冗余。
- 新策略默认只观察；没有安全控制和质量证据前不阻断真实工具调用。

## 小步顺序

1. 定义可回放`ActionRecord`：Intent、Resource、Data field、执行参数、结果哈希、是否改变状态。
2. 纯规则分类通用Tool/Bash意图：Discover / Read / Search / Transform / Write / Verify / Present / Unknown。
3. 构建`ActionLedger`与`VerificationBudget`：写入后重置验证窗口，同范围无新结果的重复验证产生Warn候选。
4. Context只消费ActionLedger投影，不重复维护另一套工具指纹逻辑。
5. DeerFlow适配器先以observe-only接线并记录审计；Advisory/Enforce作为独立Profile变量。
6. 在v1/v1.1/v1.2和历史轨迹做零模型反事实，检查必要Write/Transform未被阻止、协议无孤儿、无secret进入审计。

## 完成门禁

- 现有83项测试不回归并新增Intent、Resource、预算重置、并发隔离、审计重放测试。
- v1.2轨迹能聚合重复partial-data检查，但生成CSV与最终验证保持允许。
- 不运行MiniBench第二题或完整107；只有Advisory候选通过零模型控制后才创建新付费Profile。

