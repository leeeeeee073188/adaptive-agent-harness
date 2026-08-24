"""Runtime-independent request/result contract for Harness execution adapters."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from adaptive_harness.ledger import SessionLedger

_CREDENTIAL_SUFFIXES = (
    "api_key",
    "apikey",
    "access_token",
    "bearer_token",
    "credential",
    "credentials",
    "password",
    "secret",
)
_SECRET_VALUE = re.compile(r"^(?:sk-[A-Za-z0-9_-]{8,}|bearer\s+\S+)$", re.IGNORECASE)


@dataclass(frozen=True)
class RuntimeRequest:
    task: str
    run_id: str | None = None
    task_id: str | None = None
    public_schema: Mapping[str, object] | None = None
    ledger_path: Path | None = None
    thread_id: str | None = None
    client_options: Mapping[str, Any] = field(default_factory=dict)
    tool_schemas: Sequence[Mapping[str, Any]] = ()
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeResult:
    run_id: str
    ledger: SessionLedger
    completed: bool
    content: str


class RuntimeAdapter(Protocol):
    async def run(self, request: RuntimeRequest) -> RuntimeResult: ...


def reject_runtime_credentials(value: Any) -> None:
    """Reject secret-bearing request options before an Environment is built."""

    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).casefold().replace("-", "_").replace(" ", "_")
            if not normalized.endswith("_env") and (
                normalized in {"authorization", "token"}
                or any(
                    normalized == suffix or normalized.endswith(f"_{suffix}")
                    for suffix in _CREDENTIAL_SUFFIXES
                )
            ):
                raise ValueError(f"runtime request must not contain credentials: {key!r}")
            reject_runtime_credentials(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            reject_runtime_credentials(nested)
    elif isinstance(value, str) and _SECRET_VALUE.match(value.strip()):
        raise ValueError("runtime request must not contain credential-shaped values")
