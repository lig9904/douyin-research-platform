"""Read-only assembly of bounded, privacy-reviewed L3 model evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg

L3_EVIDENCE_VERSION = "l3-evidence-v1.0.0"
ELIGIBLE_TRANSCRIPT_QUALITY = frozenset({"usable", "low_confidence", "no_speech"})
MAX_TITLE_CHARS = 500
MAX_DESCRIPTION_CHARS = 5_000
MAX_TRANSCRIPT_CHARS = 30_000
MAX_EVIDENCE_BYTES = 128_000

_REVIEW_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}\Z")


@dataclass(frozen=True, slots=True)
class L3EvidenceBundle:
    """One exact in-memory input for an L3 provider call."""

    video_id: UUID = field(repr=False)
    evidence_version: str
    input_fingerprint: str
    evidence_modalities: tuple[str, ...]
    evidence_bundle: Mapping[str, object] = field(repr=False)


class L3EvidenceAssembler:
    """Build model input from persisted L2 evidence without external calls.

    The caller supplies a completed privacy-review attestation. This class does
    not claim to detect personal data and fails before opening the database when
    that attestation is absent or invalid.
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def assemble(
        self,
        video_id: UUID | str,
        *,
        privacy_reviewed: bool,
        privacy_review_version: str,
    ) -> L3EvidenceBundle:
        review_version = _validate_privacy_review(
            reviewed=privacy_reviewed,
            version=privacy_review_version,
        )

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("set transaction read only")
            video = self._load_video(cur, video_id)
            self._assert_selected(cur, video["id"])
            comments = self._load_comment_features(cur, video["id"])
            transcript = self._load_transcript(cur, video["id"])

        bundle: dict[str, object] = {
            "evidence_version": L3_EVIDENCE_VERSION,
            "modalities": ["metadata", "comments", "transcript"],
            "video_metadata": {
                "platform": video["platform"],
                "title": video["title"],
                "description": video["description"],
                "published_at": _timestamp(video["published_at"]),
                "duration_ms": video["duration_ms"],
                "availability_status": video["availability_status"],
            },
            "comment_features": comments,
            "transcript": transcript,
            "privacy_review": {
                "reviewed": True,
                "version": review_version,
            },
        }
        encoded = _canonical_json(bundle)
        if len(encoded) > MAX_EVIDENCE_BYTES:
            raise ValueError("L3 evidence bundle exceeds the byte limit")

        return L3EvidenceBundle(
            video_id=video["id"],
            evidence_version=L3_EVIDENCE_VERSION,
            input_fingerprint=hashlib.sha256(encoded).hexdigest(),
            evidence_modalities=("metadata", "comments", "transcript"),
            evidence_bundle=bundle,
        )

    @staticmethod
    def _load_video(cur, video_id: UUID | str) -> dict[str, Any]:
        cur.execute(
            """
            select
              id, platform, title, description, published_at, duration_ms,
              availability_status, research_level
            from source_video
            where id=%s
            """,
            (video_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("source video does not exist")
        if row[7] < 2:
            raise ValueError("L3 evidence assembly requires research level 2")
        _validate_content_length("video title", row[2], MAX_TITLE_CHARS)
        _validate_content_length("video description", row[3], MAX_DESCRIPTION_CHARS)
        return dict(
            zip(
                (
                    "id",
                    "platform",
                    "title",
                    "description",
                    "published_at",
                    "duration_ms",
                    "availability_status",
                    "research_level",
                ),
                row,
                strict=True,
            )
        )

    @staticmethod
    def _assert_selected(cur, video_id: UUID) -> None:
        cur.execute(
            """
            select exists(
              select 1
              from research_promotion_decision
              where video_id=%s
                and target_level=3
                and outcome='selected'
            )
            """,
            (video_id,),
        )
        if not cur.fetchone()[0]:
            raise ValueError("video was not selected by the L2 to L3 gate")

    @staticmethod
    def _load_comment_features(cur, video_id: UUID) -> dict[str, object]:
        cur.execute(
            """
            select
              feature_version,
              sampled_comment_count, root_comment_count, sampled_reply_count,
              source_observation_count, text_present_count,
              question_text_count, like_known_count, like_sum, like_median,
              reply_known_count, reply_sum, reply_median, mean_text_length,
              eligible_text_count, normalized_unique_text_count,
              duplicate_text_count, duplicate_group_count,
              max_duplicate_group_size, url_text_count, mention_text_count,
              emoji_only_text_count, repeated_char_text_count, short_text_count,
              template_like_text_count, char_bigram_count,
              unique_char_bigram_count, top_char_bigram_count,
              top_char_bigram_share
            from video_comment_feature_snapshot
            where video_id=%s
            order by calculated_at desc, id desc
            limit 1
            """,
            (video_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("latest comment feature snapshot is required")
        keys = (
            "feature_version",
            "sampled_comment_count",
            "root_comment_count",
            "sampled_reply_count",
            "source_observation_count",
            "text_present_count",
            "question_text_count",
            "like_known_count",
            "like_sum",
            "like_median",
            "reply_known_count",
            "reply_sum",
            "reply_median",
            "mean_text_length",
            "eligible_text_count",
            "normalized_unique_text_count",
            "duplicate_text_count",
            "duplicate_group_count",
            "max_duplicate_group_size",
            "url_text_count",
            "mention_text_count",
            "emoji_only_text_count",
            "repeated_char_text_count",
            "short_text_count",
            "template_like_text_count",
            "char_bigram_count",
            "unique_char_bigram_count",
            "top_char_bigram_count",
            "top_char_bigram_share",
        )
        features = {
            key: _json_number(value) for key, value in zip(keys, row, strict=True)
        }
        features.update(
            {
                "population_scope": "collected_sample_only",
                "semantic_inference": False,
                "raw_comment_text_included": False,
            }
        )
        return features

    @staticmethod
    def _load_transcript(cur, video_id: UUID) -> dict[str, object]:
        cur.execute(
            """
            select
              asr_provider, model_id, model_revision, engine_version,
              language, text_content, audio_duration_ms, quality_status,
              case
                when segments is null then 0
                when jsonb_typeof(segments)='array' then jsonb_array_length(segments)
                else null
              end
            from transcript
            where video_id=%s
            order by created_at desc, id desc
            limit 1
            """,
            (video_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError("latest transcript evidence is required")
        quality_status = row[7]
        if quality_status not in ELIGIBLE_TRANSCRIPT_QUALITY:
            raise ValueError(
                "latest transcript quality is not eligible for L3 evidence"
            )
        if row[8] is None:
            raise ValueError("latest transcript segments must be an array")
        for name, value in zip(
            ("ASR provider", "model ID", "model revision", "engine version"),
            row[:4],
            strict=True,
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"latest transcript {name} is required")
        if quality_status != "no_speech" and not row[5].strip():
            raise ValueError("latest transcript text is required")
        _validate_content_length("transcript text", row[5], MAX_TRANSCRIPT_CHARS)
        return {
            "asr_provider": row[0],
            "model_id": row[1],
            "model_revision": row[2],
            "engine_version": row[3],
            "language": row[4],
            "text": row[5],
            "audio_duration_ms": row[6],
            "quality_status": quality_status,
            "segment_count": row[8],
            "measurement_error_possible": True,
            "source_reference_included": False,
        }


def _validate_privacy_review(*, reviewed: bool, version: str) -> str:
    if reviewed is not True:
        raise ValueError("L3 evidence requires a completed privacy review")
    if not isinstance(version, str):
        raise ValueError("L3 privacy review version is invalid")
    normalized = version.strip()
    if not _REVIEW_VERSION_RE.fullmatch(normalized):
        raise ValueError("L3 privacy review version is invalid")
    return normalized


def _validate_content_length(name: str, value: str | None, limit: int) -> None:
    if value is not None and len(value) > limit:
        raise ValueError(f"{name} exceeds the character limit")


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_number(value: object) -> object:
    if not isinstance(value, Decimal):
        return value
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
