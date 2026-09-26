from pathlib import Path


APP = (
    Path(__file__).resolve().parents[1]
    / "windmill/f/content_research/research_dashboard.raw_app/VideoLibrary.tsx"
)


def test_zero_follower_is_marked_unverified_without_changing_raw_value() -> None:
    source = APP.read_text(encoding="utf-8")
    assert "v === 0 ? '0 · 待核' : formatCount(v)" in source
    assert "上游接口返回账号粉丝 0" in source
    assert "账号粉丝为 0 时，须核对账号主页或独立账号接口后再用于筛选" in source
    assert "[formatFollowerCount(detail.author_follower_count), '账号粉丝']" in source
    assert "<td>{item.author_follower_count === 0" in source
