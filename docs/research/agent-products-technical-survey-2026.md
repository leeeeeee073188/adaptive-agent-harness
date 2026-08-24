# Agent Products Technical Survey (2026-08-24)

Survey date: 2026-08-24.

Scope: Anthropic Claude Code / Agent SDK, OpenAI Codex / Agents SDK, LangGraph, AutoGen / Magentic-One, Google ADK, OpenHands, SWE-agent, Letta / MemGPT, BrowserGym / AgentLab, and Aider.

Method: official docs, official technical blogs, official repos, and official papers only. I treated current docs pages as living sources and used the current published pages available on 2026-08-24.

## Executive summary

If you are designing an independent, general-purpose agent harness, the recurring pattern across the strongest systems is:

1. **Separate the loop from the model.** The harness owns the turn loop, tool dispatch, state updates, and resume logic; the model only chooses actions.
2. **Split short-term context from long-term memory.** The best systems keep working context small and persist durable facts separately.
3. **Treat tool access as policy, not convenience.** Hooks, permissions, sandboxes, approvals, and deterministic callbacks are the real control surface.
4. **Make verification first-class.** Traces, tests, benchmark runners, and replayable trajectories are more valuable than a prettier chat UI.
5. **Use subagents for isolation, not magic.** Subagents help when the task can be decomposed into narrow, independently scoped jobs.
6. **Make observability cheap.** Step-level traces, cost accounting, and replay dramatically reduce debugging time.
7. **Cost control is a harness responsibility.** The products that scale best all expose some combination of model choice, usage reporting, cached context, or per-run telemetry.

## Comparison matrix

Legend: `Strong` = first-class and explicit; `Partial` = present but secondary; `External` = handled mostly by surrounding tooling; `Eval` = primarily benchmark/evaluation oriented rather than a production runtime.

| Product | Runtime loop | Context | Tool governance | Planning / state | Memory / experience | Verification | Subagent | Observability | Cost posture |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Claude Code / Agent SDK | Terminal/SDK agent loop | Fresh session context + project files + compaction | Hooks, permissions, MCP, disallowed tools | Session lifecycle, compaction, hooks | CLAUDE.md + auto memory | Code review, hooks, analytics | Strong | OTel + analytics + usage reports | Token-based; cloud/provider billed |
| OpenAI Codex / Agents SDK | Responses API-driven loop, CLI/app server, sandbox workflows | Sessions + conversations + resumable state | Guardrails, handoffs, approvals, sandbox | Runner/session orchestration | Built-in session memory | Traces, evals, sandbox, tests | Strong | Built-in tracing | Token/API pricing; sandbox usage priced separately |
| LangGraph | Graph runtime | Checkpoints + stores | Deterministic graph nodes + HITL interrupts | Graph state + time travel | Short-term checkpoints, long-term stores | Replay/time travel + LangSmith | Strong | LangSmith tracing | External; depends on model/storage/provider |
| AutoGen / Magentic-One | Team/group-chat orchestrator | Memory modules, RAG, message history | Team orchestration, user proxy, logging | Orchestrator-managed teams | Memory plugins / RAG | Tracing, logging, HITL | Strong | OTel tracing + logs | External; mostly model/provider dependent |
| Google ADK | Multi-agent workflow engine | Session / state / memory split | Callbacks, plugins, workflow agents | Deterministic workflows + A2A | Cross-session memory service | Optimize/evaluate + traces | Strong | Cloud Trace / OTel / partner tools | External; model + runtime + telemetry services |
| OpenHands | CLI, web UI, REST/WebSocket agent server | Automatic context compression + search | Sandbox provider, skills, MCP, LLM config | Task planning + decomposition | Skills + search + repo state | Evaluation harness, live status, web search | Partial | Live status + WebSocket events | Provider/model billed; cloud adds usage/budget reporting |
| SWE-agent | Main agent loop in a YAML-driven harness | Files + repo context + sandboxed repo | Config files, tool bundles, Docker/SWE-ReX | Agent class + environment config | Minimal by design | Benchmarking + trajectory inspector | Partial | Trajectories / inspector | External model + sandbox cost |
| Letta / MemGPT | Stateful agent harness | Persisted state and context constitution | Skills, permissions, memory guards | Threads/conversations + harness state | MemFS + dreaming + editable memory | Trace retrieval + recollection | Strong | OTEL trace retrieval | Subscription / API plan + tool execution cost |
| BrowserGym / AgentLab | Benchmark/eval environment | Task state in environments | Benchmark-defined actions/observations | Study/run orchestration | Reproducibility journal | Leaderboard, replay, traces | External | AgentXray + reproducibility artifacts | Eval infra cost; model/runtime external |
| Aider | Local git-centric CLI loop | Repo map + selected files + chat history | Git and chat commands | Chat modes, commit/undo workflow | Minimal; file-focused | Diffs, commits, tokens, benchmarks | No native subagent layer | Token command + git history; limited telemetry | Token-based provider cost; no separate runtime bill |

