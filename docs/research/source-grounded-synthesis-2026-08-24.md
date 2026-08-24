# Source-grounded agent synthesis note (2026-08-24)

Scope: explain why a harness can regress into copying public intermediate artifacts, then extract reusable mechanisms for provenance, claim-evidence tracking, and verification-before-final. This note only uses primary sources: papers, official docs, and pinned OSS reference implementations.

## What this is trying to prevent

**Inference from the current A42 failure mode:** the agent likely treated an already-public intermediate artifact as if it were a valid final answer, instead of requiring a source-backed derivation chain plus an independent verification gate. That failure pattern is common when a harness records *actions* but not *claim lineage*, or when it lets a synthesis step reuse visible intermediate text without checking whether each claim is actually supported by the underlying evidence.

The design response is not “make the model think harder.” It is to make the runtime prove, per claim, that:

1. the claim has a provenance trail,
2. the supporting evidence is present and fresh enough,
3. the claim set covers the task obligations, and
4. a verifier has approved the final draft before release.

## Mechanisms worth importing

| Mechanism | What the source shows | Harness mapping | Boundary |
| --- | --- | --- | --- |
| **Evidence collection during browsing** | WebGPT records quote extracts with page title/domain so humans can later inspect references. | Make every browse / fetch action emit a durable evidence item with source URI, snippet, and retrieval state. | Works best for evidence-backed QA; not a substitute for full claim verification. |
| **Attributed QA / citation support** | Attributed QA formalizes answer + supporting pointer; RARR finds unsupported content and revises it toward attribution. | Split answer drafting from attribution repair; unsupported text should be revisable, not silently accepted. | Citation quality and factual support are different; both must be checked. |
| **Claim-evidence decomposition** | PaperTrail decomposes answers and source docs into discrete claims and evidence, then maps support. CAGE generates claims from a graph before realizing surface citations. | Add a claim-evidence graph between retrieval and final synthesis. Do not let a final answer be a free-form blob without claim nodes. | Most useful when answers are multi-claim or long-form. |
| **Process supervision** | “Let’s Verify Step by Step” shows step-level supervision beats outcome-only supervision on math. | Attach feedback to intermediate steps, not just the terminal answer. | Great for reasoning traces; weaker for broad open-ended web synthesis unless paired with source checks. |
| **Verification-before-final** | Chain-of-Verification drafts an answer, generates verification questions, answers them independently, then writes the final response. | Add a dedicated verification pass that cannot read the draft as authority. | A verifier can still be biased if it sees the whole draft; keep the checks separable. |
| **Verification-aware planning** | VeriMAP encodes planner-defined passing criteria as verification functions for subtasks. | Put coverage obligations into the plan itself: each subtask should declare what counts as “done.” | Strongest when subtasks are structurally distinct and can be checked mechanically. |

## Primary sources

### Evidence / attribution / provenance

