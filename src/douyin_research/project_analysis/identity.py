"""Stable, project-scoped provider task keys without credentials or delivery URLs."""

from __future__ import annotations

import hashlib
import json
import re
from uuid import UUID

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}\Z")


def _uuid(value: UUID | str, name: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc


def _version(value: str, name: str) -> str:
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")
    return value


def _fingerprint(value: str, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a SHA-256 hex digest")
    return value


def _key(kind: str, parts: tuple[str, ...]) -> str:
    digest = hashlib.sha256(
        json.dumps((kind, *parts), ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"project-{kind}:{digest}"


def project_asr_task_key(
    *, project_id: UUID | str, video_id: UUID | str, review_id: UUID | str,
    asset_id: UUID | str, review_version: str, media_fingerprint: str,
    model_id: str, model_revision: str, engine_version: str,
) -> str:
    """A fresh review in another project can never replay an old ASR job."""
    return _key("asr-v1", (
        _uuid(project_id, "project_id"), _uuid(video_id, "video_id"),
        _uuid(review_id, "review_id"), _uuid(asset_id, "asset_id"),
        _version(review_version, "review_version"),
        _fingerprint(media_fingerprint, "media_fingerprint"),
        _version(model_id, "model_id"), _version(model_revision, "model_revision"),
        _version(engine_version, "engine_version"),
    ))


def project_l3_task_key(
    *, project_id: UUID | str, video_id: UUID | str, review_id: UUID | str,
    review_version: str, evidence_fingerprint: str, model_id: str,
    model_revision: str, prompt_version: str, schema_version: str,
) -> str:
    """Bind one L3 call to its project's exact human-reviewed evidence."""
    return _key("l3-v1", (
        _uuid(project_id, "project_id"), _uuid(video_id, "video_id"),
        _uuid(review_id, "review_id"), _version(review_version, "review_version"),
        _fingerprint(evidence_fingerprint, "evidence_fingerprint"),
        _version(model_id, "model_id"), _version(model_revision, "model_revision"),
        _version(prompt_version, "prompt_version"), _version(schema_version, "schema_version"),
    ))
