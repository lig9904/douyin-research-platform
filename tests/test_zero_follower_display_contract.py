from pathlib import Path


APP = (
    Path(__file__).resolve().parents[1]
    / "windmill/f/content_research/research_dashboard.raw_app/VideoLibrary.tsx"
)


def test_zero_follower_is_marked_unverified_without_changing_raw_value() -> None:
    source = APP.read_text(encoding="utf-8")
    assert "v === 0 && !verified ? '0 · 待核' : formatCount(v)" in source
    assert "上游接口返回账号粉丝 0" in source
    assert "账号粉丝为 0 且未经独立账号接口核验" in source
    assert "message={hasContradictoryZeroPlay(detail)" in source
    assert "播放量为 0，仍需核对" in source
    assert "[formatFollowerCount(detail.author_follower_count, hasVerifiedFollowerCount(detail)), '账号粉丝']" in source
    assert "<td>{item.author_follower_count === 0 && !hasVerifiedFollowerCount(item)" in source
