from datetime import date, datetime, timezone
from hashlib import sha256
import json

import httpx
import pytest

from douyin_research.providers.daily_spend import DailySpendError
from douyin_research.providers.volcengine_billing import parse_volcengine_daily_product_bill
from douyin_research.providers.volcengine_billing_transport import (
    fetch_complete_list_bill_detail,
    sign_list_bill_detail,
)


DAY = date(2026, 9, 27)
WHEN = datetime(2026, 9, 27, 10, 11, 12, tzinfo=timezone.utc)
PAYER = 123456789


def row(code: str, name: str, *, amount: str = "0.01", payer: int = PAYER):
    return {
        "PayerID": str(payer), "Product": code, "ProductZh": name,
        "ExpenseDate": DAY.isoformat(), "Currency": "CNY",
        "PayableAmount": amount, "PaidAmount": amount, "UnpaidAmount": "0",
    }


def response(rows, *, total=None, offset=0, warning=None):
    return {
        "ResponseMetadata": {"Action": "ListBillDetail", "Service": "billing",
                             "RequestId": f"request-{offset}"},
        "Result": {"List": rows, "Total": len(rows) if total is None else total,
                   "Limit": 300, "Offset": offset, "Warning": warning},
    }


def fetch(handler, **overrides):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return fetch_complete_list_bill_detail(
            billing_date=DAY, payer_id=PAYER, access_key_id="AK_TEST",
            secret_access_key="SK_TEST", requested_at=WHEN,
            client=client, **overrides,
        )


def test_signing_uses_exact_body_hash_and_billing_scope():
    body = b'{"BillPeriod":"2026-09"}'
    headers = sign_list_bill_detail(
        access_key_id="AK_TEST", secret_access_key="SK_TEST",
        body=body, requested_at=WHEN,
    )
    assert headers["X-Date"] == "20260927T101112Z"
    assert headers["X-Content-Sha256"] == sha256(body).hexdigest()
    assert headers["Host"] == "open.volcengineapi.com"
    assert headers["Authorization"].startswith(
        "HMAC-SHA256 Credential=AK_TEST/20260927/cn-north-1/billing/request, "
        "SignedHeaders=content-type;host;x-content-sha256;x-date, Signature="
    )
    assert "SK_TEST" not in str(headers)
    assert headers["Authorization"].split("Signature=")[1] == (
        "e1f7c760fb8d022260ac4e93368c07e713b18c5243554f147d910ec73fc91dfd"
    )


def test_two_pages_are_complete_before_parser_accepts(monkeypatch):
    monkeypatch.setattr("douyin_research.providers.volcengine_billing_transport.sleep", lambda _: None)
    seen = []

    def handler(request):
        assert request.method == "POST"
        assert str(request.url) == "https://open.volcengineapi.com/?Action=ListBillDetail&Version=2022-01-01"
        assert request.headers["authorization"].startswith("HMAC-SHA256 Credential=AK_TEST/")
        data = json.loads(request.content)
        assert data["BillPeriod"] == "2026-09"
        assert data["ExpenseDate"] == DAY.isoformat()
        assert data["GroupTerm"] == 2 and data["GroupPeriod"] == 1
        assert data["NeedRecordNum"] == 1 and data["IgnoreZero"] == 0
        assert data["PayerID"] == [PAYER]
        seen.append(data["Offset"])
        if data["Offset"] == 0:
            rows = [row(f"product-{i}", f"产品{i}") for i in range(300)]
        else:
            rows = [row("product-300", "产品300")]
        return httpx.Response(200, json=response(rows, total=301, offset=data["Offset"]))

    complete = fetch(handler)
    assert seen == [0, 300]
    assert [page["request_id"] for page in complete["_transport_audit"]["pages"]] == [
        "request-0", "request-300",
    ]
    assert len(complete["_transport_audit"]["pages"][0]["page_sha256"]) == 64
    parsed = parse_volcengine_daily_product_bill(
        complete, billing_date=DAY, account_scope=f"payer:{PAYER}",
        allowed_product_names=tuple(f"产品{i}" for i in range(301)), fetched_at=WHEN,
    )
    assert len(parsed) == 301
    assert all(str(item.payable_cost) == "0.010000" for item in parsed)