## Product-by-product notes

### 1) Anthropic Claude Code / Agent SDK

**What it is**
- Claude Code is an agentic coding tool in the terminal/IDE/browser that reads code, edits files, runs commands, and integrates with developer tools.
- The Agent SDK exposes the same loop and context-management model as a library for Python and TypeScript.

**Runtime loop**
- The key abstraction is a harnessed agent loop that plans its own steps and calls tools.
- Anthropic’s docs explicitly split the extension layer into skills, subagents, hooks, MCP, and plugins.

**Context / state / memory**
- Claude Code starts each session with a fresh context window.
- Persistent project guidance lives in `CLAUDE.md`, while auto memory captures learned preferences.
- Anthropic’s context-window guidance emphasizes server-side compaction for long-running agentic workflows.

**Tool governance**
- Hooks are the strongest policy primitive: they run at defined lifecycle points and receive JSON context.
- Permissions can deny specific subagents or tools.
- This is a useful pattern when you want deterministic enforcement instead of prompt-only compliance.

**Planning / verification / observability**
- Code review is a separate, specialized surface that runs multiple agents over a diff.
- Monitoring docs describe OpenTelemetry-based export, metrics, logs, traces, and analytics dashboards.
- Claude Code usage data can also be reported in admin/analytics surfaces.

**Subagents**
- Claude Code supports custom subagents and even lets admins block named subagents.
- This is a strong pattern for a harness that wants narrow specialist roles without giving the main agent all tools at once.

**Cost**
- Claude Code charges by API token consumption; subscription plans exist for the product surface.
- Anthropic’s cost docs also show that cloud-provider deployments bill through the provider rather than Anthropic’s own analytics pipeline.

**Transferable mechanism**
- The best reusable idea is the **hook-first policy layer**: deterministic lifecycle hooks, plus a soft memory layer (`CLAUDE.md` / auto memory), plus explicit subagent controls.

**Main risk**
- Hooks and memory are powerful but not the same as hard guarantees; they need disciplined configuration and review.
- Cost can drift if you allow many parallel sessions or large-context workflows.

**Official sources**
- https://docs.anthropic.com/en/docs/claude-code/overview
- https://docs.anthropic.com/en/docs/claude-code/sdk
- https://docs.anthropic.com/en/docs/claude-code/memory
- https://docs.anthropic.com/en/docs/claude-code/hooks
- https://docs.anthropic.com/en/docs/claude-code/sub-agents
- https://docs.anthropic.com/en/docs/claude-code/code-review
- https://docs.anthropic.com/en/docs/claude-code/monitoring-usage
- https://docs.anthropic.com/en/docs/claude-code/costs
- https://docs.anthropic.com/en/docs/build-with-claude/context-windows

### 2) OpenAI Codex / Agents SDK

**What it is**
- OpenAI now treats Codex as a suite of agent surfaces: CLI, app, cloud, app server, and related workflows.
- The Agents SDK is the code-first orchestration layer; the Responses API remains the lower-level direct model path.

