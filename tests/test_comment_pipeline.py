from __future__ import annotations

from datetime import date
from dataclasses import replace
from uuid import UUID

import pytest

from douyin_research.l2.comment_pipeline import (
    COMMENT_PIPELINE_RULE_VERSION,
    MAX_BATCH_VIDEOS,
    CommentPipeline,
    CommentPipelineSettings,
    _advisory_lock_key,
    _settings_fingerprint,
    _eligibility_fingerprint,
    _validate_settings,
)


class _UnusedCollector:
    def collect(self, *_args, **_kwargs):  # pragma: no cover - construction only
        raise AssertionError("collector must not be called by configuration tests")


def test_constructor_uses_controlled_nonempty_worker_identity() -> None:
    with pytest.raises(ValueError, match="worker_identity"):
        CommentPipeline("postgresql://unused", collector=_UnusedCollector(), worker_identity=" ")

    service = CommentPipeline(
        "postgresql://unused",
        collector=_UnusedCollector(),
        worker_identity="windmill-scheduled:test-research/t3",
    )
    assert service.worker_identity == "windmill-scheduled:test-research/t3"
    assert service.rule_version == COMMENT_PIPELINE_RULE_VERSION


@pytest.mark.parametrize(
    "settings, message",
    [
        (CommentPipelineSettings(top_n=0), "top_n"),
        (CommentPipelineSettings(top_n=21), "top_n"),
        (CommentPipelineSettings(top_n=1, min_score=101), "min_score"),
        (CommentPipelineSettings(top_n=1, comment_count=0), "comment_count"),
        (CommentPipelineSettings(top_n=1, max_pages=11), "max_pages"),
    ],
)
def test_settings_are_bounded_before_any_database_or_provider_work(settings, message) -> None:
    with pytest.raises(ValueError, match=message):
        _validate_settings(settings)


def test_fingerprint_and_advisory_key_bind_source_rule_and_trusted_settings() -> None:
    source_id = UUID("00000000-0000-0000-0000-000000000001")
    base = CommentPipelineSettings(top_n=3, min_score=70, quota_date=date(2026, 9, 21))
    same = CommentPipelineSettings(top_n=3, min_score=70, quota_date=date(2026, 9, 21))
    changed = CommentPipelineSettings(top_n=3, min_score=71, quota_date=date(2026, 9, 21))
    assert _settings_fingerprint(source_id, COMMENT_PIPELINE_RULE_VERSION, base) == _settings_fingerprint(
        source_id, COMMENT_PIPELINE_RULE_VERSION, same
    )
    assert _settings_fingerprint(source_id, COMMENT_PIPELINE_RULE_VERSION, base) == _settings_fingerprint(
        source_id, COMMENT_PIPELINE_RULE_VERSION, changed
    )
    assert _advisory_lock_key(source_id, COMMENT_PIPELINE_RULE_VERSION) == _advisory_lock_key(
        source_id, COMMENT_PIPELINE_RULE_VERSION
    )
    assert isinstance(MAX_BATCH_VIDEOS, int) and MAX_BATCH_VIDEOS == 20


def test_promotion_changes_preserve_collection_key_but_collection_changes_do_not():
    source = UUID(int=1)
    base = CommentPipelineSettings(top_n=3)
    fingerprint = _settings_fingerprint(source, COMMENT_PIPELINE_RULE_VERSION, base)
    changed = replace(base, top_n=5, min_score=80, quota_date=date(2026, 9, 22), quota_key="other")
    assert _settings_fingerprint(source, COMMENT_PIPELINE_RULE_VERSION, changed) == fingerprint
    assert _settings_fingerprint(source, COMMENT_PIPELINE_RULE_VERSION, replace(base, max_items=30)) != fingerprint


def test_eligibility_binds_successful_set_and_feature_evidence_not_order():
    run, a, b = UUID(int=1), UUID(int=2), UUID(int=3)
    first = _eligibility_fingerprint(run, {a: "sha-a"})
    recovered = _eligibility_fingerprint(run, {a: "sha-a", b: "sha-b"})
    assert first != recovered
    assert recovered == _eligibility_fingerprint(run, {b: "sha-b", a: "sha-a"})
    assert recovered != _eligibility_fingerprint(run, {a: "changed", b: "sha-b"})
