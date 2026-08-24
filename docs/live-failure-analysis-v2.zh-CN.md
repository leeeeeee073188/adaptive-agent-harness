# Runtime Evolution v2 真实失败分析

状态：基于已完成的单任务 Development 轨迹；不包含新的模型调用  
分析对象：Vanilla、early v2、v2.3—v2.7  
声明边界：只解释一个公开 Development 任务上的失败链，不推断 MiniBench 或完整 107 题成功率。

## 1. 结论

v2.6 解决了“完全不交付”这一层问题，却没有解决“交付物可证明地正确”。它在第二个
Policy turn 中被要求立即交付，于是把工作区已有结果原样复制到输出目录；Harness 只验证
文件存在，最终得到 2/5，而不是任务通过。v2.7 增加 review turn 后又暴露两个跨 turn
状态错误：写入状态和 delivery 状态被重置，导致验证读取被再次阻断，并最终覆盖出更差的
空产物。

首要问题不是 DeepSeek 本身，也不是 LLM Judge。当前失败链由以下 Harness 缺口共同造成：

1. 模型实际看到的工作集丢失了完整观察和 durable failure/recovery state；
2. Completion Contract 只有 artifact-exists，没有公共来源覆盖、结构一致性和语义就绪度；
3. 预算是 per-turn / exact-scope 的局部上限，不是跨 turn、资源级、阶段感知的调度器；
4. delivery-first 是二态开关，不区分 missing、drafted、validation、repair；
5. Offline Experience 已有治理结构，但没有候选经验进入本次运行时工作集。

## 2. 直接证据

### 2.1 质量与成本

| 版本 | Capacity | Checks | Token | Tool calls | 输出 |
|---|---:|---:|---:|---:|---:|
| Vanilla | 0.0 | 0/5 | 266,169 | 18 | 0 |
| early v2 | 0.0 | 0/5 | 1,815,707 | 176 | 0 |
| v2.6 | 0.4 | 2/5 | 236,570 | 42 | 1 |
| v2.7 | 0.2 | 1/5 | 348,639 | 64 | 1 |

来源：`evidence/a22-live-model-evolution/summary.json`。v2.6 是部分改善，不是通过；v2.7
质量和成本均回退。

### 2.2 v2.6 执行轨迹

- 第一 turn 主要重复读取 workspace 文件，最后以
  `Tool call limit reached: run limit exceeded (21/20 calls)` 结束；
- Completion Gate 只报告缺少 `outputs/quality_audit.json`；
- recovery 指令要求下一次成功工具动作必须写 artifact；
- 第二 turn 首个成功动作把 `workspace/analysis/results.json` 复制到输出目录；
- 之后模型再次读取相同文件，没有完成公共 API 原始数据的系统核验，又一次触发 tool limit；
- 输出文件与既有 `analysis/results.json` 内容哈希相同。

这证明 v2.6 的质量提升来自“恢复交付”，不是来自更好的数据推理。

### 2.3 Context starvation

v2.6 的 Action Ledger 有 32 条已审计动作，其中 27 条是 read；同一公共资源跨
`read_file` 和 `bash` 被多次读取，例如：

- `workspace/handoff.md`：9 次；
- `workspace/slack_excerpt.txt`：7 次；
- `workspace/analysis/audit.py`：7 次；
- `workspace/analysis/results.json`：5 次。

实际 model-call Context Audit 最多丢弃 48 条历史消息。更关键的是，所有 model-call
audit 的 failure layer Token 都是 0。`TaskAwareContextManager` 为工具历史保留的主要是
ActionCluster、哈希、字符数和约 260 字符的首尾 preview，并明确省略 full result
（`src/adaptive_harness/context.py::_tool_history_items`）。所以“Harness 知道读过什么”并不
等于“模型仍看得到读到的关键事实”。

### 2.4 Completion 语义不足

当前公开 Contract 只抽取出：

```text
Required artifact exists: outputs/quality_audit.json
```

`FileArtifactObservationProvider` 只记录 exists、size、sha256；
`EvidenceCompletionGate` 因此可以在文件存在时通过。它没有证明：

- 公共 source-of-truth 是否真的被访问；
- prompt 明示的每一类结果是否都被覆盖；
- JSON 是否满足公开 schema、唯一性和跨字段一致性；
- artifact 是否只是复制了被标为“不是真值”的草稿/中间结果。

### 2.5 跨 turn 状态不一致

`DeerFlowToolActionLedgerMiddleware` 当前用包含 `policy-turn` 的 key 同时管理：

- ToolActionLedger；
- delivery-satisfied；
- per-turn non-mutating budget。

这三类状态的生命周期不同。ToolActionLedger mutation epoch 和 delivery-satisfied 应覆盖
整个 task run，只有 action budget 应按 turn 重置。v2.7 第三 turn 已出现直接证据：