**Runtime loop**
- OpenAI’s agent-loop writeups describe the harness as the logic that orchestrates user input, the model, and tool use.
- Codex CLI uses the Responses API for inference, and the harness controls prompt assembly, tool exposure, and context management.
- The app-server architecture adds durable thread lifecycles, event streaming, and JSON-RPC-style client/server boundaries.

**Context / state / memory**
- The Agents SDK provides sessions and built-in session memory.
- Codex blogs describe threads that can be created, resumed, forked, and archived.
- The SDK’s state model is deliberately separate from the underlying model call, which is exactly what a general harness needs.

**Tool governance**
- The Agents SDK docs emphasize tools, handoffs, approvals, tracing, and sandbox execution.
- Handoffs are the main subagent-like primitive: keep each specialist narrow and route only when the next branch truly needs different instructions or tools.
- Sandboxes separate orchestration from execution.

**Planning / verification / observability**
- OpenAI’s own docs tie together evaluations, trace grading, and the Agents SDK tracing surface.
- Tracing is built in and emits structured records of model calls, tool calls, handoffs, guardrails, and custom spans.
- Codex’s launch materials also emphasize iteratively running tests until passing results.

**Subagents**
- The SDK’s orchestration/handoff model is effectively a first-class specialist-routing mechanism.
- That is a strong fit for large tasks with clear decomposition boundaries.

**Cost**
- OpenAI exposes pricing for the platform, including separate container/sandbox and tool-call pricing.
- Codex pricing announcements also make usage-to-spend more explicit for teams.

**Transferable mechanism**
- The most reusable idea is **orchestration separated from execution**: a thin harness that owns sessions, tracing, and handoffs, while sandboxed workers do the risky work.

**Main risk**
- The system is powerful but intentionally leaves many policy decisions to the developer.
- Without explicit guardrails and evals, the flexibility can turn into a lot of orchestration debt.

**Official sources**
- https://developers.openai.com/api/docs/guides/agents
- https://developers.openai.com/api/docs/guides/agents/quickstart
- https://developers.openai.com/api/docs/guides/agents/orchestration
- https://developers.openai.com/api/docs/guides/agents/running-agents
- https://developers.openai.com/api/docs/guides/agents/integrations-observability
- https://developers.openai.com/api/docs/guides/agents/sandboxes
- https://developers.openai.com/api/docs/guides/agent-evals
- https://developers.openai.com/api/docs/pricing
- https://openai.com/index/unrolling-the-codex-agent-loop/
- https://openai.com/index/unlocking-the-codex-harness/
- https://openai.com/index/the-next-evolution-of-the-agents-sdk/
- https://openai.com/index/codex-flexible-pricing-for-teams/

### 3) LangGraph

**What it is**
- LangGraph is a low-level agent orchestration framework that emphasizes control, state, and persistence.
- LangGraph’s public positioning is “balance agent control with agency.”

**Runtime loop**
- The harness is a graph: nodes, edges, interrupts, and checkpoints.
- That makes the control flow explicit rather than hidden inside a monolithic agent loop.

**Context / state / memory**
- LangGraph separates short-term memory, checkpoints, and long-term stores.
- Checkpointers persist thread state step by step; stores hold app-defined long-term data.
- Time travel lets you replay or fork from a checkpoint.

**Tool governance**
- Governance is not presented as a single permission system; instead it comes from graph design, deterministic nodes, and human-in-the-loop interruptions.
- That is powerful but means policy often lives in the graph architecture itself.

**Planning / verification / observability**
- Persistence and time travel are verification primitives: you can inspect what happened, replay it, and branch from it.
- LangSmith is the companion observability platform with tracing, monitoring, cost tracking, and evals.

**Subagents**
- LangGraph supports single-agent, multi-agent, and hierarchical patterns.
- It is especially strong where you want a supervisor graph, rather than a black-box multi-agent team.

**Cost**
- LangGraph itself is not a cost product; cost is mostly carried by your model, storage, and observability stack.
- That is flexible, but it means the harness does not hide the economics for you.

**Transferable mechanism**
- The best reusable idea is **checkpointed graph state with time travel**. For a generic harness, this is the cleanest way to get resumability, replay, and human review.

