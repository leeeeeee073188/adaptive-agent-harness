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

Implemented architecture core (A0 + A1):

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
```

The DeerFlow bridge reconciles incremental `messages-tuple` events with
cumulative `values` snapshots, deduplicates tool calls/results, treats the
`end` usage record as authoritative, and preserves interrupted runs as a
replayable ledger. DeerFlow remains a runtime provider; it does not own task
state or evaluation policy.

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

Design references: [DeepSeek Harness architecture](https://github.com/deepseek-ai/deepseek-harness/blob/main/docs/architecture.md)
and [Tencent Youtu-Agent](https://github.com/Tencent/Youtu-agent).
