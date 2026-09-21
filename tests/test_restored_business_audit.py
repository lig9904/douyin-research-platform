from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts/test-server-restored-business-audit.py"
SPEC = importlib.util.spec_from_file_location("restored_business_audit", PATH)
assert SPEC and SPEC.loader
audit_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_module)
TEST_DSN = os.getenv("TEST_DATABASE_URL")


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


def test_full_chain_requires_bound_audio_asr_and_l3_evidence() -> None:
    normalized = " ".join(audit_module.FULL_CHAIN_SQL.split())
    assert "m.kind='audio'" in normalized
    assert "asr_cost.status='completed'" in normalized
    assert "asr_cost.task_type='asr_transcription'" in normalized
    assert "aj.task_key=asr_cost.task_key" in normalized
    assert "l3_cost.status='completed'" in normalized
    assert "l3_cost.task_type='l3_structured_research'" in normalized
    assert "a.status='completed'" in normalized
    assert "a.analysis_type='l3_structured_research'" in normalized
    assert "a.analysis_level='L3'" in normalized
    assert "lj.task_key=l3_cost.task_key" in normalized
    assert "asr_cost.input_fingerprint is not distinct from m.content_sha256" in normalized
    assert "mr.asset_id=m.id" in normalized
    assert normalized.count("input_fingerprint is not distinct from") >= 4


def test_completed_execution_link_queries_reject_key_type_and_fingerprint_mismatch() -> None:
    asr = " ".join(audit_module.BAD_LINK_SQL["asr_execution_cost"].split())
    l3 = " ".join(audit_module.BAD_LINK_SQL["l3_execution_cost"].split())
    l3_result = " ".join(audit_module.BAD_LINK_SQL["l3_execution_analysis"].split())
    assert "c.task_key is distinct from j.task_key" in asr
    assert "c.task_type is distinct from 'asr_transcription'" in asr
    assert "c.input_fingerprint is distinct from j.source_fingerprint" in asr
    assert "c.task_key is distinct from j.task_key" in l3
    assert "c.task_type is distinct from 'l3_structured_research'" in l3
    assert "c.input_fingerprint is distinct from j.input_fingerprint" in l3
    assert "a.analysis_level='L3'" in l3_result
    assert "a.input_fingerprint is not distinct from j.input_fingerprint" in l3_result


def test_asr_execution_requires_an_active_approved_audio_asset() -> None:
    normalized = " ".join(audit_module.BAD_LINK_SQL["asr_execution_media_review"].split())
    assert "join asr_media_review r on r.asset_id=a.id" in normalized
    assert "a.kind='audio'" in normalized
    assert "a.content_sha256 is not distinct from j.source_fingerprint" in normalized
    assert "r.active" in normalized
    assert "r.identity_source='windmill_end_user_email_allowlist_v1'" in normalized


@pytest.fixture
def rollback_connection():
    if not TEST_DSN:
        pytest.skip("isolated TEST_DATABASE_URL not configured")
    import psycopg

    connection = psycopg.connect(TEST_DSN)
    try:
        yield connection
    finally:
        # This fixture creates only UUID-scoped synthetic rows and never clears
        # shared tables.  The rollback is intentionally unconditional.
        connection.rollback()
        connection.close()


def test_real_pg_rejects_asr_job_cost_task_key_mismatch(rollback_connection) -> None:
    """Optional SQL regression against the isolated test DB, with rollback only."""
    video_id = uuid4()
    asset_id = uuid4()
    cost_id = uuid4()
    job_id = uuid4()
    source_fingerprint = "a" * 64
    object_key = f"sha256/{source_fingerprint[:2]}/{source_fingerprint}"
    connection = rollback_connection
    connection.execute(
        "insert into source_video(id,platform,platform_video_id) values (%s,'douyin',%s)",
        (video_id, f"restore-audit-{video_id}"),
    )
    connection.execute(
        """insert into media_asset(id,video_id,kind,storage_location,bucket,object_key,
          content_sha256,size_bytes,content_type)
          values (%s,%s,'audio','restore-audit','private',%s,%s,1,'audio/wav')""",
        (asset_id, video_id, object_key, source_fingerprint),
    )
    connection.execute(
        """insert into asr_media_review(asset_id,review_version,asset_fingerprint,
          delivery_origin,reviewed_by,identity_source,active)
          values (%s,'restore-audit-v1',%s,'https://media.example.test','test',
          'windmill_end_user_email_allowlist_v1',true)""",
        (asset_id, "b" * 64),
    )
    connection.execute(
        """insert into research_task_cost(id,task_key,task_type,task_version,video_id,status,
          input_fingerprint,api_cost,asr_cost,llm_cost,cost_currency,cost_basis)
          values (%s,'cost-key','asr_transcription','v1',%s,'completed',%s,0,0,0,'CNY','actual')""",
        (cost_id, video_id, source_fingerprint),
    )
    connection.execute(
        """insert into asr_execution_job(id,task_key,video_id,provider,model_id,model_revision,
          engine_version,source_fingerprint,media_ref_fingerprint,status,cost_currency,
          budget_date,budget_key,task_cost_id)
          values (%s,'different-job-key',%s,'test','test','test','test',%s,%s,'completed','CNY',
          current_date,'test',%s)""",
        (job_id, video_id, source_fingerprint, "c" * 64, cost_id),
    )
    row = connection.execute(audit_module.BAD_LINK_SQL["asr_execution_cost"]).fetchone()
    assert row == (1,)


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
