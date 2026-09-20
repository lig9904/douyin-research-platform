from __future__ import annotations

import hashlib

import httpx
import pytest

from douyin_research.l2.asr_execution import ASRProviderRequest
from douyin_research.providers.volcengine_asr import (
    VOLCENGINE_ASR_ENGINE_VERSION,
    VOLCENGINE_ASR_MODEL_ID,
    VOLCENGINE_ASR_MODEL_REVISION,
    VolcengineASRReadinessFacts,
    VolcengineDoubaoASRProvider,
    ReviewedASRMediaDelivery,
    VerifiedLiveVolcengineDoubaoASRProvider,
    _map_poll_state,
    _map_submit_state,
    _poll_wire_request,
    _raise_sanitized_http_failure,
    _submit_wire_request,
    assess_volcengine_asr_readiness,
)

SYNTHETIC_SECRET = "synthetic-secret-never-log"


def _request(**overrides) -> ASRProviderRequest:
    values = {
        "task_key": "synthetic-task",
        "media_ref": "https://media.example.test/audio.mp3",
        "source_fingerprint": "source-fingerprint",
        "model_id": VOLCENGINE_ASR_MODEL_ID,
        "model_revision": VOLCENGINE_ASR_MODEL_REVISION,
        "engine_version": VOLCENGINE_ASR_ENGINE_VERSION,
    }
    values.update(overrides)
    return ASRProviderRequest(**values)


def _facts(**overrides) -> VolcengineASRReadinessFacts:
    values = {
        "secret_configured": True,
        "model_enabled": True,
        "media_delivery_verified": True,
        "price_catalog_version": "volc-price-2026-09-19",
        "cost_reconciliation_version": "asr-cost-reconciliation-v1",
        "polling_policy_version": "volc-polling-policy-2026-09-19",
        "polling_billed": False,
    }
    values.update(overrides)
    return VolcengineASRReadinessFacts(**values)


def test_readiness_reports_each_missing_fact_without_network() -> None:
    report = assess_volcengine_asr_readiness(
        _facts(
            secret_configured=False,
            model_enabled=False,
            media_delivery_verified=False,
            price_catalog_version=None,
            cost_reconciliation_version=None,
            polling_policy_version=None,
            polling_billed=None,
        )
    )

    assert report.review_complete is False
    assert report.production_ready is False
    assert report.blockers == (
        "secret_not_configured",
        "model_not_enabled",
        "media_delivery_not_verified",
        "price_catalog_version_missing",
        "cost_reconciliation_version_missing",
        "polling_policy_version_missing",
        "polling_billing_fact_missing",
    )


def test_complete_readiness_is_still_permanently_non_production() -> None:
    first = assess_volcengine_asr_readiness(_facts())
    second = assess_volcengine_asr_readiness(_facts())

    assert first.review_complete is True
    assert first.production_ready is False
    assert first.report_fingerprint == second.report_fingerprint
    assert first.to_dict()["facts"] == {
        "secret_configured": True,
        "model_enabled": True,
        "media_delivery_verified": True,
        "price_catalog_version": "volc-price-2026-09-19",
        "cost_reconciliation_version": "asr-cost-reconciliation-v1",
        "polling_policy_version": "volc-polling-policy-2026-09-19",
        "polling_billed": False,
    }


def test_polling_cost_fact_is_explicit_and_billed_polling_blocks_review() -> None:
    report = assess_volcengine_asr_readiness(_facts(polling_billed=True))
    assert report.review_complete is False
    assert report.blockers == ("polling_billed_not_supported",)


@pytest.mark.parametrize(
    "overrides",
    [
        {"secret_configured": "yes"},
        {"model_enabled": 1},
        {"media_delivery_verified": None},
        {"price_catalog_version": ""},
    ],
)
def test_readiness_rejects_invalid_non_secret_fact_shapes(overrides) -> None:
    with pytest.raises(ValueError, match="readiness (fact|version)"):
        assess_volcengine_asr_readiness(_facts(**overrides))


def test_adapter_cannot_be_enabled_at_runtime_and_never_calls_http() -> None:
    calls = []
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: calls.append(request) or httpx.Response(200)
        )
    )
    provider = VolcengineDoubaoASRProvider(
        api_key=SYNTHETIC_SECRET,
        audio_format="mp3",
        source_fingerprint="source-fingerprint",
        client=client,
    )

    assert provider.contract.production_ready is False
    with pytest.raises(ValueError, match="not production ready"):
        provider.submit(_request())
    with pytest.raises(ValueError, match="not production ready"):
        provider._post(_poll_wire_request("synthetic-provider-task"))
    with pytest.raises(TypeError, match="production_ready"):
        VolcengineDoubaoASRProvider(
            api_key=SYNTHETIC_SECRET,
            audio_format="mp3",
            source_fingerprint="source-fingerprint",
            production_ready=True,
            client=client,
        )
    assert calls == []


