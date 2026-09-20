"""Reconcile one supplier's reported daily total without storing bill payloads.

TikHub's daily-usage endpoint is free.  This module deliberately records its
daily aggregate as a supplier snapshot rather than attempting to apportion a
bill to individual API calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping
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
    billing_date: date
    cost_currency: str
    billing_timezone: str
    total_cost: Decimal
    balance_cost: Decimal
    free_credit_cost: Decimal
    total_requests: int
    paid_requests: int
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


def _billing_date(value: Any, fetched_at: datetime) -> date:
    if not isinstance(value, str):
        raise DailySpendError("daily usage response is invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise DailySpendError("daily usage response is invalid") from exc
    # This is a live daily endpoint, not a historical backfill interface.  A
    # small future allowance accommodates the supplier's west-coast timezone.
    if parsed < date(2020, 1, 1) or parsed > fetched_at.date() + timedelta(days=1):
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
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise DailySpendError("daily usage response timezone is invalid") from None
    total_requests = _count(data.get("total_request_per_day"))
    paid_requests = _count(data.get("paid_request_per_day"))
    if paid_requests > total_requests:
        raise DailySpendError("daily usage response is invalid")
    return SupplierDailySpend(
        provider=TIKHUB_PROVIDER,
        account_scope=account_scope.strip(),
        billing_date=_billing_date(data.get("date"), observed_at),
        cost_currency=TIKHUB_CURRENCY,
        billing_timezone=timezone_name.strip(),
        total_cost=_amount(data.get("usage")),
        balance_cost=_amount(data.get("balance_usage")),
        free_credit_cost=_amount(data.get("free_credit_usage")),
        total_requests=total_requests,
        paid_requests=paid_requests,
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


def upsert_supplier_daily_spend(dsn: str, snapshot: SupplierDailySpend) -> bool:
    """Write a daily snapshot, preserving a newer observation if it exists."""

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            insert into supplier_daily_spend (
              provider, account_scope, billing_date, cost_currency, billing_timezone,
              total_cost, balance_cost, free_credit_cost, total_requests, paid_requests, fetched_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (provider, account_scope, billing_date, cost_currency) do update set
              billing_timezone = excluded.billing_timezone,
              total_cost = excluded.total_cost,
              balance_cost = excluded.balance_cost,
              free_credit_cost = excluded.free_credit_cost,
              total_requests = excluded.total_requests,
              paid_requests = excluded.paid_requests,
              fetched_at = excluded.fetched_at
            where supplier_daily_spend.fetched_at <= excluded.fetched_at
            returning fetched_at
            """,
            (
                snapshot.provider, snapshot.account_scope, snapshot.billing_date,
                snapshot.cost_currency, snapshot.billing_timezone, snapshot.total_cost,
                snapshot.balance_cost, snapshot.free_credit_cost, snapshot.total_requests,
                snapshot.paid_requests, snapshot.fetched_at,
            ),
        )
        wrote = cur.fetchone() is not None
    return wrote


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
