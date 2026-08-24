# Adaptive Agent Harness：简历项目技术案例

> 一个面向真实长链路任务、模块可替换、事件溯源、证据驱动完成与离线受控自进化的 Agent Harness。Bench 是验证手段，不是核心业务逻辑。

## 1. 项目背景

Vanilla DeerFlow 在冻结 Dev20 上为 8/20 通过、40% pass rate。失败集中于 Browser Grounding、Constraint Miss、Plan Incomplete、Wrong Tool 与 Artifact Error；部分Browser轨迹在单Turn内消耗约600k Token后仍提前结束。

项目没有继续堆Prompt或直接复制现成框架，而是借鉴：

- DeepSeek Harness：ServiceKey、Plugin lifecycle、scoped capability、event-sourced session、Turn/Step；
- Tencent Youtu-Agent：Agent/Environment/Toolkit/Context分离、trajectory/judgement/practice分层；
- DeerFlow：成熟的模型、Sandbox、Browser和MCP Runtime；
- RealReplicaBench：隔离任务、确定性验证与冻结MiniBench。

## 2. 核心架构

```text
Profile / Bundle / Overlay
  → Plugin Kernel + Scoped Service Registry
    → RuntimeAdapter / Environment / Toolkit / Context / ToolRuntime
      → Turn / Step lifecycle
        → append-only SessionLedger
          → TaskState / Evidence / Progress / Recovery projections

Offline:
  Outcome / Rollout → Practice → immutable Profile Candidate
    → Paired Shadow → Gate → Promote / Reject / Rollback
```

关键不变量：

1. Model-visible means Ledger-backed；
2. TaskState可删除并从JSONL完整重建；
3. Verifier、rubric、ground truth永不进入在线Context；
4. Completion必须有Provider支持的Evidence；
5. Recovery执行不等于Recovery有效；
6. 没有足够真实Outcome样本时Practice默认关闭。
7. Core不感知Bench任务ontology；业务Contract与外部协议由Integration注入；
8. 在线任务不能修改生产Profile，自进化只能发生在离线控制平面。

## 3. 可陈述创新点

### 3.1 Capability-negotiated Completion

Contract解析与Runtime可验证能力分离。Provider显式声明`supports(criterion)`；无法验证的criterion继续保留为observe-only，既不伪造能力，也不误阻止成功任务。

### 3.2 Durable Recovery闭环

```text
completion/checked
→ recovery/decided
→ recovery/executed
→ state/updated
→ progress/checked
→ recovery/outcome-evaluated
```

Recovery预算从Ledger重建；重复negative evidence不算Progress；只有下一Turn真实Progress或Completion通过才给Recovery credit。

### 3.3 Confidence-gated Offline Practice

多Action Outcome标记为confounded，不用于单Action晋升。默认要求：

- isolated samples ≥ 5；
- effective rate ≥ 60%；
- Wilson 95% lower bound ≥ 0.30。

当前真实Outcome为0，因此Practice保持disabled。

### 3.4 Evidence-bound Evaluation

MiniBench16通过task order hash、Dataset fingerprint、Profile fingerprint、固定image/model/seed和32-cell paired manifest锁定。历史不同配置结果只用于风险定位，不冒充paired baseline。

### 3.5 Governed Evolution Plane

自进化不等于让Agent在线修改Prompt。每个候选都是带parent fingerprint、变更假设和证据引用的不可变Profile版本，只能经过paired Shadow评估。门禁同时检查integrity、leakage、样本量、质量置信下界、Token成本和回归数；证据不足保持Shadow，硬门禁失败Reject，晋升后仍可从JSONL确定性Rollback。

RealReplica的mail/calendar/document规则和Gmail/Docs/Workbench Provider已从Core迁入integration。源码边界测试确保通用`task_contract.py`与`deerflow_policy.py`不会再次吸收Bench业务词汇。

### 3.6 Task-aware Context Working Set

Context不是无限历史，而是五层决策工作集：Immutable Task、Active State、Evidence、Failure/Recovery、Admissible Experience。选择器在Profile Token预算内评分，并保持工具调用/结果原子性；被淘汰的大结果留下脱敏参数、结果哈希、预览和重复次数。动态数据保持Human role，静态防注入规则保持System role，选择决策写入Ledger但不复制正文。

这项能力经历了可审计的失败迭代：v1过度压缩后反复read并触发loop guard；v1.1通过同一Development任务，但Token 320,936、工具32次，相对较早的同题Vanilla探索性对照分别+111.1%和+23，成本门禁Reject；v1.2使用执行参数+结果哈希识别无进展重复，同题保持1.0且产物SHA一致，Token降到166,070（方向性+9.23%），但Tool仍+7、耗时+76.8%，单样本不足以晋升。

### 3.7 Tool Action Ledger / Verification Budget