**Main risk**
- Because LangGraph is low-level, it can become a framework for building your own framework.
- That gives control, but it also increases design and maintenance burden.

**Official sources**
- https://www.langchain.com/langgraph
- https://docs.langchain.com/oss/python/langgraph/overview
- https://docs.langchain.com/oss/python/langgraph/persistence
- https://docs.langchain.com/oss/python/langgraph/add-memory
- https://docs.langchain.com/oss/python/langgraph/checkpointers
- https://docs.langchain.com/oss/python/langgraph/use-time-travel
- https://docs.langchain.com/langsmith/trace-with-langgraph
- https://www.langchain.com/langsmith/observability

### 4) AutoGen / Magentic-One

**What it is**
- AutoGen is a framework for building agentic systems; Magentic-One is its generalist multi-agent team architecture.
- The stable docs present AgentChat, teams, memory, logging, tracing, and human-in-the-loop support.

**Runtime loop**
- Magentic-One is organized around a group-chat/orchestrator style loop.
- The orchestrator manages the conversation flow rather than forcing a single linear planner.

**Context / state / memory**
- AutoGen has memory modules and RAG patterns for retrieving relevant facts into the agent’s context.
- That makes it useful when you want the agent to consult an external store before acting.

**Tool governance**
- Governance is mostly expressed through team orchestration, agent roles, user proxies, and the limits you place on each agent’s tools.
- In practice, this gives flexibility, but it also means you must design your own policy shape carefully.

**Planning / verification / observability**
- AutoGen exposes human-in-the-loop control during runs.
- It also has built-in tracing and observability powered by OpenTelemetry.
- Logging and tracing are distinct enough to support debugging and later analysis.

**Subagents**
- Multi-agent teamwork is one of AutoGen’s core strengths.
- Magentic-One is the clearest example of a built-in specialist-team pattern among the surveyed projects.

**Cost**
- AutoGen does not try to own model cost; that is mostly a provider concern.
- This keeps the framework lightweight, but it also means you need external budgeting and usage controls.

**Transferable mechanism**
- The key transferable idea is **team orchestration with explicit human feedback points**. That is a clean pattern for tasks that need collaboration and intervention.

**Main risk**
- Multi-agent systems can become opaque if you do not have good traces and a well-defined role split.
- Memory and RAG help, but they can also obscure why a decision was made if you do not log retrieval well.

**Official sources**
- https://github.com/microsoft/autogen
- https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/index.html
- https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/magentic-one.html
- https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/memory.html
- https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tracing.html
- https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/human-in-the-loop.html
- https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/logging.html
- https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/migration-guide.html

### 5) Google ADK

**What it is**
- Google’s ADK is a multi-language agent development kit that explicitly targets production agents.
- The docs position it around reliable agents, workflow agents, callbacks, plugins, sessions, memory, observability, and multi-agent collaboration.

**Runtime loop**
- ADK supports template workflow agents such as sequential, parallel, and custom workflows.
- This makes the loop a first-class design object rather than an implicit prompt trick.

**Context / state / memory**
- ADK has a clean split between session, state, and memory.
- Session is the current interaction; state is temporary session data; memory is cross-session knowledge.
- That split is one of the best conceptual models in the survey.

**Tool governance**
- Callbacks and plugins let you observe or intervene at key stages.
- A2A and workflow composition add another layer for multi-agent coordination.

**Planning / verification / observability**
- ADK has built-in observability docs, Cloud Trace integration, and evaluation/optimization surfaces.
- The docs also make token usage visible in streaming metadata, which is useful for cost accounting.

**Subagents**
- ADK is very explicit about sub-agents, coordinator agents, sequential/parallel execution, and agent teams.
- It is one of the strongest options if you want structured multi-agent systems without inventing your own control plane.

**Cost**
- ADK itself is open source, but deployment, memory, tracing, and runtime services may incur platform costs.
- The docs explicitly note that model choice affects capability, cost, and performance.

**Transferable mechanism**
- The most reusable idea is **the Session / State / Memory split**. For a generic harness, that is the cleanest vocabulary for separating working context from durable knowledge.

