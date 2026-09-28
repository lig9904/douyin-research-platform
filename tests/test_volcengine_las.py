from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from douyin_research.providers.volcengine_las import (
    LAS_BASE_URL,
    LAS_MODEL_ID,
    LAS_OPERATOR_ID,
    LAS_TEMPLATE,
    LASProtocolError,
    LASRequestUncertain,
    ReviewedLASVideoDelivery,
    VolcengineLASProvider,
)


def _delivery() -> ReviewedLASVideoDelivery:
    url = "https://minio.yudao.cc:6443/douyin-research-media/video.mp4?X-Amz-Signature=private"
    return ReviewedLASVideoDelivery(
        url=url, asset_sha256="a" * 64, review_version="project-video-v1",
        query_sha256=hashlib.sha256(url.split("?", 1)[1].encode()).hexdigest(),
    )


def _reply(task_id: str, status: str, *, code: str = "0", data=None,
           end_time: str | None = "2026-09-27T12:00:00+08:00") -> httpx.Response:
    body = {"metadata": {"task_id": task_id, "task_status": status,
                         "business_code": code, "end_time": end_time}}
    if data is not None:
        body["data"] = data
    return httpx.Response(200, json=body)


def test_las_submit_then_poll_uses_fixed_contract_and_one_request_each() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/submit"):
            return _reply("task-one", "PENDING")
        return _reply("task-one", "COMPLETED", data={
            "final_summary": "[00:00-00:10] 角色出场并作出选择。",
            "token_usages": [{"model_name": LAS_MODEL_ID,
                              "token_usage": {"prompt_tokens": 120, "completion_tokens": 8}}],
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = VolcengineLASProvider(api_key="secret-only-in-test", client=client)
        submitted = provider.submit(_delivery())
        polled = provider.poll(submitted.task_id)

    assert submitted.task_id == "task-one"
    assert len(submitted.response_sha256) == 64
    assert polled.task_status == "COMPLETED"
    assert polled.final_summary and "角色出场" in polled.final_summary
    assert len(polled.response_sha256) == 64
    assert len(requests) == 2
    assert [r.url.path for r in requests] == ["/api/v1/submit", "/api/v1/poll"]
    assert all(str(r.url).startswith(LAS_BASE_URL) for r in requests)
    assert all(r.headers["authorization"] == "Bearer secret-only-in-test" for r in requests)
    submit_body = json.loads(requests[0].content)
    assert submit_body == {
        "operator_id": LAS_OPERATOR_ID, "operator_version": "v1",
        "data": {"video_url": _delivery().url, "task_template": LAS_TEMPLATE,
                 "model_name": LAS_MODEL_ID},
    }
    assert json.loads(requests[1].content)["task_id"] == "task-one"
    assert "secret-only-in-test" not in repr(provider)
    assert "X-Amz-Signature" not in repr(_delivery())


@pytest.mark.parametrize("url", [
    "http://minio.yudao.cc/video.mp4",
    "https://user:password@minio.yudao.cc/video.mp4",
    "https://minio.yudao.cc/video.mp4#fragment",
    "https://minio.yudao.cc/video.mp4\nX-Amz-Signature=private",
    "file:///tmp/video.mp4",
])
def test_las_delivery_rejects_unsafe_url(url: str) -> None:
    with pytest.raises(ValueError):
        ReviewedLASVideoDelivery(url, "a" * 64, "v1")


def test_las_delivery_requires_exact_signed_query_digest() -> None:
    with pytest.raises(ValueError, match="signed query"):
        ReviewedLASVideoDelivery(
            "https://minio.yudao.cc/video.mp4?secret=yes", "a" * 64, "v1", "b" * 64,
        )


def test_las_submit_timeout_is_uncertain_and_never_retried() -> None:
    seen = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen
        seen += 1
        raise httpx.ReadTimeout("provider timeout", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = VolcengineLASProvider(api_key="test", client=client)
        with pytest.raises(LASRequestUncertain, match="reconcile without resubmitting"):
            provider.submit(_delivery())
    assert seen == 1
    assert provider.max_retries == 0


def test_las_submit_accepts_immediate_completion_but_does_not_follow_redirect() -> None:
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: _reply("instant", "COMPLETED")
    )) as client:
        assert VolcengineLASProvider(api_key="test", client=client).submit(_delivery()).task_id == "instant"

    seen: list[httpx.Request] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(307, headers={"Location": "https://other.example/steal"})

    with httpx.Client(transport=httpx.MockTransport(redirect), follow_redirects=True) as client:
        with pytest.raises(LASRequestUncertain, match="reconcile without resubmitting"):
            VolcengineLASProvider(api_key="test", client=client).submit(_delivery())
    assert len(seen) == 1


def test_las_mismatched_poll_identity_and_false_completion_are_rejected() -> None:
    for response in (
        _reply("other-task", "COMPLETED", data={"final_summary": "text", "token_usages": []}),
        _reply("expected", "COMPLETED", code="500", data={
            "final_summary": "text", "token_usages": [{"model_name": LAS_MODEL_ID,
                                                       "token_usage": {"prompt_tokens": 1}}],
        }),
    ):
        with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
            with pytest.raises(LASProtocolError):
                VolcengineLASProvider(api_key="test", client=client).poll("expected")


@pytest.mark.parametrize("response", [
    _reply("task", "COMPLETED", data={
        "final_summary": "text", "token_usages": [{"model_name": LAS_MODEL_ID,
                                                "token_usage": {}}],
    }),
    _reply("task", "COMPLETED", end_time="2026-09-27T12:00:00", data={
        "final_summary": "text", "token_usages": [{"model_name": LAS_MODEL_ID,
                                                "token_usage": {"prompt_tokens": 1}}],
    }),
])
def test_las_invalid_usage_or_naive_terminal_time_is_not_success(response: httpx.Response) -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
        with pytest.raises(LASProtocolError):
            VolcengineLASProvider(api_key="test", client=client).poll("task")


def test_las_rejected_submit_and_oversized_poll_never_become_results() -> None:
    with httpx.Client(transport=httpx.MockTransport(
        lambda _: _reply("rejected", "FAILED", code="Parameter.Invalid")
    )) as client:
        with pytest.raises(LASProtocolError, match="not accepted"):
            VolcengineLASProvider(api_key="test", client=client).submit(_delivery())

    too_big = httpx.Response(200, content=b"x" * 2_000_001)
    with httpx.Client(transport=httpx.MockTransport(lambda _: too_big)) as client:
        with pytest.raises(LASProtocolError, match="too large"):
            VolcengineLASProvider(api_key="test", client=client).poll("task-one")
