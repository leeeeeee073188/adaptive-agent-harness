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

Implemented M0 architecture kernel:

```text
Profile / Bundle
    -> Plugin Kernel + Scoped Service Registry
        -> Environment / Toolkit / Context / Model / Tool seams
            -> Turn-Step Agent Driver
                -> Append-only SessionLedger
                    -> deterministic message/state projections

Evaluation plane (offline): RolloutRecord -> JudgeResult -> admissible ExperienceCandidate
```

Run the zero-model verification suite:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m compileall -q src tests
```
