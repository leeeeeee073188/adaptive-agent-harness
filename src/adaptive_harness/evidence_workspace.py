"""Visible evidence workspace compiled from tool calls and tool results.

The workspace is a small, deterministic, secret-safe dashboard of resources the
agent has already inspected. It is intentionally independent from a benchmark or
runtime: callers provide ordinary chat messages containing assistant tool calls
and matching tool-result messages.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from adaptive_harness.action_ledger import classify_tool_action
from adaptive_harness.redaction import canonical_http_resource, scrub_tool_result_text


@dataclass(frozen=True)
class EvidenceResource:
    resource: str
    access_count: int
    tools: tuple[str, ...]
    size: int
    hash: str
    archived: bool
    distinct_results: int
    last_sequence: int

    def to_dashboard_row(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "access_count": self.access_count,
            "tools": list(self.tools),
            "size": self.size,
            "hash": self.hash,
            "archived": self.archived,
            "distinct_results": self.distinct_results,
            "last_sequence": self.last_sequence,
        }


@dataclass(frozen=True)
class EvidenceExcerpt:
    resource: str
    hash: str
    tool_name: str
    archived: bool
    text: str
    sequence: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "hash": self.hash,
            "tool_name": self.tool_name,
            "archived": self.archived,
            "text": self.text,
            "sequence": self.sequence,
        }


@dataclass(frozen=True)
class EvidenceSnapshot:
    resources: tuple[EvidenceResource, ...]
    excerpts: tuple[EvidenceExcerpt, ...]
    archived_resources: int
    repeated_results: int


@dataclass(frozen=True)
class _ObservedResult:
    sequence: int
    resource: str
    tool_name: str
    safe_content: str
    original_size: int
    content_hash: str
    archived: bool
    excerpt: str


class EvidenceWorkspace:
    """Immutable, bounded view over resources already observed by tools."""

    def __init__(self, snapshot: EvidenceSnapshot) -> None:
        self._snapshot = snapshot

    @classmethod
    def from_messages(
        cls,
        messages: Sequence[Mapping[str, Any]],
        *,
        task_terms: str | Sequence[str] = (),
        max_full_chars: int = 1200,
        max_excerpt_chars: int = 700,
    ) -> EvidenceWorkspace:
        if max_full_chars < 0:
            raise ValueError("max_full_chars must be non-negative")
        if max_excerpt_chars <= 0:
            raise ValueError("max_excerpt_chars must be positive")
        terms = _task_terms(task_terms)
        observations = tuple(
            _iter_observations(
                messages,
                terms=terms,
                max_full_chars=max_full_chars,
                max_excerpt_chars=max_excerpt_chars,
            )
        )
        grouped: dict[str, list[_ObservedResult]] = {}
        for observation in observations:
            grouped.setdefault(observation.resource, []).append(observation)

        resources: list[EvidenceResource] = []
        excerpts: list[EvidenceExcerpt] = []
        repeated_results = 0
        global_excerpt_hashes: set[str] = set()
        for resource, rows in sorted(grouped.items()):
            latest = max(rows, key=lambda row: row.sequence)
            seen_hashes: set[str] = set()
            for row in rows:
                if row.content_hash in seen_hashes:
                    repeated_results += 1
                    continue
                seen_hashes.add(row.content_hash)
                if row.content_hash in global_excerpt_hashes:
                    continue
                global_excerpt_hashes.add(row.content_hash)
                excerpts.append(
                    EvidenceExcerpt(
                        resource=row.resource,
                        hash=row.content_hash,
                        tool_name=row.tool_name,
                        archived=row.archived,
                        text=row.excerpt,
                        sequence=row.sequence,
                    )
                )
            resources.append(
                EvidenceResource(
                    resource=resource,
                    access_count=len(rows),
                    tools=tuple(sorted({row.tool_name for row in rows})),
                    size=max(row.original_size for row in rows),
                    hash=latest.content_hash,
                    archived=any(row.archived for row in rows),
                    distinct_results=len(seen_hashes),
                    last_sequence=latest.sequence,
                )
            )
        snapshot = EvidenceSnapshot(
            resources=tuple(sorted(resources, key=_resource_priority)),
            excerpts=tuple(sorted(excerpts, key=_excerpt_priority)),
            archived_resources=sum(1 for item in resources if item.archived),
            repeated_results=repeated_results,
        )
        return cls(snapshot)

    def snapshot(self) -> EvidenceSnapshot:
        return self._snapshot

    def to_context_block(self, *, excerpt_limit: int = 8) -> dict[str, Any]:
        if excerpt_limit < 0:
            raise ValueError("excerpt_limit must be non-negative")
        return {
            "resources": [resource.to_dashboard_row() for resource in self.dashboard_resources()],
            "evidence_excerpts": [
                excerpt.to_payload() for excerpt in self.evidence_excerpts(excerpt_limit)
            ],
            "stats": self.stats(),
            "retention": _RETENTION_TEXT,
        }

    def dashboard_resources(self, *, limit: int = 8) -> tuple[EvidenceResource, ...]:
        return self._snapshot.resources[:limit]

    def evidence_excerpts(self, limit: int = 8) -> tuple[EvidenceExcerpt, ...]:
        if limit < 0:
            raise ValueError("excerpt limit must be non-negative")
        return self._snapshot.excerpts[:limit]

    def stats(self) -> dict[str, int]:
        return {
            "resource_count": len(self._snapshot.resources),
            "archived_resources": self._snapshot.archived_resources,
            "repeated_results": self._snapshot.repeated_results,
        }


_RETENTION_TEXT = (
    "Large or duplicate tool results are archived here by resource and content hash. "
    "Use the retained excerpts and dashboard instead of re-reading unchanged resources."
)
_IMPORTANT_LINE = re.compile(
    r"(?i)(required|requirement|constraint|must|expected|schema|column|header|title|error|fail|missing|invalid|"
    r"必须|要求|约束|错误|失败|缺失|标题|字段)"
)
_BASH_RESOURCE = re.compile(
    r"(?:/task/)?(?:workspace|outputs|snapshots|tmp)(?:/[A-Za-z0-9_.*?{}\-]+)+(?:\.[A-Za-z0-9]{1,10})?|"
    r"/task/[A-Za-z0-9_.*?{}\-/]+(?:\.[A-Za-z0-9]{1,10})?|"
    r"\b[A-Za-z0-9_-]+\.(?:json|csv|md|txt|yaml|yml|toml|html|png|jpg|pdf)\b"
)


def _iter_observations(
    messages: Sequence[Mapping[str, Any]],
    *,
    terms: tuple[str, ...],
    max_full_chars: int,
    max_excerpt_chars: int,
) -> tuple[_ObservedResult, ...]:
    pending: dict[str, tuple[str, Mapping[str, Any], tuple[str, ...]]] = {}
    observations: list[_ObservedResult] = []
    sequence = 0
    for message in messages:
        if message.get("role") == "assistant":
            for raw in message.get("tool_calls") or ():
                parsed = _parse_tool_call(raw)
                if parsed is None:
                    continue
                call_id, tool_name, arguments = parsed
                resources = _resources_for(tool_name, arguments)
                if resources:
                    pending[call_id] = (tool_name, arguments, resources)
        elif message.get("role") == "tool":
            call_id = str(message.get("tool_call_id") or "")
            pending_call = pending.pop(call_id, None)
            if pending_call is None:
                continue
            tool_name, _, resources = pending_call
            content = _render_content(message.get("content", ""))
            original_size = len(content)
            safe_content = scrub_tool_result_text(content)
            content_hash = hashlib.sha256(safe_content.encode()).hexdigest()[:16]
            archived = len(safe_content) > max_full_chars
            excerpt = (
                _build_excerpt(safe_content, terms=terms, max_chars=max_excerpt_chars)
                if archived
                else _clip(safe_content, max_full_chars)
            )
            sequence += 1
            for resource in resources:
                observations.append(
                    _ObservedResult(
                        sequence=sequence,
                        resource=resource,
                        tool_name=tool_name,
                        safe_content=safe_content,
                        original_size=original_size,
                        content_hash=content_hash,
                        archived=archived,
                        excerpt=excerpt,
                    )
                )
    return tuple(observations)


def _parse_tool_call(raw: Any) -> tuple[str, str, Mapping[str, Any]] | None:
    if not isinstance(raw, Mapping) or raw.get("id") is None:
        return None
    call_id = str(raw["id"])
    if isinstance(raw.get("function"), Mapping):
        function = raw["function"]
        name = str(function.get("name") or raw.get("name") or "unknown")
        arguments = _parse_arguments(function.get("arguments") or raw.get("arguments") or raw.get("args") or {})
    else:
        name = str(raw.get("name") or "unknown")
        arguments = _parse_arguments(raw.get("arguments") or raw.get("args") or {})
    return call_id, name, arguments


def _parse_arguments(raw: Any) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {"raw_arguments": raw}
        return dict(value) if isinstance(value, Mapping) else {"raw_arguments": raw}
    return {}


def _resources_for(tool_name: str, arguments: Mapping[str, Any]) -> tuple[str, ...]:
    resources = set(classify_tool_action(tool_name, arguments).resources)
    command = str(arguments.get("cmd") or arguments.get("command") or "")
    resources.update(_BASH_RESOURCE.findall(command))
    for key, value in arguments.items():
        normalized_key = str(key).lower().replace("-", "_")
        if normalized_key in {"path", "file", "directory", "url", "endpoint"} and isinstance(value, str):
            resources.add(value)
    return tuple(sorted({_canonical_resource(resource) for resource in resources if resource}))


def _canonical_resource(value: str) -> str:
    stripped = value.strip("'\"` ,;:()[]{}")
    safe_url = canonical_http_resource(stripped)
    if safe_url is not None:
        return safe_url
    if stripped.lower().startswith(("http://", "https://")):
        return "<redacted-resource>"
    resource = scrub_tool_result_text(stripped)
    if resource == "<redacted sensitive content>":
        return "<redacted-resource>"
    return resource.replace("\\", "/").removeprefix("/task/")


def _render_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _build_excerpt(text: str, *, terms: tuple[str, ...], max_chars: int) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    scored = []
    for index, line in enumerate(lines):
        lowered = line.lower()
        score = 0
        if _IMPORTANT_LINE.search(line):
            score += 5
        score += sum(1 for term in terms if term in lowered)
        if index == 0:
            score += 1
        scored.append((-score, index, line))
    chosen = sorted(scored)[: max(1, min(12, len(scored)))]
    rendered_lines = [
        f"L{index + 1}: {_clip(line, 160)}"
        for _, index, line in sorted(chosen, key=lambda item: item[1])
    ]
    important = [line for line in rendered_lines if _IMPORTANT_LINE.search(line)]
    ordered = important + [line for line in rendered_lines if line not in important]
    retained: list[str] = []
    used = 0
    for line in ordered:
        cost = len(line) + (1 if retained else 0)
        if retained and used + cost > max_chars:
            continue
        retained.append(line)
        used += cost
        if used >= max_chars:
            break
    return "\n".join(sorted(retained, key=_line_number))


def _line_number(line: str) -> int:
    match = re.match(r"L(\d+):", line)
    return int(match.group(1)) if match else 0


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 20:
        return text[:max_chars]
    head = max_chars // 2
    tail = max_chars - head - 12
    return f"{text[:head]}…<omitted>…{text[-tail:]}"


def _task_terms(raw: str | Sequence[str]) -> tuple[str, ...]:
    if isinstance(raw, str):
        text = raw
    else:
        text = " ".join(str(item) for item in raw)
    terms = re.findall(r"[A-Za-z0-9_]{3,}|[\u4e00-\u9fff]{2,}", text.lower())
    stop = {"the", "and", "for", "with", "this", "that", "from", "into", "task", "complete"}
    return tuple(sorted({term for term in terms if term not in stop}, key=lambda term: (-len(term), term))[:40])


def _resource_priority(resource: EvidenceResource) -> tuple[int, int, int, str]:
    repeated = int(resource.access_count > resource.distinct_results)
    return (-repeated, -resource.last_sequence, -int(resource.archived), resource.resource)


def _excerpt_priority(excerpt: EvidenceExcerpt) -> tuple[int, int, str, str]:
    important = int(_IMPORTANT_LINE.search(excerpt.text) is not None)
    return (-important, -excerpt.sequence, excerpt.resource, excerpt.hash)
