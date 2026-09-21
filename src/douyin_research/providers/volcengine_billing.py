"""Validate Volcano daily product bill responses without performing API signing.

The billing credential and signed transport are deliberately separate from the
Ark and ASR credentials.  This module accepts only an already decoded response,
normalizes product-scoped daily rows, and never stores the raw bill payload.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping, Sequence

from .daily_spend import DailySpendError, SupplierDailySpend

VOLCENGINE_BILLING_PROVIDER = "volcengine-billing"
VOLCENGINE_BILLING_TIMEZONE = "Asia/Shanghai"
_QUANTUM = Decimal("0.000001")
_MAX_ABS_AMOUNT = Decimal("1000000000000")


def _money(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise DailySpendError("Volcano daily bill response is invalid")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise DailySpendError("Volcano daily bill response is invalid") from None
    if not amount.is_finite() or abs(amount) > _MAX_ABS_AMOUNT:
        raise DailySpendError("Volcano daily bill response is invalid")
    return amount.quantize(_QUANTUM, rounding=ROUND_HALF_UP)


def _text(value: Any, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise DailySpendError("Volcano daily bill response is invalid")
    return value.strip()


def parse_volcengine_daily_product_bill(
    payload: Mapping[str, Any],
    *,
    billing_date: date,
    account_scope: str,
    allowed_product_names: Sequence[str],
    fetched_at: datetime | None = None,
    supplier_final: bool = False,
) -> tuple[SupplierDailySpend, ...]:
    """Parse a ListBillDetail product/day response into explicit scope rows.

    Empty or partial responses are failures, not zero-cost snapshots.  Every
    requested product display name must appear so a filtered-out product cannot
    silently disappear from the daily total.
    """
    if not isinstance(payload, Mapping) or not isinstance(payload.get("Result"), Mapping):
        raise DailySpendError("Volcano daily bill response is invalid")
    metadata = payload.get("ResponseMetadata")
    if not isinstance(metadata, Mapping) or metadata.get("Error"):
        raise DailySpendError("Volcano daily bill response is invalid")
    result = payload["Result"]
    records = result.get("List")
    if not isinstance(records, list) or not records:
        raise DailySpendError("Volcano daily bill response is empty")
    expected = {_text(name) for name in allowed_product_names}
    if not expected or len(expected) != len(allowed_product_names):
        raise ValueError("allowed product names must be unique and non-empty")
    account = _text(account_scope)
    warning_value = result.get("Warning")
    if warning_value not in (None, ""):
        if not isinstance(warning_value, str) or len(warning_value) > 1000:
            raise DailySpendError("Volcano daily bill response is invalid")
        # A warning can mean a requested product/filter was ignored.  Do not
        # turn a degraded response into a stored zero or overwrite a good row.
        raise DailySpendError("Volcano daily bill response contains a warning")
    warning = None
    if not isinstance(supplier_final, bool):
        raise ValueError("supplier_final must be a boolean")
    total = result.get("Total")
    if isinstance(total, bool) or not isinstance(total, int) or total != len(records):
        raise DailySpendError("Volcano daily bill response is invalid")
    observed = fetched_at or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        raise ValueError("fetched_at must be timezone-aware")
    grouped: dict[tuple[str, str, str], list[Decimal]] = defaultdict(
        lambda: [Decimal("0"), Decimal("0"), Decimal("0")]
    )
    name_to_code: dict[str, str] = {}
    code_to_name: dict[str, str] = {}
    for record in records:
        if not isinstance(record, Mapping):
            raise DailySpendError("Volcano daily bill response is invalid")
        product_name = _text(record.get("ProductZh"))
        product_code = _text(record.get("Product"))
        if product_name not in expected:
            raise DailySpendError("Volcano daily bill contains an unexpected product")
        if name_to_code.setdefault(product_name, product_code) != product_code or code_to_name.setdefault(product_code, product_name) != product_name:
            raise DailySpendError("Volcano daily bill product identity is ambiguous")
        try:
            row_date = date.fromisoformat(_text(record.get("ExpenseDate"), maximum=10))
        except ValueError:
            raise DailySpendError("Volcano daily bill response is invalid") from None
        if row_date != billing_date:
            raise DailySpendError("Volcano daily bill date does not match request")
        currency = _text(record.get("Currency"), maximum=8).upper()
        if currency != "CNY":
            raise DailySpendError("Volcano daily bill currency is unsupported")
        amounts = grouped[(product_code, product_name, currency)]
        amounts[0] += _money(record.get("PayableAmount"))
        amounts[1] += _money(record.get("PaidAmount"))
        amounts[2] += _money(record.get("UnpaidAmount"))
    seen = {name for _, name, _ in grouped}
    if seen != expected:
        raise DailySpendError("Volcano daily bill response is incomplete")
    finality = "final" if supplier_final else "preliminary"
    return tuple(
        SupplierDailySpend(
            provider=VOLCENGINE_BILLING_PROVIDER,
            account_scope=account,
            bill_scope_key=f"product:{product_code}",
            scope_kind="product_subset",
            scope_label=product_name,
            billing_date=billing_date,
            cost_currency=currency,
            billing_timezone=VOLCENGINE_BILLING_TIMEZONE,
            total_cost=payable.quantize(_QUANTUM),
            balance_cost=None,
            free_credit_cost=None,
            payable_cost=payable.quantize(_QUANTUM),
            paid_cost=paid.quantize(_QUANTUM),
            unpaid_cost=unpaid.quantize(_QUANTUM),
            total_requests=None,
            paid_requests=None,
            billing_finality=finality,
            source_warning=warning,
            fetched_at=observed.astimezone(timezone.utc),
        )
        for (product_code, product_name, currency), (payable, paid, unpaid)
        in sorted(grouped.items())
    )
