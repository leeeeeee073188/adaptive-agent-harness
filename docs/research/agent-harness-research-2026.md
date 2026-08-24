# Agent Harness 架构研究：提升复杂任务成功率与成本效率

研究日期：2026-08-24  
研究范围：以 **独立 agent harness** 为目标，参考 DeepSeek Harness、Youtu-Agent、SWE-agent、OpenHands 等生产级实现，并结合 ReAct、Reflexion、Self-Refine、CRITIC、LATS、Toolformer、ExpeL、Generative Agents、Voyager 等论文，以及 AgentBench / GAIA / OSWorld / WebArena 等评测基准，提炼对 `adaptive-agent-harness` 可迁移的架构原则。

> 核心结论：**不要把 bench 当业务问题去“特判”**。更稳妥的路径是把 harness 做成“可审计、可回放、可离线进化”的执行系统：运行时只做计划、执行、验证、恢复与证据记录；学习与策略演化全部离线化；经验只作为受门禁控制的检索资产，而不是在线修改全局 Prompt / Policy。

---

## 1. 结论摘要

1. **最有效的 agent 提升，往往来自简单且可组合的工作流，而不是更复杂的框架。** Anthropic 明确建议优先采用简单、可组合的模式，并在能用确定性方案时不要强行 agent 化。OpenAI 的实践指南也强调：先建立 eval 基线，再用更小模型替换可行部分，把成本与延迟降下来。[Anthropic](https://www.anthropic.com/engineering/building-effective-agents), [OpenAI](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)

2. **运行时最关键的是“结构化循环”而不是“单次更聪明的回答”。** ReAct、Self-Refine、CRITIC、LATS 都在不同层面验证了：reason / act / verify / revise / search 的闭环，比纯生成更稳。

3. **记忆/经验应该是“可提炼、可检索、可拒绝”的资产，而不是完整轨迹的原样回放。** Reflexion、Generative Agents、ExpeL、Voyager、Training-Free GRPO 都支持“从经历中抽取可复用知识”，但都隐含一个前提：经验要经过筛选、抽象、验证，不能把原始失败轨迹直接塞回上下文。

4. **产品级 harness 的共识是：环境、工具、评估、运行、学习必须分层。** DeepSeek Harness 把几乎所有能力都做成插件；Youtu-Agent 直接把 Environment / Toolkits / Agent / Evaluation 分开；OpenHands 把 sandbox、multi-agent coordination、benchmark integration 单独处理；SWE-agent 强调 ACI（Agent-Computer Interface）与简洁命令/反馈格式。

5. **bench 只是外部验证，不应成为在线控制逻辑。** AgentBench、GAIA、OSWorld、WebArena 共同表明：真实交互环境里最常见的瓶颈是长程推理、指令遵循、GUI grounding、工具使用和恢复能力；因此优化重点应该是 harness 能力，而不是对某个 benchmark 关键词做特殊处理。

---

## 2. 逐条研究结论：论文 / 技术报告 / 产品实现

> 下表按“关键机制 → 实证结果 → 可迁移点 → 局限”组织。  
> 只保留对 harness 设计有直接价值的内容。

| 来源 | 关键机制 | 实证结果 | 对本项目可迁移点 | 局限 |
|---|---|---|---|---|
| [ReAct](https://arxiv.org/abs/2210.03629) | 将 reasoning trace 与 task action 交错生成，让推理帮助计划更新、异常处理，动作帮助获取外部信息。 | 在 HotpotQA / Fever 上缓解幻觉与误差传播；在 ALFWorld / WebShop 上优于 imitation / RL 基线。 | 适合做 **Turn/Step 级** 的默认循环：先想、再做、再读环境反馈。 | 仍然依赖 prompt 质量；没有自动的经验演化层。 |
| [Reflexion](https://arxiv.org/abs/2303.11366) | 通过语言反馈做“verbal reinforcement”，把反思文本存入 episodic memory，下一轮再用。 | HumanEval pass@1 达到 91%，高于 GPT-4 的 80%。 | 适合做 **离线经验摘要**：把失败原因、修复策略、停止条件写成经验卡。 | 如果把反思文本原样在线注入，容易污染上下文并放大偏差。 |
| [Self-Refine](https://arxiv.org/abs/2303.17651) | 同一模型循环执行 feedback → refine。 | 7 个任务平均提升约 20% absolute。 | 可作为 **轻量 self-critique / self-repair** 基础模式。 | 单模型自评存在自嗨风险，最好配合外部 verifier。 |
| [CRITIC](https://arxiv.org/abs/2305.11738) | 用外部工具对输出做交互式 critique，再据反馈修正。 | 在 QA / 代码 / toxicity 等任务上持续增益。 | 适合做 **验证器驱动的修复环**：先检查，再修补，不要盲改。 | 工具设计和验证粒度决定收益；工具过粗会导致修复无效。 |
| [LATS](https://arxiv.org/abs/2310.04406) | 把 MCTS 引入 agent，用 value function + self-reflection 做搜索。 | HumanEval 92.7%；WebShop 平均 75.9。 | 适合在 **no-progress / 高不确定分支** 上做受预算约束的搜索。 | 代价高；不适合默认全局开启。 |
| [Toolformer](https://arxiv.org/abs/2302.04761) | 学会决定何时调用什么工具、传什么参数、如何把结果纳入后续预测。 | 自监督方式就能学会 API 调用，提升多任务零样本表现。 | 对 harness 的启示是：**工具接口要稳定、可学习、可文档化**。 | 这是偏模型侧的能力，不等于运行时 harness 本身自动变强。 |
| [Generative Agents](https://arxiv.org/abs/2304.03442) | 记忆流 + 反思 + 规划，按时间组织经验并提炼高层 reflection。 | 在沙盒世界里能形成更可信的人类式行为和社交涌现。 | 说明 memory 不应只是 RAG，而应包含 **反思层** 与 **规划层**。 | 主要是模拟型场景，不直接覆盖复杂工具任务。 |
| [ExpeL](https://arxiv.org/abs/2308.10144) | 训练任务中自动收集经验，提炼自然语言 insights，测试时回忆这些经验。 | 随经验积累，性能持续提升。 | 和你当前“Candidate Experience / Experience Store”方向高度一致。 | 经验若缺少去泄漏、泛化和验证，容易过拟合训练任务。 |
| [Agent Workflow Memory](https://arxiv.org/abs/2409.07429) | 从轨迹中归纳可复用 workflow，按当前任务选择性注入；同时支持 offline 与 online induction。 | 在 Mind2Web / WebArena 上相对成功率提升 24.6% / 51.1%，成功任务步骤数下降；跨任务、网站、域差距增大时仍有迁移增益。 | 把 Experience 从“建议文本”升级为带前置条件、步骤、停止条件的 **Workflow Memory**；本项目只采用 offline induction。 | online induction 会违反当前“不在单次执行中改全局 Policy”的约束；workflow 也可能把站点/任务细节带入经验库。 |
| [HiAgent](https://arxiv.org/abs/2408.09559) | 用 subgoal 作为层次化 working-memory chunk，完成子目标后以摘要替换局部 action-observation 历史。 | 五类长程任务上成功率约翻倍，平均步骤减少 3.8。 | 支持把 context 从“最近消息 + action cluster”升级为 **subgoal/evidence block**，避免完整观察被短 preview 取代。 | 摘要仍可能丢证据；必须保留可恢复的 full-fidelity payload 和内容哈希。 |
| [VISTA](https://arxiv.org/abs/2606.30005) | 训练外、模型无关的 typed/addressable memory blocks；向 agent 显示每个 block 的 Token、时效和访问历史，并把全文归档为可恢复 payload。 | 论文报告在 LOCA-Bench、BrowseComp-Plus、GAIA 跨上下文规模迁移；LOCA-Bench 上 Gemini-3-Flash 从 22.7 提升到 50.7。 | 与本次 v2.6 的 context starvation 高度对应：实现 **Visible Evidence Workspace**，让模型知道看过什么、什么被归档、如何恢复。 | 2026 年新预印本，结果需要独立复现；dashboard 本身也占 Token，不能无界增长。 |
| [AdaCoM](https://arxiv.org/abs/2605.30785) | 用外部 context manager 对冻结 agent 做灵活的 context 修改，并揭示 fidelity-reliability trade-off。 | 论文报告在 web search / deep research 上跨多个 agent 提升，且相近能力 agent 间迁移最好。 | 说明 context policy 应按模型能力与任务压力标定，而不是固定 4096 Token 和固定 layer ratio。 | 需要训练外部管理器；当前项目应先实现确定性、可审计的 block policy，再考虑学习型选择器。 |
| [Voyager](https://arxiv.org/abs/2305.16291) | 自动课程 + 技能库 + 迭代提示，把成功技能写成可执行代码并可检索复用。 | Minecraft 中获得 3.3x 更多 unique items、2.3x 更长距离、15.3x 更快技术树 milestone。 | 很适合借鉴其 **skill library / curriculum / self-verification** 三件套。 | 场景强环境化；直接照搬到浏览器/软件任务会失真。 |
| [Training-Free GRPO](https://arxiv.org/abs/2510.08191) | 不改参数，而是从 rollout 中蒸馏 token prior / experiential knowledge，再注入 API 调用。 | 在数学推理和 web search 上，对 DeepSeek-V3.1-Terminus 有明显 out-of-domain 提升。 | 与你的“**离线经验演化，不在线改全局 policy**”约束非常匹配。 | 经验蒸馏需要非常严格的样本门禁，否则会把噪声当策略。 |
| [AgentBench](https://arxiv.org/abs/2308.03688) | 8 个交互环境评估 LLM-as-Agent。 | 指出主要失败来自 long-term reasoning、decision-making、instruction following。 | 说明 harness 的短板通常在 **长程控制** 而不是单轮回答能力。 | Bench 生态随时间变化，历史分数不宜直接外推。 |
| [GAIA](https://arxiv.org/abs/2311.12983) | 真实世界问题，需要 reasoning / multimodality / web browsing / tool-use。 | 人类 92%，GPT-4 + plugins 15%。 | 对本项目意味着：要把 **多模态、网页、工具** 当一等公民。 | 人类题目与真实生产任务存在分布差异。 |
| [OSWorld](https://arxiv.org/abs/2404.07972) | 真机环境、执行式评测、跨 OS 和跨应用任务。 | 人类 72.36%，最好模型 12.24%；主要卡在 GUI grounding 和 operational knowledge。 | 说明 computer-use harness 必须重视 **grounding + 操作知识 + 可靠评价脚本**。 | 真实桌面环境的噪声很高，评测脚本质量决定结论可信度。 |
| [WebArena](https://arxiv.org/abs/2307.13854) | 自建 web 环境，模拟购物/论坛/协作开发/内容管理等真实网站任务。 | 证明 web agent 需要在 realistic environment 中被评估。 | 对你的浏览器/网页类任务，建议使用 **可复现环境 + execution-based eval**。 | 仅靠网页相似度或答案匹配无法覆盖流程正确性。 |
| [SWE-agent](https://arxiv.org/abs/2405.15793), [docs](https://github.com/princeton-nlp/SWE-agent/blob/main/docs/background/index.md) | ACI：用简单 LM-centric 命令和反馈格式，让模型更易浏览、修改、执行代码。 | SWE-bench full test set 12.29% SOTA（其文档所述）。 | 对本项目的启示是：**工具命令比“更大的 prompt”更重要**。 | 软件工程场景依赖 ACI 设计，不能直接复制到所有任务。 |
| [Agentless](https://arxiv.org/abs/2407.01489) | 不让 LLM 决定后续动作，而是固定三阶段：localization → repair → patch validation。 | SWE-bench Lite 32.00%，约 $0.70，且构建了更严格的 SWE-bench Lite-S。 | 强烈支持“**先把流程收敛成确定的阶段，再逐步增加 agent 自由度**”。 | 适合某些软件任务；对开放式 web / 多模态任务不一定够用。 |
| [OpenHands](https://arxiv.org/abs/2407.16741), [repo](https://github.com/OpenHands/openhands) | 平台化 agent：sandbox、安全执行、多 agent 协调、benchmark 接入。 | 覆盖 15 个任务，包含 SWE-bench / WebArena。 | 值得借鉴其 **平台层与 agent 层分离** 的产品化方式。 | 平台越强，越需要控制复杂度与调试可观测性。 |
| [DeepSeek Harness](https://deepseek.com/harness/en/), [repo](https://github.com/deepseek-ai/deepseek-harness) | “Everything is a plugin”，模型、工具、技能、session、sandbox、storage、loops、UI 都可替换；append-only session log。 | 官方明确强调每次运行可回放、可 fork、可 search。 | 与你的“harness 核心 + 可替换 runtime adapter”方向高度一致。 | 当前仍在 developer preview，部分 API / 细节可能快速演进。 |
| [Youtu-Agent](https://tencentcloudadp.github.io/youtu-agent/), [repo](https://github.com/TencentCloudADP/youtu-agent) | Environment / Toolkits / Agent / Evaluation 明确分层；支持 Workflow 与 OrchestraAgent；有 Agent Practice 和 Agent RL。 | 71.47% WebWalkerQA、72.8% GAIA、工具合成成功率 >81%、Practice 让 AIME 2025 提升 +5.4%。 | 非常适合做你项目的 **结构参考**：分层、自动生成、离线经验学习、RL 训练接口。 | 文档与论文处于快速演进期，落地时要冻结版本并做兼容测试。 |
| [Anthropic：Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) | 建议从简单、可组合模式开始；工作流与 agent 要区分；强调 ground truth、sandbox、guardrails、tool docs。 | 官方总结：最成功的实现通常不是复杂框架，而是简单组合。 | 对你的项目最重要：**复杂任务也不要先上复杂框架；先把简单闭环做稳。** | 这是一篇工程建议，不是严格论文；应与可量化 eval 一起使用。 |
| [OpenAI practical guide](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/) / [Agents SDK](https://developers.openai.com/api/docs/guides/agents) / [next evolution](https://openai.com/index/the-next-evolution-of-the-agents-sdk/) | 先做 eval 基线，再换小模型降成本；核心组件是 Model / Tools / Instructions；需要 handoffs、guardrails、tracing、sandbox。 | 官方明确把 sandbox 和 harness 分离，强调安全、持久化与 scale。 | 对你很有用：**把成本控制做成体系，而不是只在 prompt 里写“省 token”**。 | 官方指南偏产品实践；需要结合你的真实任务分布和 verifier。 |

---

## 3. 对 `adaptive-agent-harness` 的结构性启示

### 3.1 运行时：必须是“计划 → 执行 → 验证 → 恢复”的状态机

从 ReAct、Self-Refine、CRITIC、LATS、Anthropic/OpenAI 的工程建议来看，agent 不是“更大一段 prompt”，而是一个带退出条件的闭环。

**建议落地方式：**

- `Plan`：生成可执行计划，但不承诺最终正确；
- `Act`：调用工具 / 环境；
- `Observe`：把环境反馈写入 ledger；
- `Verify`：用 deterministic / model / hybrid verifier 检查；
- `Recover`：只在有明确错误类型时修复；
- `Stop`：通过、失败、超预算、无进展、需人工介入。

**关键要求：**

- 每一步都可回放；
- 每一步都要有证据；
- 每一步都要有退出条件；
- 不允许无穷重试掏空 token。

### 3.2 工具层：把 ACI 做成“可学习接口”，不是随意堆工具

SWE-agent 的核心提醒是：**工具格式、命令格式、反馈格式，本身就是模型学习的一部分**。  
DeepSeek Harness 也把工具、session、loop、sandbox 都做成插件。

**建议：**

- 每个工具要有：
  - 输入 schema
  - 输出 schema
  - 副作用说明
  - 错误语义
  - 代价提示
- 工具要能被独立测试；
- 工具文档要能直接供模型消费；
- 对高风险工具，加入更细粒度的 action/result 分段和 guardrails。

### 3.3 经验层：只做离线演化，不做在线自修改

Reflexion、ExpeL、Voyager、Training-Free GRPO 都指向同一件事：**经验能提升下一次任务，但最好通过抽象后的知识或经验 prior，而不是把原始轨迹直接重复塞回模型上下文。**

**建议的经验管道：**

`Rollout` → `Candidate Experience` → `去泄漏` → `泛化抽象` → `去重` → `离线验证` → `Promote to Experience Store`

**可迁移的结构字段：**

- 触发条件 / 失败类型
- 有效策略 / 无效策略
- 前置条件 / 停止条件
- 工具组合顺序
- 典型错误信号
- 适用任务族 / 不适用任务族
- 经验置信度

**运行时限制：**

- 只允许检索经验；
- 不允许在线修改全局 Prompt / Policy；
- 不允许单次任务自动晋升经验；
- 不允许 benchmark id、verifier wording、expected answer 进入 experience 语义主体。

### 3.4 规划层：默认单 agent，复杂时再升级到 orchestrator-workers

Anthropic 和 OpenAI 都明确建议：先用单 agent / 简单 workflow，复杂度只有在确实需要时再增加。Youtu-Agent 的 `Workflow` 和 `OrchestraAgent`、OpenAI 的 single-agent / multi-agent 也给了相同方向。

**实践建议：**

- 默认：单 agent + 工具 + verifier；
- 中等复杂：planner / executor / reporter；
- 高复杂且子任务不确定：orchestrator-workers；
- 只有在 no-progress、分支不确定性高、或者任务天然可并行时，再启用 tree search / 多 agent。

### 3.5 验证层：把 verifier 做成一等公民

AgentBench / GAIA / OSWorld / WebArena 的共识是：交互式任务最难的不是“会不会说”，而是“能不能在真实环境里保持正确性”。  
因此 harness 必须把验证从“最后一步看一眼”变成“每一轮都可检查的证据流”。

**建议双层验证：**

1. **确定性验证**：schema、文件存在、diff、执行结果、断言、状态机条件；
2. **语义验证**：LLM judge / reviewer / critique；
3. **最终门禁**：两者一致时再晋升或结束。

### 3.6 成本控制：不是限制模型“少想”，而是阻止无意义重复

Anthropic 和 OpenAI 都强调成本与 latency 约束。对当前目标而言，最有效的不是把 token 上限写死，而是：

- 检测重复相同 scope / strategy 的无进展循环；
- 对每类任务设定最大允许重试深度；
- 只在状态变化或新证据出现时才允许重试；
- 简单子问题使用便宜模型，复杂子问题才升级；
- verifier / judge 与主模型职责分离。

**一句话：**

> 省 token 的核心不是“少让模型说话”，而是“少做无效动作、少走回头路、少重复验证、少把同一错误翻译成多轮新错误”。

---

## 4. 对当前项目最值得优先落地的 7 个设计点

1. **Typed Policy Session**
   - 统一输出 `Continue / Complete / Recover / BlockScope / Stop`。
   - 所有判断先写 ledger，再影响下一步动作。

2. **Ledger-first Runtime**
   - 模型看见的内容必须可追溯；
   - 不允许隐式 side channel 改变输入。

3. **Experience Store**
   - 只存“可复用经验卡”，不存原始 bench 题面；
   - 每张经验卡都要能说明适用条件与失效条件。

4. **No-progress Governor**
   - 发现连续重复、重复验证、重复失败，就强制 replan / shrink / stop。

5. **Search-only-on-demand**
   - 默认不用树搜索；
   - 只在任务不确定性高且预算允许时开启 LATS 风格探索。

6. **Model routing by subtask**
   - 便宜模型处理路由、分类、摘要、简单检索；
   - 更强模型处理关键决策、修复、最终验证。

7. **Evaluation stratification**
   - 用任务类型 / 难度 / 失败模式做分层抽样；
   - 这些标签只用于评测覆盖和离线分析，不直接成为 runtime policy。

---

## 5. 适用于 RealReplicaBench 的评价框架建议

如果目标是“提升复杂任务执行成功率，同时尽量控制 token 成本”，那么 RealReplicaBench 侧的 bench 设计最好满足：

- **任务族覆盖**：不同 task type 都要有代表；
- **难度分层**：easy / medium / hard 都覆盖；
- **失败模式分层**：grounding、tool misuse、planning miss、artifact miss、recovery failure；
- **成本指标同步记录**：token、tool call、latency、重试次数、恢复次数；
- **冻结评估**：子集和规则固定后，不再按结果动态改题；
- **禁止 leakage**：经验学习不能看到 verifier 私有答案和 benchmark 私有措辞。

这样可以把“在 107 个任务里找一个既省钱又有代表性的子集”做成一个 **统计上合理的 stratified benchmark**，而不是业务特判。

---

## 6. 主要风险与局限

1. **论文结果不等于生产结果。**  
   ReAct / Reflexion / LATS 等很多工作在特定任务上有效，但未必直接适用于你的真实 browser / desktop / multimodal 链路。

2. **更强的 agent 不一定更省 token。**  
   没有 no-progress 门禁时，更复杂的搜索和反思经常只会放大成本。

3. **经验学习最容易被污染。**  
   如果不做去泄漏、泛化和离线验证，经验库会把 benchmark 特征学进去。

4. **工具层设计比模型层优化更慢，但更稳。**  
   你能从更好的 ACI、验证器和状态机里拿到的收益，通常比单纯换更大模型更可靠。

5. **当前官方文档更新很快。**  
   DeepSeek Harness、Youtu-Agent、OpenHands、OpenAI Agents SDK 都处于快速演进期；落地时要冻结版本并重新跑 conformance / regression。

---

## 7. 可直接复用的设计原则

- **Bench 是验证手段，不是核心业务逻辑。**
- **Runtime 只负责执行，学习只允许离线发生。**
- **Experience 是被门禁管理的资产，不是原始轨迹拼贴。**
- **先做简单闭环，再逐步增加自治与搜索。**
- **工具是模型可学习接口，文档与测试和工具实现同等重要。**
- **成本控制靠终止条件和重复抑制，不靠一句“少花 token”口号。**

---

## 8. 参考来源

### 论文 / 官方技术资料

- ReAct: Synergizing Reasoning and Acting in Language Models — https://arxiv.org/abs/2210.03629
- Reflexion: Language Agents with Verbal Reinforcement Learning — https://arxiv.org/abs/2303.11366
- Self-Refine: Iterative Refinement with Self-Feedback — https://arxiv.org/abs/2303.17651
- CRITIC: Large Language Models Can Self-Correct with Tool-Interactive Critiquing — https://arxiv.org/abs/2305.11738
- Language Agent Tree Search Unifies Reasoning Acting and Planning in Language Models — https://arxiv.org/abs/2310.04406
- Toolformer: Language Models Can Teach Themselves to Use Tools — https://arxiv.org/abs/2302.04761
- Generative Agents: Interactive Simulacra of Human Behavior — https://arxiv.org/abs/2304.03442
- ExpeL: LLM Agents Are Experiential Learners — https://arxiv.org/abs/2308.10144
- Agent Workflow Memory — https://arxiv.org/abs/2409.07429
- HiAgent: Hierarchical Working Memory Management — https://arxiv.org/abs/2408.09559
- VISTA: LLM Agents Are Latent Context Managers — https://arxiv.org/abs/2606.30005
- Learning Agent-Compatible Context Management for Long-Horizon Tasks (AdaCoM) — https://arxiv.org/abs/2605.30785
- Voyager: An Open-Ended Embodied Agent with Large Language Models — https://arxiv.org/abs/2305.16291
- Training-Free Group Relative Policy Optimization — https://arxiv.org/abs/2510.08191
- AgentBench: Evaluating LLMs as Agents — https://arxiv.org/abs/2308.03688
- GAIA: a benchmark for General AI Assistants — https://arxiv.org/abs/2311.12983
- OSWorld: Benchmarking Multimodal Agents for Open-Ended Tasks in Real Computer Environments — https://arxiv.org/abs/2404.07972
- WebArena: A Realistic Web Environment for Building Autonomous Agents — https://arxiv.org/abs/2307.13854
- OpenHands: An Open Platform for AI Software Developers as Generalist Agents — https://arxiv.org/abs/2407.16741

### 官方产品 / 工程指南

- Anthropic — Building effective agents — https://www.anthropic.com/engineering/building-effective-agents
- OpenAI — A practical guide to building agents — https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/
- OpenAI — Agents SDK — https://developers.openai.com/api/docs/guides/agents
- OpenAI — The next evolution of the Agents SDK — https://openai.com/index/the-next-evolution-of-the-agents-sdk/
- DeepSeek Harness 官方介绍 — https://deepseek.com/harness/en/
- Youtu-Agent 官方文档 — https://tencentcloudadp.github.io/youtu-agent/

### 生产级 OSS 参考实现（附 pinned ref）

- `deepseek-ai/deepseek-harness@b150a551b8d465e31e418e1b2eaf5e79bbb7d28e:docs/architecture.md:L11-L27` — plugin tree / reversible effects / session log / turn-flow / runtime modes
- `TencentCloudADP/youtu-agent@c2caa539f4c95ae1c39ed24dc8a99cb3651e1d5d:README.md:L23-L34` — environment / tools / agent / evaluation separation and practice / RL modules
- `princeton-nlp/SWE-agent@3ea751c087f32b16e039a2233dd6eefecef325d5:docs/background/index.md:L7-L12` — Agent-Computer Interface and simple LM-centric commands
- `OpenHands/openhands@8511fff62d3084587cda1add483fe5ea9c8bfd7e:README.md:L33-L47` — self-hosted control center, backends, sandboxing, automations, agent portability

---

## 9. 最后建议

如果你现在要把 `adaptive-agent-harness` 做成一个真正可持续演进的独立项目，最应该坚持的是：

1. **用 harness 设计提升成功率，而不是用 benchmark 特判伪装成功率。**
2. **把经验学习放到离线控制面，运行时只检索，不改全局策略。**
3. **把工具、环境、评估、记忆、恢复拆开，形成可替换插件或服务边界。**
4. **把成本控制绑定到“无进展检测 + 退出条件 + 选择性搜索”，而不是固定 token 比例。**

这条路线和你当前 ADR 的方向是一致的，也和当前最强的公开论文/产品实现趋势一致。
