from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "windmill/f/content_research/research_dashboard.raw_app"


def test_business_ui_uses_play_based_display_rate_without_inventing_missing_counts() -> None:
    formula = (APP / "src/playInteractionRate.ts").read_text(encoding="utf-8")
    home = (APP / "App.tsx").read_text(encoding="utf-8")
    library = (APP / "VideoLibrary.tsx").read_text(encoding="utf-8")
    assert "likes + comments + shares) / plays" in formula
    assert "values.some" in formula and "return '—'" in formula
    assert "author_follower_count" not in formula
    for page in (home, library):
        assert "formatPlayInteractionRate" in page
        assert "互动/播放（合并估算）" in page
        assert "follower_efficiency" not in page
