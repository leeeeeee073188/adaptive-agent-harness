"""Profiles and bundles: deterministic composition and experiment identity."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

_CREDENTIAL_KEY_SUFFIXES = (
    "api_key",
    "apikey",
    "access_token",
    "bearer_token",
    "credential",
    "credentials",
    "password",
    "secret",
    "secret_key",
)
_SECRET_VALUE = re.compile(r"^(?:sk-[A-Za-z0-9_-]{8,}|bearer\s+\S+)$", re.IGNORECASE)


def _reject_credentials(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            is_env_reference = normalized.endswith("_env")
            if not is_env_reference and (
                normalized in {"authorization", "token"}
                or any(
                    normalized == suffix or normalized.endswith(f"_{suffix}")
                    for suffix in _CREDENTIAL_KEY_SUFFIXES
                )
            ):
                raise ValueError(f"profile fingerprints must not contain credentials: {key!r}")
            _reject_credentials(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_credentials(nested)
    elif isinstance(value, str) and _SECRET_VALUE.match(value.strip()):
        raise ValueError("profile fingerprints must not contain credential-shaped values")


@dataclass(frozen=True)
class PluginSpec:
    name: str
    config: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


@dataclass(frozen=True)
class Bundle:
    name: str
    plugins: tuple[PluginSpec, ...]


@dataclass(frozen=True)
class Profile:
    name: str
    bundles: tuple[Bundle, ...]
    overlays: tuple[PluginSpec, ...] = ()

    def compose(self) -> tuple[PluginSpec, ...]:
        rows: dict[str, PluginSpec] = {}
        for bundle in self.bundles:
            for spec in bundle.plugins:
                rows[spec.name] = spec
        for spec in self.overlays:
            rows[spec.name] = spec
        return tuple(item for item in rows.values() if item.enabled)

    def fingerprint(self) -> str:
        _reject_credentials([item.config for item in self.compose()])
        payload = {
            "name": self.name,
            "plugins": [asdict(item) for item in self.compose()],
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()
