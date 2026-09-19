from __future__ import annotations

import io
import json

import pytest

from douyin_research.mcp.readonly import (
    CanonicalResearchQueries,
    MCPInputError,
    _public_l3_analysis,
)
from douyin_research.mcp.server import ReadOnlyMCPServer, serve


RAW_SENTINEL = "RAW_TRANSCRIPT_SENTINEL"


class FakeQueries:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def search_cases(self, *, query: str = "", platform=None, limit: int = 10):
        self.calls.append(("search_cases", (query, platform, limit)))
        return {"items": [{"video_id": "case-1"}], "read_only": True}

    def get_case_detail(self, *, video_id: str):
        self.calls.append(("get_case_detail", video_id))
        return {
            "video_id": video_id,
            "l3_analysis": {"output_summary": {"limitations_count": 1}},
            "read_only": True,
        }

    def get_cost_summary(self, *, days: int = 30):
        self.calls.append(("get_cost_summary", days))
        return {"days": days, "task_costs": [], "daily_budgets": [], "read_only": True}


class FailingQueries(FakeQueries):
    def search_cases(self, **kwargs):
        raise RuntimeError(f"database {RAW_SENTINEL} secret")


def _request(request_id: int, method: str, params=None) -> dict[str, object]:
    message: dict[str, object] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _content(response: dict[str, object]) -> object:
    result = response["result"]
    assert isinstance(result, dict)
    content = result["content"]
    assert isinstance(content, list)
    return json.loads(content[0]["text"])


def test_initialize_and_tool_list_expose_only_read_operations() -> None:
    server = ReadOnlyMCPServer(FakeQueries())
    initialized = server.dispatch(_request(1, "initialize"))
    assert initialized is not None
    assert initialized["result"]["capabilities"] == {"tools": {"listChanged": False}}

    listed = server.dispatch(_request(2, "tools/list"))
    assert listed is not None
    tools = listed["result"]["tools"]
    assert [tool["name"] for tool in tools] == [
        "search_cases",
        "get_case_detail",
        "get_cost_summary",
    ]
    names = {tool["name"] for tool in tools}
    assert names.isdisjoint(
        {"refresh_case", "fetch_more_comments", "run_deep_analysis", "reserve_budget"}
    )
    assert all(tool["inputSchema"]["additionalProperties"] is False for tool in tools)


def test_stdio_protocol_initializes_and_calls_core_tool() -> None:
    queries = FakeQueries()
    input_stream = io.StringIO(
        "\n".join(
            json.dumps(item)
            for item in (
                _request(1, "initialize"),
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                _request(
                    2,
                    "tools/call",
                    {"name": "search_cases", "arguments": {"query": "案例", "limit": 3}},
                ),
            )
        )
        + "\n"
    )
    output_stream = io.StringIO()
    serve(ReadOnlyMCPServer(queries), input_stream=input_stream, output_stream=output_stream)
    responses = [json.loads(line) for line in output_stream.getvalue().splitlines()]

    assert [response["id"] for response in responses] == [1, 2]
    assert responses[0]["result"]["protocolVersion"] == "2024-11-05"
    assert _content(responses[1]) == {
        "items": [{"video_id": "case-1"}],
        "read_only": True,
    }
    assert queries.calls == [("search_cases", ("案例", None, 3))]


@pytest.mark.parametrize(
    "params",
    [
        {"name": "unknown", "arguments": {}},
        {"name": "search_cases", "arguments": {"execute": True}},
        {"name": "search_cases", "arguments": {"limit": "5"}},
        {"name": "get_case_detail", "arguments": {"video_id": "not-a-uuid"}},
    ],
)
def test_tools_fail_closed_on_unknown_or_unsafe_arguments(params) -> None:
    server = ReadOnlyMCPServer(FakeQueries())
    response = server.dispatch(_request(1, "tools/call", params))
    assert response is not None
    result = response["result"]
    assert result["isError"] is True
    assert result["content"][0]["text"] in {"MCP_TOOL_NOT_FOUND", "MCP_INPUT_INVALID"}


def test_database_errors_are_replaced_with_fixed_tool_error() -> None:
    response = ReadOnlyMCPServer(FailingQueries()).dispatch(
        _request(1, "tools/call", {"name": "search_cases", "arguments": {}})
    )
    assert response is not None
    serialized = json.dumps(response)
    assert "MCP_READ_UNAVAILABLE" in serialized
    assert RAW_SENTINEL not in serialized


def test_parse_error_never_terminates_following_message() -> None:
    queries = FakeQueries()
    output_stream = io.StringIO()
    serve(
        ReadOnlyMCPServer(queries),
        input_stream=io.StringIO("not-json\n" + json.dumps(_request(2, "ping")) + "\n"),
        output_stream=output_stream,
    )
    responses = [json.loads(line) for line in output_stream.getvalue().splitlines()]
    assert responses[0]["error"] == {"code": -32700, "message": "parse error"}
    assert responses[1]["result"] == {}


