"""Reconcile one supplier's reported daily total without storing bill payloads.

TikHub's daily-usage endpoint is free.  This module deliberately records its
daily aggregate as a supplier snapshot rather than attempting to apportion a
bill to individual API calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import math

import httpx
import psycopg


TIKHUB_DAILY_USAGE_URL = "https://api.tikhub.io/api/v1/tikhub/user/get_user_daily_usage"
TIKHUB_PROVIDER = "tikhub"
TIKHUB_CURRENCY = "USD"
_AMOUNT_QUANTUM = Decimal("0.000001")
_MAX_REQUESTS = 1_000_000_000


class DailySpendError(RuntimeError):
    """A safe, non-provider-specific daily spend synchronization failure."""


@dataclass(frozen=True, slots=True)
class SupplierDailySpend:
    provider: str
    account_scope: str
    bill_scope_key: str
    scope_kind: str
    scope_label: str
    billing_date: date
    cost_currency: str
    billing_timezone: str
    total_cost: Decimal
    balance_cost: Decimal | None
    free_credit_cost: Decimal | None
    payable_cost: Decimal | None
    paid_cost: Decimal | None
    unpaid_cost: Decimal | None
    total_requests: int | None
    paid_requests: int | None
    billing_finality: str
    source_warning: str | None
    fetched_at: datetime


def _amount(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise DailySpendError("daily usage response is invalid")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise DailySpendError("daily usage response is invalid") from exc
    if not amount.is_finite() or amount < 0:
        raise DailySpendError("daily usage response is invalid")
    # TikHub commonly serializes a binary float.  Store a stable money value,
    # not an implementation-specific tail such as 0.050000000000000003.
    try:
        return amount.quantize(_AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise DailySpendError("daily usage response is invalid") from exc


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DailySpendError("daily usage response is invalid")
    if value < 0 or value > _MAX_REQUESTS:
        raise DailySpendError("daily usage response is invalid")
    return value


def _billing_date(value: Any, fetched_at: datetime, billing_timezone: ZoneInfo) -> date:
    if not isinstance(value, str):
        raise DailySpendError("daily usage response is invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DailySpendError("daily usage response is invalid") from exc
    # This is a live daily endpoint, not a historical backfill interface.
    # The supplier's reported timezone, not UTC, determines its current day.
    # Keep the prior day's closing snapshot only during the first two elapsed
    # hours after local midnight; the Windmill job reports rollover separately.
    local_time = fetched_at.astimezone(billing_timezone)
    today = local_time.date()
    midnight_utc = datetime.combine(today, time.min, billing_timezone).astimezone(timezone.utc)
    if parsed != today and not (
        parsed == today - timedelta(days=1)
        and fetched_at - midnight_utc < timedelta(hours=2)
    ):
        raise DailySpendError("daily usage response is invalid")
    return parsed


def parse_tikhub_daily_usage(
    payload: Mapping[str, Any],
    *,
    account_scope: str = "default",
    fetched_at: datetime | None = None,
) -> SupplierDailySpend:
    """Validate the documented TikHub daily aggregate response.

    No omitted field has a fallback: inventing a date or a zero would turn a
    failed supplier response into misleading accounting data.
    """

    if not isinstance(payload, Mapping) or not isinstance(payload.get("data"), Mapping):
        raise DailySpendError("daily usage response is invalid")
    if payload.get("code") != 200:
        raise DailySpendError("daily usage response is invalid")
    if not isinstance(account_scope, str) or not account_scope.strip():
        raise ValueError("account_scope is required")
    observed_at = fetched_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")
    observed_at = observed_at.astimezone(timezone.utc)
    data = payload["data"]
    timezone_name = payload.get("time_zone")
    if not isinstance(timezone_name, str) or not timezone_name.strip() or len(timezone_name) > 128:
        raise DailySpendError("daily usage response is invalid")
    try:
        billing_timezone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise DailySpendError("daily usage response timezone is invalid") from None
    total_requests = _count(data.get("total_request_per_day"))
    paid_requests = _count(data.get("paid_request_per_day"))
    if paid_requests > total_requests:
        raise DailySpendError("daily usage response is invalid")
    return SupplierDailySpend(
        provider=TIKHUB_PROVIDER,
        account_scope=account_scope.strip(),
        bill_scope_key="account",
        scope_kind="account_total",
        scope_label="账户总费用",
        billing_date=_billing_date(data.get("date"), observed_at, billing_timezone),
        cost_currency=TIKHUB_CURRENCY,
        billing_timezone=timezone_name.strip(),
        total_cost=_amount(data.get("usage")),
        balance_cost=_amount(data.get("balance_usage")),
        free_credit_cost=_amount(data.get("free_credit_usage")),
        payable_cost=None,
        paid_cost=None,
        unpaid_cost=None,
        total_requests=total_requests,
        paid_requests=paid_requests,
        billing_finality="preliminary",
        source_warning=None,
        fetched_at=observed_at,
    )


def fetch_tikhub_daily_usage(
    api_key: str,
    *,
    account_scope: str = "default",
    timeout_seconds: float = 15.0,
    client: httpx.Client | None = None,
    fetched_at: datetime | None = None,
) -> SupplierDailySpend:
    """Make exactly one zero-retry REST request and validate the result."""

    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("TikHub API key is required")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    owned_client = client is None
    http_client = client or httpx.Client(timeout=float(timeout_seconds))
    try:
        response = http_client.get(
            TIKHUB_DAILY_USAGE_URL,
            headers={"Authorization": f"Bearer {api_key.strip()}", "Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Deliberately do not copy a response body, request id, or key into a
        # scheduled job error or database field.
        raise DailySpendError("TikHub daily usage request failed") from None
    finally:
        if owned_client:
            http_client.close()
    return parse_tikhub_daily_usage(payload, account_scope=account_scope, fetched_at=fetched_at)


_UPSERT_DAILY_SPEND = """
            insert into supplier_daily_spend (
              provider, account_scope, bill_scope_key, scope_kind, scope_label,
              billing_date, cost_currency, billing_timezone, total_cost,
              balance_cost, free_credit_cost, payable_cost, paid_cost, unpaid_cost,
              total_requests, paid_requests, billing_finality, source_warning, fetched_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (provider, account_scope, bill_scope_key, billing_date, cost_currency) do update set
              scope_kind = excluded.scope_kind,
              scope_label = excluded.scope_label,
              billing_timezone = excluded.billing_timezone,
              total_cost = excluded.total_cost,
              balance_cost = excluded.balance_cost,
              free_credit_cost = excluded.free_credit_cost,
              payable_cost = excluded.payable_cost,
              paid_cost = excluded.paid_cost,
              unpaid_cost = excluded.unpaid_cost,
              total_requests = excluded.total_requests,
              paid_requests = excluded.paid_requests,
              billing_finality = excluded.billing_finality,
              source_warning = excluded.source_warning,
              fetched_at = excluded.fetched_at
            where supplier_daily_spend.fetched_at <= excluded.fetched_at
              and (supplier_daily_spend.billing_finality <> 'final'
                   or excluded.billing_finality = 'final')
            returning fetched_at
            """


def _snapshot_values(snapshot: SupplierDailySpend) -> tuple[Any, ...]:
    if snapshot.source_warning:
        raise DailySpendError("supplier daily spend contains a warning and was not stored")
    return (
        snapshot.provider, snapshot.account_scope, snapshot.bill_scope_key,
        snapshot.scope_kind, snapshot.scope_label, snapshot.billing_date,
        snapshot.cost_currency, snapshot.billing_timezone, snapshot.total_cost,
        snapshot.balance_cost, snapshot.free_credit_cost, snapshot.payable_cost,
        snapshot.paid_cost, snapshot.unpaid_cost, snapshot.total_requests,
        snapshot.paid_requests, snapshot.billing_finality,
        snapshot.source_warning, snapshot.fetched_at,
    )


def upsert_supplier_daily_spend(dsn: str, snapshot: SupplierDailySpend) -> bool:
    """Write one snapshot, preserving newer observations and supplier finality."""

    values = _snapshot_values(snapshot)
    with psycopg.connect(dsn) as conn:
        return _upsert_supplier_daily_spend_values(conn, values)


def upsert_supplier_daily_spend_in_transaction(
    conn: psycopg.Connection, snapshot: SupplierDailySpend,
) -> bool:
    """Write within the caller's transaction so bill and gap resolve together."""

    return _upsert_supplier_daily_spend_values(conn, _snapshot_values(snapshot))


def _upsert_supplier_daily_spend_values(
    conn: psycopg.Connection, values: tuple[Any, ...],
) -> bool:
    with conn.cursor() as cur:
        cur.execute(_UPSERT_DAILY_SPEND, values)
        return cur.fetchone() is not None


def upsert_supplier_daily_spend_batch(
    dsn: str, snapshots: Sequence[SupplierDailySpend],
) -> tuple[bool, ...]:
    """Commit a complete product/day set atomically, or leave it unchanged."""

    if not snapshots:
        raise ValueError("a non-empty supplier bill batch is required")
    keys = [(
        item.provider, item.account_scope, item.bill_scope_key,
        item.billing_date, item.cost_currency,
    ) for item in snapshots]
    if len(keys) != len(set(keys)):
        raise ValueError("supplier bill batch contains duplicate scopes")
    scopes = {(
        item.provider, item.account_scope, item.billing_date, item.fetched_at,
        item.billing_timezone, item.billing_finality,
    ) for item in snapshots}
    if len(scopes) != 1:
        raise ValueError("supplier bill batch must share one account, day, timezone, finality and fetch time")
    values = tuple(_snapshot_values(item) for item in snapshots)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for _, item_values in sorted(zip(keys, values), key=lambda entry: entry[0]):
            cur.execute(_UPSERT_DAILY_SPEND, item_values)
            if cur.fetchone() is None:
                # An older observation or preliminary replacement of a final
                # row invalidates the entire product/day snapshot.
                conn.rollback()
                return (False,) * len(values)
    return (True,) * len(values)


def sync_tikhub_daily_spend(
    dsn: str,
    api_key: str,
    *,
    account_scope: str = "default",
    timeout_seconds: float = 15.0,
    client: httpx.Client | None = None,
    fetched_at: datetime | None = None,
) -> tuple[SupplierDailySpend, bool]:
    """Fetch then persist; a fetch failure never creates a synthetic zero row."""

    snapshot = fetch_tikhub_daily_usage(
        api_key,
        account_scope=account_scope,
        timeout_seconds=timeout_seconds,
        client=client,
        fetched_at=fetched_at,
    )
    return snapshot, upsert_supplier_daily_spend(dsn, snapshot)
