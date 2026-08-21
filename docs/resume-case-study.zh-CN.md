# Adaptive Agent Harness：简历项目技术案例

> 一个面向真实长链路业务任务、事件溯源、能力可替换、证据驱动完成与严格成本门禁的 Agent Harness。

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
  Rollout → Judgement → Paired Statistics → Confidence-gated Practice
```

关键不变量：

1. Model-visible means Ledger-backed；
2. TaskState可删除并从JSONL完整重建；
3. Verifier、rubric、ground truth永不进入在线Context；
4. Completion必须有Provider支持的Evidence；
5. Recovery执行不等于Recovery有效；
6. 没有足够真实Outcome样本时Practice默认关闭。

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

## 4. 已验证结果

| 项目 | 证据 |
|---|---:|
| 零模型单元测试 | 67 |
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

- 设计并实现事件溯源Agent Harness：自研Plugin Kernel、typed ServiceKey、Turn/Step lifecycle、append-only Ledger及可重建TaskState，参考DeepSeek Harness与Youtu-Agent实现Runtime/Evaluation/Practice解耦，累计67项零模型测试与4/4历史轨迹精确回放。
- 构建Capability-negotiated Completion与Durable Recovery链路，在冻结MiniBench16上实现Contract/Provider任务覆盖16/16；历史counterfactual捕获5/10 missing-state/artifact类失败且成功任务误拦截0/6，12条人工失败标签Recovery映射覆盖12/12、blind retry为0。
- 建立成本敏感paired evaluation与Wilson置信门禁：固定model/image/seed/Profile fingerprint，发现首个Candidate虽保持1.0质量但单次Token观测+65.6%，通过surface fingerprint和counterfactual replay判定不可归因于Harness，并停止后续付费扩跑，避免用单次成功掩盖成本不确定性。

## 7. 90秒面试讲述

“这个项目解决的不是让Agent多一个Prompt，而是让长链路执行可观测、可替换、可恢复、可做因果评测。我把DeerFlow降为Runtime Provider，自研了Plugin Kernel和append-only Ledger，所有TaskState、Evidence、Progress、Recovery都能回放。完成判断不是模型说done，而是Contract与Runtime Provider能力协商；没有Provider的criterion只能observe-only，不能伪装验证。Recovery也拆成decision、execution、progress、outcome四层，并用Wilson门禁阻止零样本在线学习。测试上我冻结16任务MiniBench，只跑了一个fresh pair；两边都1.0，但Candidate Token高65.6%，所以立即停止。进一步用完全一致的模型输入fingerprint和事件反事实回放证明这不是已知Harness开销，而是方差未定。这套项目的核心价值是工程边界和证据纪律，而不是包装一个未经验证的成功率数字。”

## 8. 声明边界

当前不能声称：

- MiniBench整体成功率提升；
- Recovery已经使真实失败任务转为通过；
- Browser fallback guard已经上线；
- Practice已经从真实Outcome学习。

所有可声明指标及SHA-256见[`../evidence/index.json`](../evidence/index.json)。
