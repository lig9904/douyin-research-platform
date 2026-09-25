from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "windmill/f/content_research/research_dashboard.raw_app"


def test_optional_comparison_and_counterexample_editor_is_project_bounded() -> None:
    page = (APP / "DecisionLoop.tsx").read_text(encoding="utf-8")
    assert "可比案例与反例（可选）" in page
    assert "role: 'comparable' | 'counterexample'" in page
    assert "evidence_refs: []" in page
    assert "data.eligible_videos.filter" in page
    assert "候选列表最多显示最近 200 条" in page
    assert "cardForm.evidence_refs.some(ref => !ref.video_id || !ref.reason.trim())" in page
    assert "该来源现已撤回或不可用，仅留历史依据" in page
