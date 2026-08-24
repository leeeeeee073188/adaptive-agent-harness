"""Shared secret redaction for context, action, and evidence surfaces.

The functions here are intentionally dependency-free and conservative. They
prefer removing sensitive values over preserving exact formatting because these
surfaces are replayed into model context, audit previews, and dashboards.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

REDACTED = "<redacted>"
REDACTED_SENSITIVE_CONTENT = "<redacted sensitive content>"

_URL = re.compile(r"https?://[^\s`<>()\[\]{}\"']+", re.I)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}\b")
_SK_VALUE = re.compile(r"(?i)\bsk-[a-z0-9_-]{8,}\b")
_KEY_VALUE_SECRET = re.compile(
    r"""(?ix)
    (?P<prefix>(?:^|[,{\s'\"])[\"']?)
    (?P<key>
        authorization
        | password
        | api[_-]?key
        | secret
        | token
        | access[_-]?token
        | refresh[_-]?token
        | client[_-]?secret
        | [a-z0-9_.-]+[_-]token
        | [a-z0-9_.-]+[_-]secret
    )
    (?P<quote>[\"']?)
    \s*[:=]\s*
    (?P<value>[\"']?[^\s,}\]]+)
    """
)

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "client_secret",
    "credential",
    "password",
    "refresh_token",
    "secret",
    "token",
    "access_token",
}

_SENSITIVE_QUERY_KEYS = {
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "client_secret",
    "code",
    "credential",
    "password",
    "refresh_token",
    "secret",
    "signature",
    "token",
    "access_token",
}

_SAFE_HOST = re.compile(r"^[A-Za-z0-9.-]+(?::\d+)?$")


def normalize_key(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_").replace(".", "_")


def is_sensitive_key(value: Any) -> bool:
    key = normalize_key(value)
    return key in _SENSITIVE_KEYS or key.endswith("_token") or key.endswith("_secret")


def sensitive_keys(value: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(str(key) for key in value if is_sensitive_key(key)))


def deep_redact(value: Any) -> Any:
    """Recursively redact sensitive structured values and secret-like strings."""

    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            redacted[str(key)] = REDACTED if is_sensitive_key(key) else deep_redact(item)
        return redacted
    if isinstance(value, tuple):
        return tuple(deep_redact(item) for item in value)
    if isinstance(value, list):
        return [deep_redact(item) for item in value]
    if isinstance(value, str):
        return scrub_text(value, drop_sensitive_lines=False)
    return value


def redact_mapping(value: Mapping[str, Any], *, drop_keys: set[str] | None = None) -> dict[str, Any]:
    drop_keys = drop_keys or set()
    result: dict[str, Any] = {}
    for key, item in value.items():
        normalized = normalize_key(key)
        if normalized in drop_keys:
            continue
        result[str(key)] = REDACTED if is_sensitive_key(key) else deep_redact(item)
    return result


def render_redacted(value: Any) -> str:
    if isinstance(value, str):
        return scrub_text(value, drop_sensitive_lines=True)
    return json.dumps(deep_redact(value), ensure_ascii=False, sort_keys=True, default=str)


def scrub_tool_result_text(value: Any) -> str:
    raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    scrubbed = scrub_text(raw, drop_sensitive_lines=True).strip()
    return scrubbed or REDACTED_SENSITIVE_CONTENT


def scrub_text(text: str, *, drop_sensitive_lines: bool = False) -> str:
    if not text:
        return text
    if drop_sensitive_lines:
        lines = []
        for line in text.splitlines():
            if _line_has_sensitive_material(line):
                continue
            lines.append(_scrub_line(line))
        return "\n".join(lines)
    return _scrub_line(text)


def canonical_http_resource(raw: str) -> str | None:
    """Return a secret-safe URL resource, never falling back to raw userinfo URLs."""

    cleaned = raw.strip().strip("'\"` ,;:()[]{}")
    parsed = urlparse(cleaned)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    hostname = parsed.hostname
    if not hostname:
        return None
    netloc = hostname.lower()
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None:
        netloc = f"{netloc}:{port}"
    if not _SAFE_HOST.match(netloc):
        return None
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [
        (key, value)
        for key, value in query_items
        if normalize_key(key) not in _SENSITIVE_QUERY_KEYS
        and not normalize_key(key).endswith("_token")
        and not normalize_key(key).endswith("_secret")
    ]
    query = urlencode(filtered, doseq=True)
    return urlunparse((parsed.scheme.lower(), netloc, parsed.path or "", "", query, ""))


def scrub_urls(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        raw = match.group(0).rstrip(".,;:)]}")
        suffix = match.group(0)[len(raw) :]
        safe = canonical_http_resource(raw)
        return f"{safe or REDACTED}{suffix}"

    return _URL.sub(replace, text)


def _line_has_sensitive_material(line: str) -> bool:
    return bool(_KEY_VALUE_SECRET.search(line) or _BEARER_VALUE.search(line) or _SK_VALUE.search(line))


def _scrub_line(text: str) -> str:
    scrubbed = scrub_urls(text)
    scrubbed = _BEARER_VALUE.sub(REDACTED, scrubbed)
    scrubbed = _SK_VALUE.sub(REDACTED, scrubbed)
    return _KEY_VALUE_SECRET.sub(lambda match: f"{match.group('prefix')}{match.group('key')}={REDACTED}", scrubbed)