把每次工具执行投影为Intent、Resource、Data field、结果哈希和Mutation epoch；成功写入后重置验证窗口，同范围重复验证或连续验证超预算只生成可回放Warn。中间件当前仅observe，不拦截工具。7条轨迹200个Action聚合为145个cluster，分类覆盖80.5%，产生22个验证告警；2条stable-pass控制为0告警，2条历史失败控制为16个。由于仍有39个Unknown且成功对照太少，v1.3没有付费运行或上线。

### 3.8 Cross-surface Advice Gate

补齐Browser Navigate/Observe/Interact和API mutation后，200个Action分类覆盖达到100%。5条确定性Browser+2条stable-pass控制均0告警，2条失败控制均命中；Wilson false-warning upper=0.354、failure-signal lower=0.342，只解锁非阻断Advice，不解锁Enforcement。

v1.4的Advice只在最终第三次验证触发，单题虽然1.0但189,696 Token、23 Tool，Reject。v1.5把不变资源的第4次Read纳入Advice，同题1.0、154,036 Token（方向性+1.31%），但实际Advice触发0次、Tool仍+7、耗时+128%，因此不能把改善归因于策略，Keep Shadow。

## 4. 已验证结果

| 项目 | 证据 |
|---|---:|
| 当前 Adaptive 零模型单元测试 | 293 |
| Core中的RealReplica业务词汇 | 0（边界测试） |
| Evolution在线修改Profile | disabled |
| Evolution Shadow/Promote/Reject/Rollback | deterministic + replayable |
| Context历史反事实 | 4 runs / 99 snapshots；估算消息面压缩中位数49.95% |
| Context v1 | 0.0；143,570 Token；Reject |
| Context v1.1 | 1.0；320,936 Token；成本门禁Reject |
| Context v1.2 | 1.0；166,070 Token；产物SHA一致；Keep Shadow |
| Tool Action Ledger | 7 runs / 200 actions / 145 clusters / 22 warnings |
| v1.3 | observe-only容器与MiniBench预检通过；0 paid runs |
| v1.4 Advice | 1.0；189,696 Token；23 Tool；Reject |
| v1.5 Read Advice | 1.0；154,036 Token；0 Advice触发；Keep Shadow |
| Runtime v2.6 | 2/5；236,570 Token；42 Tool；当前历史最优 Shadow |
| Evidence Workspace v3 | 0/5；350,771 Token；46 Tool；Reject |
| v3.1 source-first | 0/5；216,688 Token；42 Tool；无产物 |
| v3.2 materializer | 1/5；231,897 Token；47 Tool；全空产物，不晋升 |
| v3.3 pre-canary | 135 项核心 gate + pinned-container probe；仅授权一次同题 canary |
| v3.3 live | 0/5；277,043 Token；46 Tool；Response Gate 覆盖 Evidence assessments |
| v3.4 thinking=max | 1/5；719,574 Token；64 Tool；1,043.6秒；mutation-epoch失败，不晋升 |
| v3.5 thinking=high | A39 离线修复 epoch；A40/A41 高强度 request/container gate 通过；等待同题 canary |
| 历史DeerFlow exact replay | 4/4 |
| MiniBench Contract coverage | 16/16 |
| Provider-enforced task coverage | 16/16 |
| Completion counterfactual：失败被阻止 | 5/10 |
| Completion counterfactual：成功误阻止 | 0/6 |
| Human failure→Recovery映射 | 12/12 |
| Constraint/Artifact目标切片 | 5/5 |
| Blind retry建议 | 0 |
| 首个fresh paired canary | Baseline 1.0 / Candidate 1.0 |
| Candidate canonical Ledger | 999 events |

首个paired canary观测到Candidate Token +65.6%，但两侧prompt/config/model/image fingerprint完全一致、Candidate只有1 Turn、counterfactual event replay完全一致，因此该cell可归因Harness model-token delta为0，差值标记为provider/trajectory variance。由于只有4个可比vanilla样本、CV 22.4%，项目仍停止后续付费扩跑。

### Runtime Evolution v2 真实模型结果

统一切换到`deepseek-v4-flash-vision-exp`后，只运行一个Development canary。Vanilla使用266,169 Token、18次工具调用后未生成产物；早期v2因工具风暴达到1,815,707 Token、176次工具调用并失败。通过原子工具预算、LangGraph model-batch终止和delivery-first recovery，v2.6成功生成JSON，将公开检查从0/5提升到2/5、Capacity从0.0提升到0.4，观察Token降至236,570（相对Vanilla -11.1%），耗时从135.8秒降至75.2秒，但工具调用仍增至42次且最终任务未通过。

后续v2.7增加公开artifact review turn后回退到1/5、348,639 Token，因此被撤销。最终选择v2.6为Shadow，不运行Transfer、Held-out或更多任务。可声明“恢复产物交付、显著缓解工具风暴，并取得部分公开质量/成本改善”，不能声明RealReplicaBench成功率提升。

## 5. 主动拒绝/未上线的方案