- [WebGPT: Browser-assisted question-answering with human feedback](https://arxiv.org/abs/2112.09332) — browsing collects references while the model searches; the recorded extracts are meant to support factual evaluation.
- [Attributed Question Answering: Evaluation and Modeling for Attributed Large Language Models](https://arxiv.org/abs/2212.08037) — formalizes answer + support pointer as a task.
- [RARR: Researching and Revising What Language Models Say, Using Language Models](https://arxiv.org/abs/2210.08726) — researches unsupported output and revises it toward attribution.
- [PaperTrail: A Claim-Evidence Interface for Grounding ...](https://arxiv.org/html/2602.21045v1) — decomposes answers and source documents into claims and evidence.
- [CAGE: Cognitive Attribution Graphs for Faithful Inline Citation ...](https://arxiv.org/html/2607.24236v1) — separates graph-based claim construction from surface citation realization.
- [From Agent Traces to Trust: Evidence Tracing and Execution Provenance in LLM Agents](https://arxiv.org/abs/2606.04990) — frames agent trace provenance as connections among evidence, tools, memory, actions, and final answers.

### Verification / process supervision

- [Let’s Verify Step by Step](https://arxiv.org/abs/2305.20050) — step-level supervision beats outcome-only supervision on hard reasoning tasks.
- [Improving mathematical reasoning with process supervision](https://openai.com/index/improving-mathematical-reasoning-with-process-supervision/) — OpenAI’s public summary of why intermediate-step feedback matters.
- [Chain-of-Verification Reduces Hallucination in Large Language Models](https://arxiv.org/abs/2309.11495) — draft, verify, then produce a final verified response.
- [Verification-Aware Planning for Multi-Agent Systems](https://arxiv.org/abs/2510.17109) — planner-defined verification functions become subtask gates.
- [LLM-as-a-Verifier: A General-Purpose Verification Framework](https://arxiv.org/html/2607.05391v1) — verification can be scaled and used as dense feedback / progress monitoring.

### Product / harness reference docs

- [OpenAI Agents SDK](https://developers.openai.com/api/docs/guides/agents), [orchestration](https://developers.openai.com/api/docs/guides/agents/orchestration), [guardrails and human review](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals), [tracing](https://developers.openai.com/api/docs/guides/agents/integrations-observability), [sandboxes](https://developers.openai.com/api/docs/guides/agents/sandboxes) — clear split between orchestration, execution, guardrails, and traceability.
- [Claude Code hooks](https://docs.anthropic.com/en/docs/claude-code/hooks), [sub-agents](https://docs.anthropic.com/en/docs/claude-code/sub-agents), [memory](https://docs.anthropic.com/en/docs/claude-code/memory) — lifecycle hooks are deterministic policy points; subagents isolate narrow tasks; memory is context, not hard enforcement.
- [Google ADK sessions](https://google.github.io/adk-docs/sessions/), [callbacks](https://google.github.io/adk-docs/callbacks/), [runtime](https://google.github.io/adk-docs/runtime/), [safety](https://google.github.io/adk-docs/safety/) — separate session/state/memory, then enforce policy with callbacks and in-tool guardrails.
- [OpenHands architecture overview](https://docs.openhands.dev/sdk/arch/overview), [design principles](https://docs.openhands.dev/sdk/arch/design), [observability](https://docs.openhands.dev/sdk/guides/observability), [evaluation harness](https://docs.openhands.dev/openhands/usage/developers/evaluation-harness) — composable boundaries, stateless-by-default design, built-in tracing, and benchmark integration.

## Pinned OSS reference implementations

These are supplemental, not replacements for the papers/docs above.

- `deepseek-ai/deepseek-harness@99f6f02fecdb7dff40c3fbc9470f5907c29f74ca:README.md:L5-L11` — current README says the harness is plugin-based, built by DeepSeek AI, and still in developer preview. Recent activity signal: commit `99f6f02` on 2026-08-17.
- `deepseek-ai/deepseek-harness@99f6f02fecdb7dff40c3fbc9470f5907c29f74ca:python/sdk-runtime/src/deepseek_harness_runtime/runtime/cordis.yml:L1-L49` — runtime config separates JSON-RPC server, agent spine, model adapter, JSONL persistence, checkpoint policy, bash, and filesystem; persistence and checkpoint policy are distinct seams.
- `deepseek-ai/deepseek-harness@99f6f02fecdb7dff40c3fbc9470f5907c29f74ca:packages/context/session-reference/README.md:L5-L17` — session references are bounded, read-only snapshots of other sessions, and the resulting target log is replay-stable even if the source session later changes.
- `deepseek-ai/deepseek-harness@99f6f02fecdb7dff40c3fbc9470f5907c29f74ca:docs/subsystems/session.md:L247-L253` and `docs/subsystems/tools.md:L170-L172` — surface events record source event sequences, while tool execution ends in an immutable authoritative result after monotonic policy stages.
- `TencentCloudADP/youtu-agent@c2caa539f4c95ae1c39ed24dc8a99cb3651e1d5d:README.md:L23-L34` — Youtu-Agent explicitly separates agent generation, experience learning, RL, tracing, and evaluation; recent activity signal: commit `c2caa53` on 2026-03-21.
- `TencentCloudADP/youtu-agent@c2caa539f4c95ae1c39ed24dc8a99cb3651e1d5d:README.md:L215-L226` — the repo frames itself as a reusable baseline for research and a portable scaffold for real applications, with visual tracing and one-click evaluation scripts.
- `bytedance/deer-flow@b3b7042ddd179f1321b20cee2e33cf0afc48bb13:README.md:L781-L805` and `L1108-L1114` — DeerFlow emphasizes progressive skill loading, slash-activated tool policies, and subagents as an optimization with structured return and synthesis. Recent activity signal: commit `b3b7042` on 2026-08-19.

## What this implies for the harness

1. **Track lineage, not just text.** Every intermediate artifact should know where it came from: external source, tool output, memory item, or prior draft.
2. **Make claims first-class.** Final synthesis should read from a claim graph, not from an unconstrained buffer of notes.
3. **Require coverage obligations.** The planner should say what must be verified before completion, and the verifier should refuse to finalize if coverage is incomplete.
4. **Separate draft authority from final authority.** A copied public draft or intermediate result can be a candidate evidence item, but never the final answer by itself.
5. **Use process supervision where the task is multi-step.** Intermediate decisions need their own checks, especially when the output is long-form or multi-claim.
6. **Keep verification independent.** The final gate should not simply re-read the draft as if it were evidence.

## A42-specific design takeaway

**Inference from the sources above:** A42 likely needs a harness rule that treats any “known public result” as *untrusted until re-grounded*. If the agent finds a public intermediate artifact that looks similar to the expected answer, the runtime should:

1. store it as evidence with provenance,
2. decompose it into claims,
3. check each claim against the original sources or tool outputs,
4. reject any unsupported claim or prompt the agent to re-derive it,
5. only then allow a final synthesis step.

That would make “copying a public intermediate result” much harder to pass off as a valid completion.
