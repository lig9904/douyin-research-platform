"""Read a complete Volcano Billing ListBillDetail day without persisting raw data.

This is a read-only transport.  Billing AK/SK must be supplied by a separate
server-side secret; Ark, ASR and LAS API keys cannot authenticate this API.
There are no automatic retries or redirects.  Callers must validate the full
returned bill with the parser for the requested grouping before writing it.

Contract: https://docs.volcengine.com/docs/BillingCenter/ListBillDetail-Pagequerybilldetails
Signature: https://github.com/volcengine/volc-openapi-demos/tree/main/signature
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from hashlib import sha256
from hmac import digest as hmac_digest
import json
import re
from time import sleep
from typing import Any, Mapping, Sequence

import httpx

from .daily_spend import DailySpendError


_ENDPOINT = "https://open.volcengineapi.com/"
_HOST = "open.volcengineapi.com"
_REGION = "cn-north-1"
_SERVICE = "billing"
_QUERY = "Action=ListBillDetail&Version=2022-01-01"
_SIGNED_HEADERS = "content-type;host;x-content-sha256;x-date"
_PAGE_SIZE = 300
_MAX_RECORDS = 30_000
_PRODUCT_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


def _hmac(key: bytes, data: str) -> bytes:
    return hmac_digest(key, data.encode("utf-8"), "sha256")


def sign_list_bill_detail(
    *, access_key_id: str, secret_access_key: str, body: bytes,
    requested_at: datetime,
) -> dict[str, str]:
    """Sign the exact UTF-8 POST body using Volcano OpenAPI HMAC-SHA256 v4."""
    if not isinstance(access_key_id, str) or not access_key_id.strip():
        raise ValueError("billing access key id is required")
    if not isinstance(secret_access_key, str) or not secret_access_key.strip():
        raise ValueError("billing secret access key is required")
    if not isinstance(body, bytes) or requested_at.tzinfo is None:
        raise ValueError("body bytes and timezone-aware request time are required")
    x_date = requested_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short_date = x_date[:8]
    body_hash = sha256(body).hexdigest()
    canonical_headers = (
        f"content-type:application/json\nhost:{_HOST}\n"
        f"x-content-sha256:{body_hash}\nx-date:{x_date}\n"
    )
    canonical_request = (
        f"POST\n/\n{_QUERY}\n{canonical_headers}\n"
        f"{_SIGNED_HEADERS}\n{body_hash}"
    )
    scope = f"{short_date}/{_REGION}/{_SERVICE}/request"
    string_to_sign = (
        f"HMAC-SHA256\n{x_date}\n{scope}\n"
        f"{sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )
    key = _hmac(secret_access_key.encode("utf-8"), short_date)
    key = _hmac(key, _REGION)
    key = _hmac(key, _SERVICE)
    key = _hmac(key, "request")
    signature = _hmac(key, string_to_sign).hex()
    return {
        "Content-Type": "application/json",
        "Host": _HOST,
        "X-Content-Sha256": body_hash,
        "X-Date": x_date,
        "Authorization": (
            f"HMAC-SHA256 Credential={access_key_id.strip()}/{scope}, "
            f"SignedHeaders={_SIGNED_HEADERS}, Signature={signature}"
        ),
    }


def _body(
    billing_date: date, payer_id: int, product_codes: Sequence[str], offset: int,
    group_term: int,
) -> bytes:
    request: dict[str, Any] = {
        "BillPeriod": billing_date.strftime("%Y-%m"),
        "ExpenseDate": billing_date.isoformat(),
        "GroupTerm": group_term,
        "GroupPeriod": 1,
        "PayerID": [payer_id],
        "IgnoreZero": 0,
        "NeedRecordNum": 1,
        "Limit": _PAGE_SIZE,
        "Offset": offset,
    }
    if product_codes:
        request["Product"] = list(product_codes)
    return json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def fetch_complete_list_bill_detail(
    *, billing_date: date, payer_id: int, access_key_id: str,
    secret_access_key: str, product_codes: Sequence[str] = (),
    group_term: int = 2,
    client: httpx.Client | None = None, requested_at: datetime | None = None,
    maximum_records: int = _MAX_RECORDS,
) -> Mapping[str, Any]:
    """Return one complete day of product or account rows; never infer zero.

    The result is intentionally in-memory and includes only the provider's
    decoded response shape so the matching strict parser can reject mismatched
    scope, dates/currencies or degraded responses.  Callers must not log or
    persist this raw mapping.
    """
    if not isinstance(billing_date, date) or isinstance(billing_date, datetime):
        raise ValueError("billing_date must be a date")
    if isinstance(payer_id, bool) or not isinstance(payer_id, int) or payer_id <= 0:
        raise ValueError("a positive billing payer_id is required")
    if isinstance(product_codes, (str, bytes)) or any(
        not isinstance(code, str) or not _PRODUCT_CODE.fullmatch(code)
        for code in product_codes
    ) or len(set(product_codes)) != len(product_codes):
        raise ValueError("product codes must be distinct provider codes")
    if isinstance(group_term, bool) or group_term not in (2, 3):
        raise ValueError("group_term must be product (2) or account (3)")
    if group_term == 3 and product_codes:
        raise ValueError("account daily bill must not filter products")
    if not 1 <= maximum_records <= _MAX_RECORDS:
        raise ValueError("maximum_records is outside the supported range")
    when = requested_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        raise ValueError("requested_at must be timezone-aware")
    http_client = client or httpx.Client(
        timeout=15.0, follow_redirects=False, trust_env=False,
    )
    close_client = client is None
    rows: list[Mapping[str, Any]] = []
    expected_total: int | None = None
    seen_row_identities: set[tuple[str, str, str, str]] = set()
    page_audit: list[dict[str, Any]] = []
    offset = 0
    try:
        while expected_total is None or offset < expected_total:
            body = _body(billing_date, payer_id, product_codes, offset, group_term)
            headers = sign_list_bill_detail(
                access_key_id=access_key_id, secret_access_key=secret_access_key,
                body=body, requested_at=when,
            )
            try:
                response = http_client.post(
                    _ENDPOINT + "?" + _QUERY, content=body, headers=headers,
                    follow_redirects=False, timeout=15.0,
                )
                if response.status_code != 200:
                    raise DailySpendError("Volcano billing request failed")
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                raise DailySpendError("Volcano billing request failed") from None
            if not isinstance(payload, Mapping):
                raise DailySpendError("Volcano billing response is invalid")
            metadata = payload.get("ResponseMetadata")
            result = payload.get("Result")
            if (not isinstance(metadata, Mapping) or metadata.get("Error")
                or metadata.get("Action") != "ListBillDetail"
                or metadata.get("Service") != _SERVICE
                or not isinstance(result, Mapping)):
                raise DailySpendError("Volcano billing response is invalid")
            if result.get("Warning") not in (None, ""):
                raise DailySpendError("Volcano billing response is degraded")
            total = result.get("Total")
            page = result.get("List")
            if (isinstance(total, bool) or not isinstance(total, int)
                or total < 0 or total > maximum_records
                or not isinstance(page, list) or len(page) > _PAGE_SIZE
                or any(not isinstance(row, Mapping) for row in page)
                or result.get("Offset", offset) != offset
                or result.get("Limit", _PAGE_SIZE) not in (_PAGE_SIZE, len(page))):
                raise DailySpendError("Volcano billing pagination is invalid")
            if expected_total is None:
                expected_total = total
            if group_term == 3 and total != 1:
                raise DailySpendError("Volcano account daily bill is not a single row")
            if total != expected_total or offset + len(page) > total:
                raise DailySpendError("Volcano billing pagination changed during fetch")
            if total == 0:
                raise DailySpendError("Volcano daily bill has no issued rows")
            if len(page) != min(_PAGE_SIZE, total - offset):
                raise DailySpendError("Volcano billing page is missing")
            for row in page:
                if str(row.get("PayerID", "")) != str(payer_id):
                    raise DailySpendError("Volcano billing payer does not match request")
                # Reject changed-amount copies as well as byte-identical copies.
                # Account mode also requires exactly one row for this payer/day.
                identity = tuple(str(row.get(key, "")) for key in (
                    "PayerID", "ExpenseDate", "Product", "Currency",
                ))
                if identity in seen_row_identities:
                    raise DailySpendError("Volcano billing product/day row is duplicated")
                seen_row_identities.add(identity)
            request_id = metadata.get("RequestId")
            if request_id is not None and (
                not isinstance(request_id, str) or len(request_id) > 256
            ):
                raise DailySpendError("Volcano billing request id is invalid")
            page_audit.append({
                "offset": offset,
                "count": len(page),
                "request_id": request_id,
                "page_sha256": sha256(response.content).hexdigest(),
            })
            rows.extend(page)
            offset += _PAGE_SIZE
            if offset < total:
                # Billing documents a 5 QPS ceiling.  No retry or replay occurs.
                sleep(0.21)
        if expected_total is None or len(rows) != expected_total:
            raise DailySpendError("Volcano billing pagination is incomplete")
        return {
            "ResponseMetadata": {"Action": "ListBillDetail", "Service": _SERVICE},
            "Result": {"List": rows, "Total": expected_total},
            "_transport_audit": {
                "billing_date": billing_date.isoformat(), "payer_id": str(payer_id),
                "product_codes": list(product_codes), "group_term": group_term,
                "fetched_at": when.isoformat(),
                "pages": page_audit,
            },
        }
    finally:
        if close_client:
            http_client.close()
