# Adaptive Agent Harness

Architecture-first harness for real-world, long-horizon business tasks.

The project is intentionally **not** another monolithic agent loop. It combines:

- DeepSeek Harness ideas: plugin-composed services, reversible effects,
  scoped capability seams, append-only session events, and explicit Turn/Step
  lifecycle;
- Tencent Youtu-Agent ideas: config-driven Agent/Environment/Toolkit/Context
  composition, trajectory recording, rollout/judgement separation, and an
  offline experience-practice plane;
- DeerFlow as the first runtime adapter rather than the architectural core;
- RealReplicaBench as an external evaluation adapter rather than runtime logic.

Implemented architecture core (A0–A3):

```text
Profile / Bundle
    -> Plugin Kernel + Scoped Service Registry
        -> Environment / Toolkit / Context / Model / Tool seams
            -> Turn-Step Agent Driver
                -> Append-only SessionLedger
                    -> deterministic message/state projections

Evaluation plane (offline): RolloutRecord -> JudgeResult -> admissible ExperienceCandidate

DeerFlow RuntimeAdapter:
    DeerFlowClient.stream -> canonical event reconciliation -> SessionLedger
    request/header snapshot + Environment build/cleanup + partial-run recovery

TaskContract / TaskState:
    public prompt/schema -> typed criteria -> durable task facts
    Ledger -> disposable TaskState projection -> bounded model working set

Tool Reliability / Completion Gate:
    pre -> execute -> post -> classify -> bounded recovery -> durable result
    proposed finish -> contract projection -> runtime evidence -> accept/reject
```

The DeerFlow bridge reconciles incremental `messages-tuple` events with
cumulative `values` snapshots, deduplicates tool calls/results, treats the
`end` usage record as authoritative, and preserves interrupted runs as a
replayable ledger. DeerFlow remains a runtime provider; it does not own task
state or evaluation policy.

Task contracts are derived conservatively from the public task prompt/schema.
Artifact, exact-count, observation, and dependency criteria are checked only
against runtime evidence. Evaluation-only fields such as verifier, rubric,
ground truth, and expected answers are rejected at the contract boundary.
Projection context is bounded, while the ledger retains complete evidence.

Tool reliability is deterministic and opt-in: only transient failures are
retried within a profile budget. The completion gate is also an optional
service, enabling clean baseline/candidate ablations. Tool prose is never
treated as proof; only explicit structured evidence can satisfy a criterion.

Run the zero-model verification suite:

```bash
uvx ruff check src tests scripts
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests scripts
```

Replay a historical RealReplicaBench run at zero model cost:

```bash
PYTHONPATH=src python scripts/replay_deerflow_m0.py /path/to/task-run
```

The checked-in `evidence/a1-replay/summary.json` records four exact historical
replays across file, browser, API/MCP, and browser-vision tasks. All response,
tool-count, and token-usage checks pass with zero new model calls.
`evidence/a2-projection/summary.json` records the zero-model A2 contract/state
checks and confirms all four A1 historical replay hashes remain unchanged.
`evidence/a3-reliability/summary.json` records bounded-retry, failure-ledger,
premature-completion, structured-evidence, and ablation checks.

Run the frozen MiniBench16 offline preflight before any paid evaluation:

```bash
PYTHONPATH=src python scripts/preflight_minibench16.py /path/to/RealReplicaBench
```

The preflight validates the exact 16-task order/hash, Development-only split,
type/difficulty/capability balance, public-prompt contract coverage, historical
baseline availability, and a 32-cell controlled baseline/candidate manifest.
It reports `paid_run_ready=false` until both contract coverage and the live
DeerFlow policy bridge are complete; historical results are never reused as a
formal paired baseline.

The public-client policy bridge now supports bounded same-thread continuation:
each DeerFlow turn is translated into durable facts, explicit tool artifacts
and filesystem inspections become evidence, and an unsupported finish claim
is returned to the same thread as ledger-backed feedback. MiniBench public
contract coverage is 16/16. Container wiring is intentionally still a closed
gate, so this implementation alone does not authorize paid runs.

A reproducible `--network none` probe now copies the Harness into the pinned
DeerFlow image and exercises the real `DeerFlowClient.stream` path with a
deterministic in-process agent. It proves package import, same-thread bridge
execution, Ledger persistence, and cleanup with zero provider calls. Formal
readiness requires the RealReplica candidate runner to invoke this exact path;
the runner now does so behind `deerflow.adaptive_policy_enabled`, archives
`adaptive-ledger.jsonl`, and disables its legacy external kill poller. The
evidence-bound preflight is ready for a paired MiniBench canary, but never
starts paid work automatically.

The first fresh paired canary (`file-google-trends-csv-flatten`) produced equal
1.0 scores and semantically identical output. The candidate also produced a
999-event canonical Ledger with artifact hash evidence. Its observed token
usage was 251,784 versus 152,041 for baseline (+65.6%), exceeding the 25%
single-run limit, so the remaining Block 1 tasks were not started. However,
prompt/config/model/image fingerprints were identical, the candidate completed
in one turn, and counterfactual event replay was exact; attributable Harness
model-token overhead is therefore zero for this cell. Four comparable vanilla
runs have a 22.4% token coefficient of variation, so the observed delta remains
an unresolved provider/trajectory-variance signal rather than a proven Harness
regression.

Design references: [DeepSeek Harness architecture](https://github.com/deepseek-ai/deepseek-harness/blob/main/docs/architecture.md)
and [Tencent Youtu-Agent](https://github.com/Tencent/Youtu-agent).
