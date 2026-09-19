from __future__ import annotations

import json

import httpx
import pytest

from douyin_research.l2.asr_execution import ASRProviderRequest
from douyin_research.providers.volcengine_asr import (
    VOLCENGINE_ASR_ENGINE_VERSION,
    VOLCENGINE_ASR_MODEL_ID,
    VOLCENGINE_ASR_MODEL_REVISION,
    VolcengineDoubaoASRProvider,
)


SECRET = "synthetic-secret-never-log"


def _request(**overrides) -> ASRProviderRequest:
    values = {
        "task_key": "synthetic-task",
        "media_ref": "https://media.example.test/audio.mp3?signature=redacted",
        "source_fingerprint": "source-fingerprint",
        "model_id": VOLCENGINE_ASR_MODEL_ID,
        "model_revision": VOLCENGINE_ASR_MODEL_REVISION,
        "engine_version": VOLCENGINE_ASR_ENGINE_VERSION,
    }
    values.update(overrides)
    return ASRProviderRequest(**values)


def _provider(handler, *, production_ready=True):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return VolcengineDoubaoASRProvider(
        api_key=SECRET,
        audio_format="mp3",
        source_fingerprint="source-fingerprint",
        source_provider="synthetic-media",
        production_ready=production_ready,
        client=client,
    )


def test_adapter_defaults_to_fail_closed_without_network() -> None:
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, headers={"X-Api-Status-Code": "20000000"})

    provider = _provider(handler, production_ready=False)
    with pytest.raises(ValueError, match="not production ready"):
        provider.submit(_request())
    assert calls == []
    assert SECRET not in repr(provider)


def test_submit_uses_documented_headers_and_conservative_features() -> None:
    captured = {}

    def handler(request):
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, headers={"X-Api-Status-Code": "20000000"})

    provider = _provider(handler)
    state = provider.submit(_request())

    assert state.status == "submitted"
    assert state.provider_task_ref
    assert captured["headers"]["X-Api-Key"] == SECRET
    assert captured["headers"]["X-Api-Resource-Id"] == VOLCENGINE_ASR_ENGINE_VERSION
    assert captured["headers"]["X-Api-Sequence"] == "-1"
    body = captured["body"]
    assert body["user"] == {"uid": "douyin-research-platform"}
    assert body["audio"]["format"] == "mp3"
    assert body["request"] == {
        "model_name": "bigmodel",
        "enable_itn": True,
        "enable_punc": True,
        "enable_ddc": False,
        "show_utterances": True,
        "enable_emotion_detection": False,
        "enable_gender_detection": False,
    }


def test_poll_maps_running_and_completed_result_to_evidence() -> None:
    responses = iter(
        [
            httpx.Response(200, headers={"X-Api-Status-Code": "20000001"}),
            httpx.Response(
                200,
                headers={"X-Api-Status-Code": "20000000"},
                json={
                    "audio_info": {"duration": 1200},
                    "result": {
                        "text": "测试转写。",
                        "utterances": [
                            {
                                "start_time": 0,
                                "end_time": 1200,
                                "text": "测试转写。",
                            }
                        ],
                    },
                },
            ),
        ]
    )

    def handler(request):
        assert request.headers["X-Api-Request-Id"] == "task-ref"
        assert request.headers.get("X-Api-Sequence") is None
        assert json.loads(request.content) == {}
        return next(responses)

    provider = _provider(handler)
    running = provider.poll("task-ref")
    completed = provider.poll("task-ref")

    assert running.status == "running"
    assert completed.status == "completed"
    assert completed.evidence is not None
    assert completed.evidence.text == "测试转写。"
    assert completed.evidence.audio_duration_ms == 1200
    assert completed.evidence.segments[0].start_ms == 0
    assert completed.evidence.segments[0].end_ms == 1200
    assert completed.evidence.segments[0].confidence is None
    assert completed.cost is not None
    assert completed.cost.api_cost is None
    assert completed.cost.asr_cost is None
    assert completed.cost.llm_cost == 0
    assert completed.cost.basis == "unknown"


def test_no_speech_and_provider_error_are_explicit() -> None:
    responses = iter(
        [
            httpx.Response(200, headers={"X-Api-Status-Code": "20000003"}),
            httpx.Response(200, headers={"X-Api-Status-Code": "45000001"}),
        ]
    )
    provider = _provider(lambda request: next(responses))

    no_speech = provider.poll("task-ref")
    failed = provider.poll("task-ref")

    assert no_speech.status == "completed"
    assert no_speech.evidence is not None
    assert no_speech.evidence.quality_status == "no_speech"
    assert no_speech.evidence.text == ""
    assert failed.status == "failed"
    assert failed.error_code == "volcengine_45000001"


def test_secret_and_upstream_payload_are_not_exposed_on_http_error() -> None:
    def handler(request):
        return httpx.Response(
            401,
            json={"message": "upstream-secret-diagnostic"},
        )

    provider = _provider(handler)
    with pytest.raises(RuntimeError, match="HTTP request failed") as exc:
        provider.submit(_request())
    rendered = repr(exc.value)
    assert SECRET not in rendered
    assert "upstream-secret-diagnostic" not in rendered


@pytest.mark.parametrize(
    "media_ref",
    [
        "http://media.example.test/audio.mp3",
        "https://user:password@media.example.test/audio.mp3",
        "https://media.example.test/audio.mp3#fragment",
        "not-a-url",
    ],
)
def test_media_reference_must_be_safe_https(media_ref) -> None:
    calls = []
    provider = _provider(lambda request: calls.append(request))
    with pytest.raises(ValueError, match="credential-free HTTPS"):
        provider.submit(_request(media_ref=media_ref))
    assert calls == []


def test_task_reference_is_stable_for_reconciliation() -> None:
    refs = []

    def handler(request):
        refs.append(request.headers["X-Api-Request-Id"])
        return httpx.Response(200, headers={"X-Api-Status-Code": "20000000"})

    first = _provider(handler).submit(_request())
    second = _provider(handler).submit(_request())
    assert first.provider_task_ref == second.provider_task_ref
    assert refs == [first.provider_task_ref, second.provider_task_ref]
