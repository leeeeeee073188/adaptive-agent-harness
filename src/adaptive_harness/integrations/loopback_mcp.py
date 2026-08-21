"""Minimal read-only JSON-RPC MCP client for local integration adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def validate_loopback_endpoint(endpoint: str) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("MCP observation providers are restricted to loopback http endpoints")


def call_mcp_tool(endpoint: str, name: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    request = Request(
        endpoint,
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": f"adaptive-observe:{name}",
                "method": "tools/call",
                "params": {"name": name, "arguments": dict(arguments)},
            }
        ).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as response:
            envelope = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError) as error:
        raise ValueError(f"MCP observation call failed: {name}") from error
    if not isinstance(envelope, Mapping) or envelope.get("error"):
        raise ValueError(f"MCP observation returned an error: {name}")
    result = envelope.get("result")
    if not isinstance(result, Mapping):
        raise ValueError(f"MCP observation result is malformed: {name}")
    for block in result.get("content") or ():
        if not isinstance(block, Mapping) or block.get("type") != "text":
            continue
        try:
            payload = json.loads(str(block.get("text") or ""))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            return payload
    raise ValueError(f"MCP observation contains no structured text payload: {name}")

