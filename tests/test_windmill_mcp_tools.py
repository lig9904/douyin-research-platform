from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

from windmill.f.content_research.research_tool_lib import queries as _queries


TOOLS = ROOT / "windmill/f/content_research/research_tools"
READ_TOOLS = (
    "search_cases",
    "get_case_detail",
    "get_hot_videos",
    "get_blackhorse_videos",
    "search_accounts",
    "get_account_videos",
    "get_metric_history",
)


def test_gateway_folder_contains_exact_bounded_read_surface() -> None:
    runnable = {
        path.name.removesuffix(".script.yaml")
        for path in TOOLS.glob("*.script.yaml")
    }
    assert runnable == set(READ_TOOLS)

    for name in READ_TOOLS:
        module = importlib.import_module(
            f"windmill.f.content_research.research_tools.{name}"
        )
        assert "db" not in inspect.signature(module.main).parameters

        metadata = (TOOLS / f"{name}.script.yaml").read_text(encoding="utf-8")
        assert "additionalProperties: false" in metadata
        assert "resource-postgresql" not in metadata
        assert "type: static" not in metadata
        assert "concurrent_limit: 4" in metadata
        assert "timeout: 10" in metadata

        lock = (TOOLS / f"{name}.script.lock").read_text(encoding="utf-8")
        assert "psycopg==3.3.6" in lock
        assert "wmill==1.815.0" in lock


def test_tool_layer_has_no_write_or_provider_surface() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in TOOLS.glob("*.py")
    ).lower()
    for forbidden in (
        "insert into",
        "update daily_budget",
        "delete from",
        "httpx",
        "tikhub",
        "volcengine",
        "provider_factory",
        "run_deep_analysis",
    ):
        assert forbidden not in source
    assert _queries.RESEARCH_DB_RESOURCE == "f/content_research/research_db"


def test_safe_tool_replaces_input_and_runtime_errors() -> None:
    assert _queries.safe_tool(
        lambda: (_ for _ in ()).throw(_queries.ToolInputError("secret input"))
    ) == {"ok": False, "error": "MCP_INPUT_INVALID"}
    assert _queries.safe_tool(
        lambda: (_ for _ in ()).throw(RuntimeError("database SECRET_SENTINEL"))
    ) == {"ok": False, "error": "MCP_READ_UNAVAILABLE"}


@pytest.mark.parametrize(
    ("call", "valid"),
    [
        (lambda: _queries.validate_limit(50), 50),
        (lambda: _queries.validate_days(90, maximum=90), 90),
        (lambda: _queries.validate_query("x" * 120), "x" * 120),
        (lambda: _queries.validate_platform("wechat_channels"), "wechat_channels"),
        (lambda: _queries.validate_score(60.5), _queries.Decimal("60.5")),
    ],
)
def test_public_input_bounds_accept_contract_edges(call, valid) -> None:
    assert call() == valid


@pytest.mark.parametrize(
    "call",
    [
        lambda: _queries.validate_limit(0),
        lambda: _queries.validate_limit(True),
        lambda: _queries.validate_days(91, maximum=90),
        lambda: _queries.validate_query("x" * 121),
        lambda: _queries.validate_platform("bad platform"),
        lambda: _queries.validate_score(101),
        lambda: _queries.validate_uuid("not-a-uuid", field="video_id"),
    ],
)
def test_public_input_bounds_fail_closed(call) -> None:
    with pytest.raises(_queries.ToolInputError):
        call()


def test_read_queries_use_bound_parameters_and_return_allowlisted_rows(monkeypatch) -> None:
    calls: list[tuple[str, tuple[object, ...]]] = []

    def fake_all(_db, sql, params):
        calls.append((sql, params))
        return [{"video_id": "00000000-0000-0000-0000-000000000001"}]

    monkeypatch.setattr(_queries, "_all", fake_all)
    db = {"host": "db", "user": "u", "password": "p", "dbname": "research"}

    cases = _queries.search_cases(
        db, query="case", platform="douyin", limit=3
    )
    hot = _queries.get_hot_videos(db, platform="douyin", days=7, limit=4)
    blackhorse = _queries.get_blackhorse_videos(
        db, platform="douyin", min_score=61, limit=5
    )
    accounts = _queries.search_accounts(
        db, query="account", platform="douyin", limit=6
    )
    account_videos = _queries.get_account_videos(
        db,
        account_id="00000000-0000-0000-0000-000000000002",
        days=30,
        limit=7,
    )
    history = _queries.get_metric_history(
        db,
        video_id="00000000-0000-0000-0000-000000000001",
        days=14,
        limit=8,
    )

    assert all(result["ok"] is True and result["read_only"] is True for result in (
        cases, hot, blackhorse, accounts, account_videos, history
    ))
    assert calls[0][1] == ("douyin", "douyin", "case", "case", 3)
    assert calls[1][1] == ("douyin", "douyin", 7, 4)
    assert calls[2][1] == ("douyin", "douyin", _queries.Decimal("61"), 5)
    assert calls[3][1] == (
        "douyin", "douyin", "account", "account", "account", 6
    )
    assert calls[4][1][1:] == (30, 7)
    assert calls[5][1][1:] == (14, 8)

    sql = "\n".join(item[0].lower() for item in calls)
    assert "raw_payload" not in sql
    assert "raw_metrics" not in sql
    assert "source_url" not in sql
    assert "profile_url" not in sql
    assert "text_content" not in sql