def test_submit_wire_shape_excludes_secret_and_preserves_contract_mapping() -> None:
    wire = _submit_wire_request(
        _request(),
        audio_format="mp3",
        language="zh-CN",
    )

    headers = dict(wire.headers)
    assert wire.path.endswith("/submit")
    assert "X-Api-Key" not in headers
    assert headers["X-Api-Resource-Id"] == VOLCENGINE_ASR_ENGINE_VERSION
    assert headers["X-Api-Sequence"] == "-1"
    assert headers["X-Api-Request-Id"] == wire.provider_task_ref
    assert wire.payload == {
        "user": {"uid": "douyin-research-platform"},
        "audio": {
            "format": "mp3",
            "url": "https://media.example.test/audio.mp3",
            "language": "zh-CN",
        },
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": False,
            "show_utterances": True,
            "enable_emotion_detection": False,
            "enable_gender_detection": False,
        },
    }
    provider = VolcengineDoubaoASRProvider(
        api_key=SYNTHETIC_SECRET,
        audio_format="mp3",
        source_fingerprint="source-fingerprint",
    )
    submitted = _map_submit_state(
        "20000000",
        wire.provider_task_ref,
        unknown_cost=provider._unknown_cost(),
    )
    failed = _map_submit_state(
        "45000001",
        wire.provider_task_ref,
        unknown_cost=provider._unknown_cost(),
    )
    assert submitted.status == "submitted"
    assert failed.status == "failed"
    assert failed.error_code == "volcengine_45000001"


def test_poll_wire_and_status_mapping_are_pure_and_preserve_evidence() -> None:
    provider = VolcengineDoubaoASRProvider(
        api_key=SYNTHETIC_SECRET,
        audio_format="mp3",
        source_fingerprint="source-fingerprint",
        source_provider="synthetic-media",
    )
    wire = _poll_wire_request("task-ref")
    assert wire.path.endswith("/query")
    assert dict(wire.headers)["X-Api-Request-Id"] == "task-ref"
    assert "X-Api-Sequence" not in dict(wire.headers)
    assert wire.payload == {}

    running = _map_poll_state(
        "20000001",
        "task-ref",
        payload=None,
        evidence_builder=provider._evidence,
        cost=provider._unknown_cost(),
    )
    completed = _map_poll_state(
        "20000000",
        "task-ref",
        payload={
            "audio_info": {"duration": 1200},
            "result": {
                "text": "测试转写。",
                "utterances": [
                    {"start_time": 0, "end_time": 1200, "text": "测试转写。"}
                ],
            },
        },
        evidence_builder=provider._evidence,
        cost=provider._unknown_cost(),
    )
    no_speech = _map_poll_state(
        "20000003",
        "task-ref",
        payload=None,
        evidence_builder=provider._evidence,
        cost=provider._unknown_cost(),
    )
    failed = _map_poll_state(
        "45000001",
        "task-ref",
        payload=None,
        evidence_builder=provider._evidence,
        cost=provider._unknown_cost(),
    )

    assert running.status == "running"
    assert completed.status == "completed"
    assert completed.evidence is not None
    assert completed.evidence.text == "测试转写。"
    assert completed.evidence.audio_duration_ms == 1200
    assert completed.evidence.segments[0].start_ms == 0
    assert completed.evidence.segments[0].end_ms == 1200
    assert no_speech.evidence is not None
    assert no_speech.evidence.quality_status == "no_speech"
    assert failed.status == "failed"
    assert failed.error_code == "volcengine_45000001"


@pytest.mark.parametrize(
    "media_ref",
    [
        "http://media.example.test/audio.mp3",
        "https://user:password@media.example.test/audio.mp3",
        "https://media.example.test/audio.mp3#fragment",
        "https://media.example.test/audio.mp3?signature=redacted",
        "not-a-url",
    ],
)
def test_submit_wire_rejects_unsafe_media_references(media_ref) -> None:
    with pytest.raises(ValueError, match="(credential-free HTTPS|ReviewedASRMediaDelivery)"):
        _submit_wire_request(
            _request(media_ref=media_ref),
            audio_format="mp3",
            language="zh-CN",
        )


def test_wire_task_reference_is_stable_and_http_failure_is_sanitized() -> None:
    first = _submit_wire_request(_request(), audio_format="mp3", language="zh-CN")
    second = _submit_wire_request(_request(), audio_format="mp3", language="zh-CN")
    assert first.provider_task_ref == second.provider_task_ref

    with pytest.raises(RuntimeError, match="HTTP request failed") as exc:
        _raise_sanitized_http_failure()
    assert SYNTHETIC_SECRET not in repr(exc.value)
    assert "upstream-secret-diagnostic" not in repr(exc.value)