**Main risk**
- Because ADK is built for production breadth, there are many integration surfaces.
- That is good for enterprise use, but it can increase setup and operational complexity for a small harness.

**Official sources**
- https://google.github.io/adk-docs/
- https://google.github.io/adk-docs/agents/
- https://google.github.io/adk-docs/agents/workflow-agents/
- https://google.github.io/adk-docs/agents/workflow-agents/sequential-agents/
- https://google.github.io/adk-docs/agents/workflow-agents/parallel-agents/
- https://google.github.io/adk-docs/sessions/
- https://google.github.io/adk-docs/sessions/state/
- https://google.github.io/adk-docs/sessions/memory/
- https://google.github.io/adk-docs/callbacks/
- https://google.github.io/adk-docs/plugins/
- https://google.github.io/adk-docs/observability/
- https://google.github.io/adk-docs/observability/cloud-trace/
- https://google.github.io/adk-docs/integrations/agentops/
- https://google.github.io/adk-docs/optimize/
- https://google.github.io/adk-docs/tutorials/agent-team/
- https://google.github.io/adk-docs/a2a/

### 6) OpenHands

**What it is**
- OpenHands is a coding agent ecosystem with a Software Agent SDK, Agent Server, Cloud product, and sandboxed runtimes.
- It now delegates LLM orchestration to its Agent SDK.

**Runtime loop**
- The main agent implements the CodeAct idea: converse with the human, then either ask for clarification or act through code execution.
- That gives OpenHands a very practical “conversation + action” model.

**Context / state / memory**
- OpenHands highlights task planning and decomposition plus automatic context compression.
- It also supports searchable web context via configured search.
- Skills/global skills are the main extension mechanism for task-specific knowledge.

**Tool governance**
- The strongest governance lever is the sandbox provider choice: Docker, process, or remote sandbox.
- The docs are very clear that process mode is faster but unsafe, while Docker is the recommended isolation layer.
- MCP, tool settings, and environment variables round out the policy surface.

**Planning / verification / observability**
- OpenHands has live status, WebSocket events, and an evaluation harness.
- The docs also encourage benchmark-driven development and list use cases such as vulnerability remediation and automated QA.

**Subagents**
- OpenHands’ extension model is skills-focused rather than subagent-centric.
- It can still delegate work, but the docs I reviewed emphasize skills, orchestration, and sandboxing more than explicit nested specialist agents.

**Cost**
- The Cloud product includes usage reporting and budget management.
- The SDK docs also emphasize model/provider choice as a cost lever and publish provider token rates for hosted models.

**Transferable mechanism**
- The best thing to borrow is **sandbox choice as a first-class runtime policy**. A general harness should let the operator choose between fast/unsafe and slower/isolated execution modes.

**Main risk**
- OpenHands is broad and powerful, but that also makes it operationally heavy.
- If you use cloud search, MCP, and cloud sandboxes together, you inherit multiple external dependencies and cost surfaces.

**Official sources**
- https://docs.all-hands.dev/
- https://docs.all-hands.dev/sdk/index
- https://docs.all-hands.dev/modules/usage/about
- https://docs.all-hands.dev/modules/usage/agents
- https://docs.all-hands.dev/openhands/usage/runtimes/overview
- https://docs.all-hands.dev/usage/how-to/cli-mode
- https://docs.all-hands.dev/usage/cloud/cloud-api
- https://docs.all-hands.dev/usage/cloud/openhands-cloud
- https://docs.all-hands.dev/usage/configuration-options
- https://docs.all-hands.dev/usage/search-engine-setup
- https://docs.all-hands.dev/usage/key-features
- https://docs.all-hands.dev/usage/llms/llms
- https://docs.all-hands.dev/usage/llms/openhands-llms
- https://docs.all-hands.dev/usage/how-to/debugging
- https://docs.all-hands.dev/modules/usage/troubleshooting
- https://docs.all-hands.dev/openhands/usage/developers/websocket-connection
- https://docs.all-hands.dev/usage/local-setup

### 7) SWE-agent