def test_account_day_query_is_unfiltered_and_keeps_provider_total():
    def handler(request):
        data = json.loads(request.content)
        assert data["GroupTerm"] == 3 and data["GroupPeriod"] == 1
        assert data["PayerID"] == [PAYER]
        assert "Product" not in data
        assert data["IgnoreZero"] == 0 and data["NeedRecordNum"] == 1
        account = row("", "", amount="0.23")
        account["BillPeriod"] = "2026-09"
        return httpx.Response(200, json=response([account]))

    complete = fetch(handler, group_term=3)
    assert complete["Result"]["Total"] == 1
    assert complete["Result"]["List"][0]["Product"] == ""
    assert complete["_transport_audit"]["group_term"] == 3


@pytest.mark.parametrize("rows", [[], [row("", ""), row("", "")]])
def test_account_day_rejects_zero_or_multiple_rows(rows):
    with pytest.raises(DailySpendError, match="account daily bill"):
        fetch(lambda _: httpx.Response(200, json=response(rows)), group_term=3)


@pytest.mark.parametrize("kind", ["empty", "partial", "short_first", "warning", "payer", "total_change", "bad_offset", "page_overlap", "page_overlap_changed", "same_page_duplicate", "wrong_action", "missing_service", "redirect", "denied"])
def test_rejects_unissued_or_incomplete_pages_without_retry(kind, monkeypatch):
    monkeypatch.setattr("douyin_research.providers.volcengine_billing_transport.sleep", lambda _: None)
    seen = []

    def handler(request):
        offset = json.loads(request.content)["Offset"]
        seen.append(offset)
        if kind == "redirect":
            return httpx.Response(302, headers={"location": "https://elsewhere.example/"})
        if kind == "denied":
            return httpx.Response(403, json={"Error": {"Code": "AccessDenied"}})
        if kind in {"wrong_action", "missing_service"}:
            payload = response([row("ark", "豆包大模型")])
            if kind == "wrong_action":
                payload["ResponseMetadata"]["Action"] = "ListBill"
            else:
                del payload["ResponseMetadata"]["Service"]
            return httpx.Response(200, json=payload)
        if kind == "empty":
            return httpx.Response(200, json=response([], total=0))
        if kind == "same_page_duplicate":
            return httpx.Response(200, json=response([
                row("ark", "豆包大模型", amount="0.01"),
                row("ark", "豆包大模型", amount="0.02"),
            ]))
        if kind == "warning":
            return httpx.Response(200, json=response([row("ark", "豆包大模型")], warning="degraded"))
        if kind == "payer":
            return httpx.Response(200, json=response([row("ark", "豆包大模型", payer=999)]))
        if kind == "bad_offset":
            return httpx.Response(200, json=response([row("ark", "豆包大模型")], offset=1))
        if kind == "short_first":
            return httpx.Response(200, json=response([row("ark", "豆包大模型")], total=301))
        if offset == 0:
            return httpx.Response(200, json=response(
                [row(f"product-{i}", f"产品{i}") for i in range(300)], total=301,
            ))
        if kind == "partial":
            return httpx.Response(200, json=response([], total=301, offset=300))
        if kind in {"page_overlap", "page_overlap_changed"}:
            return httpx.Response(200, json=response(
                [row("product-0", "产品0", amount=(
                    "0.02" if kind == "page_overlap_changed" else "0.01"
                ))], total=301, offset=300,
            ))
        return httpx.Response(200, json=response([row("asr", "录音识别")], total=302, offset=300))

    with pytest.raises(DailySpendError):
        fetch(handler)
    assert seen == ([0, 300] if kind in {
        "partial", "total_change", "page_overlap", "page_overlap_changed",
    } else [0])


def test_invalid_configuration_is_rejected_before_http():
    with pytest.raises(ValueError):
        fetch_complete_list_bill_detail(
            billing_date=DAY, payer_id=0, access_key_id="AK_TEST",
            secret_access_key="SK_TEST",
        )
    with pytest.raises(ValueError):
        fetch_complete_list_bill_detail(
            billing_date=DAY, payer_id=PAYER, access_key_id="AK_TEST",
            secret_access_key="SK_TEST", product_codes=("ark", "ark"),
        )
    with pytest.raises(ValueError, match="must not filter"):
        fetch_complete_list_bill_detail(
            billing_date=DAY, payer_id=PAYER, access_key_id="AK_TEST",
            secret_access_key="SK_TEST", group_term=3, product_codes=("ark",),
        )
