"""Persisted ASR delivery approval, separate from scheduled execution identity."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from urllib.parse import urlsplit
from uuid import UUID

import psycopg

from douyin_research.media_assets import MediaAssetReference, MediaAssetStore


IDENTITY_SOURCE = "windmill_end_user_email_allowlist_v1"
REVIEWERS_PATH = "f/content_research/l3_privacy_reviewers"
DELIVERY_CONFIG_PATH = "f/content_research/media_storage_config"


def _variable(path: str) -> str:
    import wmill
    return wmill.get_variable(path)


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username is not None
        or parsed.password is not None or parsed.path not in {"", "/"}
        or parsed.query or parsed.fragment):
        raise ValueError("HTTPS media delivery origin required")
    return f"https://{parsed.netloc}"


def asset_fingerprint(asset: MediaAssetReference, origin: str) -> str:
    if asset.kind != "audio" or asset.content_type != "audio/wav":
        raise ValueError("ASR review requires a normalized WAV asset")
    # source_response_id can be cleared on retention cleanup and is not media
    # identity. Object metadata, ownership and origin remain strictly bound.
    values = asdict(asset)
    values.pop("source_response_id")
    values["delivery_origin"] = _origin(origin)
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str,
                                    separators=(",", ":")).encode()).hexdigest()


def prepare_asr_media(dsn: str, *, video_id: UUID | str, asset_id: UUID | str,
                      review_version: str) -> dict:
    """Read-only, authenticated preview; never approves or exposes object URLs."""
    from douyin_research.l3.review import authorize_reviewer

    authorize_reviewer(os.environ.get("WM_END_USER_EMAIL"), _variable(REVIEWERS_PATH))
    try:
        origin = _origin(json.loads(_variable(DELIVERY_CONFIG_PATH))["public_endpoint"])
    except Exception:
        raise RuntimeError("media delivery configuration unavailable") from None
    if not isinstance(review_version, str) or not 1 <= len(review_version.strip()) <= 128:
        raise ValueError("media review version required")
    asset = MediaAssetStore(dsn).get(video_id, asset_id)
    if asset is None:
        raise ValueError("audio asset does not belong to video")
    fingerprint = asset_fingerprint(asset, origin)
    with psycopg.connect(dsn) as conn:
        row = conn.execute("select asset_fingerprint,active from asr_media_review where asset_id=%s and review_version=%s",
                           (asset.id, review_version)).fetchone()
    status = "not_reviewed" if row is None else (
        "revoked" if not row[1] else "approved" if row[0] == fingerprint else "stale")
    return {"video_id": str(asset.video_id), "asset_id": str(asset.id),
            "review_version": review_version, "asset_fingerprint": fingerprint,
            "content_sha256": asset.content_sha256, "size_bytes": asset.size_bytes,
            "content_type": asset.content_type, "delivery_origin": origin,
            "review_status": status, "db_writes": 0, "external_calls": 0}


def approve_asr_media(dsn: str, *, video_id: UUID | str, asset_id: UUID | str,
                      review_version: str, expected_asset_fingerprint: str) -> dict:
    """Human-only operation. Neither actor nor allowlist comes from a form."""
    from douyin_research.l3.review import authorize_reviewer

    actor = authorize_reviewer(os.environ.get("WM_END_USER_EMAIL"), _variable(REVIEWERS_PATH))
    try:
        origin = _origin(json.loads(_variable(DELIVERY_CONFIG_PATH))["public_endpoint"])
    except Exception:
        raise RuntimeError("media delivery configuration unavailable") from None
    if not isinstance(review_version, str) or not 1 <= len(review_version.strip()) <= 128:
        raise ValueError("media review version required")
    asset = MediaAssetStore(dsn).get(video_id, asset_id)
    if asset is None:
        raise ValueError("audio asset does not belong to video")
    fingerprint = asset_fingerprint(asset, origin)
    if fingerprint != expected_asset_fingerprint:
        raise ValueError("media changed since review preview")
    with psycopg.connect(dsn) as conn:
        conn.execute(
            """insert into asr_media_review(asset_id,review_version,asset_fingerprint,
              delivery_origin,reviewed_by,identity_source) values (%s,%s,%s,%s,%s,%s)
              on conflict(asset_id,review_version) do nothing""",
            (asset.id, review_version, fingerprint, origin, actor, IDENTITY_SOURCE),
        )
        row = conn.execute("""select asset_fingerprint,delivery_origin,active from asr_media_review
                              where asset_id=%s and review_version=%s""",
                           (asset.id, review_version)).fetchone()
        if row != (fingerprint, origin, True):
            raise ValueError("media review version conflicts with prior approval")
    return {"status": "approved", "asset_id": str(asset.id), "review_version": review_version,
            "asset_fingerprint": fingerprint}


def assert_reviewed_delivery(dsn: str, *, video_id: UUID | str, asset_id: UUID | str,
                             review_version: str, media_url: str, source_fingerprint: str) -> str:
    """Verify persisted approval and return a stable URL-independent identity."""
    asset = MediaAssetStore(dsn).get(video_id, asset_id)
    if asset is None:
        raise ValueError("reviewed media asset is unavailable")
    with psycopg.connect(dsn) as conn:
        row = conn.execute("""select asset_fingerprint,delivery_origin from asr_media_review
                              where asset_id=%s and review_version=%s and active
                                and identity_source=%s""",
                           (asset.id, review_version, IDENTITY_SOURCE)).fetchone()
    if row is None or row[0] != asset_fingerprint(asset, row[1]):
        raise ValueError("media review is missing or stale")
    parsed = urlsplit(media_url)
    if (parsed.username is not None or parsed.password is not None or parsed.fragment
        or f"{parsed.scheme}://{parsed.netloc}" != row[1]
        or parsed.path != f"/{asset.bucket}/{asset.object_key}"
        or source_fingerprint != asset.content_sha256):
        raise ValueError("media delivery does not match reviewed asset")
    return row[0]