**What it is**
- SWE-agent is a repository-level software engineering agent built around a command-line runtime and a configurable environment.
- The current docs say it is in maintenance-only mode and has been superseded by mini-swe-agent.

**Runtime loop**
- The `Agent` class runs the main loop.
- Configuration is primarily YAML-based, and the environment class defines repo/deployment behavior.

**Context / state / memory**
- SWE-agent is intentionally lean: the core context is the repository, the issue/problem statement, and whatever the agent adds into its working loop.
- The docs emphasize added files, trajectories, and sandboxed execution more than durable semantic memory.

**Tool governance**
- Tooling is explicit and command-based; custom tools can be added through config.
- Repo type and base commit are set through the environment configuration, which is a useful pattern for reproducibility.

**Planning / verification / observability**
- Benchmarking is first-class, especially on SWE-bench.
- The trajectory inspector shows results and whether a run was successful.
- The docs also stress unit/integration tests when changing behavior.

**Subagents**
- SWE-agent is mostly single-agent and tool-driven; it does not lean on a rich native subagent hierarchy.
- That simplicity is part of the design.

**Cost**
- Cost is mostly external: model API plus sandbox/runtime resources.
- The project’s value proposition is research reproducibility, not cost abstraction.

**Transferable mechanism**
- The best reusable idea is **benchmark-oriented trajectories**: every meaningful run should produce a replayable artifact that can be scored or inspected later.

**Main risk**
- The project is now maintenance-only, so it is not the best foundation for a new long-lived harness unless you specifically want its research patterns.
- The lean design is great for simplicity but limited for richer governance and memory needs.

**Official sources**
- https://swe-agent.com/latest/
- https://swe-agent.com/latest/background/
- https://swe-agent.com/latest/usage/hello_world/
- https://swe-agent.com/latest/usage/cl_tutorial/
- https://swe-agent.com/latest/usage/batch_mode/
- https://swe-agent.com/latest/usage/competitive_runs/
- https://swe-agent.com/latest/usage/inspector/
- https://swe-agent.com/latest/reference/agent/
- https://swe-agent.com/latest/reference/env/
- https://swe-agent.com/latest/reference/env_config/
- https://swe-agent.com/latest/config/config/
- https://swe-agent.com/latest/installation/changelog/
- https://swe-agent.com/latest/dev/contribute/
- https://swe-agent.com/0.7/background/aci/
- https://swe-agent.com/latest/installation/source/
- https://swe-agent.com/latest/usage/trajectories/
- https://openreview.net/forum?id=mXpq6ut8J3

### 8) Letta / MemGPT

**What it is**
- Letta is a stateful-agent platform whose whole thesis is that memory should survive across conversations and devices.
- The current docs frame the Letta harness as the runtime that connects LLMs to persisted context, manages turns, and executes tools.

**Runtime loop**
- The harness owns the conversation and execution state.
- Letta’s Agent SDK is built on top of that harness and adds advanced computer-use tools, skills, and subagents.

**Context / state / memory**
- This is Letta’s core advantage.
- Memory persists across conversations, can be edited by the agent, and is organized through MemFS.
- Dreaming runs background subagents that review recent conversations and consolidate lessons without interrupting active work.

**Tool governance**
- Skills and memory guards shape what agents can touch.
- The docs explicitly protect other agents’ memory directories and let subagents inherit only the parent-scope memory they are allowed to see.

**Planning / verification / observability**
- Letta exposes trace retrieval for runs, including agent steps, tool executions, and timing spans.
- That gives a concrete observability layer rather than just a transcript.

**Subagents**
- Subagents are a first-class part of the harness, not an add-on.
- Dreaming itself is based on background subagents.

**Cost**
- Letta has explicit pricing for personal, API, and team plans.
- Tool execution can have direct billing on API plans, which makes cost very visible.

**Transferable mechanism**
- The best reusable idea is **editable persistent memory with background reflection**.
- If you want an agent that improves across sessions, Letta is the clearest example in this survey.

**Main risk**
- Persistent state is powerful but creates governance and privacy risk.
- If you do not define memory ownership, retention, and scrubbing rules, state will become operationally messy.