def test_secret_is_absent_from_adapter_and_readiness_report_representations() -> None:
    provider = VolcengineDoubaoASRProvider(
        api_key=SYNTHETIC_SECRET,
        audio_format="mp3",
        source_fingerprint="source-fingerprint",
    )
    report = assess_volcengine_asr_readiness(_facts())

    assert SYNTHETIC_SECRET not in repr(provider)
    assert SYNTHETIC_SECRET not in repr(report)
    assert SYNTHETIC_SECRET not in str(report.to_dict())


def _live_provider(client: httpx.Client, **overrides) -> VerifiedLiveVolcengineDoubaoASRProvider:
    values = {
        "api_key": SYNTHETIC_SECRET,
        "audio_format": "m4a",
        "source_fingerprint": "source-fingerprint",
        "reviewed_media_delivery": ReviewedASRMediaDelivery(
            url="https://vod-ai-test.tos-cn-beijing.volces.com/demo/speech.m4a",
            review_version="official-demo-url-v1",
        ),
        "client": client,
    }
    values.update(overrides)
    return VerifiedLiveVolcengineDoubaoASRProvider(**values)


def test_verified_live_submit_then_poll_uses_fixed_contract_once_per_operation() -> None:
    calls: list[httpx.Request] = []

    def handler(wire: httpx.Request) -> httpx.Response:
        calls.append(wire)
        if wire.url.path.endswith("/submit"):
            return httpx.Response(200, headers={"X-Api-Status-Code": "20000000"})
        return httpx.Response(200, headers={"X-Api-Status-Code": "20000000"}, json={
            "audio_info": {"duration": 1200},
            "result": {"text": "测试转写。", "utterances": [
                {"start_time": 0, "end_time": 1200, "text": "测试转写。"}
            ]},
        })

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        p = _live_provider(client)
        submitted = p.submit(_request(media_ref="https://vod-ai-test.tos-cn-beijing.volces.com/demo/speech.m4a"))
        completed = p.poll(submitted.provider_task_ref)
    assert submitted.status == "submitted"
    assert completed.status == "completed"
    assert completed.evidence is not None and completed.evidence.text == "测试转写。"
    assert len(calls) == 2
    assert str(calls[0].url).startswith("https://openspeech.bytedance.com/api/v3/auc/bigmodel/")
    assert calls[0].headers["x-api-key"] == SYNTHETIC_SECRET
    assert calls[0].headers["x-api-resource-id"] == VOLCENGINE_ASR_ENGINE_VERSION
    assert calls[0].headers["x-api-sequence"] == "-1"


def test_live_query_url_requires_exact_reviewed_record_and_does_not_leak_it() -> None:
    signed = "https://media.example.test/audio.mp3?signature=synthetic-url-secret"
    digest = hashlib.sha256(b"signature=synthetic-url-secret").hexdigest()
    reviewed = ReviewedASRMediaDelivery(
        url=signed, review_version="signed-url-review-v1", query_sha256=digest,
    )
    assert "synthetic-url-secret" not in repr(reviewed)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200)), follow_redirects=False) as client:
        p = _live_provider(client, reviewed_media_delivery=reviewed)
        with pytest.raises(ValueError, match="does not match reviewed delivery") as captured:
            p.submit(_request(media_ref="https://media.example.test/audio.mp3?signature=other"))
    assert "synthetic-url-secret" not in str(captured.value)


def test_live_transport_sanitizes_http_failure_and_maps_provider_error() -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
        401, text="provider echoed synthetic-secret-never-log",
    )), follow_redirects=False) as client:
        p = _live_provider(client)
        with pytest.raises(RuntimeError, match="HTTP request failed") as captured:
            p.submit(_request(media_ref="https://vod-ai-test.tos-cn-beijing.volces.com/demo/speech.m4a"))
    assert SYNTHETIC_SECRET not in str(captured.value)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
        200, headers={"X-Api-Status-Code": "45000001"},
    )), follow_redirects=False) as client:
        failed = _live_provider(client).submit(_request(
            media_ref="https://vod-ai-test.tos-cn-beijing.volces.com/demo/speech.m4a"
        ))
    assert failed.status == "failed"
    assert failed.error_code == "volcengine_45000001"


def test_live_provider_has_no_runtime_production_switch_or_secret_in_repr() -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200)), follow_redirects=False) as client:
        p = _live_provider(client)
        assert p.contract.production_ready is True
        assert SYNTHETIC_SECRET not in repr(p)
        with pytest.raises(TypeError, match="production_ready"):
            VerifiedLiveVolcengineDoubaoASRProvider(
                api_key="x", audio_format="m4a", source_fingerprint="f",
                reviewed_media_delivery=ReviewedASRMediaDelivery(
                    url="https://vod-ai-test.tos-cn-beijing.volces.com/demo/speech.m4a",
                    review_version="v1",
                ),
                production_ready=False,
            )
