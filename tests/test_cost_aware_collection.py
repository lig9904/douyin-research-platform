from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import json

import httpx
import pytest

from douyin_research.providers.cost_accounting import quote_call
from douyin_research.providers.endpoints import get_endpoint
from douyin_research.providers.store import MemoryProviderStore
from douyin_research.providers.tikhub_provider import TikHubProvider
from douyin_research.providers.transport import TikHubTransport
from douyin_research.providers.video_fetch_plan import plan_exact_video_brief, plan_video_fetches
from douyin_research.l0l1.real_data import make_plan


@pytest.mark.parametrize("n,detail,stats", [
    (0, "0", "0"), (1, ".001", ".001"), (2, ".002", ".001"),
    (3, ".003", ".002"), (10, ".010", ".005"), (25, ".025", ".013"),
    (49, ".049", ".025"), (50, ".050", ".025"), (51, ".051", ".026"),
    (1000, "1", ".5"),
])
def test_plans_price_real_request_units(n, detail, stats):
    ids = [str(i) for i in range(n)]
    for purpose, expected in (("detail", detail), ("statistics", stats)):
        plan = plan_video_fetches(ids + ids, purpose=purpose)
        assert sum((p.estimated_cost_usd for p in plan), Decimal(0)) == Decimal(expected)
        assert [v for p in plan for v in p.video_ids] == ids
        assert all(len(p.video_ids) <= 50 for p in plan)
    if n == 49:
        assert len(plan_video_fetches(ids, purpose="statistics")) == 1


@pytest.mark.parametrize("ids", [[None], [""], ["  "], ["1,2"], [123]])
def test_invalid_ids_fail_during_pure_plan(ids):
    with pytest.raises(ValueError):
        plan_video_fetches(ids)


@pytest.mark.parametrize("count,expected_calls,expected_cost", [
    (1, 1, ".001"), (2, 2, ".002"), (3, 1, ".010"),
    (10, 1, ".010"), (11, 2, ".011"), (12, 2, ".020"), (20, 2, ".020"),
])
def test_exact_video_brief_routes_within_two_paid_calls(count, expected_calls, expected_cost):
    ids = [str(7500000000000000000 + value) for value in range(count)]
    plan = plan_exact_video_brief(ids)
    assert len(plan) == expected_calls
    assert [value for request in plan for value in request.video_ids] == ids
    assert sum((request.estimated_cost_usd for request in plan), Decimal(0)) == Decimal(expected_cost)


def test_exact_video_page_rejects_provider_extra_or_missing_ids_before_ingest():
    requested = "7521318904971578681"
    wrong = "7653684558261710777"

    def response(request):
        assert request.url.path.endswith("/fetch_one_video")
        assert request.url.params["aweme_id"] == requested
        return httpx.Response(200, json={"code": 200, "data": {"aweme_detail": {
            "aweme_id": wrong, "desc": "not requested",
        }}})

    provider, store = _provider(response)
    with pytest.raises(ValueError, match="do not match"):
        provider.fetch_exact_video_ids([requested])
    assert len(store.calls) == 1
    assert store.calls[0].status == "error"
    assert store.cache == {}
    assert store.calls[0].estimated_cost == pytest.approx(.001)


def test_exact_video_page_recovers_after_mismatched_live_response():
    requested = "7521318904971578681"
    wrong = "7653684558261710777"
    attempts = []

    def response(request):
        attempts.append(request)
        video_id = wrong if len(attempts) == 1 else requested
        return httpx.Response(200, json={"code": 200, "data": {"aweme_detail": {
            "aweme_id": video_id, "desc": "detail",
        }}})

    provider, store = _provider(response)
    with pytest.raises(ValueError, match="do not match"):
        provider.fetch_exact_video_ids([requested])
    page = provider.fetch_exact_video_ids([requested])
    assert [item.video.platform_video_id for item in page.items] == [requested]
    assert page.cached is False
    assert len(attempts) == 2
    assert [call.status for call in store.calls] == ["error", "success"]


def test_exact_video_page_ingests_only_requested_ids_and_keeps_cache_evidence():
    ids = [str(7500000000000000000 + value) for value in range(3)]
    requests = []

    def response(request):
        requests.append(request)
        assert request.url.path.endswith("/fetch_multi_video")
        batch = json.loads(request.content)
        assert batch == ids
        return httpx.Response(200, json={"code": 200, "data": {"aweme_details": [
            {"aweme_id": video_id, "desc": "exact detail"} for video_id in batch
        ]}})

    provider, store = _provider(response)
    first = provider.fetch_exact_video_ids(ids)
    second = provider.fetch_exact_video_ids(ids)
    assert [item.video.platform_video_id for item in first.items] == ids
    assert first.cached is False and second.cached is True
    assert len(requests) == 1
    assert [call.cached for call in store.calls] == [False, True]
    assert store.calls[0].estimated_cost == pytest.approx(.010)


def _provider(handler, *, retries=0):
    store = MemoryProviderStore()
    transport = TikHubTransport(
        "unit-test-not-a-key", max_retries=retries,
        http_client=httpx.Client(base_url="https://api.tikhub.io", transport=httpx.MockTransport(handler)),
    )
    return TikHubProvider(transport=transport, store=store, detail_strategy="cost_aware"), store


@pytest.mark.parametrize("retries,sdk", [(3, False), (0, True)])
def test_guard_rejects_hidden_or_multiple_attempts_before_reservation(retries, sdk):
    requests, reserved = [], []
    provider, store = _provider(lambda request: requests.append(request), retries=retries)
    provider.transport.prefer_sdk = sdk
    provider.before_external_call = lambda spec: reserved.append(spec.key)
    with pytest.raises(ValueError, match="single-attempt REST"):
        provider.fetch_videos(["1"])
    assert requests == reserved == store.calls == []