- Browser Progress Guard和Form Tools：付费canary无收益，删除；
- Browser shell/CDP fallback guard：失败覆盖6/7，但成功Browser对照为0，保持disabled；
- Recovery在线学习：真实Outcome为0，Practice保持disabled；
- Context v1/v1.1：分别因质量和成本回归Reject，未因单题通过而上线；
- Tool Action Ledger enforcement：分类覆盖和成功控制不足，保持observe-only；
- v1.4/v1.5：分别因成本回归和因果证据不足未晋升；
- 完整107任务：未运行，只使用冻结MiniBench16和历史轨迹。

## 6. 简历Bullet（建议版本）

- 设计并实现事件溯源Agent Harness：自研Plugin Kernel、typed ServiceKey、Turn/Step lifecycle、append-only Ledger及可重建TaskState，参考并扩展DeepSeek Harness与Youtu-Agent的分层思路，累计282项零模型测试与4/4历史轨迹精确回放。
- 实现五层Task-aware Context Working Set与DeerFlow模型调用中间件：在4条历史轨迹99个完整快照上估算消息面压缩中位数49.95%，并用不可变Profile记录v1质量失败、v1.1成本失败、v1.2继续Shadow，避免把单题1.0包装成架构收益。
- 构建Tool Action Ledger与Verification Budget，将7条轨迹200次调用按Intent/Resource/Mutation epoch压缩为145个cluster；observe-only反事实在2条stable-pass控制上0告警、2条失败控制上16告警，因80.5%分类覆盖不足而拒绝直接拦截。
- 建立Wilson门禁的跨界面Tool Advice：补齐Browser/API后分类覆盖100%，7条成功控制0告警、2条失败控制全命中；实测拒绝v1.4成本回归，并对v1.5的近基线Token结果因0次Advice触发而拒绝因果归因。
- 构建离线受控Evolution Plane：不可变Profile版本经paired Shadow及integrity/leakage/质量置信区间/Token成本/回归门禁后才能晋升，决策全量Ledger化且支持确定性Rollback；Core与RealReplica业务语义通过Extractor/Provider接口解耦。
- 构建Capability-negotiated Completion与Durable Recovery链路，在冻结MiniBench16上实现Contract/Provider任务覆盖16/16；历史counterfactual捕获5/10 missing-state/artifact类失败且成功任务误拦截0/6，12条人工失败标签Recovery映射覆盖12/12、blind retry为0。
- 建立成本敏感paired evaluation与Wilson置信门禁：固定model/image/seed/Profile fingerprint，发现首个Candidate虽保持1.0质量但单次Token观测+65.6%，通过surface fingerprint和counterfactual replay判定不可归因于Harness，并停止后续付费扩跑，避免用单次成功掩盖成本不确定性。
- 基于真实`deepseek-v4-flash-vision-exp`失败轨迹迭代Runtime Evolution：从1.82M Token/176 Tool的工具风暴收敛到v2.6的236,570 Token/42 Tool，并把单任务公开检查从0/5提升到2/5；因最终未通过和Tool仍回归，主动保持Shadow并撤销质量/成本更差的v2.7。
- 从v2.6/v2.7轨迹定位Context starvation、浅层Completion和跨Turn状态回退，设计Visible Evidence Workspace、公共Source/Artifact Contract、Evidence-driven Soft Phase与Evidence-gap Recovery；历史轨迹和固定容器零模型门禁通过后仍只授权一个Development canary。
- v3真实canary证明“证据可见”不足以自动改变动作选择：虽然failure state已进入上下文，模型仍用路径别名和批量shell重复读取并回退到0/5；据此加入跨工具/Turn的resource级预算和source-first阶段，而不是放宽Token/Tool上限。
- v3.1把Token从350,771降到216,688却仍未访问显式公共API；进一步将安全loopback GET做成可审计的source obligation materializer，让Harness在首轮前物化有限、脱敏、可验证的公共证据，而非继续依赖模型自行发现。

## 7. 90秒面试讲述

“这个项目不是围绕Bench写特判，而是增强模型Harness。我把DeerFlow降为Runtime Provider，自研Plugin Kernel、Ledger、Contract/Evidence和五层Task-aware Context，所有模型可见选择都能审计。真实DeepSeek v4视觉模型测试先暴露1.82M Token、176次工具调用的工具风暴；我通过原子预算、graph termination和delivery-first recovery把它收敛到236,570 Token，并将公开检查从0/5提升到2/5。但因为最终仍未通过且Tool仍回归，我把它保留为Shadow并停止扩跑。这个例子说明自进化不是在线改Prompt，而是不可变Profile经过质量、成本、泄漏和回归门禁。RealReplica只负责外部验证，项目价值是通用架构、失败可解释和证据纪律。”

## 8. 声明边界

当前不能声称：

- MiniBench整体成功率提升；
- Recovery已经使真实失败任务转为通过；
- Browser fallback guard已经上线；
- Practice已经从真实Outcome学习。
- Harness已经在线自主修改或晋升生产Profile。
- Context已经证明MiniBench整体成功率或实际Token成本改善。

所有可声明指标及SHA-256见[`../evidence/index.json`](../evidence/index.json)。
