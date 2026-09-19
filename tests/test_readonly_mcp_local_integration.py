"""Opt-in smoke test against the isolated local douyin_research database.

Run only from the local environment wrapper documented in
docs/LOCAL_MCP_READONLY_V1.md.  It receives credentials through the process
environment and deliberately never includes them in assertions or output.
"""

from __future__ import annotations

import json
import os
from urllib.parse import quote

import psycopg
import pytest

from douyin_research.mcp.server import ReadOnlyMCPServer
from douyin_research.mcp.readonly import CanonicalResearchQueries


def _local_dsn() -> str:
    if os.environ.get("RUN_LOCAL_MCP_INTEGRATION") != "1":
        pytest.skip("set RUN_LOCAL_MCP_INTEGRATION=1 for the isolated local smoke test")
    required = (
        "L3_LOCAL_REVIEWER_USER",
        "L3_LOCAL_REVIEWER_PASSWORD",
        "POSTGRES_BIND_HOST",
        "POSTGRES_PORT",
        "RESEARCH_DB_NAME",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip("local MCP smoke environment is incomplete")
    return (
        "postgresql://"
        f"{quote(os.environ['L3_LOCAL_REVIEWER_USER'], safe='')}:"
        f"{quote(os.environ['L3_LOCAL_REVIEWER_PASSWORD'], safe='')}@"
        f"{os.environ['POSTGRES_BIND_HOST']}:{os.environ['POSTGRES_PORT']}/"
        f"{os.environ['RESEARCH_DB_NAME']}?sslmode=disable"
    )


def _request(request_id: int, method: str, params=None) -> dict[str, object]:
    request: dict[str, object] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        request["params"] = params
    return request


def _tool_data(response: dict[str, object]) -> dict[str, object]:
    result = response["result"]
    assert isinstance(result, dict)
    assert result.get("isError") is not True
    content = result["content"]
    assert isinstance(content, list) and len(content) == 1
    text = content[0]["text"]
    assert isinstance(text, str)
    data = json.loads(text)
    assert isinstance(data, dict)
    return data


def test_local_readonly_mcp_end_to_end() -> None:
    """Exercise all local MCP reads without exposing canonical text in pytest output."""

    dsn = _local_dsn()
    server = ReadOnlyMCPServer(CanonicalResearchQueries(dsn))

    initialized = server.dispatch(_request(1, "initialize"))
    assert initialized is not None
    assert initialized["result"]["protocolVersion"] == "2024-11-05"

    listed = server.dispatch(_request(2, "tools/list"))
    assert listed is not None
    assert [tool["name"] for tool in listed["result"]["tools"]] == [
        "search_cases",
        "get_case_detail",
        "get_cost_summary",
    ]

    searched = server.dispatch(
        _request(3, "tools/call", {"name": "search_cases", "arguments": {"limit": 1}})
    )
    assert searched is not None
    search_data = _tool_data(searched)
    items = search_data["items"]
    assert isinstance(items, list) and items
    assert all("title" not in item for item in items)
    video_id = items[0]["video_id"]
    assert isinstance(video_id, str)

    detail = server.dispatch(
        _request(4, "tools/call", {"name": "get_case_detail", "arguments": {"video_id": video_id}})
    )
    assert detail is not None
    detail_data = _tool_data(detail)
    assert detail_data["video_id"] == video_id
    assert detail_data["read_only"] is True
    assert "title" not in detail_data
    analysis = detail_data["l3_analysis"]
    if analysis is not None:
        assert set(analysis) == {
            "model",
            "model_revision",
            "prompt_version",
            "schema_version",
            "created_at",
            "output_summary",
            "cost",
        }
        assert "output" not in analysis
        assert isinstance(analysis["output_summary"], dict)

    costs = server.dispatch(
        _request(5, "tools/call", {"name": "get_cost_summary", "arguments": {"days": 1}})
    )
    assert costs is not None
    cost_data = _tool_data(costs)
    assert cost_data["days"] == 1
    assert cost_data["read_only"] is True


def test_local_reviewer_database_write_is_rejected() -> None:
    """The role and its default transaction must reject a write attempt."""

    conn = psycopg.connect(_local_dsn())
    try:
        with conn.cursor() as cur:
            cur.execute("select has_table_privilege(current_user, 'public.source_video', 'INSERT')")
            assert cur.fetchone() == (False,)
            with pytest.raises(psycopg.Error) as raised:
                cur.execute("insert into public.source_video default values")
            assert raised.value.sqlstate in {"25006", "42501"}
    finally:
        conn.rollback()
        conn.close()
