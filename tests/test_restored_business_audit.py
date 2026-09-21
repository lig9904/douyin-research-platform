from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts/test-server-restored-business-audit.py"
SPEC = importlib.util.spec_from_file_location("restored_business_audit", PATH)
assert SPEC and SPEC.loader
audit_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_module)


class FakeCursor:
    def __init__(self, *, missing_table: bool = False, bad_link: bool = False, full_chain: int = 1) -> None:
        self.missing_table = missing_table
        self.bad_link = bad_link
        self.full_chain = full_chain
        self.queries: list[str] = []
        self.params: list[tuple[object, ...]] = []
        self._last = ""

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, sql: str, *params: object) -> None:
        self._last = " ".join(sql.split())
        self.queries.append(self._last)
        self.params.append(params)

    def fetchone(self) -> tuple[object, ...]:
        if self._last == "show transaction_read_only":
            return ("on",)
        if self._last == "select current_database()":
            return (audit_module.RESTORE_DATABASE,)
        if "count(distinct v.id)" in self._last:
            return (self.full_chain,)
        if "left join" in self._last or "not exists" in self._last:
            return (1 if self.bad_link and "left join research_task_cost c on c.id=t.task_cost_id" in self._last else 0,)
        if self._last.startswith("select count(*)"):
            return (1,)
        raise AssertionError(self._last)

    def fetchall(self) -> list[tuple[str]]:
        tables = sorted(audit_module.REQUIRED_TABLES)
        if self.missing_table:
            tables.pop()
        return [(item,) for item in tables]


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self.cursor_value = cursor
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self.cursor_value

    def close(self) -> None:
        self.closed = True


def _dsn() -> str:
    return "postgresql://audit:secret@example.invalid/test_server_research_restore"


def test_rejects_any_non_restore_database_before_connect() -> None:
    invoked = False

    def connect(*_: object, **__: object) -> FakeConnection:
        nonlocal invoked
        invoked = True
        return FakeConnection(FakeCursor())

    with pytest.raises(audit_module.AuditError, match="fixed temporary"):
        audit_module.audit("postgresql://audit:secret@example.invalid/douyin_research", connect=connect)
    assert not invoked


def test_uses_one_read_only_transaction_and_reports_counts() -> None:
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    calls: list[dict[str, object]] = []

    def connect(*_args: object, **kwargs: object) -> FakeConnection:
        calls.append(kwargs)
        return connection

    result = audit_module.audit(_dsn(), connect=connect)

    assert result["status"] == "business_chain_present"
    assert result["counts"]["full_chain_videos"] == 1
    assert result["invalid_link_count"] == 0
    assert result["v1_release_accepted"] is False
    assert cursor.queries[:4] == [
        "begin transaction read only",
        "show transaction_read_only",
        "select current_database()",
        "set local search_path=public",
    ]
    table_query = next(index for index, query in enumerate(cursor.queries) if "information_schema.tables" in query)
    assert cursor.params[table_query][0][0] == sorted(audit_module.REQUIRED_TABLES)
    assert isinstance(cursor.params[table_query][0][0], list)
    assert calls == [{"autocommit": True}]
    assert connection.closed


def test_bad_cross_video_cost_association_is_not_accepted() -> None:
    cursor = FakeCursor(bad_link=True)
    result = audit_module.audit(_dsn(), connect=lambda *_args, **_kwargs: FakeConnection(cursor))
    assert result["status"] == "invalid_associations"
    assert result["invalid_link_count"] == 1


def test_empty_restore_db_is_explicitly_insufficient_not_a_v1_pass() -> None:
    cursor = FakeCursor(full_chain=0)
    result = audit_module.audit(_dsn(), connect=lambda *_args, **_kwargs: FakeConnection(cursor))
    assert result["status"] == "insufficient_samples"
    assert result["counts"]["full_chain_videos"] == 0
    assert result["v1_release_accepted"] is False


@pytest.mark.parametrize("statement,value,message", [
    ("show transaction_read_only", "off", "read-only"),
    ("select current_database()", "douyin_research", "not connected"),
])
def test_runtime_guards_close_connection_before_business_queries(statement, value, message) -> None:
    class GuardCursor(FakeCursor):
        def fetchone(self):
            return (value,) if self._last == statement else super().fetchone()

    cursor = GuardCursor()
    connection = FakeConnection(cursor)
    with pytest.raises(audit_module.AuditError, match=message):
        audit_module.audit(_dsn(), connect=lambda *_args, **_kwargs: connection)
    assert connection.closed
    assert not any("count(" in query for query in cursor.queries)


def test_missing_table_closes_connection_without_partial_report() -> None:
    cursor = FakeCursor(missing_table=True)
    connection = FakeConnection(cursor)
    with pytest.raises(audit_module.AuditError, match="missing"):
        audit_module.audit(_dsn(), connect=lambda *_args, **_kwargs: connection)
    assert connection.closed
    assert not any("count(" in query for query in cursor.queries)


def test_full_chain_requires_completed_costs_and_completed_analysis() -> None:
    normalized = " ".join(audit_module.FULL_CHAIN_SQL.split())
    assert "asr_cost.status='completed'" in normalized
    assert "l3_cost.status='completed'" in normalized
    assert "a.status='completed'" in normalized


def test_driver_exception_is_redacted_by_cli(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    marker = "postgresql://secret-user:secret-pass@private-host/test_server_research_restore"
    monkeypatch.setenv(audit_module.DSN_ENV, marker)

    def fail(_: str, **__: object) -> FakeConnection:
        raise RuntimeError(marker)

    monkeypatch.setattr(audit_module, "_default_connect", fail)
    # The default parameter was bound at definition time, so patch audit itself
    # to exercise the outer redaction boundary without opening any connection.
    monkeypatch.setattr(audit_module, "audit", lambda _: (_ for _ in ()).throw(RuntimeError(marker)))
    assert audit_module.main() == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert marker not in output
    assert "could not complete" in output
