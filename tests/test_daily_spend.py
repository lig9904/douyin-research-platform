from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
import pytest

from douyin_research.providers.daily_spend import (
    DailySpendError,
    TIKHUB_DAILY_USAGE_URL,
    fetch_tikhub_daily_usage,
    parse_tikhub_daily_usage,
    upsert_supplier_daily_spend,
)


OBSERVED_AT = datetime(2026, 9, 20, 8, 30, tzinfo=timezone.utc)
DSN = os.getenv("TEST_DATABASE_URL")


def _load_sync_script():
    path = Path("windmill/f/content_research/collectors/sync_daily_spend.py")
    spec = importlib.util.spec_from_file_location("sync_daily_spend_contract", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _payload(**data_overrides):
    data = {
        "date": "2026-09-20",
        "usage": 0.050000000000000003,
        "balance_usage": 0.04,
        "free_credit_usage": 0.01,
        "total_request_per_day": 5,
        "paid_request_per_day": 2,
    }
    data.update(data_overrides)
    return {"code": 200, "time_zone": "America/Los_Angeles", "data": data}


@pytest.mark.parametrize("field,value", [("code", 500), ("time_zone", "invalid/timezone")])
def test_rejects_error_envelope_or_invalid_timezone(field, value):
    payload = _payload()
    payload[field] = value
    with pytest.raises(DailySpendError):
        parse_tikhub_daily_usage(payload, fetched_at=OBSERVED_AT)


def test_parses_documented_tikhub_daily_response_without_retaining_payload():
    snapshot = parse_tikhub_daily_usage(_payload(), fetched_at=OBSERVED_AT)
    assert snapshot.provider == "tikhub"
    assert snapshot.account_scope == "default"
    assert snapshot.billing_date.isoformat() == "2026-09-20"
    assert snapshot.billing_timezone == "America/Los_Angeles"
    assert snapshot.cost_currency == "USD"
    assert snapshot.total_cost == Decimal("0.050000")
    assert snapshot.balance_cost == Decimal("0.040000")
    assert snapshot.free_credit_cost == Decimal("0.010000")
    assert (snapshot.total_requests, snapshot.paid_requests) == (5, 2)
    assert not hasattr(snapshot, "payload")


@pytest.mark.parametrize(
    "overrides",
    [
        {"usage": -1},
        {"usage": "NaN"},
        {"total_request_per_day": -1},
        {"total_request_per_day": True},
        {"paid_request_per_day": 6},
        {"date": "not-a-date"},
        {"date": "2019-12-31"},
        {"date": "2026-09-22"},
    ],
)
def test_rejects_invalid_or_misleading_daily_response(overrides):
    with pytest.raises(DailySpendError, match="daily usage response is invalid"):
        parse_tikhub_daily_usage(_payload(**overrides), fetched_at=OBSERVED_AT)


def test_requires_all_documented_fields_and_provider_timezone():
    payload = _payload()
    payload["data"].pop("date")
    with pytest.raises(DailySpendError):
        parse_tikhub_daily_usage(payload, fetched_at=OBSERVED_AT)
    payload = _payload()
    payload.pop("time_zone")
    with pytest.raises(DailySpendError):
        parse_tikhub_daily_usage(payload, fetched_at=OBSERVED_AT)


def test_fetch_uses_one_free_rest_request_with_bearer_auth_and_no_retries():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_payload())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        snapshot = fetch_tikhub_daily_usage(
            "secret-value", client=client, fetched_at=OBSERVED_AT, timeout_seconds=4
        )
    finally:
        client.close()
    assert snapshot.total_cost == Decimal("0.050000")
    assert len(seen) == 1
    assert str(seen[0].url) == TIKHUB_DAILY_USAGE_URL
    assert seen[0].headers["authorization"] == "Bearer secret-value"


