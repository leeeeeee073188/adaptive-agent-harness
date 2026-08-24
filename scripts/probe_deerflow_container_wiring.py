#!/usr/bin/env python3
"""Exercise the exact RealReplica adaptive runner inside the pinned image."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from preflight_minibench16 import DEFAULT_IMAGE, _variant_specs

from adaptive_harness.integrations.realreplica import RealReplicaMiniBenchAdapter
from adaptive_harness.model_routes import PRIMARY_MODEL
from adaptive_harness.profiles import candidate_policy_profile

ROOT = Path(__file__).resolve().parents[1]
DEERFLOW_PYTHON = "/opt/deer-flow/backend/.venv/bin/python"
EXPECTED_DEERFLOW_SHA = DEFAULT_IMAGE.rsplit(":", 1)[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("realreplica_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    args = parser.parse_args()

    realreplica_root = args.realreplica_root.resolve()
    if args.image != DEFAULT_IMAGE:
        raise SystemExit("container probe only accepts the pinned MiniBench image")
    sys.path.insert(0, str(realreplica_root))
    from real_replica_bench.harnesses.deerflow.runner import (  # noqa: PLC0415
        _adaptive_deerflow_invocation_script,
        build_deerflow_config,
    )

    dataset = RealReplicaMiniBenchAdapter().load(realreplica_root)
    _, candidate = _variant_specs(dataset.seed)
    container = f"adaptive-runner-{uuid.uuid4().hex[:12]}"
    image_id = _run("docker", "image", "inspect", args.image, "--format", "{{.Id}}")
    source_dir = ROOT / "src/adaptive_harness"
    runner_path = realreplica_root / "real_replica_bench/harnesses/deerflow/runner.py"
    candidate_config = realreplica_root / "configs/realreplicabench_adaptive_minibench16.yaml"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        generated_script = tmp_path / "adaptive-runner.py"
        prompt_path = tmp_path / "prompt.md"
        result_path = tmp_path / "result.json"
        ledger_path = tmp_path / "ledger.jsonl"
        context_config_path = tmp_path / "context-config.yaml"
        generated_script.write_text(_adaptive_deerflow_invocation_script() + "\n")
        prompt_path.write_text("帮我把商品发上线，发品系统打开后提交。\n")
        context_config_path.write_text(
            build_deerflow_config(
                adaptive_context_enabled=True,
                adaptive_action_ledger_enabled=True,
            ).to_yaml(),
            encoding="utf-8",
        )
        try:
            _run(
                "docker",
                "run",
                "-d",
                "--name",
                container,
                "--network",
                "none",
                "--entrypoint",
                "sleep",
                args.image,
                "infinity",
            )
            network_mode = _run(
                "docker", "inspect", container, "--format", "{{.HostConfig.NetworkMode}}"
            )
            _run(
                "docker",
                "exec",
                container,
                "mkdir",
                "-p",
                "/tmp/adaptive-src",
                "/tmp/adaptive-probe/task/outputs",
            )
            _run("docker", "cp", str(source_dir), f"{container}:/tmp/adaptive-src/adaptive_harness")
            _run("docker", "cp", str(generated_script), f"{container}:/tmp/adaptive-runner.py")
            _run("docker", "cp", str(prompt_path), f"{container}:/tmp/prompt.md")
            _run(
                "docker",
                "cp",
                str(context_config_path),
                f"{container}:/tmp/context-config.yaml",
            )
            source_sha = _run(
                "docker",
                "exec",
                container,
                "bash",
                "-lc",
                "cat /opt/deer-flow/.deerflow-source-sha 2>/dev/null || git -C /opt/deer-flow rev-parse HEAD",
            )
            _run(
                "docker",
                "exec",
                "-e",
                "PYTHONPATH=/tmp/adaptive-src",
                "-e",
                "DEER_FLOW_CONFIG_PATH=/tmp/context-config.yaml",
                "-e",
                "DEERFLOW_MODEL_API_KEY=dummy",
                "-e",
                "ADAPTIVE_HARNESS_WIRING_PROBE=1",
                "-w",
                "/opt/deer-flow",
                container,
                DEERFLOW_PYTHON,
                "/tmp/adaptive-runner.py",
                "/tmp/prompt.md",
                "/tmp/adaptive-probe/result.json",
                "/tmp/adaptive-probe/ledger.jsonl",
                "/tmp/context-config.yaml",
                PRIMARY_MODEL,
                "probe-thread",
                "disabled",
                "100",
                "/tmp/adaptive-probe/task",
                "{}",
            )
            _run("docker", "cp", f"{container}:/tmp/adaptive-probe/result.json", str(result_path))
            _run("docker", "cp", f"{container}:/tmp/adaptive-probe/ledger.jsonl", str(ledger_path))
        finally:
            subprocess.run(
                ["docker", "rm", "-f", container],
                check=False,
                capture_output=True,
                text=True,
            )

        payload = json.loads(result_path.read_text())
        adaptive = payload.get("adaptive") or {}
        runner_text = runner_path.read_text()
        config_text = candidate_config.read_text()
        checks = {
            "adaptive_package_imported": True,
            "container_network_disabled": network_mode == "none",
            "deerflow_source_pinned": source_sha == EXPECTED_DEERFLOW_SHA,
            "embedded_client_stream_exercised": adaptive.get("turns") == 2,
            "ledger_persisted": ledger_path.is_file() and ledger_path.stat().st_size > 0,
            "policy_bridge_enabled": adaptive.get("completed") is True,
            "executable_policy_profile_assembled": adaptive.get(
                "policy_profile_plugins"
            )
            == [
                "context",
                "response_completion",
                "task_contract_builder",
                "evidence_completion",
                "semantic_progress",
                "durable_recovery",
                "resource_guardrail",
            ],
            "policy_profile_fingerprint_matches": adaptive.get(
                "policy_profile_fingerprint"
            )
            == candidate_policy_profile(
                context_config={
                    "max_input_tokens": 4096,
                    "recent_history_fraction": 0.20,
                }
            ).fingerprint(),
            "context_middleware_imported": adaptive.get("context_middleware_imported") is True,
            "action_ledger_middleware_imported": (
                adaptive.get("action_ledger_middleware_imported") is True
            ),
            "action_ledger_request_shape_valid": (
                adaptive.get("action_ledger_request_shape_valid") is True
            ),
            "action_scope_block_enforced": adaptive.get(
                "action_scope_block_enforced"
            )
            is True,
            "global_nonmutating_budget_enforced": adaptive.get(
                "global_nonmutating_budget_enforced"
            )
            is True,
            "context_middleware_request_shape_valid": (
                adaptive.get("context_middleware_request_shape_valid") is True
            ),
            "context_profile_session_bound": adaptive.get(
                "context_profile_session_bound"
            )
            is True,
            "pinned_candidate_config_loaded": True,
            "realreplica_candidate_config_enabled": "adaptive_policy_enabled: true" in config_text,
            "realreplica_context_profile_wired": "adaptive_context_enabled=adaptive_source is not None" in runner_text,
            "realreplica_action_ledger_wired": (
                "adaptive_action_ledger_enabled=adaptive_source is not None" in runner_text
            ),
            "realreplica_runner_copies_source": "/tmp/adaptive-src/adaptive_harness" in runner_text,
            "realreplica_runner_persists_ledger": "adaptive-ledger.jsonl" in runner_text,
            "runner_probe_used_zero_models": adaptive.get("model_calls") == 0,
        }
        runner_wired = all(
            checks[key]
            for key in (
                "realreplica_candidate_config_enabled",
                "realreplica_context_profile_wired",
                "realreplica_action_ledger_wired",
                "realreplica_runner_copies_source",
                "realreplica_runner_persists_ledger",
                "runner_probe_used_zero_models",
            )
        )
        report = {
            "passed": all(checks.values()),
            "scope": "RealReplica adaptive candidate runner wiring probe",
            "runtime_image": args.image,
            "runtime_image_id": image_id,
            "deerflow_source_sha": source_sha,
            "dataset_fingerprint": dataset.fingerprint,
            "candidate_profile_fingerprint": candidate.profile_fingerprint,
            "adaptive_source_sha256": _sha256_tree(source_dir),
            "runner_source_sha256": hashlib.sha256(runner_path.read_bytes()).hexdigest(),
            "probe_script_sha256": hashlib.sha256(generated_script.read_bytes()).hexdigest(),
            "model_calls": 0,
            "new_model_tokens": 0,
            "realreplica_candidate_runner_wired": runner_wired,
            "checks": checks,
            "ledger_event_count": adaptive.get("ledger_event_count"),
            "ledger_sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
            "usage": adaptive.get("usage") or {},
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["passed"] else 1


def _run(*command: str) -> str:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed ({result.returncode}): {' '.join(command)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return (result.stdout or "").strip()


def _sha256_tree(root: Path) -> str:
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