```text
ValueError: mutation epoch cannot move backwards
```

同时，上一 turn 已写 artifact，下一 turn 的 read-only validation 仍被
`[HARNESS DELIVERY REQUIRED]` 阻断。二者来自同一生命周期建模错误。

## 3. 因果树

```text
最终失败
├─ artifact 语义错误
│  ├─ 复制已有中间结果，未完成公共源重算
│  ├─ Contract 只检查存在性
│  └─ review 没有公共一致性检查清单
├─ 关键事实没有稳定留在模型工作集
│  ├─ full tool result 被压成短 preview
│  ├─ history 大量丢弃
│  ├─ durable failures/recovery 没进入实际 model middleware state
│  └─ Experience layer 为空
├─ 工具循环
│  ├─ 模型因观察不可见而重读
│  ├─ 同一资源可通过不同工具/批量组合形成新 scope
│  └─ 预算只终止，不提供可恢复的 observation handle
└─ 跨 turn 恢复回退
   ├─ delivery 状态按 turn 重置
   ├─ mutation epoch 按 turn 重置但 Guardrail 按 task 持久
   └─ review turn 在错误阶段被强制再次写入
```

## 4. 模型问题与 Harness 问题的边界

### 模型侧直接表现

- 多次声称“需要先理解数据”，但重复读取而不推进；
- 已识别“不能信任旧结果”，最后仍复制旧结果；
- 没有把任务中的 scope/date-range/payload-shape 警告转为可执行检查清单。

### Harness 可修复部分

- 不应让完整观察在下一次推理前消失，只留下哈希和短 preview；
- 不应把“文件存在”当作唯一完成条件；
- 不应只返回 block/error，而应返回已有 observation 的可寻址缓存或明确未满足义务；
- 不应混用 task、turn、phase 三种生命周期；
- 不应让 review 直接覆盖已交付 artifact，必须支持 staging、validation、commit/rollback。

因此不能把失败简单归因于模型能力，也不能通过增加自由重试解决。正确方向是让 Harness
把状态、证据、阶段和验证变成模型可见且机器可执行的控制面。

## 5. 下一代通用架构要求

### R1. Visible Evidence Workspace

把观察表示为 typed、addressable、content-addressed blocks，向模型显示资源、版本、访问次数、
Token 占用和保留/归档状态。对重复资源返回已缓存观察，而不是仅返回错误。完整 payload 留在
外部存储，工作集保留任务相关片段和恢复 handle。

### R2. Durable State Handoff

Policy Ledger 投影出的 failures、latest completion、recovery、artifact evidence 必须进入实际
DeerFlow model middleware，而不只是 bridge 外层的审计调用。

### R3. Evidence-driven Soft Phase Controller

阶段由 Ledger evidence 推进，而不是由 assistant 文本声明：

```text
contract → source coverage → synthesis → draft → validate → commit
```

它是软控制器：不规定模型必须用哪种工具，但对阶段义务、预算预留和允许的恢复动作给出约束。

### R4. Public Artifact Contract

只从 model-visible prompt/schema 推导：文件格式、required keys、唯一性、数量、来源覆盖和显式
一致性约束。禁止读取 private verifier、expected answer 或 Held-out 信息。

### R5. Hierarchical Budget Scheduler

同时追踪 task、turn、phase、resource 四层预算；只阻止无进展重复，不设置任意 25% Token
增长上限。必须为 synthesis、write、validate 保留额度。

### R6. Transactional Artifact Lifecycle

review 操作写入 staging artifact；公共检查通过才 commit。失败则保留上一个已交付版本并进入
repair，不允许用空文件或低质量草稿覆盖更好版本。

### R7. Failure-aware Offline Experience

从 Development 成败轨迹离线蒸馏与下列状态匹配的经验：context starvation、untrusted draft、
source coverage missing、artifact inconsistent、tool storm。经验必须通过去泄漏、泛化、去重、
离线回放与 Transfer 门禁，运行时只读检索，不修改全局 Prompt/Policy。

## 6. 验证顺序

1. 先用 v2.6/v2.7 Ledger 做零模型 replay，复现 context starvation、delivery reset 和 epoch 回退；
2. 为 R1—R6 分别增加合成任务回归测试，避免 RealReplica 特判；
3. 用不同类型的 Development 历史轨迹验证无误拦截；
4. 只在零模型门禁全部通过后运行一个 Development canary；
5. Candidate 未通过或工具循环未消失时，不进入更多任务、Transfer、Held-out 或完整 107。

## 7. 不采用的捷径

- 不把 grader reason、expected answer 或任务 ID 写进经验；
- 不为该 Google Trends 任务写专用 prompt、解析器或答案模板；
- 不靠提高 turn/tool/token 上限掩盖循环；
- 不用单个 LLM critic 的“看起来正确”代替公共确定性检查；
- 不在单次运行中在线修改全局 Prompt/Policy。