**Official sources**
- https://docs.letta.com/
- https://docs.letta.com/reference/terminology
- https://docs.letta.com/agent-sdk/memory/
- https://docs.letta.com/concepts/memfs
- https://docs.letta.com/configuration/memory/
- https://docs.letta.com/configuration/subagents
- https://docs.letta.com/configuration/skills
- https://docs.letta.com/configuration/permissions
- https://docs.letta.com/api/resources/runs/subresources/trace/methods/retrieve
- https://docs.letta.com/v1-sdk
- https://docs.letta.com/agent-sdk/agents
- https://docs.letta.com/pricing
- https://docs.letta.com/reference/changelog/

### 9) BrowserGym / AgentLab

**What it is**
- BrowserGym is a gym-like environment for web task automation and research.
- AgentLab is the experimental harness built around BrowserGym for development, testing, benchmarking, leaderboard work, and reproducibility.

**Runtime loop**
- The runtime is task-environment-centric rather than chat-centric.
- AgentLab runs large batches of experiments, and BrowserGym provides the environment/task layer.

**Context / state / memory**
- Context is mostly environmental state, task metadata, and the experiment record.
- That makes it excellent for controlled benchmarks but not a substitute for a memory-rich assistant runtime.

**Tool governance**
- The environment itself constrains what the agent can observe and do.
- This is more of a benchmark harness than a general policy engine.

**Planning / verification / observability**
- AgentLab’s key strength is reproducibility: studies, leaderboard runs, reproducibility journals, and replay tools.
- AgentXray provides visual trace inspection of experiment runs.

**Subagents**
- There is no native focus on subagents in the way there is in Claude/OpenAI/ADK/Letta.
- The value is in controlled evaluation, not specialist decomposition.

**Cost**
- Cost is mostly in the benchmark infra and the model/provider you connect.
- AgentLab explicitly supports multiple providers and parallel experiments, so cost can scale quickly if you are not careful.

**Transferable mechanism**
- The best reusable idea is **reproducible experiment orchestration**: study objects, traces, replay, and a leaderboard mindset.

**Main risk**
- This is not a consumer or production agent harness.
- It is a research/evaluation substrate, so using it as your whole runtime would overfit the harness to benchmarks.

**Official sources**
- https://github.com/ServiceNow/BrowserGym
- https://github.com/ServiceNow/AgentLab
- https://github.com/ServiceNow/AgentLab/releases
- https://github.com/ServiceNow/BrowserGym/releases
- https://github.com/ServiceNow/BrowserGym/blob/main/browsergym/webarena/README.md
- https://github.com/ServiceNow/BrowserGym/blob/main/browsergym/webarena_verified/README.md

### 10) Aider

**What it is**
- Aider is a terminal-based AI pair-programming tool that works directly in a local git repository.
- It is intentionally simple, git-native, and code-edit-focused.

**Runtime loop**
- Aider runs a chat session over selected files.
- Its chat modes (`code`, `ask`, `architect`, `help`) are the main control surface.

**Context / state / memory**
- Aider builds a repo map using tree-sitter and keeps the conversation scoped to the files you add.
- It has practical context-control commands like `/tokens`, `/drop`, and `/clear`.

**Tool governance**
- Governance is mostly through git and explicit chat commands.
- Aider automatically commits changes with descriptive commit messages and supports undoing AI changes.
- That is a very strong pattern for local safety.

**Planning / verification / observability**
- The tool is designed around diffs, commits, and benchmark-style leaderboards rather than rich internal traces.
- The docs explicitly say Aider never enforces token limits itself; it only reports provider errors.

**Subagents**
- Aider does not have a native subagent hierarchy in the way the other systems do.
- Instead, it relies on mode switching and a human in the loop.

**Cost**
- Cost is basically the model/provider cost plus your own local runtime.
- Aider’s docs also make clear that model choice matters and that pricing/capability tradeoffs are external.

**Transferable mechanism**
- The best thing to steal is **git-native undo/commit safety** combined with a compact repo map.

