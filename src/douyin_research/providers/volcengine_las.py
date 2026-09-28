"""One-request LAS whole-video transport; orchestration must persist before submit.

This adapter deliberately has no retry or scheduling logic. A trusted project
worker must verify a project-local video approval and durably claim its 040
``live_pre_dispatch`` attempt before calling :meth:`submit` exactly once.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx


LAS_BASE_URL = "https://operator.las.cn-beijing.volces.com"
LAS_OPERATOR_ID = "las_video_understanding"
LAS_OPERATOR_VERSION = "v1"
LAS_TEMPLATE = "omni_video_audio_captioning@v1"
LAS_MODEL_ID = "doubao-seed-2-0-lite-260428"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_POLL_STATES = frozenset({"PENDING", "RUNNING", "COMPLETED", "FAILED", "TIMEOUT"})


class LASProtocolError(ValueError):
    """Provider response is unsafe to bind; after HTTP this still forbids resubmit."""


class LASRequestUncertain(RuntimeError):
    """Transport or HTTP outcome is uncertain; never automatically resubmit."""


@dataclass(frozen=True, slots=True, repr=False)
class ReviewedLASVideoDelivery:
    """Exact HTTPS video URL reviewed by a project worker, never logged.

    The digest covers a presigned URL's query without storing that bearer-like
    query in ordinary audit records. This type is not itself an authorization:
    the caller must recheck the current project approval and asset SHA in DB.
    """

    url: str
    asset_sha256: str
    review_version: str
    query_sha256: str | None = None

    def __post_init__(self) -> None:
        if (not isinstance(self.url, str) or len(self.url) > 8192 or
                any(ord(char) < 33 or ord(char) == 127 for char in self.url)):
            raise ValueError("LAS video delivery URL invalid")
        parsed = urlsplit(self.url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
                parsed.password or parsed.fragment or not parsed.path.startswith("/")):
            raise ValueError("LAS video delivery requires credential-free HTTPS authority")
        if not isinstance(self.asset_sha256, str) or not _SHA256.fullmatch(self.asset_sha256):
            raise ValueError("LAS video asset SHA-256 invalid")
        if (not isinstance(self.review_version, str) or
                not 1 <= len(self.review_version.strip()) <= 160):
            raise ValueError("LAS video review version invalid")
        if parsed.query:
            if (not isinstance(self.query_sha256, str) or
                    not _SHA256.fullmatch(self.query_sha256) or
                    hashlib.sha256(parsed.query.encode("utf-8")).hexdigest() != self.query_sha256):
                raise ValueError("LAS signed query SHA-256 mismatch")
        elif self.query_sha256 is not None:
            raise ValueError("LAS query SHA-256 requires a signed query")

    def __repr__(self) -> str:
        return ("ReviewedLASVideoDelivery("
                f"review_version={self.review_version!r}, "
                f"asset_sha256={self.asset_sha256[:12]!r}, "
                f"has_query={bool(urlsplit(self.url).query)!r})")


@dataclass(frozen=True, slots=True)
class LASSubmission:
    task_id: str
    task_status: str
    response_sha256: str


@dataclass(frozen=True, slots=True)
class LASPoll:
    task_id: str
    task_status: str
    business_code: str
    response_sha256: str
    final_summary: str | None
    token_usages: tuple[Mapping[str, Any], ...]
    end_time: str | None


class VolcengineLASProvider:
    """Fixed Beijing LAS whole-video client with zero client-side retries."""

    max_retries = 0

    def __init__(self, *, api_key: str, client: httpx.Client | None = None,
                 timeout_seconds: float = 30.0) -> None:
        if not isinstance(api_key, str) or not api_key.strip() or "\n" in api_key or "\r" in api_key:
            raise ValueError("LAS API key required")
        if not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 120:
            raise ValueError("LAS timeout invalid")
        self._api_key = api_key
        self._client = client or httpx.Client(base_url=LAS_BASE_URL)
        self._owns_client = client is None
        self._timeout = float(timeout_seconds)

    def __repr__(self) -> str:
        return "VolcengineLASProvider(model='doubao-seed-2-0-lite-260428', max_retries=0)"

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> VolcengineLASProvider:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def submit(self, delivery: ReviewedLASVideoDelivery) -> LASSubmission:
        if not isinstance(delivery, ReviewedLASVideoDelivery):
            raise TypeError("reviewed LAS video delivery required")
        body = {
            "operator_id": LAS_OPERATOR_ID,
            "operator_version": LAS_OPERATOR_VERSION,
            "data": {
                "video_url": delivery.url,
                "task_template": LAS_TEMPLATE,
                "model_name": LAS_MODEL_ID,
            },
        }
        payload, digest = self._post("/api/v1/submit", body)
        metadata = _metadata(payload)
        if metadata["business_code"] != "0" or metadata["task_status"] not in {
            "PENDING", "RUNNING", "COMPLETED"
        }:
            raise LASProtocolError("LAS submit was not accepted")
        return LASSubmission(metadata["task_id"], metadata["task_status"], digest)

    def poll(self, task_id: str) -> LASPoll:
        task_id = _task_id(task_id)
        payload, digest = self._post("/api/v1/poll", {
            "operator_id": LAS_OPERATOR_ID,
            "operator_version": LAS_OPERATOR_VERSION,
            "task_id": task_id,
        })
        metadata = _metadata(payload)
        if metadata["task_id"] != task_id or metadata["task_status"] not in _POLL_STATES:
            raise LASProtocolError("LAS poll task identity or state mismatch")
        status = metadata["task_status"]
        data = payload.get("data")
        summary: str | None = None
        usages: tuple[Mapping[str, Any], ...] = ()
        if status == "COMPLETED":
            if metadata["business_code"] != "0" or not isinstance(data, dict):
                raise LASProtocolError("LAS completion is not a successful result")
            summary = data.get("final_summary")
            if not isinstance(summary, str) or not summary.strip() or len(summary) > 250_000:
                raise LASProtocolError("LAS final summary invalid")
            raw_usages = data.get("token_usages")
            if not isinstance(raw_usages, list) or not raw_usages or len(raw_usages) > 20:
                raise LASProtocolError("LAS token usages invalid")
            for usage in raw_usages:
                model_name = usage.get("model_name") if isinstance(usage, dict) else None
                if not isinstance(model_name, str) or not 1 <= len(model_name.strip()) <= 160:
                    raise LASProtocolError("LAS token usage model invalid")
                tokens = usage.get("token_usage")
                if not isinstance(tokens, dict) or not tokens or not any(
                    name in tokens for name in ("prompt_tokens", "completion_tokens", "total_tokens")
                ) or any(
                    type(value) is not int or value < 0 for value in tokens.values()
                ):
                    raise LASProtocolError("LAS token usage counts invalid")
            usages = tuple(raw_usages)
        end_time = metadata.get("end_time")
        if end_time is not None and (not isinstance(end_time, str) or len(end_time) > 80):
            raise LASProtocolError("LAS end time invalid")
        if status in {"COMPLETED", "FAILED", "TIMEOUT"}:
            if not end_time:
                raise LASProtocolError("LAS terminal end time required")
            try:
                parsed_end_time = datetime.fromisoformat(end_time)
            except ValueError:
                raise LASProtocolError("LAS end time invalid") from None
            if parsed_end_time.tzinfo is None or parsed_end_time.utcoffset() is None:
                raise LASProtocolError("LAS end time requires timezone")
        return LASPoll(task_id, status, metadata["business_code"], digest,
                       summary, usages, end_time)

    def _post(self, path: str, body: dict[str, object]) -> tuple[dict[str, Any], str]:
        try:
            response = self._client.post(
                LAS_BASE_URL + path, json=body,
                headers={"Authorization": f"Bearer {self._api_key}",
                         "Content-Type": "application/json"},
                timeout=self._timeout,
                follow_redirects=False,
            )
        except httpx.RequestError:
            raise LASRequestUncertain("LAS request outcome unknown; reconcile without resubmitting") from None
        if response.status_code != 200:
            raise LASRequestUncertain("LAS HTTP outcome not accepted; reconcile without resubmitting")
        raw = response.content
        if len(raw) > 2_000_000:
            raise LASProtocolError("LAS response too large")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            raise LASProtocolError("LAS response is not JSON") from None
        if not isinstance(payload, dict):
            raise LASProtocolError("LAS response object required")
        return payload, hashlib.sha256(raw).hexdigest()


def _task_id(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 512 or any(c.isspace() for c in value):
        raise LASProtocolError("LAS task ID invalid")
    return value


def _metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise LASProtocolError("LAS metadata object required")
    result = dict(metadata)
    result["task_id"] = _task_id(result.get("task_id"))
    if not isinstance(result.get("task_status"), str):
        raise LASProtocolError("LAS task status invalid")
    code = result.get("business_code")
    if not isinstance(code, (str, int)) or isinstance(code, bool):
        raise LASProtocolError("LAS business code invalid")
    result["business_code"] = str(code)
    return result
