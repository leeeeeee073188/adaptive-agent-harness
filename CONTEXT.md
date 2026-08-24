# Adaptive Agent Harness

This context defines the language for an independent Agent Harness whose primary outcome is successful execution of externally verified real-world tasks. DeerFlow is its current execution foundation, not the product boundary.

## Product boundary

**Agent Harness**:
The independent product that governs, observes, evaluates, and improves agent task execution across replaceable execution foundations.
_Avoid_: DeerFlow plugin, benchmark wrapper, agent framework

**Runtime Foundation**:
An existing agent runtime that supplies mature execution capabilities to the Agent Harness; DeerFlow is the current Runtime Foundation.
_Avoid_: Harness Core, benchmark

**Runtime Adapter**:
The boundary through which the Agent Harness drives and observes a Runtime Foundation without making that foundation the owner of Harness policy.
_Avoid_: Harness, middleware collection

**Runtime Conformance**:
The shared behavioural contract every Runtime Adapter must satisfy so Harness policy has the same meaning across execution foundations.
_Avoid_: provider-specific test, import compatibility

**Primary Model**:
The version-pinned multimodal model used for both language reasoning and image-aware reasoning within a Profile.
_Avoid_: text model, separate visual agent, fallback model

## Outcomes and evaluation

**Task Execution Success**:
Externally verified completion of a task's required outcome and constraints.
_Avoid_: process success, non-empty response, model says done

**Task Execution Success Rate**:
The proportion of eligible RealReplicaBench evaluations that achieve Task Execution Success; this is the project's primary outcome.
_Avoid_: runtime completion rate, artifact existence rate, unit-test pass rate

**Stable-pass Regression**:
A previously reliable task that loses Task Execution Success under a Candidate; zero Stable-pass Regression is a promotion constraint.
_Avoid_: score variance, expected candidate failure

**Wasteful Repetition**:
Repeated execution that produces no new task evidence, world-state change, or materially different strategy.
_Avoid_: all retries, all repeated tools, high Token usage

**Action Scope**:
The runtime surface, target object, operation intent, and materially relevant arguments of an attempted action; it is used to distinguish a repeated strategy from a genuinely different attempt.
_Avoid_: tool name, model turn, exact command string

**No-progress Event**:
An action within the same Action Scope that yields no new task evidence, world-state change, or materially different strategy. A post-mutation verification action is not a No-progress Event merely because it repeats an earlier read.
_Avoid_: failed action, retry, expensive action

**Resource Guardrail**:
A constraint on Wasteful Repetition and runaway execution, not a fixed cap on Token growth for a Candidate that improves Task Execution Success.
_Avoid_: Token optimisation target, universal Token ceiling

**Development Rollout**:
A recorded task execution from the Development split that may be used to derive and evaluate improvements.
_Avoid_: Held-out result, training answer

**Development Partition**:
The MiniBench tasks whose Rollouts may be used to distil Candidate Experiences and develop Profiles.
_Avoid_: all official Development tasks, Transfer Validation

**Trajectory**:
The ordered, replayable execution facts produced by a Rollout.
_Avoid_: chat history, final response

**Shadow Evaluation**:
An offline comparison that measures a Candidate without changing the active global behaviour.
_Avoid_: production rollout, promotion

**Transfer Validation**:
Evaluation of an Experience on tasks other than the Development Rollouts from which it was distilled.
_Avoid_: source-task replay, memorisation check

**Transfer Partition**:
The MiniBench tasks reserved for Transfer Validation and excluded from Candidate Experience distillation.
_Avoid_: Development Partition, final evaluation

**Held-out Evaluation Partition**:
The MiniBench tasks reserved for evaluating a final Candidate and excluded from distillation, retrieval tuning, and promotion decisions. This is an internal project partition drawn from the official Development pool, not the benchmark's official held-out dataset.
_Avoid_: Transfer Partition, official RealReplicaBench held-out split

## Offline Experience Evolution

**Failure Pattern**:
A transferable description of how execution failed, abstracted away from task identity and expected answers.
_Avoid_: failed task id, verifier feedback, task-specific patch

**Experience Group**:
A set of Development Rollouts sharing a transferable task state and Failure Pattern, compared to identify strategies associated with better outcomes.
_Avoid_: all rollouts for one task id, semantic topic cluster

**Experience**:
A transferable strategy distilled from Development Rollouts for use in similar task states or failure modes.
_Avoid_: memory, answer cache, global prompt patch

**Experience Trigger**:
The structured task features, runtime surface, failure type, and observed state that define when an Experience applies.
_Avoid_: task id, keyword-only match

**Progress Signal**:
The new evidence or state change expected when an Experience is effective.
_Avoid_: tool activity, another model turn, strategy text

**Visual Evidence**:
Ledger-backed task evidence derived from image input by the Primary Model and attributable to its source image and model call.
_Avoid_: visual model opinion, screenshot summary, hidden vision context

**Candidate Experience**:
An Experience awaiting leakage, generalisation, deduplication, and offline validation checks.
_Avoid_: admitted experience, production policy

**Experience Store**:
The governed collection of promoted Experiences available for runtime retrieval.
_Avoid_: trajectory archive, prompt history, benchmark answer store

**Experience Leakage**:
Candidate Experience content that reveals or permits reconstruction of benchmark answers, verifier-private reasoning, expected outputs, or task-specific solution details.
_Avoid_: all task provenance, all concrete tool names

**Offline Experience Evolution**:
The process that distils Candidate Experiences from successful and failed Development Rollouts, validates them offline, and promotes eligible candidates to the Experience Store.
_Avoid_: online self-modification, prompt mutation, model fine-tuning

**Experience Retrieval**:
Runtime selection of promoted Experiences using current task state and failure type, without changing global Prompt or Policy during the task.
_Avoid_: online learning, global policy update

**Retrieval Outcome**:
Attributed evidence describing whether a retrieved Experience was adopted and followed by the expected Progress Signal, Task Execution Success, ineffectiveness, or harm.
_Avoid_: retrieval count, model acknowledgement, Experience popularity

**Experience Quarantine**:
Removal of an Experience from runtime eligibility while evidence of ineffectiveness, harm, leakage, or obsolescence is reviewed.
_Avoid_: deletion, failed retrieval, permanent retirement

**Profile Promotion**:
The governed activation of a versioned Harness configuration that controls policies, budgets, retrieval, and Runtime Adapter behaviour.
_Avoid_: Experience Promotion, runtime retrieval

**Experience Promotion**:
The governed admission of one validated Candidate Experience to the Experience Store, independently of Profile Promotion.
_Avoid_: Profile release, source-task success

**Promotion**:
The governed admission of an offline-validated Candidate Experience or Profile into an active store or configuration.
_Avoid_: retrieval, experimentation, automatic prompt rewrite
