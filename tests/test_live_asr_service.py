from __future__ import annotations

import hashlib
import os
import sys
from types import SimpleNamespace
from decimal import Decimal
from dataclasses import replace
from datetime import date, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from douyin_research.l0l1 import DailyBudgetGuard
from douyin_research.l2 import ASR_BUDGET_KEY, ASRProviderState, TaskCost, TranscriptEvidence, TranscriptSegment
from douyin_research.l2.live_asr_service import (
    LiveASRRequest,
    LiveASRService,
    execution_request,
    live_asr_task_key,
    reviewed_media_delivery,
)
from douyin_research.providers.execution_contracts import (
    ASR_ASYNC_CAPABILITY,
    EXECUTION_CONTRACT_VERSION,
    VerifiedExecutionContract,
)
from douyin_research.providers.volcengine_asr import (
    VOLCENGINE_ASR_BASE_URL,
    VOLCENGINE_ASR_ENGINE_VERSION,
    VOLCENGINE_ASR_MODEL_ID,
    VOLCENGINE_ASR_MODEL_REVISION,
    VOLCENGINE_ASR_PROVIDER,
)


DSN = os.getenv("TEST_DATABASE_URL")


@pytest.mark.parametrize("identity", [None, "", " ", "worker\nspoof", "x" * 129])
def test_invalid_server_identity_prevents_provider_construction(monkeypatch, identity):
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(get_variable=lambda path: identity))
    request = _request(uuid4())
    with pytest.raises(RuntimeError, match="identity unavailable"):
        LiveASRService(request.dsn).run(request,
            provider_factory=lambda *_: pytest.fail("provider must not be constructed"))


def test_identity_uses_fixed_server_variable_not_logged_in_user(monkeypatch):
    paths = []
    monkeypatch.setenv("WM_END_USER_EMAIL", "not-the-worker@example.test")
    monkeypatch.setitem(sys.modules, "wmill", SimpleNamespace(
        get_variable=lambda path: paths.append(path) or "configured-worker"))
    core = execution_request(_request(uuid4()))
    assert core.actor == "configured-worker/asr"
    assert core.trigger_source == "schedule"
    assert paths == ["f/content_research/automation_worker_identity"]


def _request(video_id: UUID, **overrides: object) -> LiveASRRequest:
    query = "Expires=123&Signature=private-signature"
    values: dict[str, object] = {
        "dsn": DSN or "postgresql://example.test/db",
        "video_id": video_id,
        "media_url": f"https://media.example.test/audio.mp3?{query}",
        "media_review_version": "media-review-v1",
        "media_query_sha256": hashlib.sha256(query.encode()).hexdigest(),
        "audio_format": "wav",
        "source_fingerprint": "source-content-fingerprint-1",
        "api_key": "private-api-key",
        "reviewed_asset_id": uuid4(),
        "source_provider": "object-storage",
        "max_polls": 0,
    }
    values.update(overrides)
    return LiveASRRequest(**values)


def test_task_key_is_stable_for_rotated_delivery_urls_and_omits_secrets():
    video_id = uuid4()
    first = live_asr_task_key(video_id, "content-fingerprint")
    second = live_asr_task_key(video_id, "content-fingerprint")
    assert first == second
    assert "content-fingerprint" not in first
    assert "https" not in first
    assert len(first) < 200


def test_signed_url_requires_exact_review_hash_and_core_request_keeps_unknown_cost(monkeypatch):
    monkeypatch.setattr("douyin_research.l2.live_asr_service._worker_identity", lambda: "scheduled-research-worker/asr")
    request = _request(uuid4())
    delivery = reviewed_media_delivery(request)
    assert "private-signature" not in repr(delivery)
    core = execution_request(request)
    assert core.estimated_api_cost is None
    assert core.estimated_asr_cost is None
    assert core.execute is True
    assert "private-api-key" not in repr(core)
    with pytest.raises(ValueError, match="SHA-256 does not match"):
        reviewed_media_delivery(_request(uuid4(), media_query_sha256="a" * 64))


class _FakeLiveProvider:
    provider_name = VOLCENGINE_ASR_PROVIDER
    max_retries = 0

    def __init__(self, submit_state: ASRProviderState, poll_states=()):
        self.contract = VerifiedExecutionContract(
            provider=VOLCENGINE_ASR_PROVIDER,
            capability=ASR_ASYNC_CAPABILITY,
            contract_version=EXECUTION_CONTRACT_VERSION,
            model_id=VOLCENGINE_ASR_MODEL_ID,
            model_revision=VOLCENGINE_ASR_MODEL_REVISION,
            base_url=VOLCENGINE_ASR_BASE_URL,
            auth_scheme="header",
            auth_header_name="X-Api-Key",
            submit_path="/api/v3/auc/bigmodel/submit",
            status_path="/api/v3/auc/bigmodel/query",
            request_schema_version="doubao-asr-file-v3-request-2026-06-26",
            response_schema_version="doubao-asr-file-v3-response-2026-06-26",
            cost_currency="CNY",
            polling_billed=False,
            max_retries=0,
            timeout_seconds=30,
            verified_source_fingerprint="a" * 64,
            production_ready=True,
        )
        self.submit_state = submit_state
        self.poll_states = list(poll_states)
        self.submit_calls = []
        self.poll_calls = []

    def submit(self, request):
        self.submit_calls.append(request)
        return self.submit_state

    def poll(self, provider_task_ref):
        self.poll_calls.append(provider_task_ref)
        return self.poll_states.pop(0)


