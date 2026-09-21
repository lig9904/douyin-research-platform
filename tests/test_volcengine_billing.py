from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import os
from uuid import uuid4

import psycopg
import pytest

from douyin_research.providers.daily_spend import DailySpendError, upsert_supplier_daily_spend
from douyin_research.providers.volcengine_billing import parse_volcengine_daily_product_bill

NOW = datetime(2026, 9, 21, 6, 30, tzinfo=timezone.utc)
PRODUCTS = ("字节跳动大模型服务（豆包大模型）", "豆包录音文件识别模型2.0")
DSN = os.getenv("TEST_DATABASE_URL")


def _row(code, name, payable, paid="0", unpaid="0"):
    return {"Product": code, "ProductZh": name, "ExpenseDate": "2026-09-21",
            "Currency": "CNY", "PayableAmount": payable, "PaidAmount": paid,
            "UnpaidAmount": unpaid}


def _payload(rows, warning=None):
    return {"ResponseMetadata": {"RequestId": "not-persisted"},
            "Result": {"List": rows, "Total": len(rows), "Warning": warning}}


def test_parses_two_product_scopes_and_signed_adjustment_without_fake_counts():
    rows = parse_volcengine_daily_product_bill(
        _payload([
            _row("ark", PRODUCTS[0], "0.010001", paid="0.005", unpaid="0.005001"),
            _row("asr", PRODUCTS[1], "0.8", paid="0.8"),
            _row("ark", PRODUCTS[0], "-0.002", paid="-0.002"),
        ]), billing_date=date(2026, 9, 21), account_scope="primary",
        allowed_product_names=PRODUCTS, fetched_at=NOW,
    )
    assert [(row.bill_scope_key, row.scope_label) for row in rows] == [
        ("product:ark", PRODUCTS[0]), ("product:asr", PRODUCTS[1])]
    assert rows[0].total_cost == Decimal("0.008001")
    assert (rows[0].payable_cost, rows[0].paid_cost, rows[0].unpaid_cost) == (
        Decimal("0.008001"), Decimal("0.003000"), Decimal("0.005001"))
    assert rows[1].total_cost == Decimal("0.800000")
    assert all(row.total_requests is None and row.paid_requests is None for row in rows)
    assert all(row.scope_kind == "product_subset" and row.billing_finality == "preliminary" for row in rows)
    assert not hasattr(rows[0], "payload")


def test_warning_is_rejected_instead_of_overwriting_an_accepted_snapshot():
    warning = "Some filters were ignored"
    with pytest.raises(DailySpendError, match="contains a warning"):
        parse_volcengine_daily_product_bill(
            _payload([_row("ark", PRODUCTS[0], "0"), _row("asr", PRODUCTS[1], "0")], warning),
            billing_date=date(2026, 9, 21), account_scope="primary",
            allowed_product_names=PRODUCTS, fetched_at=NOW,
        )


def test_generic_writer_rejects_warning_before_connecting():
    row = parse_volcengine_daily_product_bill(
        _payload([_row("ark", PRODUCTS[0], "0"), _row("asr", PRODUCTS[1], "0")]),
        billing_date=date(2026, 9, 21), account_scope="primary",
        allowed_product_names=PRODUCTS, fetched_at=NOW,
    )[0]
    with pytest.raises(DailySpendError, match="was not stored"):
        upsert_supplier_daily_spend("must-not-connect", replace(row, source_warning="degraded"))


@pytest.mark.parametrize("mutation", ["empty", "missing_product", "unexpected_product", "wrong_date", "wrong_currency", "error", "nan", "missing_amount", "partial_page", "ambiguous_code"])
def test_rejects_incomplete_or_misleading_daily_bill(mutation):
    rows = [_row("ark", PRODUCTS[0], "0"), _row("asr", PRODUCTS[1], "0")]
    payload = _payload(rows)
    if mutation == "empty": payload["Result"]["List"] = []
    elif mutation == "missing_product": payload["Result"]["List"].pop()
    elif mutation == "unexpected_product": payload["Result"]["List"][1]["ProductZh"] = "其他产品"
    elif mutation == "wrong_date": payload["Result"]["List"][0]["ExpenseDate"] = "2026-09-20"
    elif mutation == "wrong_currency": payload["Result"]["List"][0]["Currency"] = "USD"
    elif mutation == "error": payload["ResponseMetadata"]["Error"] = {"Code": "Denied"}
    elif mutation == "nan": payload["Result"]["List"][0]["PayableAmount"] = "NaN"
    elif mutation == "missing_amount": payload["Result"]["List"][0].pop("PaidAmount")
    elif mutation == "partial_page": payload["Result"]["Total"] = 3
    elif mutation == "ambiguous_code": payload["Result"]["List"][1]["Product"] = "ark"
    with pytest.raises(DailySpendError):
        parse_volcengine_daily_product_bill(
            payload, billing_date=date(2026, 9, 21), account_scope="primary",
            allowed_product_names=PRODUCTS, fetched_at=NOW,
        )


@pytest.mark.skipif(not DSN, reason="isolated TEST_DATABASE_URL required")
def test_two_product_scopes_coexist_and_older_worker_isolated_per_scope():
    account = f"volcano-{uuid4()}"
    newer = parse_volcengine_daily_product_bill(
        _payload([_row("ark", PRODUCTS[0], "0.01", "0.01"), _row("asr", PRODUCTS[1], "0.8", "0.8")]),
        billing_date=date(2026, 9, 21), account_scope=account,
        allowed_product_names=PRODUCTS, fetched_at=NOW,
    )
    older = parse_volcengine_daily_product_bill(
        _payload([_row("ark", PRODUCTS[0], "99", "99"), _row("asr", PRODUCTS[1], "99", "99")]),
        billing_date=date(2026, 9, 21), account_scope=account,
        allowed_product_names=PRODUCTS,
        fetched_at=datetime(2026, 9, 21, 6, 0, tzinfo=timezone.utc),
    )
    try:
        assert [upsert_supplier_daily_spend(DSN, row) for row in newer] == [True, True]
        assert [upsert_supplier_daily_spend(DSN, row) for row in older] == [False, False]
        with psycopg.connect(DSN) as conn:
            stored = conn.execute(
                "select bill_scope_key,total_cost,total_requests from supplier_daily_spend "
                "where provider='volcengine-billing' and account_scope=%s order by bill_scope_key",
                (account,),
            ).fetchall()
        assert stored == [("product:ark", Decimal("0.010000"), None),
                          ("product:asr", Decimal("0.800000"), None)]
    finally:
        with psycopg.connect(DSN) as conn:
            conn.execute("delete from supplier_daily_spend where provider='volcengine-billing' and account_scope=%s", (account,))
