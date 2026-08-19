"""Profiles and bundles: deterministic composition and experiment identity."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


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
        payload = {
            "name": self.name,
            "plugins": [asdict(item) for item in self.compose()],
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()