def test_database_cursor_is_explicitly_read_only_and_always_rolls_back(monkeypatch) -> None:
    class Cursor:
        def __init__(self) -> None:
            self.executed: list[tuple[str, object]] = []

        def execute(self, sql, params=None) -> None:
            self.executed.append((sql, params))

    class Connection:
        def __init__(self) -> None:
            self.value = Cursor()
            self.rolled_back = False
            self.closed = False

        def cursor(self):
            return self.value

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    connection = Connection()
    monkeypatch.setattr(
        _queries.psycopg, "connect", lambda *args, **kwargs: connection
    )

    with _queries._read_cursor(
        {"host": "db", "user": "u", "password": "p", "dbname": "research"}
    ):
        pass

    assert connection.value.executed == [
        ("begin read only", None),
        ("set local lock_timeout = '1s'", None),
        ("set local statement_timeout = '3s'", None),
    ]
    assert connection.rolled_back is True
    assert connection.closed is True


def test_fixed_resource_is_loaded_server_side(monkeypatch) -> None:
    class FakeWindmill:
        def __init__(self) -> None:
            self.paths: list[str] = []

        def get_resource(self, path: str):
            self.paths.append(path)
            return {
                "host": "db",
                "user": "reader",
                "password": "secret",
                "dbname": "research",
            }

    fake = FakeWindmill()
    monkeypatch.setattr(_queries, "wmill", fake)
    db = _queries.research_db()

    assert fake.paths == ["f/content_research/research_db"]
    assert db["user"] == "reader"


def test_case_detail_returns_only_l3_counts_and_strongly_binds_cost(monkeypatch) -> None:
    sentinel = "MCP_RAW_SENTINEL"
    executed: list[tuple[str, object]] = []
    rows = iter(
        [
            {
                "video_id": "00000000-0000-0000-0000-000000000001",
                "platform": "douyin",
                "l3_selected": True,
            },
            {
                "model": "model",
                "model_revision": "revision",
                "prompt_version": "prompt",
                "schema_version": "l3-research-v1.0.0",
                "created_at": None,
                "comment_semantics_count": 2,
                "limitations_count": 1,
                "mechanism_hypotheses_are_inferences": True,
                "api_cost": 0,
                "asr_cost": 0,
                "llm_cost": 0,
                "total_cost": 0,
                "cost_currency": "CNY",
                "cost_basis": "verified",
                # Even an unexpected row key must never cross the public boundary.
                "output": {"comment_semantics": [sentinel]},
            },
        ]
    )

    class Cursor:
        def execute(self, sql, params=None) -> None:
            executed.append((sql, params))

        def fetchone(self):
            return next(rows)

    class ReadContext:
        def __enter__(self):
            return Cursor()

        def __exit__(self, exc_type, exc, traceback) -> None:
            return None

    monkeypatch.setattr(_queries, "_read_cursor", lambda _db: ReadContext())
    result = _queries.get_case_detail(
        {"host": "db", "user": "reader", "password": "secret", "dbname": "research"},
        video_id="00000000-0000-0000-0000-000000000001",
    )

    assert result["ok"] is True
    assert result["l3_analysis"]["output_summary"] == {
        "privacy_reviewed": True,
        "comment_semantics_count": 2,
        "limitations_count": 1,
        "mechanism_hypotheses_are_inferences": True,
    }
    assert sentinel not in repr(result)

    l3_sql = executed[1][0].lower()
    assert "a.output," not in l3_sql
    assert "c.task_type='l3_structured_research'" in l3_sql
    assert "c.task_version='l3-research-v1.0.0'" in l3_sql
    assert "c.video_id=a.video_id" in l3_sql
    assert "c.input_fingerprint=a.input_fingerprint" in l3_sql
    assert "c.output_fingerprint=a.output_fingerprint" in l3_sql
