#!/usr/bin/env python3
"""Generate zero-model bridge evidence inside the pinned DeerFlow container."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from preflight_minibench16 import DEFAULT_IMAGE, _variant_specs

from adaptive_harness.integrations.realreplica import RealReplicaMiniBenchAdapter

ROOT = Path(__file__).resolve().parents[1]
DEERFLOW_PYTHON = "/opt/deer-flow/backend/.venv/bin/python"
EXPECTED_DEERFLOW_SHA = DEFAULT_IMAGE.rsplit(":", 1)[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("realreplica_root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    args = parser.parse_args()

    if args.image != DEFAULT_IMAGE:
        raise SystemExit("container probe only accepts the pinned MiniBench image")
    dataset = RealReplicaMiniBenchAdapter().load(args.realreplica_root)
    _, candidate = _variant_specs(dataset.seed)
    container = f"adaptive-wiring-{uuid.uuid4().hex[:12]}"
    image_id = _run("docker", "image", "inspect", args.image, "--format", "{{.Id}}")
    inner_script = ROOT / "scripts/container_wiring_probe.py"
    source_dir = ROOT / "src/adaptive_harness"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        inner_output = tmp_path / "inner.json"
        inner_ledger = tmp_path / "ledger.jsonl"
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
            _run("docker", "exec", container, "mkdir", "-p", "/tmp/adaptive-src", "/tmp/adaptive-probe")
            _run("docker", "cp", str(source_dir), f"{container}:/tmp/adaptive-src/adaptive_harness")
            _run("docker", "cp", str(inner_script), f"{container}:/tmp/container_wiring_probe.py")
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
                "DEER_FLOW_CONFIG_PATH=/opt/deer-flow/config.example.yaml",
                "-w",
                "/opt/deer-flow",
                container,
                DEERFLOW_PYTHON,
                "/tmp/container_wiring_probe.py",
                "--output",
                "/tmp/adaptive-probe/result.json",
                "--ledger",
                "/tmp/adaptive-probe/ledger.jsonl",
            )
            _run("docker", "cp", f"{container}:/tmp/adaptive-probe/result.json", str(inner_output))
            _run("docker", "cp", f"{container}:/tmp/adaptive-probe/ledger.jsonl", str(inner_ledger))
        finally:
            subprocess.run(
                ["docker", "rm", "-f", container],
                check=False,
                capture_output=True,
                text=True,
            )

        inner = json.loads(inner_output.read_text(encoding="utf-8"))
        checks = {
            **dict(inner.get("checks") or {}),
            "container_network_disabled": network_mode == "none",
            "deerflow_source_pinned": source_sha == EXPECTED_DEERFLOW_SHA,
            "ledger_copied_from_container": inner_ledger.is_file() and inner_ledger.stat().st_size > 0,
        }
        report: dict[str, Any] = {
            "passed": bool(inner.get("passed")) and all(checks.values()),
            "scope": "pinned DeerFlow container wiring probe",
            "runtime_image": args.image,
            "runtime_image_id": image_id,
            "deerflow_source_sha": source_sha,
            "dataset_fingerprint": dataset.fingerprint,
            "candidate_profile_fingerprint": candidate.profile_fingerprint,
            "adaptive_source_sha256": _sha256_tree(source_dir),
            "probe_script_sha256": hashlib.sha256(inner_script.read_bytes()).hexdigest(),
            "model_calls": 0,
            "new_model_tokens": 0,
            "realreplica_candidate_runner_wired": False,
            "checks": checks,
            "ledger_event_count": inner.get("ledger_event_count"),
            "ledger_sha256": hashlib.sha256(inner_ledger.read_bytes()).hexdigest(),
            "usage": inner.get("usage") or {},
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
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