def test_guarded_rest_failure_consumes_exactly_one_reservation():
    requests, reserved = [], []

    def handler(request):
        requests.append(request)
        return httpx.Response(503)

    provider, store = _provider(handler)
    provider.before_external_call = lambda spec: reserved.append(spec.key)
    with pytest.raises(Exception, match="503"):
        provider.fetch_videos(["1"])
    assert len(requests) == len(reserved) == 1
    assert store.calls[0].metadata["http_attempt_count"] == 1


def test_three_details_use_three_single_calls_and_cache_preserves_observation_time():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path.endswith("/fetch_one_video")
        return httpx.Response(200, json={"code": 200, "data": {"aweme_detail": {
            "aweme_id": request.url.params["aweme_id"], "desc": "mock detail",
            "statistics": {"digg_count": 12},
        }}})

    provider, store = _provider(handler)
    first = provider.fetch_videos(["1", "2", "3", "1"])
    second = provider.fetch_videos(["3", "2", "1"])
    assert len(requests) == 3
    assert len(first) == len(second) == 3
    assert sum(c.estimated_cost for c in store.calls) == pytest.approx(.003)
    assert all(c.actual_cost is None for c in store.calls[:3])
    assert all(c.metadata["http_attempt_count"] == 1 for c in store.calls[:3])
    assert all(c.metadata["http_attempt_count"] == 0 for c in store.calls[3:])
    assert first[0].metrics.captured_at == second[-1].metrics.captured_at
    assert len({c.metadata["logical_call_id"] for c in store.calls}) == 6


def test_statistics_normalize_flat_counts_without_inventing_missing_fields():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path.endswith("/fetch_video_statistics")
        ids = request.url.params["aweme_ids"].split(",")
        assert len(ids) <= 2
        return httpx.Response(200, json={"code": 200, "data": {"statistics_list": [
            {"aweme_id": i, "play_count": 120, "digg_count": 5, "share_count": 0} for i in ids
        ]}})

    provider, store = _provider(handler)
    observations = provider.fetch_video_statistics(["1", "2", "3"])
    assert len(requests) == 2
    assert len(observations) == 3
    assert sum(c.estimated_cost for c in store.calls) == pytest.approx(.002)
    for item in observations:
        assert item.metrics.play_count == 120
        assert item.metrics.share_count == 0
        assert item.metrics.comment_count is None
        assert item.metrics.metric_status["comment_count"] == "unavailable"
        assert item.account is None
        assert item.video.title is None


def test_retry_timeout_is_unknown_additional_cost_not_free(monkeypatch):
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("private-url-must-not-be-logged", request=request)
        return httpx.Response(200, json={"code": 200, "data": {"aweme_id": "1"}})

    monkeypatch.setattr("douyin_research.providers.transport.time.sleep", lambda _: None)
    provider, store = _provider(handler, retries=2)
    provider.fetch_videos(["1"])
    call = store.calls[0]
    assert len(store.calls) == 1
    assert call.metadata["http_attempt_count"] == 2
    assert call.metadata["unknown_attempt_count"] == 1
    assert call.metadata["billing_status"] == "unknown"
    assert call.estimated_cost == pytest.approx(.001)
    assert call.actual_cost is None
    assert "private-url" not in str(call.metadata)


@pytest.mark.parametrize("status", [401, 429, 503])
def test_final_http_error_retains_attempt_and_proven_nonbillable_cost(status):
    provider, store = _provider(lambda _: httpx.Response(status, text="no"))
    with pytest.raises(RuntimeError):
        provider.fetch_videos(["1"])
    call = store.calls[0]
    assert call.http_status == status
    assert call.metadata["http_attempt_count"] == 1
    assert call.metadata["cost_basis"] == "nonbillable_http"
    assert call.actual_cost == 0


def test_malformed_200_still_has_quote_not_zero_cost():
    provider, store = _provider(lambda _: httpx.Response(200, text="not json"))
    with pytest.raises(ValueError):
        provider.fetch_videos(["1"])
    assert store.calls[0].http_status == 200
    assert store.calls[0].estimated_cost == pytest.approx(.001)
    assert store.calls[0].actual_cost is None


def test_unknown_quote_and_opaque_sdk_attempts_remain_unknown():
    spec = replace(get_endpoint("douyin.app.one_video"), unit_cost_usd=None)
    estimate, actual, meta = quote_call(spec, successful_response=True, attempts=({"http_status": 200},))
    assert estimate is None and actual is None
    assert meta["billing_status"] == "unknown"
    _, _, meta = quote_call(get_endpoint("douyin.app.one_video"), successful_response=True)
    assert meta["http_attempt_count"] is None
    assert meta["unknown_attempt_count"] is None


def test_cost_aware_intake_plan_explicitly_counts_extra_cheaper_requests():
    plan = make_plan(max_items=3, detail_strategy="cost_aware")
    assert plan.max_external_calls == 4
    assert plan.max_cost_usd is None
    assert plan.retry_count == 0
    with pytest.raises(ValueError, match="at least 4"):
        make_plan(max_items=3, detail_strategy="cost_aware", max_external_calls=2)
    assert make_plan(max_items=3).max_external_calls == 2
    with pytest.raises(ValueError, match="date_window_hours"):
        make_plan(date_window_hours=2)
