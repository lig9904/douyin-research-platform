"""Deterministic, local account-profile similarity for L2 research.

The scorer consumes only already persisted profile fields.  It does not infer
topics from text, request embeddings, or call a provider/LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import log10
from typing import Iterable


ACCOUNT_SIMILARITY_VERSION = "account-similarity-v1.0.0"
_WEIGHTS = {
    "content_domains": 45,
    "account_type": 15,
    "certification_type": 10,
    "follower_count": 20,
    "video_count": 10,
}
_MIN_COMPARABLE_WEIGHT = 45


@dataclass(frozen=True, slots=True)
class AccountSimilarityProfile:
    """A deliberately small, non-textual account profile.

    ``None`` means the source has not supplied a value.  It is never treated
    as zero or as a matching category.
    """

    account_id: str
    platform: str
    content_domains: frozenset[str]
    account_type: str | None
    certification_type: str | None
    follower_count: int | None
    video_count: int | None

    def __post_init__(self) -> None:
        normalized_domains = frozenset(
            domain.strip()
            for domain in self.content_domains
            if isinstance(domain, str) and domain.strip()
        )
        object.__setattr__(self, "content_domains", normalized_domains)
        for field in ("account_type", "certification_type"):
            value = getattr(self, field)
            normalized = value.strip() if isinstance(value, str) and value.strip() else None
            object.__setattr__(self, field, normalized)


@dataclass(frozen=True, slots=True)
class AccountSimilarityMatch:
    account_id: str
    similarity_score: int
    evidence_coverage: int
    raw_score: int
    matched_domains: tuple[str, ...]
    matched_fields: tuple[str, ...]
    components: dict[str, int]


def rank_similar_accounts(
    target: AccountSimilarityProfile,
    candidates: Iterable[AccountSimilarityProfile],
    *,
    limit: int = 5,
) -> list[AccountSimilarityMatch]:
    """Return reproducibly ordered, same-platform comparable candidates.

    The public score is normalized by fields known on *both* accounts.  The
    coverage and raw score remain in the result so consumers cannot mistake a
    sparse profile for a fully observed comparison.  At least 45 of the 100
    possible field weight must be observed on both sides before a candidate is
    returned.
    """

    if limit < 1:
        return []

    matches: list[AccountSimilarityMatch] = []
    for candidate in candidates:
        if candidate.account_id == target.account_id or candidate.platform != target.platform:
            continue
        match = _compare(target, candidate)
        if match is not None:
            matches.append(match)

    return sorted(
        matches,
        key=lambda item: (-item.similarity_score, -item.evidence_coverage, -item.raw_score, item.account_id),
    )[:limit]


def _compare(
    target: AccountSimilarityProfile,
    candidate: AccountSimilarityProfile,
) -> AccountSimilarityMatch | None:
    components: dict[str, int] = {}
    matched_fields: list[str] = []
    matched_domains: tuple[str, ...] = ()
    comparable_weight = 0

    if target.content_domains and candidate.content_domains:
        comparable_weight += _WEIGHTS["content_domains"]
        matched_domains = tuple(sorted(target.content_domains & candidate.content_domains))
        union = target.content_domains | candidate.content_domains
        components["content_domains"] = _round_half_up(
            _WEIGHTS["content_domains"] * len(matched_domains) / len(union)
        )
        if matched_domains:
            matched_fields.append("content_domains")

    for field in ("account_type", "certification_type"):
        left = getattr(target, field)
        right = getattr(candidate, field)
        if left is not None and right is not None:
            comparable_weight += _WEIGHTS[field]
            components[field] = _WEIGHTS[field] if left == right else 0
            if left == right:
                matched_fields.append(field)

    for field in ("follower_count", "video_count"):
        left = getattr(target, field)
        right = getattr(candidate, field)
        if left is not None and right is not None:
            comparable_weight += _WEIGHTS[field]
            components[field] = _round_half_up(_WEIGHTS[field] * _magnitude_similarity(left, right))
            if components[field] > 0:
                matched_fields.append(field)

    if comparable_weight < _MIN_COMPARABLE_WEIGHT:
        return None

    raw_score = sum(components.values())
    similarity_score = _round_half_up(raw_score * 100 / comparable_weight)
    if similarity_score == 0:
        return None
    return AccountSimilarityMatch(
        account_id=candidate.account_id,
        similarity_score=similarity_score,
        evidence_coverage=comparable_weight,
        raw_score=raw_score,
        matched_domains=matched_domains,
        matched_fields=tuple(matched_fields),
        components=components,
    )


def _magnitude_similarity(left: int, right: int) -> float:
    """Compare non-negative counts without treating a missing count as zero."""

    if left < 0 or right < 0:
        raise ValueError("profile counts must be non-negative")
    distance = abs(log10(left + 1) - log10(right + 1))
    return max(0.0, 1.0 - distance / 3.0)


def _round_half_up(value: float) -> int:
    return int(value + 0.5)
