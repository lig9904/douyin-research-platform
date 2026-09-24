from __future__ import annotations

from pathlib import Path

import pytest

from douyin_research.l0l1.subject_relevance import SubjectRelevanceStore, SubjectTerms, classify


ROOT = Path(__file__).parents[1]
MIGRATION = ROOT / "db/migrations/027_subject_relevance_gate.sql"
SCHEMA = ROOT / "db/schema.sql"


def _terms(*, geography: tuple[str, ...] = ()) -> SubjectTerms:
    return SubjectTerms(
        subject_name="渔岛",
        aliases=("渔岛", "Yudao"),
        geographic_contexts=geography,
        exclusions=("招聘", "无关"),
    )


def test_rule_classification_requires_positive_subject_and_optional_geography() -> None:
    assert classify("渔岛温泉周末攻略", _terms()).decision == "relevant"
    assert classify("普通温泉周末攻略", _terms()).decision == "pending"
    assert classify("渔岛招聘公告", _terms()).decision == "irrelevant"
    assert classify("Yudao 温泉", _terms(geography=("秦皇岛",))).decision == "pending"
    result = classify("秦皇岛渔岛温泉攻略", _terms(geography=("秦皇岛",)))
    assert result.decision == "relevant"
    assert result.matched_aliases == ("渔岛",)
    assert result.matched_geographic_contexts == ("秦皇岛",)


def test_manual_correction_validation_is_bounded_before_database_access() -> None:
    store = SubjectRelevanceStore("postgresql://unused")
    with pytest.raises(ValueError, match="decision"):
        store.correct(
            project_id=__import__("uuid").uuid4(), subject_id=__import__("uuid").uuid4(),
            video_id=__import__("uuid").uuid4(), actor="reviewer@example.com",
            decision="maybe", reason="明确相关",
        )
    with pytest.raises(ValueError, match="correction"):
        store.correct(
            project_id=__import__("uuid").uuid4(), subject_id=__import__("uuid").uuid4(),
            video_id=__import__("uuid").uuid4(), actor="", decision="relevant", reason="",
        )


def test_schema_keeps_source_evidence_global_and_relevance_project_scoped() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")
    for source in (migration, schema):
        assert "research_subject_term" in source
        assert "term_type in ('alias', 'geographic_context', 'exclusion')" in source
        assert "project_video_subject_relevance" in source
        assert "decision in ('pending', 'relevant', 'irrelevant')" in source
        assert "decision_source in ('rule', 'manual')" in source
        assert "project_video_subject_relevance_audit" in source
        assert "manual_override" in source
        assert "update source_video" not in source.lower()
        assert "delete from source_video" not in source.lower()
    assert "references project_video_inclusion(project_id, video_id)" in migration
    assert "subject_id uuid" in migration
