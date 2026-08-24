#!/usr/bin/env python3
"""Zero-model gate for next-generation adaptive harness changes.

The gate intentionally uses only public task inputs, public historical agent
traces, and public artifacts. It fails closed when required inputs are missing
or point at private evaluator/reward material.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_ROOT = _REPO_ROOT / "src"
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from adaptive_harness.action_ledger import ToolIntent, classify_tool_action  # noqa: E402
from adaptive_harness.capabilities import AcceptFinalCompletion, ModelResponse  # noqa: E402
from adaptive_harness.context import ContextBudget, TaskAwareContextManager  # noqa: E402
from adaptive_harness.integrations.deerflow import DeerFlowReplaySummary  # noqa: E402
from adaptive_harness.integrations.deerflow_policy import FileArtifactObservationProvider  # noqa: E402
from adaptive_harness.profiles import candidate_policy_profile  # noqa: E402
from adaptive_harness.task_contract import CriterionKind, RuleBasedTaskContractBuilder, TaskContract  # noqa: E402

CORE_TEST_MODULES: tuple[str, ...] = (
    "tests.test_task_state",
    "tests.test_source_grounding",
    "tests.test_action_ledger",
    "tests.test_evidence_workspace",
    "tests.test_context",
    "tests.test_phase",
    "tests.test_policy_session",
    "tests.test_deerflow_policy",
    "tests.test_deerflow_context_middleware",
    "tests.test_deerflow_action_ledger_middleware",
    "tests.test_recovery",
)
CROSS_TURN_TEST_MODULES: tuple[str, ...] = (
    "tests.test_deerflow_context_middleware",
    "tests.test_policy_session",
)
FORBIDDEN_PATH_PARTS = {"private", "verifier", "reward"}
SECRET_RE = re.compile(
    r"(?i)((?<![a-z0-9])sk-[a-z0-9_-]{12,}|api[_-]?key\s*[:=]\s*['\"]?[a-z0-9_-]{12,}|"
    r"access[_-]?token\s*[:=]|password\s*[:=]|client[_-]?secret\s*[:=])"
)
TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9_/-]{2,}|[\u4e00-\u9fff]{2,}")
BENCHMARK_LEAK_RE = re.compile(
    r"(?i)(cli-google-trends-data-quality-audit|final_reward\.json|reward\.json|"
    r"(?:^|[/\\])verifier(?:[/\\]|$)|RealReplicaBench/runs/)"
)
GUARDRAIL_LITERAL_RE = re.compile(
    r"(?i)(private_verifier|expected_answer|expected_output|ground_truth|answer_key|gold_answer|rubric|verifier)"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--real-repo", type=Path, required=True)
    parser.add_argument("--task-case", type=Path, required=True)
    parser.add_argument("--historical-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_gate(
        real_repo=args.real_repo,
        task_case=args.task_case,
        historical_run=args.historical_run,
        output=args.output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


def run_gate(
    *,
    real_repo: Path,
    task_case: Path,
    historical_run: Path,
    output: Path,
    core_root: Path | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    core_root = core_root or Path(__file__).resolve().parents[1] / "src" / "adaptive_harness"
    report: dict[str, Any] = {
        "scope": "next-generation zero-model adaptive harness gate",
        "model_calls": 0,
        "new_tokens": 0,
        "new_model_tokens": 0,
        "paid_expansion_allowed": False,
        "fail_closed": False,
        "adaptive_source_sha256": _source_sha256(core_root),
        "executable_policy_profile_fingerprint": candidate_policy_profile(
            context_config={"max_input_tokens": 4096, "recent_history_fraction": 0.20}
        ).fingerprint(),
    }
    for label, path in (
        ("real_repo", real_repo),
        ("task_case", task_case),
        ("historical_run", historical_run),
        ("core_root", core_root),
    ):
        if _has_forbidden_path_part(path):
            blockers.append(f"{label}: forbidden path component in {path}")
        if not path.exists():
            blockers.append(f"{label}: missing path {path}")
    if real_repo.exists() and task_case.exists() and not task_case.resolve().is_relative_to(
        real_repo.resolve()
    ):
        blockers.append("task_case must resolve inside real_repo")
    if real_repo.exists() and historical_run.exists() and not historical_run.resolve().is_relative_to(
        real_repo.resolve()
    ):
        blockers.append("historical_run must resolve inside real_repo")

    task_prompt = ""
    contract: TaskContract | None = None
    if not blockers:
        try:
            task_prompt = _read_public_text(_task_markdown_path(task_case))
            contract = RuleBasedTaskContractBuilder().build(_safe_task_id(task_case), task_prompt)
        except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError) as error:
            blockers.append(f"contract: {error}")

    runtime = _run_test_modules(CORE_TEST_MODULES)
    cross_turn = _run_test_modules(CROSS_TURN_TEST_MODULES)
    report["runtime_conformance"] = {**runtime, "test_modules": list(CORE_TEST_MODULES)}
    report["cross_turn_tests"] = {**cross_turn, "test_modules": list(CROSS_TURN_TEST_MODULES)}
    if not runtime["passed"]:
        blockers.append("runtime conformance tests failed")
    if not cross_turn["passed"]:
        blockers.append("cross-turn tests failed")

    if contract is not None:
        contract_report = _contract_report(contract)
        report["contract"] = contract_report
        if not contract_report["artifact_shape_criterion_present"]:
            blockers.append("contract missing public artifact shape criterion")
        if not contract_report["source_access_criteria_present"]:
            blockers.append("contract missing public source-access criteria")
        if not contract_report["artifact_non_vacuity_constraint_present"]:
            blockers.append("contract missing public artifact non-vacuity constraint")
        if not contract_report["artifact_grounding_criterion_present"]:
            blockers.append("contract missing public provisional-copy grounding criterion")
    else:
        report["contract"] = {
            "artifact_shape_criterion_present": False,
            "source_access_criteria_present": False,
            "artifact_non_vacuity_constraint_present": False,
            "artifact_grounding_criterion_present": False,
            "criterion_count": 0,
        }

    runtime_semantics = _runtime_semantics_report()
    report["runtime_semantics"] = runtime_semantics
    if not runtime_semantics["runtime_limit_response_rejected"]:
        blockers.append("runtime-limit response can still pass response completion")
    if not runtime_semantics["direct_public_script_is_transform"]:
        blockers.append("direct public synthesis script is not admitted as a transform")

    if contract is not None and not blockers:
        context_report = _context_replay_report(historical_run, contract=contract, task_prompt=task_prompt)
        report["context_replay"] = context_report
        if not context_report["passed"]:
            blockers.extend(f"context replay: {reason}" for reason in context_report["blockers"])
        artifact_report = _historical_artifact_report(historical_run, contract)
        report["historical_artifact"] = artifact_report
        if not artifact_report["rejected"]:
            blockers.append("historical artifact was not rejected by public shape diagnostics")
        if not artifact_report["grounding_rejected"]:
            blockers.append("historical provisional copy was not rejected by grounding diagnostics")
    else:
        report.setdefault(
            "context_replay",
            {
                "passed": False,
                "blockers": ["prerequisite gate failed"],
                "snapshot_count": 0,
                "history_dropped": False,
                "visible_workspace_selected": False,
                "task_excerpt_overlap_count": 0,
                "secret_findings": 0,
            },
        )
        report.setdefault(
            "historical_artifact",
            {
                "rejected": False,
                "grounding_rejected": False,
                "diagnostic_count": 0,
                "diagnostic_types": [],
            },
        )

    leakage = _core_leakage_report(core_root)
    report["core_leakage"] = leakage
    if not leakage["passed"]:
        blockers.append("core leakage scan found benchmark/evaluator answer leakage")
    repository_leakage = _repository_public_artifact_scan(_REPO_ROOT)
    report["repository_public_artifact_scan"] = repository_leakage
    if not repository_leakage["passed"]:
        blockers.append("public docs/evidence scan found a secret or evaluator-private path")

    report["blockers"] = blockers
    report["passed"] = not blockers
    report["candidate_single_development_canary_allowed"] = report["passed"]
    report["fail_closed"] = bool(blockers)
    _write_report(output, report)
    return report


def _run_test_modules(modules: Sequence[str]) -> dict[str, Any]:
    command = [sys.executable, "-m", "unittest", *modules]
    env = dict(os.environ)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(_SRC_ROOT) if not existing else f"{_SRC_ROOT}{os.pathsep}{existing}"
    run = subprocess.run(command, capture_output=True, text=True, check=False, cwd=_REPO_ROOT, env=env)
    count = _extract_test_count(run.stderr) or _extract_test_count(run.stdout) or 0
    return {
        "passed": run.returncode == 0 and count > 0,
        "returncode": run.returncode,
        "test_count": count,
        "command": command,
        "stdout_tail": run.stdout[-2000:],
        "stderr_tail": run.stderr[-2000:],
    }


def _extract_test_count(output: str) -> int | None:
    match = re.search(r"Ran (\d+) tests?", output)
    return int(match.group(1)) if match else None


def _context_replay_report(historical_run: Path, *, contract: TaskContract, task_prompt: str) -> dict[str, Any]:
    blockers: list[str] = []
    snapshots = _load_message_snapshots(historical_run / "agent" / "deerflow-events.json")
    manager = TaskAwareContextManager(budget=ContextBudget(max_input_tokens=4096))
    task_terms = _terms(task_prompt)
    history_dropped = False
    visible_workspace_selected = False
    overlap_terms: set[str] = set()
    secret_findings = 0
    estimated_tokens: list[int] = []
    for messages in snapshots:
        prepared = manager.prepare(
            messages,
            environment_state={"public_workspace_root": "/task/workspace", "public_outputs_root": "/task/outputs"},
            task_state={"task": contract.to_payload()},
        )
        audit = prepared.audit
        estimated_tokens.append(int(audit.get("estimated_input_tokens") or 0))
        history_dropped = history_dropped or int(audit.get("history_dropped") or 0) > 0
        visible = audit.get("visible_evidence_workspace") or {}
        visible_workspace_selected = visible_workspace_selected or bool(visible.get("block_selected"))
        rendered = json.dumps(list(prepared.messages), ensure_ascii=False, sort_keys=True)
        secret_findings += len(SECRET_RE.findall(rendered))
        for excerpt in _visible_excerpts(prepared.messages):
            overlap_terms.update(_terms(excerpt) & task_terms)
    if not snapshots:
        blockers.append("no public values-message snapshots found")
    if not history_dropped:
        blockers.append("4096-token replay did not drop history")
    if not visible_workspace_selected:
        blockers.append("visible workspace was not selected")
    if not overlap_terms:
        blockers.append("visible excerpt/task term overlap is empty")
    if secret_findings:
        blockers.append("secret findings were detected")
    return {
        "passed": not blockers,
        "blockers": blockers,
        "snapshot_count": len(snapshots),
        "budget_tokens": 4096,
        "max_estimated_tokens": max(estimated_tokens, default=0),
        "history_dropped": history_dropped,
        "visible_workspace_selected": visible_workspace_selected,
        "task_excerpt_overlap_count": len(overlap_terms),
        "secret_findings": secret_findings,
    }


def _load_message_snapshots(path: Path) -> tuple[tuple[Mapping[str, Any], ...], ...]:
    data = _read_public_json(path)
    raw_events = data.get("events", data) if isinstance(data, Mapping) else data
    if not isinstance(raw_events, Sequence) or isinstance(raw_events, (str, bytes)):
        raise ValueError("deerflow-events.json must contain an event list")
    snapshots: list[tuple[Mapping[str, Any], ...]] = []
    seen: set[str] = set()
    for event in raw_events:
        if not isinstance(event, Mapping) or event.get("type") != "values":
            continue
        payload = event.get("data")
        raw_messages = payload.get("messages") if isinstance(payload, Mapping) else None
        if not isinstance(raw_messages, Sequence) or isinstance(raw_messages, (str, bytes)):
            continue
        messages = tuple(_normalize_message(item) for item in raw_messages if isinstance(item, Mapping))
        if not messages or not _tool_protocol_complete(messages):
            continue
        fingerprint = json.dumps(messages, ensure_ascii=False, sort_keys=True)
        if fingerprint not in seen:
            seen.add(fingerprint)
            snapshots.append(messages)
    return tuple(snapshots)


def _normalize_message(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    role = {
        "human": "user",
        "ai": "assistant",
        "tool": "tool",
        "system": "system",
    }.get(str(raw.get("type") or raw.get("role") or ""), str(raw.get("role") or ""))
    message: dict[str, Any] = {"role": role, "content": raw.get("content", "")}
    calls = raw.get("tool_calls")
    if role == "assistant" and isinstance(calls, Sequence) and not isinstance(calls, (str, bytes)):
        message["tool_calls"] = [
            {
                "id": str(item.get("id")),
                "name": str(item.get("name") or item.get("function", {}).get("name") or "tool"),
                "arguments": dict(item.get("arguments") or item.get("args") or {}),
            }
            for item in calls
            if isinstance(item, Mapping) and item.get("id") is not None
        ]
    if role == "tool" and raw.get("tool_call_id") is not None:
        message["tool_call_id"] = str(raw["tool_call_id"])
    return message


def _tool_protocol_complete(messages: Sequence[Mapping[str, Any]]) -> bool:
    pending: set[str] = set()
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            if pending:
                return False
            calls = message.get("tool_calls") or ()
            pending = {
                str(item.get("id"))
                for item in calls
                if isinstance(item, Mapping) and item.get("id") is not None
            }
        elif role == "tool":
            call_id = str(message.get("tool_call_id") or "")
            if call_id not in pending:
                return False
            pending.remove(call_id)
        elif pending:
            return False
    return not pending


def _visible_excerpts(messages: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    excerpts: list[str] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str) or "visible_evidence_excerpt" not in content:
            continue
        json_text = content.split("\n", 1)[1] if content.startswith("HARNESS_WORKING_SET_DATA\n") else content
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError:
            continue
        for item in payload.get("items") or ():
            data = item.get("data") if isinstance(item, Mapping) else None
            excerpt = data.get("visible_evidence_excerpt") if isinstance(data, Mapping) else None
            if isinstance(excerpt, Mapping):
                text = excerpt.get("text") or excerpt.get("excerpt") or excerpt.get("preview")
                if isinstance(text, str):
                    excerpts.append(text)
    return tuple(excerpts)


def _contract_report(contract: TaskContract) -> dict[str, Any]:
    shape = [
        item for item in contract.criteria
        if item.kind is CriterionKind.OBSERVATION_EQUALS
        and str(item.parameters.get("subject") or "").startswith("artifact.json_shape:")
    ]
    sources = [
        item for item in contract.criteria
        if item.kind is CriterionKind.OBSERVATION_EQUALS
        and str(item.parameters.get("subject") or "").startswith("source.access")
    ]
    artifacts = [item for item in contract.criteria if item.kind is CriterionKind.ARTIFACT_EXISTS]
    grounding = [
        item
        for item in contract.criteria
        if item.kind is CriterionKind.OBSERVATION_EQUALS
        and str(item.parameters.get("subject") or "").startswith("artifact.grounding:")
    ]
    return {
        "criterion_count": len(contract.criteria),
        "artifact_criterion_count": len(artifacts),
        "artifact_shape_criterion_present": bool(shape),
        "source_access_criteria_present": bool(sources),
        "source_access_criterion_count": len(sources),
        "artifact_non_vacuity_constraint_present": any(
            bool(item.parameters.get("non_vacuous_collection_paths")) for item in shape
        ),
        "non_vacuous_collection_path_count": sum(
            len(item.parameters.get("non_vacuous_collection_paths") or ()) for item in shape
        ),
        "artifact_grounding_criterion_present": bool(grounding),
        "provisional_source_path_count": sum(
            len(item.parameters.get("provisional_source_paths") or ()) for item in grounding
        ),
    }


def _runtime_semantics_report() -> dict[str, bool]:
    runtime_limit = AcceptFinalCompletion().check(
        "Complete the public task.",
        ModelResponse(content="Tool call limit reached: run limit exceeded (21/20 calls)."),
        {},
    )
    transform = classify_tool_action(
        "bash",
        {"command": "cd /task && python3 workspace/analysis/audit.py"},
    )
    return {
        "runtime_limit_response_rejected": not runtime_limit.passed,
        "direct_public_script_is_transform": transform.intent is ToolIntent.TRANSFORM,
    }


def _historical_artifact_report(historical_run: Path, contract: TaskContract) -> dict[str, Any]:
    provider = FileArtifactObservationProvider(historical_run / "workspace")
    summary = DeerFlowReplaySummary("", (), (), {}, 0, 0)
    diagnostic_types: Counter[str] = Counter()
    diagnostic_count = 0
    shape_checked = False
    rejected = False
    grounding_checked = False
    grounding_rejected = False
    for evidence in provider.observe(contract, summary, turn=0):
        subject = str(evidence.subject)
        if not subject.startswith(("artifact.json_shape:", "artifact.grounding:")):
            continue
        shape_checked = shape_checked or subject.startswith("artifact.json_shape:")
        grounding_checked = grounding_checked or subject.startswith("artifact.grounding:")
        diagnostics = evidence.metadata.get("diagnostics") or []
        diagnostic_count += len(diagnostics)
        diagnostic_types.update(_diagnostic_type(str(item)) for item in diagnostics)
        if subject.startswith("artifact.json_shape:"):
            rejected = rejected or not bool(evidence.value)
        else:
            grounding_rejected = grounding_rejected or not bool(evidence.value)
    return {
        "shape_checked": shape_checked,
        "rejected": rejected,
        "grounding_checked": grounding_checked,
        "grounding_rejected": grounding_rejected,
        "diagnostic_count": diagnostic_count,
        "diagnostic_types": sorted(diagnostic_types),
    }


def _diagnostic_type(diagnostic: str) -> str:
    if diagnostic.startswith("missing required key"):
        return "missing_required_key"
    if diagnostic.startswith("wrong type"):
        return "wrong_type"
    if diagnostic.startswith("duplicate identity"):
        return "duplicate_identity"
    if diagnostic.startswith("invalid json"):
        return "invalid_json"
    if diagnostic.startswith("artifact missing"):
        return "artifact_missing"
    if diagnostic.startswith("all completeness-scoped collections are empty"):
        return "all_collections_empty"
    if diagnostic.startswith("artifact exactly copies provisional source"):
        return "exact_provisional_copy"
    return "other"


def _core_leakage_report(core_root: Path) -> dict[str, Any]:
    if not core_root.exists():
        return {"passed": False, "finding_count": 1, "findings": [{"path": str(core_root), "type": "missing_core"}]}
    findings: list[dict[str, str]] = []
    guardrail_literals = 0
    for path in sorted(core_root.rglob("*.py") if core_root.is_dir() else [core_root]):
        if "__pycache__" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        guardrail_literals += len(GUARDRAIL_LITERAL_RE.findall(text))
        for match in BENCHMARK_LEAK_RE.finditer(text):
            findings.append({"path": str(path), "type": match.group(0)[:80]})
    return {
        "passed": not findings,
        "finding_count": len(findings),
        "findings": findings[:20],
        "guardrail_literal_count": guardrail_literals,
        "guardrail_literal_interpretation": (
            "generic evaluator-leakage terms are allowed only as defensive denylist/validation vocabulary; "
            "direct benchmark task ids, reward files, and private verifier paths fail this gate"
        ),
    }


def _repository_public_artifact_scan(repo_root: Path) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    public_task_reference_count = 0
    private_path = re.compile(
        r"(?i)(?:^|[/\\])private(?:[/\\])|verifier[/\\](?:reward|final_reward)\.json|"
        r"expected_answer\.json"
    )
    roots = [repo_root / "docs", repo_root / "evidence"]
    candidates = [repo_root / "README.md"]
    for root in roots:
        if root.is_dir():
            candidates.extend(path for path in root.rglob("*") if path.is_file())
    for path in sorted(set(candidates)):
        if path.suffix.lower() not in {".md", ".json", ".jsonl", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if SECRET_RE.search(text):
            findings.append({"path": str(path.relative_to(repo_root)), "type": "secret_pattern"})
        if private_path.search(text):
            findings.append(
                {"path": str(path.relative_to(repo_root)), "type": "evaluator_private_path"}
            )
        public_task_reference_count += len(
            re.findall(r"\b(?:cli|browser|file|api)-[a-z0-9-]+\b", text, re.IGNORECASE)
        )
    return {
        "passed": not findings,
        "finding_count": len(findings),
        "findings": findings[:20],
        "public_task_reference_count": public_task_reference_count,
        "policy": (
            "Public task/run identifiers are allowed in analysis documentation and aggregate evidence; "
            "credentials and evaluator-private file paths are forbidden."
        ),
    }


def _task_markdown_path(task_case: Path) -> Path:
    candidates = (task_case / "task.md", task_case / "workspace" / "task.md")
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError(f"missing public task.md under {task_case}")


def _safe_task_id(task_case: Path) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", task_case.name).strip("-") or "public-task"


def _read_public_text(path: Path) -> str:
    _reject_forbidden_path(path)
    return path.read_text(encoding="utf-8")


def _read_public_json(path: Path) -> Any:
    _reject_forbidden_path(path)
    return json.loads(path.read_text(encoding="utf-8"))


def _reject_forbidden_path(path: Path) -> None:
    if _has_forbidden_path_part(path):
        raise ValueError(f"forbidden path component in {path}")


def _has_forbidden_path_part(path: Path) -> bool:
    return any(part.lower() in FORBIDDEN_PATH_PARTS for part in path.parts)


def _terms(text: str) -> set[str]:
    stop = {
        "the", "and", "for", "with", "that", "this", "into", "from", "http", "https", "public", "write", "outputs",
        "json", "true", "short", "text", "使用", "输出", "任务",
    }
    return {term.lower() for term in TERM_RE.findall(text) if term.lower() not in stop}


def _write_report(output: Path, report: Mapping[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _source_sha256(root: Path) -> str | None:
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
