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

## 4. 已验证结果

| 项目 | 证据 |
|---|---:|
| 零模型单元测试 | 76 |
| Core中的RealReplica业务词汇 | 0（边界测试） |
| Evolution在线修改Profile | disabled |
| Evolution Shadow/Promote/Reject/Rollback | deterministic + replayable |
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

## 5. 主动拒绝/未上线的方案

- Browser Progress Guard和Form Tools：付费canary无收益，删除；
- Browser shell/CDP fallback guard：失败覆盖6/7，但成功Browser对照为0，保持disabled；
- Recovery在线学习：真实Outcome为0，Practice保持disabled；
- 完整107任务：未运行，只使用冻结MiniBench16和历史轨迹。

## 6. 简历Bullet（建议版本）

- 设计并实现事件溯源Agent Harness：自研Plugin Kernel、typed ServiceKey、Turn/Step lifecycle、append-only Ledger及可重建TaskState，参考DeepSeek Harness与Youtu-Agent实现Runtime/Policy/Evaluation/Evolution解耦，累计76项零模型测试与4/4历史轨迹精确回放。
- 构建离线受控Evolution Plane：不可变Profile版本经paired Shadow及integrity/leakage/质量置信区间/Token成本/回归门禁后才能晋升，决策全量Ledger化且支持确定性Rollback；Core与RealReplica业务语义通过Extractor/Provider接口解耦。
- 构建Capability-negotiated Completion与Durable Recovery链路，在冻结MiniBench16上实现Contract/Provider任务覆盖16/16；历史counterfactual捕获5/10 missing-state/artifact类失败且成功任务误拦截0/6，12条人工失败标签Recovery映射覆盖12/12、blind retry为0。
- 建立成本敏感paired evaluation与Wilson置信门禁：固定model/image/seed/Profile fingerprint，发现首个Candidate虽保持1.0质量但单次Token观测+65.6%，通过surface fingerprint和counterfactual replay判定不可归因于Harness，并停止后续付费扩跑，避免用单次成功掩盖成本不确定性。

## 7. 90秒面试讲述

“这个项目不是围绕Bench写特判，而是增强模型Harness。我把DeerFlow降为Runtime Provider，自研Plugin Kernel、ServiceKey和append-only Ledger，TaskState、Evidence、Progress、Recovery都能回放；再通过CriterionExtractor和Observation Provider把具体环境语义隔离在integration。自进化也不是在线改Prompt，而是不可变Profile候选经过paired Shadow、泄漏、质量置信区间、成本和回归门禁后才能晋升，所有决定可回放、可回滚。RealReplica只负责外部验证：我冻结16任务并只跑一个fresh pair，发现成本方差后停止扩跑。因此项目价值是通用架构、受控演进和证据纪律，而不是包装成功率。”

## 8. 声明边界

当前不能声称：

- MiniBench整体成功率提升；
- Recovery已经使真实失败任务转为通过；
- Browser fallback guard已经上线；
- Practice已经从真实Outcome学习。
- Harness已经在线自主修改或晋升生产Profile。

所有可声明指标及SHA-256见[`../evidence/index.json`](../evidence/index.json)。
