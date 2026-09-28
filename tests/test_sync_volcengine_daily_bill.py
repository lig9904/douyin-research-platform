"""Offline contract checks for the opt-in Volcano daily bill Windmill entrypoint."""

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from douyin_research.providers.daily_spend import DailySpendError


def _script():
    path = Path("windmill/f/content_research/collectors/sync_volcengine_daily_bill.py")
    spec = importlib.util.spec_from_file_location("sync_volcengine_daily_bill_contract", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CONFIG = json.dumps({"account_scope": "payer:123", "payer_id": 123})
CREDENTIALS = json.dumps({
    "access_key_id": "AK_TEST", "secret_access_key": "SK_TEST",
})


def test_configuration_and_credentials_are_strict():
    script = _script()
    assert script._configuration(CONFIG) == ("payer:123", 123)
    assert script._credentials(CREDENTIALS) == ("AK_TEST", "SK_TEST")
    for invalid in (
        "{}", "not-json",
        json.dumps({"account_scope": "payer:123", "payer_id": True}),
        json.dumps({"account_scope": "payer:other", "payer_id": 123}),
        json.dumps({"account_scope": "payer:123", "payer_id": 123,
                    "products": {"ark": "豆包大模型"}}),
    ):
        with pytest.raises(ValueError, match="configuration is invalid"):
            script._configuration(invalid)
    with pytest.raises(ValueError, match="credentials are invalid"):
        script._credentials('{"access_key_id":"AK_TEST"}')
    with pytest.raises(ValueError, match="billing_date must be"):
        script._day("2026-9-27")


def _account_bill(*, wrong_product=False):
    rows = [{
        "PayerID": "123", "BillPeriod": "2026-09",
        "Product": "ark" if wrong_product else "", "ProductZh": "",
        "ExpenseDate": "2026-09-27", "Currency": "CNY",
        "PayableAmount": "0.23", "PaidAmount": "0.20",
        "UnpaidAmount": "0.03",
    }]
    return {
        "ResponseMetadata": {"Action": "ListBillDetail", "Service": "billing"},
        "Result": {"List": rows, "Total": len(rows)},
        "_transport_audit": {"pages": [{"offset": 0, "count": 1,
                                         "page_sha256": "a" * 64}]},
    }


def _stub_io(monkeypatch, script, *, config=CONFIG, credentials=CREDENTIALS,
             existing=frozenset(), payload=None):
    seen = {"fetches": 0, "writes": 0, "credentials": 0}

    def variable(path):
        if path == script.CONFIG_PATH:
            return config
        seen["credentials"] += 1
        return credentials

    @contextmanager
    def single_sync(_dsn, payer, day):
        assert payer == 123 and day == date(2026, 9, 27)
        yield object()

    def fetch(**kwargs):
        seen["fetches"] += 1
        assert kwargs["payer_id"] == 123
        assert kwargs["group_term"] == 3
        assert "product_codes" not in kwargs
        assert kwargs["access_key_id"] == "AK_TEST"
        return payload if payload is not None else _account_bill()

    def write(_conn, row):
        seen["writes"] += 1
        assert row.bill_scope_key == "account"
        return True

    monkeypatch.setattr(script, "_variable", variable)
    monkeypatch.setattr(script, "_dsn", lambda _db: "test-dsn")
    monkeypatch.setattr(script, "_preflight", lambda _dsn, **_kwargs: None)
    monkeypatch.setattr(script, "_single_sync", single_sync)
    monkeypatch.setattr(script, "_existing_scopes", lambda *_args: existing)
    monkeypatch.setattr(script, "_account_snapshot_is_final", lambda *_args: False)
    monkeypatch.setattr(script, "_resolve_gap_when_available", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(script, "fetch_complete_list_bill_detail", fetch)
    monkeypatch.setattr(script, "upsert_supplier_daily_spend_in_transaction", write)
    return seen


def test_verified_account_total_is_persisted_without_raw_bill(monkeypatch):
    script = _script()
    seen = _stub_io(monkeypatch, script)
    result = script.main({}, "2026-09-27")
    assert seen == {"fetches": 1, "writes": 1, "credentials": 1}
    assert result["payable_cost"] == "0.230000"
    assert result["billing_finality"] == "preliminary"
    assert result["scope_kind"] == "account_total"
    assert result["bill_scope_key"] == "account"
    assert result["pages_fetched"] == 1
    assert result["raw_provider_payload_included"] is False
    assert "SK_TEST" not in str(result)
    assert "PayerID" not in str(result)


@pytest.mark.parametrize("existing", [
    {"product:other"},
    {"product:ark"},
    {"account", "product:asr"},
])
def test_existing_product_scope_fails_before_credentials_or_http(monkeypatch, existing):
    script = _script()
    seen = _stub_io(monkeypatch, script, existing=existing)
    with pytest.raises(RuntimeError, match="require reconciliation"):
        script.main({}, "2026-09-27")
    assert seen == {"fetches": 0, "writes": 0, "credentials": 0}


@pytest.mark.parametrize("payload,error_class", [
    (_account_bill(wrong_product=True), "SnapshotRejectedError"),
    ({**_account_bill(), "Result": {"List": [], "Total": 0}}, "SnapshotRejectedError"),
])
def test_mismatched_or_empty_bill_never_writes(monkeypatch, payload, error_class):
    script = _script()
    seen = _stub_io(monkeypatch, script, payload=payload)
    with pytest.raises(getattr(script, error_class)):
        script.main({}, "2026-09-27")
    assert seen["fetches"] == 1 and seen["writes"] == 0


def test_supplier_failure_never_writes_or_exposes_secret(monkeypatch):
    script = _script()
    seen = _stub_io(monkeypatch, script)

    def fail(**_kwargs):
        raise DailySpendError("provider denied SK_TEST")

    monkeypatch.setattr(script, "fetch_complete_list_bill_detail", fail)
    with pytest.raises(RuntimeError, match="synchronization failed") as raised:
        script.main({}, "2026-09-27")
    assert "SK_TEST" not in str(raised.value)
    assert seen["writes"] == 0


def test_unissued_bill_is_classified_without_writing_zero(monkeypatch):
    script = _script()
    seen = _stub_io(monkeypatch, script)

    def unavailable(**_kwargs):
        raise DailySpendError("Volcano daily bill has no issued rows")

    monkeypatch.setattr(script, "fetch_complete_list_bill_detail", unavailable)
    with pytest.raises(script.BillUnavailableError, match="not issued yet"):
        script.main({}, "2026-09-27")
    assert seen["writes"] == 0


def test_finality_or_freshness_rejection_cannot_return_unsaved_amount(monkeypatch):
    script = _script()
    seen = _stub_io(monkeypatch, script)
    monkeypatch.setattr(script, "upsert_supplier_daily_spend_in_transaction",
                        lambda _conn, _row: False)
    with pytest.raises(RuntimeError, match="snapshot was not written"):
        script.main({}, "2026-09-27")
    assert seen["fetches"] == 1


def test_final_snapshot_skips_credentials_and_supplier_http(monkeypatch):
    script = _script()
    seen = _stub_io(monkeypatch, script)
    monkeypatch.setattr(script, "_account_snapshot_is_final", lambda *_args: True)
    result = script.main({}, "2026-09-27")
    assert result["status"] == "already_final"
    assert result["snapshot_written"] is False
    assert seen == {"fetches": 0, "writes": 0, "credentials": 0}


@pytest.mark.parametrize("failure_name,result_code", [
    ("RuntimeError", "execution_failed"),
    ("BillUnavailableError", "bill_unavailable"),
    ("SnapshotRejectedError", "snapshot_rejected"),
    ("ScopeConflictError", "scope_conflict"),
])
def test_scheduled_failure_records_gap_without_zero_snapshot(
    monkeypatch, failure_name, result_code,
):
    script = _script()
    day = date(2026, 9, 27)
    seen = {"seeded": False, "failed": [], "calls": 0}
    monkeypatch.setattr(script, "_variable", lambda _path: CONFIG)
    monkeypatch.setattr(script, "_dsn", lambda _db: "test-dsn")
    monkeypatch.setattr(script, "_preflight", lambda _dsn, **_kwargs: None)
    monkeypatch.setattr(script, "_database_time", lambda _dsn: datetime(2026, 9, 28, tzinfo=timezone.utc))
    monkeypatch.setattr(
        script, "_seed_gaps",
        lambda *_args: seen.__setitem__("seeded", True),
    )
    monkeypatch.setattr(script, "_scheduled_targets", lambda *_args: [day])

    def fail(_db, _day, *, track_gap, expected_identity):
        assert track_gap is True
        assert expected_identity == ("payer:123", 123)
        seen["calls"] += 1
        failure_class = getattr(script, failure_name, RuntimeError)
        raise failure_class("supplier unavailable")

    monkeypatch.setattr(script, "_sync_day", fail)
    monkeypatch.setattr(
        script, "_mark_gap_failed",
        lambda _dsn, _account, failed_day, result_code, _started: (
            seen["failed"].append((failed_day, result_code)) or True
        ),
    )
    with pytest.raises(RuntimeError, match="reconciliation incomplete") as raised:
        script._scheduled_sync({}, "2026-09-21")
    assert seen == {"seeded": True, "failed": [(day, result_code)], "calls": 1}
    assert "supplier unavailable" not in str(raised.value)


def test_lock_contention_never_marks_gap_failed(monkeypatch):
    script = _script()
    day = date(2026, 9, 27)
    monkeypatch.setattr(script, "_variable", lambda _path: CONFIG)
    monkeypatch.setattr(script, "_dsn", lambda _db: "test-dsn")
    monkeypatch.setattr(script, "_preflight", lambda _dsn, **_kwargs: None)
    monkeypatch.setattr(script, "_seed_gaps", lambda *_args: None)
    monkeypatch.setattr(script, "_scheduled_targets", lambda *_args: [day])
    monkeypatch.setattr(script, "_database_time", lambda _dsn: datetime(2026, 9, 28, tzinfo=timezone.utc))
    monkeypatch.setattr(
        script, "_sync_day",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(script.ConcurrentSyncError("busy")),
    )
    monkeypatch.setattr(
        script, "_mark_gap_failed",
        lambda *_args: pytest.fail("lock contention must not overwrite another worker's gap"),
    )
    with pytest.raises(RuntimeError, match="reconciliation incomplete"):
        script._scheduled_sync({}, "2026-09-21")


def test_later_success_makes_old_failure_nonfatal(monkeypatch):
    script = _script()
    day = date(2026, 9, 27)
    monkeypatch.setattr(script, "_variable", lambda _path: CONFIG)
    monkeypatch.setattr(script, "_dsn", lambda _db: "test-dsn")
    monkeypatch.setattr(script, "_preflight", lambda _dsn, **_kwargs: None)
    monkeypatch.setattr(script, "_seed_gaps", lambda *_args: None)
    monkeypatch.setattr(script, "_scheduled_targets", lambda *_args: [day])
    monkeypatch.setattr(script, "_database_time", lambda _dsn: datetime(2026, 9, 28, tzinfo=timezone.utc))
    monkeypatch.setattr(
        script, "_sync_day",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(script.SnapshotRejectedError("old failure")),
    )
    monkeypatch.setattr(script, "_mark_gap_failed", lambda *_args: False)
    result = script._scheduled_sync({}, "2026-09-21")
    assert result["status"] == "completed"
    assert result["snapshots_written"] == 0
    assert result["covered_by_other_run_dates"] == [day.isoformat()]


def test_scheduled_run_rejects_config_drift_before_fetch(monkeypatch):
    script = _script()
    seen = _stub_io(monkeypatch, script)
    with pytest.raises(RuntimeError, match="configuration changed"):
        script._sync_day({}, date(2026, 9, 27), track_gap=True,
                         expected_identity=("payer:other", 456))
    assert seen == {"fetches": 0, "writes": 0, "credentials": 0}


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="isolated PostgreSQL required")
def test_real_database_entrypoint_preserves_account_total_and_finality(monkeypatch):
    """Exercise preflight, advisory lock and actual write without supplier I/O."""
    script = _script()
    dsn = os.environ["TEST_DATABASE_URL"]
    payer = int(uuid4().hex[:12], 16)
    account = f"payer:{payer}"
    day = date(2026, 9, 27)
    config = {"account_scope": account, "payer_id": payer}
    seen = {"fetches": 0}

    def variable(path):
        return json.dumps(config) if path == script.CONFIG_PATH else CREDENTIALS

    def fetch(**kwargs):
        seen["fetches"] += 1
        assert kwargs["payer_id"] == payer
        assert kwargs["group_term"] == 3
        payload = _account_bill()
        for row in payload["Result"]["List"]:
            row["PayerID"] = str(payer)
        return payload

    monkeypatch.setattr(script, "_dsn", lambda _db: dsn)
    monkeypatch.setattr(script, "_variable", variable)
    monkeypatch.setattr(script, "fetch_complete_list_bill_detail", fetch)
    try:
        result = script.main({}, day.isoformat())
        assert result["snapshot_written"] is True
        assert result["payable_cost"] == "0.230000"
        with psycopg.connect(dsn) as conn:
            rows = conn.execute(
                "select bill_scope_key, scope_kind, payable_cost, billing_finality "
                "from supplier_daily_spend where provider='volcengine-billing' "
                "and account_scope=%s and billing_date=%s order by bill_scope_key",
                (account, day),
            ).fetchall()
        assert [(key, kind, str(amount), finality) for key, kind, amount, finality in rows] == [
            ("account", "account_total", "0.230000", "preliminary"),
        ]
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "update supplier_daily_spend set billing_finality='final' "
                "where provider='volcengine-billing' and account_scope=%s "
                "and billing_date=%s", (account, day),
            )
        script._seed_gaps(dsn, account, day, day)
        with psycopg.connect(dsn) as conn:
            assert conn.execute(
                "select status, last_result_code from volc_billing_sync_gap "
                "where account_scope=%s and billing_date=%s", (account, day),
            ).fetchone() == ("resolved", "already_final")
        repeat = script.main({}, day.isoformat())
        assert repeat["status"] == "already_final"
        assert repeat["snapshot_written"] is False
        assert seen["fetches"] == 1
        assert script._mark_gap_failed(
            dsn, account, day, "execution_failed", script._database_time(dsn),
        ) is False
        with psycopg.connect(dsn) as conn:
            rows = conn.execute(
                "select bill_scope_key, payable_cost, billing_finality "
                "from supplier_daily_spend where provider='volcengine-billing' "
                "and account_scope=%s and billing_date=%s order by bill_scope_key",
                (account, day),
            ).fetchall()
            assert conn.execute(
                "select status from volc_billing_sync_gap "
                "where account_scope=%s and billing_date=%s", (account, day),
            ).fetchone()[0] == "resolved"
        assert [(key, str(amount), finality) for key, amount, finality in rows] == [
            ("account", "0.230000", "final"),
        ]
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "delete from supplier_daily_spend where provider='volcengine-billing' "
                "and account_scope=%s", (account,),
            )
            conn.execute(
                "delete from volc_billing_sync_gap where account_scope=%s", (account,),
            )


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="isolated PostgreSQL required")
def test_late_failure_marker_cannot_downgrade_newer_committed_snapshot(monkeypatch):
    script = _script()
    dsn = os.environ["TEST_DATABASE_URL"]
    payer = int(uuid4().hex[:12], 16)
    account = f"payer:{payer}"
    day = date(2026, 9, 27)
    config = json.dumps({"account_scope": account, "payer_id": payer})
    monkeypatch.setattr(script, "_dsn", lambda _db: dsn)
    monkeypatch.setattr(
        script, "_variable",
        lambda path: config if path == script.CONFIG_PATH else CREDENTIALS,
    )

    def fetch(**_kwargs):
        payload = _account_bill()
        payload["Result"]["List"][0]["PayerID"] = str(payer)
        return payload

    monkeypatch.setattr(script, "fetch_complete_list_bill_detail", fetch)
    try:
        script._seed_gaps(dsn, account, day, day)
        old_attempt = script._database_time(dsn)
        # A's provider attempt failed and released its lock. B then commits
        # the same day's genuine bill before A gets to mark its failure.
        assert script._sync_day({}, day, track_gap=True)["snapshot_written"] is True
        assert script._mark_gap_failed(
            dsn, account, day, "bill_unavailable", old_attempt,
        ) is False
        with psycopg.connect(dsn) as conn:
            assert conn.execute(
                "select status, attempt_count, last_result_code "
                "from volc_billing_sync_gap where account_scope=%s and billing_date=%s",
                (account, day),
            ).fetchone() == ("resolved", 1, "snapshot_written")
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "delete from supplier_daily_spend where provider='volcengine-billing' "
                "and account_scope=%s", (account,),
            )
            conn.execute("delete from volc_billing_sync_gap where account_scope=%s", (account,))


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="isolated PostgreSQL required")
def test_real_database_same_day_lock_rejects_second_worker():
    script = _script()
    dsn = os.environ["TEST_DATABASE_URL"]
    payer = int(uuid4().hex[:12], 16)
    day = date(2026, 9, 27)
    with script._single_sync(dsn, payer, day):
        with pytest.raises(script.ConcurrentSyncError, match="another Volcano"):
            with script._single_sync(dsn, payer, day):
                pytest.fail("a second worker acquired the same payer/day lock")


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="isolated PostgreSQL required")
def test_postcommit_audit_error_cannot_reopen_saved_bill(monkeypatch):
    script = _script()
    dsn = os.environ["TEST_DATABASE_URL"]
    payer = int(uuid4().hex[:12], 16)
    account = f"payer:{payer}"
    day = date(2026, 9, 27)
    config = json.dumps({"account_scope": account, "payer_id": payer})
    monkeypatch.setattr(script, "_dsn", lambda _db: dsn)
    monkeypatch.setattr(
        script, "_variable",
        lambda path: config if path == script.CONFIG_PATH else CREDENTIALS,
    )

    def fetch(**_kwargs):
        payload = _account_bill()
        payload["Result"]["List"][0]["PayerID"] = str(payer)
        del payload["_transport_audit"]
        return payload

    monkeypatch.setattr(script, "fetch_complete_list_bill_detail", fetch)
    try:
        script._seed_gaps(dsn, account, day, day)
        started = script._database_time(dsn)
        with pytest.raises(KeyError, match="_transport_audit"):
            script._sync_day({}, day, track_gap=True)
        assert script._mark_gap_failed(
            dsn, account, day, "execution_failed", started,
        ) is False
        with psycopg.connect(dsn) as conn:
            assert conn.execute(
                "select status, last_result_code from volc_billing_sync_gap "
                "where account_scope=%s and billing_date=%s", (account, day),
            ).fetchone() == ("resolved", "snapshot_written")
            assert conn.execute(
                "select count(*) from supplier_daily_spend "
                "where provider='volcengine-billing' and account_scope=%s "
                "and billing_date=%s", (account, day),
            ).fetchone()[0] == 1
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "delete from supplier_daily_spend where provider='volcengine-billing' "
                "and account_scope=%s", (account,),
            )
            conn.execute("delete from volc_billing_sync_gap where account_scope=%s", (account,))


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="isolated PostgreSQL required")
def test_seeded_old_snapshot_with_future_app_clock_does_not_hide_failure(monkeypatch):
    script = _script()
    dsn = os.environ["TEST_DATABASE_URL"]
    payer = int(uuid4().hex[:12], 16)
    account = f"payer:{payer}"
    day = date(2026, 9, 27)
    config = json.dumps({"account_scope": account, "payer_id": payer})
    monkeypatch.setattr(script, "_dsn", lambda _db: dsn)
    monkeypatch.setattr(
        script, "_variable",
        lambda path: config if path == script.CONFIG_PATH else CREDENTIALS,
    )

    def fetch(**_kwargs):
        payload = _account_bill()
        payload["Result"]["List"][0]["PayerID"] = str(payer)
        return payload

    monkeypatch.setattr(script, "fetch_complete_list_bill_detail", fetch)
    try:
        # A manual pre-045 snapshot has no gap row and its fetched_at comes
        # from an application clock, which may be ahead of PostgreSQL.
        assert script._sync_day({}, day)["snapshot_written"] is True
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "update supplier_daily_spend set fetched_at=clock_timestamp()+interval '2 days' "
                "where provider='volcengine-billing' and account_scope=%s "
                "and billing_date=%s", (account, day),
            )
        script._seed_gaps(dsn, account, day, day)
        with psycopg.connect(dsn) as conn:
            assert conn.execute(
                "select status, last_success_at from volc_billing_sync_gap "
                "where account_scope=%s and billing_date=%s", (account, day),
            ).fetchone() == ("resolved", None)
        assert script._mark_gap_failed(
            dsn, account, day, "bill_unavailable", script._database_time(dsn),
        ) is True
        with psycopg.connect(dsn) as conn:
            assert conn.execute(
                "select status, last_result_code from volc_billing_sync_gap "
                "where account_scope=%s and billing_date=%s", (account, day),
            ).fetchone() == ("pending", "bill_unavailable")
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "delete from supplier_daily_spend where provider='volcengine-billing' "
                "and account_scope=%s", (account,),
            )
            conn.execute("delete from volc_billing_sync_gap where account_scope=%s", (account,))


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="isolated PostgreSQL required")
def test_gap_survives_failure_and_bill_gap_commit_is_atomic(monkeypatch):
    script = _script()
    dsn = os.environ["TEST_DATABASE_URL"]
    payer = int(uuid4().hex[:12], 16)
    account = f"payer:{payer}"
    day = date(2026, 9, 27)
    config = json.dumps({"account_scope": account, "payer_id": payer})
    monkeypatch.setattr(script, "_dsn", lambda _db: dsn)
    monkeypatch.setattr(
        script, "_variable",
        lambda path: config if path == script.CONFIG_PATH else CREDENTIALS,
    )

    def fetch(**_kwargs):
        payload = _account_bill()
        payload["Result"]["List"][0]["PayerID"] = str(payer)
        return payload

    monkeypatch.setattr(script, "fetch_complete_list_bill_detail", fetch)
    try:
        script._seed_gaps(dsn, account, day, day)
        assert script._mark_gap_failed(
            dsn, account, day, "bill_unavailable", script._database_time(dsn),
        ) is True
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "update volc_billing_sync_gap set next_attempt_after=now()-interval '1 second' "
                "where account_scope=%s and billing_date=%s", (account, day),
            )
        assert day in script._scheduled_targets(
            dsn, account, day - timedelta(days=10), day + timedelta(days=2),
        )

        original_resolve = script._resolve_gap
        monkeypatch.setattr(
            script, "_resolve_gap",
            lambda *_args: (_ for _ in ()).throw(RuntimeError("test rollback")),
        )
        with pytest.raises(RuntimeError, match="test rollback"):
            script._sync_day({}, day, track_gap=True)
        with psycopg.connect(dsn) as conn:
            snapshot_count = conn.execute(
                "select count(*) from supplier_daily_spend where provider='volcengine-billing' "
                "and account_scope=%s and billing_date=%s", (account, day),
            ).fetchone()[0]
            status = conn.execute(
                "select status from volc_billing_sync_gap where account_scope=%s "
                "and billing_date=%s", (account, day),
            ).fetchone()[0]
        assert snapshot_count == 0 and status == "pending"

        monkeypatch.setattr(script, "_resolve_gap", original_resolve)
        # Manual repair of a scheduled pending day must clear the dashboard
        # gap in the very same transaction as the supplier snapshot.
        assert script.main({}, day.isoformat())["snapshot_written"] is True
        with psycopg.connect(dsn) as conn:
            status, result_code = conn.execute(
                "select status, last_result_code from volc_billing_sync_gap "
                "where account_scope=%s and billing_date=%s", (account, day),
            ).fetchone()
        assert (status, result_code) == ("resolved", "snapshot_written")
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "delete from supplier_daily_spend where provider='volcengine-billing' "
                "and account_scope=%s", (account,),
            )
            conn.execute(
                "delete from volc_billing_sync_gap where account_scope=%s", (account,),
            )
