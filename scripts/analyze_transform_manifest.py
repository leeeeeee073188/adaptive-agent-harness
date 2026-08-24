#!/usr/bin/env python3
"""Emit a zero-model public Transform Manifest for one isolated task workspace."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from adaptive_harness.transform_manifest import PythonTransformManifestScanner


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_root", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    scan = PythonTransformManifestScanner().scan(args.task_root.resolve())
    payload = {
        "schema_version": 1,
        "scope": "zero-model public Python Transform Manifest",
        "model_calls": 0,
        "new_model_tokens": 0,
        **scan.to_payload(),
        "missing_read_count": sum(len(item.missing_reads) for item in scan.manifests),
        "execution_performed": False,
        "paid_expansion_allowed": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