**Main risk**
- Aider is intentionally minimal.
- That makes it elegant, but it also means you must add your own stronger planning, memory, observability, and policy layers if you are building a broader harness.

**Official sources**
- https://aider.chat/
- https://aider.chat/docs/
- https://aider.chat/docs/usage.html
- https://aider.chat/docs/usage/modes.html
- https://aider.chat/docs/git.html
- https://aider.chat/docs/llms.html
- https://aider.chat/docs/troubleshooting/token-limits.html
- https://aider.chat/docs/leaderboards/
- https://aider.chat/2023/10/22/repomap.html
- https://aider.chat/HISTORY.html
- https://github.com/Aider-AI/aider

## Priority matrix for an independent generic agent harness

This is the order I would implement if I were designing a stand-alone harness that needs to work across repos, tasks, and models.

| Priority | Capability | Why it matters | Best reference patterns |
| --- | --- | --- | --- |
| P0 | **Durable turn loop + session state** | Without a real harness loop, everything else is just prompt engineering. | OpenAI sessions, LangGraph checkpoints, ADK session/state/memory, Letta harness |
| P0 | **Tool governance and sandboxing** | Prevents accidental damage and makes agent actions auditable. | Claude hooks/permissions, OpenAI guardrails + sandboxes, OpenHands sandbox providers, Claude/ADK callbacks |
| P0 | **Structured observability** | If you cannot see the loop, you cannot debug the loop. | OpenAI tracing, LangSmith, ADK trace stacks, Claude OTel monitoring, Letta trace retrieval |
| P1 | **Checkpoint / replay / time travel** | Lets you resume after failure, inspect branches, and build testable workflows. | LangGraph checkpointers/time travel, BrowserGym/AgentLab replay, OpenAI resumable state, SWE trajectories |
| P1 | **Short-term vs long-term memory split** | Keeps the working context small while preserving useful experience. | ADK Session/State/Memory, Claude CLAUDE.md + auto memory, Letta MemFS/dreaming, LangGraph stores |
| P1 | **Verification loop** | Agents need a way to prove progress, not just narrate it. | OpenAI evals/traces, Claude code review, SWE-agent benchmarks, AgentLab studies, Aider git diffs |
| P2 | **Narrow subagents / handoffs** | Improves isolation and keeps the main context clean on large tasks. | OpenAI handoffs, Claude subagents, ADK workflow agents, Letta subagents, AutoGen Magentic-One |
| P2 | **Cost accounting and budgets** | Prevents “successful” runs that are economically unusable. | Claude usage reports/costs, OpenAI pricing + traces, ADK token metadata, OpenHands budget management, Letta pricing |
| P3 | **Task-specific skills / prompts / repo maps** | Helps with specialization, but only after the core runtime is stable. | Claude skills, OpenHands skills, Letta skills, Aider repo map |
| P3 | **Benchmark harness / leaderboard plumbing** | Necessary for research, but not the core runtime itself. | BrowserGym/AgentLab, SWE-agent, Aider leaderboards, OpenHands evaluation harness |

### My recommended build order

1. **Runtime loop + session persistence + sandbox**
2. **Tool governance + explicit approvals / hooks**
3. **Tracing / logs / cost accounting**
4. **Checkpoint replay and testable state transitions**
5. **Long-term memory store and reflection**
6. **Specialist handoffs / subagents**
7. **Benchmarking and eval dashboards**
8. **Optional skills marketplace / plugin system**

## Practical takeaway

If the goal is a durable, independent agent harness, the highest-value borrowed mechanisms are:

- **Claude Code** for deterministic hooks and policy enforcement
- **OpenAI** for tracing, handoffs, and sandboxed orchestration
- **LangGraph** for checkpointed state and time travel
- **ADK** for the clean Session / State / Memory split
- **Letta** for persistent memory and reflection
- **AgentLab / BrowserGym** for reproducibility and evaluation discipline
- **Aider** for git-native safety and compact repo mapping

If I had to choose one core architecture principle from the whole survey, it would be: **make the harness own durable state, tool policy, replay, and observability; let models only choose the next action.**
