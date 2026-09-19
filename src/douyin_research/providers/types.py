"""Provider-neutral data contracts.

These models intentionally use stdlib dataclasses.  They are small transport
objects between provider adapters and the rest of the research system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, TypeVar


@dataclass(slots=True)
class AccountRef:
    provider: str
    platform: str
    platform_account_id: str
    sec_user_id: str | None = None
    nickname: str | None = None
    profile_url: str | None = None
    follower_count: int | None = None
    observed_at: datetime | None = None
    raw_ref: str | None = None


@dataclass(slots=True)
class VideoRef:
    provider: str
    platform: str
    platform_video_id: str
    account_platform_id: str | None = None
    title: str | None = None
    description: str | None = None
    source_url: str | None = None
    published_at: datetime | None = None
    duration_ms: int | None = None
    availability_status: str = "available"
    observed_at: datetime | None = None
    raw_ref: str | None = None


@dataclass(slots=True)
class MetricSnapshotInput:
    platform: str
    video_platform_id: str
    captured_at: datetime
    provider: str
    source_endpoint: str
    play_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    share_count: int | None = None
    collect_count: int | None = None
    author_follower_count: int | None = None
    metric_status: dict[str, str] = field(default_factory=dict)
    raw_ref: str | None = None


@dataclass(slots=True)
class VideoObservation:
    video: VideoRef
    account: AccountRef | None
    metrics: MetricSnapshotInput | None


@dataclass(slots=True)
class CommentSample:
    provider: str
    platform: str
    source_endpoint: str
    video_platform_id: str
    platform_comment_id: str
    text: str
    parent_platform_comment_id: str | None = None
    like_count: int | None = None
    published_at: datetime | None = None
    reply_count: int | None = None
    sample_reason: str = "top"
    observed_at: datetime | None = None
    raw_ref: str | None = None


@dataclass(slots=True)
class ProviderCallMeta:
    provider: str
    platform: str
    endpoint_key: str
    request_fingerprint: str
    status: str
    cached: bool
    started_at: datetime
    finished_at: datetime
    http_status: int | None = None
    estimated_cost: float | None = None
    actual_cost: float | None = None
    currency: str = "USD"
    provider_request_id: str | None = None
    retry_count: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


T = TypeVar("T")


@dataclass(slots=True)
class ProviderPage(Generic[T]):
    items: list[T]
    endpoint_key: str
    request_fingerprint: str
    cached: bool
    pagination: dict[str, Any] = field(default_factory=dict)
    raw_ref: str | None = None
