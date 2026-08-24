#!/usr/bin/env python3
"""Zero-model MiniBench16 dataset, contract, history, and pairing preflight."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from adaptive_harness.integrations.realreplica import (
    RealReplicaMiniBenchAdapter,
    VariantSpec,
    stable_profile_fingerprint,
)
from adaptive_harness.model_routes import PRIMARY_MODEL
from adaptive_harness.profiles import candidate_policy_profile

DEFAULT_IMAGE = "realreplicabench/deerflow:0debff98c1caf4a7d3047e8ef162d85a841b5c6d"
CANDIDATE_THINKING_EFFORT = "high"
ROOT = Path(__file__).resolve().parents[1]


def _variant_specs(seed: int) -> tuple[VariantSpec, VariantSpec]:
    baseline = VariantSpec(
        "vanilla_deerflow",
        stable_profile_fingerprint({"runtime": "deerflow", "policies": []}),
        PRIMARY_MODEL,
        DEFAULT_IMAGE,
        seed,
    )
    policy_profile = candidate_policy_profile(
        context_config={
            "max_input_tokens": 4096,
            "recent_history_fraction": 0.20,
        }
    )
    candidate = VariantSpec(
        "adaptive_harness_source_grounding_v3_8",
        stable_profile_fingerprint(
            {
                "runtime": "deerflow",
                "executable_policy_profile": policy_profile.fingerprint(),
                "context": {
                    "policy": "task-aware-v1.6",
                    "visible_evidence_workspace": True,
                    "durable_task_state_handoff": True,
                },
                "contract": {
                    "public_json_shape": True,
                    "public_json_completeness_non_vacuity": True,
                    "public_source_access": True,
                    "private_evaluator_data": False,
                    "public_provisional_copy_guard": True,
                },
                "source_grounding": {
                    "claim_lineage_core": True,
                    "model_prose_forbidden_as_authority": True,
                    "workspace_scan_max_files": 512,
                    "workspace_scan_max_file_bytes": 2097152,
                    "grounding_diagnostics_model_visible": True,
                    "blocked_grounding_preserves_artifact_recovery": True,
                    "delivery_blocks_non_output_writes": True,
                },
                "phase": {"policy": "evidence-driven-soft-phase-v1"},
                "model_reasoning": {
                    "thinking": "enabled",
                    "reasoning_effort": CANDIDATE_THINKING_EFFORT,
                    "wire_format": "openai-chat-completions",
                },
                "completion": {
                    "response_and_evidence_are_conjunctive": True,
                    "evidence_assessments_preserved_on_response_rejection": True,
                },
                "max_completion_turns": 3,
                "public_source_materializer": {
                    "loopback_get_only": True,
                    "max_bytes": 65536,
                },
                "local_cache_block_end_threshold": 2,
                "tool_reliability": {"max_attempts": 2},
                "verification_budget": {
                    "policy": "tool-action-ledger-v1",
                    "mode": "advise",
                    "max_same_scope": 2,
                    "max_same_scope_reads": 3,
                    "max_total_since_mutation": 4,
                    "max_nonmutating_actions_per_turn": 20,
                    "max_reads_per_local_resource": 3,
                    "max_total_tool_calls_per_turn": 20,
                    "delivery_first_recovery": True,
                    "direct_synthesis_transform_allowed": True,
                    "empty_placeholder_discouraged": True,
                    "task_run_ledger_state": True,
                    "turn_scoped_admission_budget": True,
                    "textual_tool_errors_fail_closed": True,
                },
                "response_completion": {
                    "runtime_control_messages_rejected": True,
                },
                "advice_gate": {
                    "classified_fraction": 1.0,
                    "success_controls": 7,
                    "success_controls_warned": 0,
                    "failure_controls": 2,
                    "failure_controls_signaled": 2,
                },
            }
        ),
        baseline.model,
        baseline.runtime_image,
        baseline.seed,
    )
    return baseline, candidate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("realreplica_root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--policy-bridge-evidence", type=Path)
    args = parser.parse_args()

    adapter = RealReplicaMiniBenchAdapter()
    dataset = adapter.load(args.realreplica_root)
    coverage = adapter.contract_coverage(dataset)
    provider_coverage = adapter.provider_coverage(dataset)
    baseline, candidate = _variant_specs(dataset.seed)
    manifest = adapter.paired_manifest(dataset, baseline, candidate)
    history = adapter.historical_baselines(args.realreplica_root, dataset, baseline)
    history_rows = list(history.values())
    criterion_kinds = Counter(kind for row in coverage.rows for kind in row.criterion_kinds)
    history_config_hashes = sorted(
        {row.run_config_sha256 for row in history_rows if row.run_config_sha256}
    )
    bridge_evidence_valid = _bridge_evidence_valid(
        args.policy_bridge_evidence,
        dataset_fingerprint=dataset.fingerprint,
        candidate_profile_fingerprint=candidate.profile_fingerprint,
    )
    gates = {
        "frozen_dataset_valid": len(dataset.tasks) == 16,
        "historical_baseline_all_tasks": len(history) == len(dataset.tasks),
        "paired_controls_valid": len(manifest.cells) == len(dataset.tasks) * 2,
        "contract_coverage_complete": coverage.covered_count == coverage.total_count,
        "live_policy_bridge_ready": bridge_evidence_valid,
        "block_1_provider_coverage": 1 in provider_coverage.ready_blocks,
    }
    failed_paid_gates = [name for name, passed in gates.items() if not passed]
    report = {
        "scope": "A4 MiniBench16 zero-model preflight",
        "model_calls": 0,
        "new_model_tokens": 0,
        "dataset": {
            "id": dataset.dataset_id,
            "fingerprint": dataset.fingerprint,
            "seed": dataset.seed,
            "counts": dataset.counts(),
            "blocks": {
                str(block): {
                    "purpose": purpose,
                    "task_count": sum(task.block == block for task in dataset.tasks),
                }
                for block, purpose in sorted(dataset.block_purposes.items())
            },
        },
        "contract_coverage": {
            "covered_tasks": coverage.covered_count,
            "total_tasks": coverage.total_count,
            "coverage_rate": coverage.covered_count / coverage.total_count,
            "criterion_count": sum(row.criterion_count for row in coverage.rows),
            "criterion_kinds": dict(sorted(criterion_kinds.items())),
            "uncovered_task_ids": list(coverage.uncovered_task_ids),
            "rows": [
                {
                    "task_id": row.task_id,
                    "block": row.block,
                    "criterion_count": row.criterion_count,
                    "criterion_kinds": list(row.criterion_kinds),
                }
                for row in coverage.rows
            ],
        },
        "provider_coverage": {
            "enforced_tasks": provider_coverage.enforced_task_count,
            "total_tasks": len(provider_coverage.rows),
            "ready_blocks": list(provider_coverage.ready_blocks),
            "full_task_coverage": provider_coverage.enforced_task_count
            == len(provider_coverage.rows),
            "all_criteria_enforced": all(
                row.observe_only_criterion_count == 0
                for row in provider_coverage.rows
            ),
            "rows": [
                {
                    "task_id": row.task_id,
                    "block": row.block,
                    "enforced_criterion_count": row.enforced_criterion_count,
                    "observe_only_criterion_count": row.observe_only_criterion_count,
                }
                for row in provider_coverage.rows
            ],
        },
        "historical_baseline": {
            "covered_tasks": len(history),
            "passed_tasks": sum(row.passed for row in history_rows),
            "failed_tasks": sum(not row.passed for row in history_rows),
            "known_total_tokens": sum(row.total_tokens or 0 for row in history_rows),
            "missing_token_usage_tasks": sum(row.total_tokens is None for row in history_rows),
            "run_config_fingerprint_count": len(history_config_hashes),
            "paired_reuse_allowed": False,
            "reason": "Historical runs cover preflight only; fresh baseline/candidate pairs are required.",
        },
        "paired_manifest": manifest.to_payload(),
        "gates": gates,
        "offline_preflight_passed": all(
            gates[key]
            for key in (
                "frozen_dataset_valid",
                "historical_baseline_all_tasks",
                "paired_controls_valid",
            )
        ),
        "paid_run_ready": all(gates.values()),
        "next_action": (
            f"Eligible paired blocks: {', '.join(map(str, provider_coverage.ready_blocks))}."
            if all(gates.values())
            else f"Close paid gates before spending tokens: {', '.join(failed_paid_gates)}."
        ),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["offline_preflight_passed"] else 1


def _bridge_evidence_valid(
    path: Path | None,
    *,
    dataset_fingerprint: str,
    candidate_profile_fingerprint: str,
) -> bool:
    if path is None or not path.is_file():
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    required_checks = {
        "adaptive_package_imported",
        "action_scope_block_enforced",
        "context_profile_session_bound",
        "completion_conjunction_preserves_assessments",
        "blocked_grounding_preserves_artifact_recovery",
        "global_nonmutating_budget_enforced",
        "delivery_first_gate_enforced",
        "delivery_synthesis_transform_allowed",
        "delivery_non_output_write_blocked",
        "embedded_client_stream_exercised",
        "executable_policy_profile_assembled",
        "max_completion_turns_frozen",
        "object_context_epoch_monotonic",
        "local_resource_read_budget_frozen",
        "public_source_materializer_wired",
        "realreplica_batch_thinking_effort_wired",
        "public_non_vacuity_gate_enforced",
        "provisional_copy_guard_enforced",
        "runtime_limit_response_rejected",
        "textual_tool_errors_fail_closed",
        "ledger_persisted",
        "policy_bridge_enabled",
        "policy_profile_fingerprint_matches",
        "tool_call_limit_middleware_imported",
        "tool_call_limit_request_shape_valid",
        "thinking_request_configured",
        "thinking_effort_matches",
    }
    return bool(
        isinstance(value, dict)
        and value.get("passed") is True
        and value.get("runtime_image") == DEFAULT_IMAGE
        and value.get("dataset_fingerprint") == dataset_fingerprint
        and value.get("candidate_profile_fingerprint") == candidate_profile_fingerprint
        and value.get("adaptive_source_sha256") == _adaptive_source_sha256()
        and value.get("model_calls") == 0
        and value.get("realreplica_candidate_runner_wired") is True
        and required_checks
        <= {key for key, passed in (value.get("checks") or {}).items() if passed is True}
    )


def _adaptive_source_sha256() -> str:
    root = ROOT / "src/adaptive_harness"
    digest = hashlib.sha256()
    for path in sorted(
        candidate
        for candidate in root.rglob("*.py")
        if candidate.is_file() and "__pycache__" not in candidate.parts
    ):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
