# /// script
# requires-python = "==3.14.*"
# dependencies = ["douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@5b93ffa7681038ecbd355c5ffbbbb3b5cdb53180", "psycopg[binary]==3.3.6", "wmill==1.815.0"]
# ///
"""Reconcile one Volcano payer's reported account/day total.

The encrypted credentials and verified payer identity are separate Windmill
variables. The scheduled mode persists non-monetary gaps before calling the
supplier and revisits them across calendar windows until a bill is saved.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
import re
from time import sleep
from typing import Iterator, TypedDict
from zoneinfo import ZoneInfo

import psycopg
from psycopg.conninfo import make_conninfo

from douyin_research.providers.daily_spend import (
    DailySpendError, upsert_supplier_daily_spend_in_transaction,
)
from douyin_research.providers.volcengine_billing import (
    parse_volcengine_daily_account_bill,
)
from douyin_research.providers.volcengine_billing_transport import (
    fetch_complete_list_bill_detail,
)


CONFIG_PATH = "f/content_research/volc_billing_config"
CREDENTIALS_PATH = "f/content_research/volc_billing_credentials"
_SCOPE = re.compile(r"[A-Za-z0-9._:-]{1,160}\Z")
_REQUIRED_COLUMNS = {
    "provider", "account_scope", "bill_scope_key", "scope_kind", "scope_label",
    "billing_date", "cost_currency", "payable_cost", "paid_cost", "unpaid_cost",
    "billing_finality", "fetched_at",
}
_SCHEDULED = "scheduled"
_BILL_UNAVAILABLE_MESSAGES = {
    "Volcano daily bill has no issued rows",
    "Volcano daily bill response is empty",
}


class BillUnavailableError(RuntimeError):
    """The supplier has not issued a usable account/day row yet."""


class SnapshotRejectedError(RuntimeError):
    """The fetched account/day response failed validation or freshness."""


class ScopeConflictError(RuntimeError):
    """An incompatible product-scope snapshot needs human reconciliation."""


class ConcurrentSyncError(RuntimeError):
    """Another worker owns this payer/day; it must own the gap outcome too."""


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


def _dsn(db: postgresql) -> str:
    return make_conninfo(
        host=db["host"], port=int(db.get("port", 5432)), user=db["user"],
        password=db["password"], dbname=db["dbname"],
        sslmode=db.get("sslmode", "prefer"),
    )


def _variable(path: str) -> str:
    import wmill

    return wmill.get_variable(path)


def _configuration(raw: str) -> tuple[str, int]:
    try:
        config = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError("Volcano billing configuration is invalid") from None
    if not isinstance(config, dict) or set(config) != {"account_scope", "payer_id"}:
        raise ValueError("Volcano billing configuration is invalid")
    account = config["account_scope"]
    payer = config["payer_id"]
    if (not isinstance(account, str) or not _SCOPE.fullmatch(account)
        or isinstance(payer, bool) or not isinstance(payer, int) or payer <= 0
        or account != f"payer:{payer}"):
        raise ValueError("Volcano billing configuration is invalid")
    return account, payer


def _credentials(raw: str) -> tuple[str, str]:
    try:
        credentials = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError("Volcano billing credentials are invalid") from None
    if (not isinstance(credentials, dict)
        or set(credentials) != {"access_key_id", "secret_access_key"}
        or any(not isinstance(value, str) or not value.strip()
               for value in credentials.values())):
        raise ValueError("Volcano billing credentials are invalid")
    return credentials["access_key_id"], credentials["secret_access_key"]


def _day(value: str) -> date:
    try:
        day = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("billing_date must be YYYY-MM-DD") from None
    if not isinstance(value, str) or day.isoformat() != value or day < date(2020, 1, 1):
        raise ValueError("billing_date must be YYYY-MM-DD")
    return day


def _preflight(dsn: str, *, require_gap: bool = False) -> None:
    with psycopg.connect(dsn) as conn:
        relation = conn.execute("select to_regclass('supplier_daily_spend')").fetchone()[0]
        if relation is None:
            raise RuntimeError("supplier daily spend migration is not ready")
        columns = {row[0] for row in conn.execute(
            "select attname from pg_attribute where attrelid=%s::regclass "
            "and attnum>0 and not attisdropped", (str(relation),),
        )}
        if not _REQUIRED_COLUMNS.issubset(columns):
            raise RuntimeError("supplier daily spend migration is not ready")
        if require_gap and conn.execute(
            "select to_regclass('volc_billing_sync_gap')",
        ).fetchone()[0] is None:
            raise RuntimeError("Volcano bill gap migration is not ready")


@contextmanager
def _single_sync(dsn: str, payer_id: int, day: date) -> Iterator[psycopg.Connection]:
    lock_name = _sync_lock_name(payer_id, day)
    with psycopg.connect(dsn) as conn:
        if not conn.execute(
            "select pg_try_advisory_xact_lock(hashtext(%s))", (lock_name,),
        ).fetchone()[0]:
            raise ConcurrentSyncError("another Volcano bill synchronization is running")
        # Transaction-scoped lock is released after commit/rollback, never
        # before the bill and gap state become visible to a competing worker.
        yield conn


def _sync_lock_name(payer_id: int, day: date) -> str:
    return f"douyin_research:volc_billing:{payer_id}:{day.isoformat()}"


def _database_time(dsn: str) -> datetime:
    # Failure ordering must use the same database clock as success timestamps.
    with psycopg.connect(dsn) as conn:
        return conn.execute("select clock_timestamp()").fetchone()[0]


def _existing_scopes(
    conn: psycopg.Connection, account: str, day: date,
) -> set[str]:
    return {row[0] for row in conn.execute(
        "select bill_scope_key from supplier_daily_spend "
        "where provider='volcengine-billing' and account_scope=%s "
        "and billing_date=%s", (account, day),
    )}


def _account_snapshot_is_final(
    conn: psycopg.Connection, account: str, day: date,
) -> bool:
    row = conn.execute(
        "select billing_finality from supplier_daily_spend "
        "where provider='volcengine-billing' and account_scope=%s "
        "and bill_scope_key='account' and billing_date=%s and cost_currency='CNY'",
        (account, day),
    ).fetchone()
    return row is not None and row[0] == "final"


def _resolve_gap(
    conn: psycopg.Connection, account: str, day: date, result_code: str,
    *, required: bool = True,
) -> None:
    row = conn.execute(
        "update volc_billing_sync_gap set status='resolved', "
        "last_attempt_at=clock_timestamp(), attempt_count=attempt_count+1, "
        "next_attempt_after=null, last_success_at=clock_timestamp(), "
        "resolved_at=clock_timestamp(), "
        "last_result_code=%s where account_scope=%s and billing_date=%s "
        "returning billing_date",
        (result_code, account, day),
    ).fetchone()
    if row is None and required:
        raise RuntimeError("Volcano bill gap was not registered")


def _resolve_gap_when_available(
    conn: psycopg.Connection, account: str, day: date,
    result_code: str, *, track_gap: bool,
) -> None:
    if track_gap:
        _resolve_gap(conn, account, day, result_code)
    elif conn.execute(
        "select to_regclass('public.volc_billing_sync_gap')",
    ).fetchone()[0] is not None:
        # Manual reconciliation may repair a pending scheduled day. Keep its
        # dashboard status in the same transaction as the saved snapshot.
        _resolve_gap(conn, account, day, result_code, required=False)


def _mark_gap_failed(
    dsn: str, account: str, day: date, result_code: str,
    attempt_started_at: datetime,
) -> bool:
    # A failed supplier call never manufactures a zero-value bill. Retry on
    # the next daily cycle while preserving prior successful snapshots. The
    # same payer/day lock serializes a late failure marker with a new success.
    if attempt_started_at.tzinfo is None:
        raise ValueError("attempt_started_at must be timezone-aware")
    payer_id = int(account.removeprefix("payer:"))
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "select pg_advisory_xact_lock(hashtext(%s))",
            (_sync_lock_name(payer_id, day),),
        )
        if _account_snapshot_is_final(conn, account, day):
            _resolve_gap(conn, account, day, "already_final")
            return False
        row = conn.execute(
            "update volc_billing_sync_gap set status='pending', "
            "last_attempt_at=clock_timestamp(), attempt_count=attempt_count+1, "
            "next_attempt_after=clock_timestamp()+interval '20 hours', "
            "resolved_at=null, "
            "last_result_code=%s "
            "where account_scope=%s and billing_date=%s "
            "and (last_success_at is null or last_success_at < %s) "
            "returning billing_date",
            (result_code, account, day, attempt_started_at),
        ).fetchone()
        if row is None:
            status = conn.execute(
                "select status, last_success_at from volc_billing_sync_gap "
                "where account_scope=%s and billing_date=%s",
                (account, day),
            ).fetchone()
            if status is None:
                raise RuntimeError("Volcano bill gap was not registered")
            # A later successful worker already settled this day. If it was
            # followed by another failure, keep the existing pending state.
            return status[0] != "resolved"
    return True


def _seed_gaps(
    dsn: str, account: str, start: date, yesterday: date,
) -> None:
    if start > yesterday:
        raise ValueError("Volcano bill coverage start is after the latest eligible day")
    with psycopg.connect(dsn) as conn:
        conn.execute(
            "insert into volc_billing_sync_gap("
            "account_scope,billing_date,status,last_success_at,resolved_at,last_result_code) "
            "select %s, day::date, "
            "case when snapshot.provider is null then 'pending' else 'resolved' end, "
            # A pre-existing snapshot has no trustworthy database-side commit
            # time. Do not compare its app-clock fetched_at with future DB-clock
            # attempts; only new _resolve_gap writes a comparable success time.
            "null::timestamptz, snapshot.fetched_at, "
            "case when snapshot.provider is null then null "
            "when snapshot.billing_finality='final' then 'already_final' "
            "else 'snapshot_written' end "
            "from generate_series(%s::date,%s::date,interval '1 day') day "
            "left join supplier_daily_spend snapshot on "
            "snapshot.provider='volcengine-billing' and snapshot.account_scope=%s "
            "and snapshot.bill_scope_key='account' and snapshot.cost_currency='CNY' "
            "and snapshot.billing_date=day::date "
            "on conflict (account_scope,billing_date) do nothing",
            (account, start, yesterday, account),
        )


def _scheduled_targets(
    dsn: str, account: str, start: date, yesterday: date,
) -> list[date]:
    # Two recent days are refreshed even after a preliminary success. Up to
    # seven older pending days catch up a week of initial history or outage in
    # one run; one stale preliminary row rotates by last attempt time.
    recent = [yesterday, yesterday - timedelta(days=1)]
    with psycopg.connect(dsn) as conn:
        pending = [row[0] for row in conn.execute(
            "select billing_date from volc_billing_sync_gap "
            "where account_scope=%s and status='pending' "
            "and billing_date >= %s and billing_date < %s and "
            "(next_attempt_after is null or next_attempt_after <= now()) "
            "order by last_attempt_at nulls first, billing_date desc limit 7",
            (account, start, recent[-1]),
        )]
        preliminary = conn.execute(
            "select billing_date from supplier_daily_spend "
            "where provider='volcengine-billing' and account_scope=%s "
            "and bill_scope_key='account' and cost_currency='CNY' "
            "and billing_finality='preliminary' and billing_date >= %s "
            "and billing_date < %s "
            "and billing_date not in (select billing_date from volc_billing_sync_gap "
            "  where account_scope=%s and status='pending') "
            "order by fetched_at asc, billing_date asc limit 1",
            (account, start, recent[-1], account),
        ).fetchone()
    return list(dict.fromkeys(recent + pending + ([preliminary[0]] if preliminary else [])))


def _sync_day(
    db: postgresql, day: date, *, track_gap: bool = False,
    expected_identity: tuple[str, int] | None = None,
) -> dict[str, object]:
    """Read and save the supplier's account total, never a product-derived sum."""
    account, payer = _configuration(_variable(CONFIG_PATH))
    if expected_identity is not None and (account, payer) != expected_identity:
        raise RuntimeError("Volcano billing configuration changed during scheduled run")
    dsn = _dsn(db)
    _preflight(dsn, require_gap=track_gap)
    with _single_sync(dsn, payer, day) as conn:
        existing_scopes = _existing_scopes(conn, account, day)
        if existing_scopes - {"account"}:
            raise ScopeConflictError("existing Volcano product scopes require reconciliation")
        if _account_snapshot_is_final(conn, account, day):
            _resolve_gap_when_available(
                conn, account, day, "already_final", track_gap=track_gap,
            )
            return {
                "status": "already_final", "provider": "volcengine-billing",
                "billing_date": day.isoformat(), "snapshot_written": False,
                "raw_provider_payload_included": False,
            }
        access_key, secret_key = _credentials(_variable(CREDENTIALS_PATH))
        observed_at = datetime.now(timezone.utc)
        try:
            payload = fetch_complete_list_bill_detail(
                billing_date=day, payer_id=payer, access_key_id=access_key,
                secret_access_key=secret_key, group_term=3,
                requested_at=observed_at,
            )
        except DailySpendError as exc:
            if str(exc) in _BILL_UNAVAILABLE_MESSAGES:
                raise BillUnavailableError("Volcano account bill is not issued yet") from None
            raise RuntimeError("Volcano daily bill synchronization failed") from None
        try:
            snapshot = parse_volcengine_daily_account_bill(
                payload, billing_date=day, payer_id=payer,
                account_scope=account,
                fetched_at=observed_at, supplier_final=False,
            )
            written = upsert_supplier_daily_spend_in_transaction(conn, snapshot)
        except DailySpendError as exc:
            if str(exc) in _BILL_UNAVAILABLE_MESSAGES:
                raise BillUnavailableError("Volcano account bill is not issued yet") from None
            raise SnapshotRejectedError("Volcano account bill was rejected") from None
        if not written:
            raise SnapshotRejectedError("Volcano daily bill snapshot was not written")
        _resolve_gap_when_available(
            conn, account, day, "snapshot_written", track_gap=track_gap,
        )
    # The audit is a non-reversible digest of page hashes and request IDs; raw
    # provider rows, authorization headers and credentials never enter the job.
    audit = payload["_transport_audit"]
    audit_digest = sha256(json.dumps(
        audit["pages"], ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    return {
        "status": "completed",
        "provider": "volcengine-billing", "account_scope": account,
        "billing_date": day.isoformat(), "billing_timezone": "Asia/Shanghai",
        "scope_kind": "account_total", "bill_scope_key": "account",
        "cost_currency": "CNY",
        "payable_cost": str(snapshot.payable_cost),
        "paid_cost": str(snapshot.paid_cost),
        "unpaid_cost": str(snapshot.unpaid_cost),
        "billing_finality": "preliminary", "pages_fetched": len(audit["pages"]),
        "transport_audit_sha256": audit_digest, "snapshot_written": True,
        "raw_provider_payload_included": False,
    }


def _scheduled_sync(
    db: postgresql, coverage_start_date: str,
) -> dict[str, object]:
    start = _day(coverage_start_date)
    account, payer = _configuration(_variable(CONFIG_PATH))
    dsn = _dsn(db)
    _preflight(dsn, require_gap=True)
    yesterday = datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=1)
    _seed_gaps(dsn, account, start, yesterday)
    targets = [day for day in _scheduled_targets(dsn, account, start, yesterday) if day >= start]
    completed: list[str] = []
    failed: list[str] = []
    skipped_final: list[str] = []
    covered_by_other_run: list[str] = []
    for index, day in enumerate(targets):
        attempt_started_at = _database_time(dsn)
        try:
            result = _sync_day(
                db, day, track_gap=True, expected_identity=(account, payer),
            )
        except ConcurrentSyncError:
            # The lock owner, not this job, decides the final gap state.
            failed.append(day.isoformat())
        except Exception as exc:
            result_code = (
                "bill_unavailable" if isinstance(exc, BillUnavailableError)
                else "snapshot_rejected" if isinstance(exc, SnapshotRejectedError)
                else "scope_conflict" if isinstance(exc, ScopeConflictError)
                else "execution_failed"
            )
            try:
                unresolved = _mark_gap_failed(
                    dsn, account, day, result_code, attempt_started_at,
                )
            except Exception:
                raise RuntimeError("Volcano bill gap persistence failed") from None
            if unresolved:
                failed.append(day.isoformat())
            else:
                covered_by_other_run.append(day.isoformat())
        else:
            (skipped_final if result["status"] == "already_final" else completed).append(day.isoformat())
        if index < len(targets) - 1:
            sleep(0.25)  # This job stays below the supplier's 5 QPS limit.
    if failed:
        raise RuntimeError(
            "Volcano bill reconciliation incomplete for "
            f"{len(failed)} day(s): {', '.join(failed)}"
        )
    return {
        "status": "completed", "provider": "volcengine-billing",
        "mode": "scheduled", "snapshots_written": len(completed),
        "billing_dates": completed, "already_final_dates": skipped_final,
        "covered_by_other_run_dates": covered_by_other_run,
        "raw_provider_payload_included": False,
    }


def main(
    db: postgresql, billing_date: str,
    coverage_start_date: str = "2026-09-21",
) -> dict[str, object]:
    if billing_date == _SCHEDULED:
        return _scheduled_sync(db, coverage_start_date)
    return _sync_day(db, _day(billing_date))