def test_fetch_failure_does_not_expose_supplier_response_text():
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500, text="private supplier detail"))
    )
    try:
        with pytest.raises(DailySpendError, match="TikHub daily usage request failed") as raised:
            fetch_tikhub_daily_usage("secret-value", client=client, fetched_at=OBSERVED_AT)
    finally:
        client.close()
    assert "private supplier detail" not in str(raised.value)
    assert "secret-value" not in str(raised.value)


def test_schema_and_windmill_contract_keep_one_daily_snapshot_not_per_call_allocation():
    schema = open("db/schema.sql", encoding="utf-8").read()
    migration = open("db/migrations/019_supplier_daily_bill_scope.sql", encoding="utf-8").read()
    script = open("windmill/f/content_research/collectors/sync_daily_spend.py", encoding="utf-8").read()
    metadata = open("windmill/f/content_research/collectors/sync_daily_spend.script.yaml", encoding="utf-8").read()
    assert "create table if not exists supplier_daily_spend" in schema
    for source in (schema, migration):
        assert "primary key (provider, account_scope, bill_scope_key, billing_date, cost_currency)" in source
        assert "paid_requests <= total_requests" in source
        assert "product_subset" in source
    assert 'API_KEY_PATH = "f/content_research/tikhub_api_key"' in script
    import re

    assert re.search(
        r'git\+https://github\.com/lig9904/douyin-research-platform@[0-9a-f]{40}"',
        script,
    )
    assert "@ce8ae1c4c3358e0064daee45a0dd35540025a6e6" in script
    for field in ("bill_scope_key", "scope_kind", "scope_label", "billing_finality"):
        assert f'"{field}"' in script
    assert "supplier daily spend scope migration is not ready" in script
    assert "REPLACE_WITH_DEPLOYED_COMMIT_SHA" not in script
    assert "concurrent_limit: 1" in metadata


def test_windmill_sync_refuses_old_schema_before_supplier_call():
    script = _load_sync_script()
    with pytest.raises(RuntimeError, match="scope migration is not ready"):
        script._require_scoped_schema({"bill_scope_key", "scope_kind"})
    script._require_scoped_schema(
        {"bill_scope_key", "scope_kind", "scope_label", "billing_finality"}
    )


def test_hourly_schedule_is_explicitly_enabled_after_deployment_preflight():
    from pathlib import Path

    schedule = Path(
        "windmill/f/content_research/collectors/sync_daily_spend.schedule.yaml"
    ).read_text(encoding="utf-8")
    assert 'schedule: "0 0 * * * *"' in schedule
    assert "timezone: Etc/UTC" in schedule
    assert "script_path: f/content_research/collectors/sync_daily_spend" in schedule
    assert "is_flow: false" in schedule
    assert "enabled: false" in schedule
    assert 'db: "$res:f/content_research/research_db"' in schedule
    assert "account_scope: default" in schedule
    assert "api_key" not in schedule


@pytest.mark.skipif(not DSN, reason="isolated TEST_DATABASE_URL required")
def test_upsert_keeps_newer_supplier_snapshot_when_an_old_worker_finishes_late():
    scope = f"daily-spend-{uuid4()}"
    newer = parse_tikhub_daily_usage(
        _payload(usage=0.052, total_request_per_day=3, paid_request_per_day=2),
        account_scope=scope,
        fetched_at=datetime(2026, 9, 20, 8, 30, tzinfo=timezone.utc),
    )
    older = parse_tikhub_daily_usage(
        _payload(usage=0.001, total_request_per_day=1, paid_request_per_day=1),
        account_scope=scope,
        fetched_at=datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc),
    )
    try:
        assert upsert_supplier_daily_spend(DSN, newer) is True
        assert upsert_supplier_daily_spend(DSN, older) is False
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(
                "select total_cost, total_requests, fetched_at from supplier_daily_spend "
                "where provider='tikhub' and account_scope=%s",
                (scope,),
            )
            cost, requests, fetched_at = cur.fetchone()
        assert cost == Decimal("0.052000")
        assert requests == 3
        assert fetched_at == newer.fetched_at
    finally:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("delete from supplier_daily_spend where provider='tikhub' and account_scope=%s", (scope,))