@pytest.mark.skipif(not DSN, reason="isolated TEST_DATABASE_URL required")
def test_live_service_reuses_running_task_and_persists_unknown_cost_without_url_or_key(monkeypatch):
    monkeypatch.setattr("douyin_research.l2.live_asr_service._worker_identity", lambda: "scheduled-research-worker/asr")
    video_id = uuid4()
    scope = str(video_id)
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "insert into source_video(id, platform, platform_video_id, research_level) values (%s,'douyin',%s,2)",
            (video_id, f"live-asr-{scope}"),
        )
        conn.commit()
    DailyBudgetGuard(DSN).configure(
        provider=VOLCENGINE_ASR_PROVIDER,
        budget_key=ASR_BUDGET_KEY,
        max_requests=None,
        max_cost=None,
        cost_currency="CNY",
    )
    from douyin_research.media_assets import MediaAssetStore
    from douyin_research.media_storage import StoredMediaObject, PrivateS3MediaStorage
    from douyin_research.l2.media_review import asset_fingerprint, IDENTITY_SOURCE
    digest = "a" * 64
    asset = MediaAssetStore(DSN).record(video_id=video_id, kind="audio", storage_location="test",
        bucket="test-media", stored=StoredMediaObject(key=PrivateS3MediaStorage.object_key(digest),
            sha256=digest, size=100, content_type="audio/wav"))
    with psycopg.connect(DSN) as conn:
        conn.execute("insert into asr_media_review(asset_id,review_version,asset_fingerprint,delivery_origin,reviewed_by,identity_source) values (%s,%s,%s,%s,%s,%s)",
            (asset.id, "media-review-v1", asset_fingerprint(asset, "https://media.example.test"),
             "https://media.example.test", "synthetic-reviewer", IDENTITY_SOURCE))
    request = _request(video_id, reviewed_asset_id=asset.id, source_fingerprint=digest,
        audio_format="wav", media_url=f"https://media.example.test/{asset.bucket}/{asset.object_key}?Expires=123&Signature=private-signature")
    submitted = _FakeLiveProvider(ASRProviderState(status="submitted", provider_task_ref="private-task-ref"))
    try:
        service = LiveASRService(DSN)
        with pytest.raises(ValueError, match="missing or stale"):
            service.run(replace(request, media_review_version="unapproved"),
                provider_factory=lambda *_: pytest.fail("unapproved media reached provider"))
        first = service.run(request, provider_factory=lambda _request, _delivery: submitted)
        assert first["status"] == "submitted"
        assert first["cost_basis"] == "unknown"
        assert submitted.submit_calls

        original_day = date.today()
        class NextDay(date):
            @classmethod
            def today(cls):
                return original_day + timedelta(days=1)
        monkeypatch.setattr("douyin_research.l2.asr_execution.date", NextDay)

        evidence = TranscriptEvidence(
            asr_provider=VOLCENGINE_ASR_PROVIDER,
            model_id=VOLCENGINE_ASR_MODEL_ID,
            model_revision=VOLCENGINE_ASR_MODEL_REVISION,
            engine_version=VOLCENGINE_ASR_ENGINE_VERSION,
            source_fingerprint=request.source_fingerprint,
            text="真实链路的隔离转写",
            segments=(TranscriptSegment(0, 1000, "真实链路的隔离转写", 0.9),),
            language="zh-CN",
            audio_duration_ms=1000,
            quality_status="usable",
        )
        completed = ASRProviderState(
            status="completed",
            provider_task_ref="private-task-ref",
            evidence=evidence,
            cost=TaskCost(None, None, Decimal("0"), "CNY", "unknown"),
        )
        resumed = _FakeLiveProvider(submitted.submit_state, [completed])
        rotated_query = "Expires=456&Signature=rotated-private-signature"
        second = service.run(
            replace(request, max_polls=1,
                media_url=request.media_url.split("?")[0] + "?" + rotated_query,
                media_query_sha256=hashlib.sha256(rotated_query.encode()).hexdigest()),
            provider_factory=lambda _request, _delivery: resumed,
        )
        assert second["status"] == "completed"
        assert resumed.submit_calls == []
        assert resumed.poll_calls == ["private-task-ref"]
        assert "private-api-key" not in repr(second)
        assert "private-signature" not in repr(second)
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(
                "select api_cost, asr_cost, total_cost, cost_basis from research_task_cost where task_key=%s",
                (second["task_key"],),
            )
            assert cur.fetchone() == (None, None, None, "unknown")
            cur.execute(
                "select media_ref_fingerprint, source_fingerprint, metadata "
                "from asr_execution_job where task_key=%s",
                (second["task_key"],),
            )
            media_hash, source_fingerprint, metadata = cur.fetchone()
            assert media_hash != request.media_url
            assert "private-signature" not in media_hash
            assert source_fingerprint == request.source_fingerprint
            assert metadata["triggered_by"] == "scheduled-research-worker/asr"
            assert metadata["trigger_source"] == "schedule"
            cur.execute("select budget_date,used_requests from daily_budget where provider=%s and budget_key=%s",
                        (VOLCENGINE_ASR_PROVIDER, ASR_BUDGET_KEY))
            assert cur.fetchall() == [(original_day, 2)]
    finally:
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("delete from source_video where id=%s", (video_id,))
            cur.execute(
                "delete from daily_budget where provider=%s and budget_key=%s",
                (VOLCENGINE_ASR_PROVIDER, ASR_BUDGET_KEY),
            )
            conn.commit()
