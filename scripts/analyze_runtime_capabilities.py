#!/usr/bin/env python3
"""Emit zero-model Runtime capability and selection evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from adaptive_harness.integrations.runtime_profiles import (
    browser_task_runtime_request,
    deerflow_runtime_profile,
    deterministic_reference_runtime_profile,
    file_task_runtime_request,
)
from adaptive_harness.runtime_capabilities import (
    CapabilityAssurance,
    RuntimeCapability,
    RuntimeCapabilitySelector,
    RuntimeRequirement,
    RuntimeSelectionRequest,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    profiles = (
        deerflow_runtime_profile(),
        deterministic_reference_runtime_profile(),
    )
    selector = RuntimeCapabilitySelector()
    strict_file = selector.select(profiles, file_task_runtime_request())
    claimed_file = selector.select(
        profiles,
        file_task_runtime_request(
            final_response_assurance=CapabilityAssurance.CLAIMED
        ),
    )
    browser = selector.select(profiles, browser_task_runtime_request())
    control = selector.select(
        profiles,
        RuntimeSelectionRequest(
            required=(
                RuntimeRequirement(RuntimeCapability.MULTI_TURN),
                RuntimeRequirement(RuntimeCapability.FINAL_RESPONSE),
                RuntimeRequirement(RuntimeCapability.DURABLE_LEDGER),
            ),
            preferred=(RuntimeCapability.BEFORE_RUN_EVIDENCE,),
        ),
    )
    payload = {
        "schema_version": 1,
        "scope": "zero-model Runtime capability negotiation",
        "model_calls": 0,
        "new_model_tokens": 0,
        "profiles": [profile.to_payload() for profile in profiles],
        "profile_fingerprints": {
            profile.runtime_id: profile.fingerprint() for profile in profiles
        },
        "decisions": {
            "strict_file": strict_file.to_payload(),
            "claimed_file": claimed_file.to_payload(),
            "strict_browser": browser.to_payload(),
            "control_plane": control.to_payload(),
        },
        "invariants": {
            "strict_file_has_no_qualified_runtime": strict_file.selected_runtime_id
            is None,
            "claimed_file_selects_deerflow_only_when_explicit": (
                claimed_file.selected_runtime_id == "deerflow"
            ),
            "strict_browser_has_no_qualified_runtime": browser.selected_runtime_id
            is None,
            "control_plane_selects_reference": (
                control.selected_runtime_id == "deterministic-reference"
            ),
        },
        "runtime_execution_performed": False,
        "paid_expansion_allowed": False,
        "claim_boundary": (
            "Capability negotiation is evidence-backed architecture only. There is no second real "
            "Runtime benchmark and no Runtime quality ranking."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if all(payload["invariants"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
