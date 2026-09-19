"""Minimal stdio MCP server for read-only canonical research queries.

The transport is newline-delimited JSON-RPC as required by MCP's stdio
transport.  Protocol messages are written only to stdout; diagnostics are
intentionally omitted so database DSNs, query terms, and upstream errors do
not enter a host's MCP transcript.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from typing import Any, Protocol, TextIO
from uuid import UUID

from .readonly import CanonicalResearchQueries, MCPInputError, json_text

_PROTOCOL_VERSION = "2024-11-05"
_SERVER_NAME = "douyin-research-readonly-local"
_SERVER_VERSION = "0.1.0"


class ResearchQueries(Protocol):
    def search_cases(
        self, *, query: str = "", platform: str | None = None, limit: int = 10
    ) -> dict[str, object]: ...

    def get_case_detail(self, *, video_id: str) -> dict[str, object]: ...

    def get_cost_summary(self, *, days: int = 30) -> dict[str, object]: ...


_TOOLS = (
    {
        "name": "search_cases",
        "description": "Search bounded canonical video-case metadata. It never returns transcript, comments, URLs, provider payloads, or credentials.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {"type": "string", "maxLength": 120},
                "platform": {"type": "string", "maxLength": 64},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
    },
    {
        "name": "get_case_detail",
        "description": "Get one canonical case and its whitelisted completed L3 result/cost summary. Raw evidence is excluded.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["video_id"],
            "properties": {"video_id": {"type": "string", "format": "uuid"}},
        },
    },
    {
        "name": "get_cost_summary",
        "description": "Read task-cost and daily-budget status. This does not reserve, update, or execute budget.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 366}},
        },
    },
)


class ReadOnlyMCPServer:
    """Dispatch a deliberately small MCP tool surface without side effects."""

    def __init__(self, queries: ResearchQueries) -> None:
        self._queries = queries

    def dispatch(self, message: object) -> dict[str, object] | None:
        if not isinstance(message, Mapping):
            return _error(None, -32600, "invalid request")
        if message.get("jsonrpc") != "2.0" or "method" not in message:
            return _error(_request_id(message), -32600, "invalid request")
        method = message["method"]
        if not isinstance(method, str):
            return _error(_request_id(message), -32600, "invalid request")
        request_id = _request_id(message)
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return _result(
                request_id,
                {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
                    "instructions": "Local read-only MCP. No provider calls, writes, budget reservations, secrets, transcript, or comment text.",
                },
            )
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": list(_TOOLS)})
        if method == "tools/call":
            if request_id is None:
                return None
            return self._call_tool(request_id, message.get("params"))
        return _error(request_id, -32601, "method not found")

    def _call_tool(self, request_id: str | int | None, params: object) -> dict[str, object]:
        if not isinstance(params, Mapping):
            return _error(request_id, -32602, "invalid tool parameters")
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, Mapping):
            return _error(request_id, -32602, "invalid tool parameters")
        try:
            if name == "search_cases":
                _only(arguments, {"query", "platform", "limit"})
                data = self._queries.search_cases(
                    query=_string(arguments.get("query", ""), "query"),
                    platform=_optional_string(arguments.get("platform"), "platform"),
                    limit=_integer(arguments.get("limit", 10), "limit"),
                )
            elif name == "get_case_detail":
                _only(arguments, {"video_id"})
                data = self._queries.get_case_detail(
                    video_id=_uuid_string(arguments.get("video_id"), "video_id")
                )
            elif name == "get_cost_summary":
                _only(arguments, {"days"})
                data = self._queries.get_cost_summary(
                    days=_integer(arguments.get("days", 30), "days")
                )
            else:
                return _tool_error(request_id, "MCP_TOOL_NOT_FOUND")
        except MCPInputError:
            return _tool_error(request_id, "MCP_INPUT_INVALID")
        except (TypeError, ValueError):
            return _tool_error(request_id, "MCP_INPUT_INVALID")
        except Exception:
            return _tool_error(request_id, "MCP_READ_UNAVAILABLE")
        return _result(
            request_id,
            {"content": [{"type": "text", "text": json_text(data)}]},
        )


def serve(
    server: ReadOnlyMCPServer,
    *,
    input_stream: TextIO = sys.stdin,
    output_stream: TextIO = sys.stdout,
) -> None:
    """Run the local stdio loop; malformed input never terminates the server."""

    for raw_line in input_stream:
        try:
            message = json.loads(raw_line)
        except json.JSONDecodeError:
            response = _error(None, -32700, "parse error")
        else:
            response = server.dispatch(message)
        if response is not None:
            output_stream.write(json_text(response) + "\n")
            output_stream.flush()


def main() -> int:
    dsn = os.environ.get("DOUYIN_RESEARCH_MCP_DATABASE_URL")
    if not dsn:
        # This intentionally avoids exposing which environment variables exist.
        print("local read-only MCP database is not configured", file=sys.stderr)
        return 2
    serve(ReadOnlyMCPServer(CanonicalResearchQueries(dsn)))
    return 0


def _request_id(message: Mapping[str, object]) -> str | int | None:
    value = message.get("id")
    return value if isinstance(value, (str, int)) and not isinstance(value, bool) else None


def _result(request_id: str | int | None, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: str | int | None, code: int, message: str) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_error(request_id: str | int | None, code: str) -> dict[str, object]:
    return _result(request_id, {"content": [{"type": "text", "text": code}], "isError": True})


def _only(arguments: Mapping[str, object], allowed: set[str]) -> None:
    if set(arguments).difference(allowed):
        raise MCPInputError("unexpected argument")


def _string(value: object, _name: str) -> str:
    if not isinstance(value, str):
        raise MCPInputError("string required")
    return value


def _optional_string(value: object, _name: str) -> str | None:
    if value is None:
        return None
    return _string(value, _name)


def _integer(value: object, _name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MCPInputError("integer required")
    return value


def _uuid_string(value: object, _name: str) -> str:
    text = _string(value, _name)
    try:
        return str(UUID(text))
    except (TypeError, ValueError):
        raise MCPInputError("UUID required") from None


if __name__ == "__main__":  # pragma: no cover - exercised by subprocess test
    raise SystemExit(main())
