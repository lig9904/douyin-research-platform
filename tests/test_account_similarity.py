from __future__ import annotations

from douyin_research.l2 import AccountSimilarityProfile, rank_similar_accounts


def _profile(
    account_id: str,
    *,
    platform: str = "douyin",
    domains: frozenset[str] = frozenset({"旅行", "海洋"}),
    account_type: str | None = "个人",
    certification_type: str | None = "个人认证",
    followers: int | None = 100_000,
    videos: int | None = 40,
) -> AccountSimilarityProfile:
    return AccountSimilarityProfile(
        account_id=account_id,
        platform=platform,
        content_domains=domains,
        account_type=account_type,
        certification_type=certification_type,
        follower_count=followers,
        video_count=videos,
    )


def test_ranking_is_deterministic_explainable_and_excludes_self() -> None:
    target = _profile("target")
    strongest = _profile("a-best", followers=120_000, videos=44)
    partial = _profile(
        "b-partial",
        domains=frozenset({"旅行", "美食"}),
        account_type="机构",
        certification_type="企业认证",
        followers=2_000_000,
        videos=300,
    )

    first = rank_similar_accounts(target, [target, partial, strongest])
    replay = rank_similar_accounts(target, [strongest, target, partial])

    assert first == replay
    assert [item.account_id for item in first] == ["a-best", "b-partial"]
    best = first[0]
    assert best.similarity_score == 99
    assert best.evidence_coverage == 100
    assert best.raw_score == 99
    assert best.matched_domains == ("旅行", "海洋")
    assert best.matched_fields == (
        "content_domains",
        "account_type",
        "certification_type",
        "follower_count",
        "video_count",
    )
    assert best.components == {
        "content_domains": 45,
        "account_type": 15,
        "certification_type": 10,
        "follower_count": 19,
        "video_count": 10,
    }


def test_missing_values_are_not_zero_filled_and_sparse_profiles_are_excluded() -> None:
    target = _profile("target")
    domains_only = _profile(
        "domains-only",
        domains=frozenset({"旅行"}),
        account_type=None,
        certification_type=None,
        followers=None,
        videos=None,
    )
    type_only = _profile(
        "type-only",
        domains=frozenset(),
        account_type="个人",
        certification_type=None,
        followers=None,
        videos=None,
    )

    matches = rank_similar_accounts(target, [type_only, domains_only])

    assert [item.account_id for item in matches] == ["domains-only"]
    assert matches[0].evidence_coverage == 45
    assert matches[0].raw_score == 23
    assert matches[0].similarity_score == 51
    assert matches[0].components == {"content_domains": 23}


def test_platform_boundary_limit_and_invalid_counts() -> None:
    target = _profile("target")
    foreign = _profile("foreign", platform="kuaishou")
    valid = _profile("valid")
    invalid = _profile("invalid", followers=-1)

    assert rank_similar_accounts(target, [foreign, valid], limit=1)[0].account_id == "valid"
    assert rank_similar_accounts(target, [valid], limit=0) == []

    try:
        rank_similar_accounts(target, [invalid])
    except ValueError as exc:
        assert str(exc) == "profile counts must be non-negative"
    else:
        raise AssertionError("negative count must fail explicitly")