def test_no_write_or_provider_surface_is_imported() -> None:
    import douyin_research.mcp.readonly as readonly
    import douyin_research.mcp.server as server

    source = open(readonly.__file__, encoding="utf-8").read() + open(
        server.__file__, encoding="utf-8"
    ).read()
    for forbidden in (
        "httpx",
        "provider_factory",
        "l3executioncoordinator",
        "insert into",
        "update ",
        "delete from",
    ):
        assert forbidden not in source.lower()


def test_query_validation_is_bounded_before_database_access() -> None:
    from douyin_research.mcp.readonly import _days, _limit, _platform, _query

    assert _limit(50) == 50
    assert _days(366) == 366
    assert _platform("douyin") == "douyin"
    assert _query("x" * 120) == "x" * 120
    for call in (
        lambda: _limit(51),
        lambda: _days(367),
        lambda: _platform("bad platform"),
        lambda: _query("x" * 121),
    ):
        with pytest.raises(MCPInputError):
            call()


def test_canonical_query_uses_explicit_read_only_transaction_and_safe_columns(monkeypatch) -> None:
    import douyin_research.mcp.readonly as readonly

    class Cursor:
        def __init__(self) -> None:
            self.executed: list[tuple[str, object]] = []

        def execute(self, sql, params=None) -> None:
            self.executed.append((sql, params))

        def fetchall(self):
            return [
                {
                    "video_id": "00000000-0000-0000-0000-000000000001",
                    "platform": "douyin",
                    "published_at": None,
                    "duration_ms": 1000,
                    "research_level": 3,
                    "availability_status": "available",
                    "play_count": 1,
                    "like_count": 2,
                    "comment_count": 3,
                    "share_count": 4,
                    "metric_captured_at": None,
                    "l3_selected": True,
                }
            ]

    class Connection:
        def __init__(self) -> None:
            self.cursor_value = Cursor()
            self.rolled_back = False
            self.closed = False

        def cursor(self):
            return self.cursor_value

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(readonly.psycopg, "connect", lambda *args, **kwargs: connection)

    result = CanonicalResearchQueries("postgresql://readonly").search_cases(
        query="案例", platform="douyin", limit=1
    )

    assert result["items"] == [
        {
            "availability_status": "available",
            "comment_count": 3,
            "duration_ms": 1000,
            "l3_selected": True,
            "like_count": 2,
            "metric_captured_at": None,
            "platform": "douyin",
            "play_count": 1,
            "published_at": None,
            "research_level": 3,
            "share_count": 4,
            "video_id": "00000000-0000-0000-0000-000000000001",
        }
    ]
    assert connection.cursor_value.executed[0] == ("begin read only", None)
    assert connection.cursor_value.executed[1] == ("set local lock_timeout = '1s'", None)
    assert connection.cursor_value.executed[2] == ("set local statement_timeout = '3s'", None)
    query_sql, query_params = connection.cursor_value.executed[3]
    assert query_params == ("douyin", "douyin", "案例", "案例", 1)
    assert "source_url" not in query_sql
    assert "description" not in query_sql
    assert "video_comment" not in query_sql
    assert "transcript" not in query_sql
    assert all(word not in query_sql.lower() for word in ("insert ", "update ", "delete "))
    assert connection.rolled_back is True
    assert connection.closed is True


def test_read_only_cursor_closes_connection_when_transaction_setup_fails(monkeypatch) -> None:
    import douyin_research.mcp.readonly as readonly

    class Cursor:
        def execute(self, sql, params=None) -> None:
            raise RuntimeError("transaction setup failed")

    class Connection:
        def __init__(self) -> None:
            self.rolled_back = False
            self.closed = False

        def cursor(self):
            return Cursor()

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(readonly.psycopg, "connect", lambda *args, **kwargs: connection)

    with pytest.raises(RuntimeError, match="transaction setup failed"):
        with readonly._ReadOnlyCursor("postgresql://readonly"):
            pass

    assert connection.rolled_back is True
    assert connection.closed is True


def test_public_l3_analysis_returns_only_counts_and_fixed_metadata() -> None:
    row = {
        "model": "safe-model",
        "model_revision": "r1",
        "prompt_version": "p1",
        "schema_version": "s1",
        "created_at": None,
        "output": {
            "narrative_structure": [RAW_SENTINEL],
            "limitations": [RAW_SENTINEL, "another private sentence"],
            "mechanism_hypotheses_are_inferences": True,
            "privacy_reviewed": True,
            "unexpected": RAW_SENTINEL,
        },
        "api_cost": 0,
        "asr_cost": 0,
        "llm_cost": 0,
        "total_cost": 0,
        "cost_currency": "CNY",
        "cost_basis": "test",
    }

    result = _public_l3_analysis(row)
    serialized = json.dumps(result, ensure_ascii=False)

    assert result["output_summary"] == {
        "narrative_structure_count": 1,
        "limitations_count": 2,
        "mechanism_hypotheses_are_inferences": True,
        "privacy_reviewed": True,
    }
    assert RAW_SENTINEL not in serialized
    assert "another private sentence" not in serialized
