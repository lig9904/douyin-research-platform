"""Deterministic L2 enrichment that does not call external APIs or LLMs."""

from .comment_features import (
    COMMENT_FEATURE_VERSION,
    CommentFeatureExtractor,
    CommentFeatureSnapshot,
)

__all__ = [
    "COMMENT_FEATURE_VERSION",
    "CommentFeatureExtractor",
    "CommentFeatureSnapshot",
]

from .promotion import (
    DEFAULT_QUOTA_KEY,
    MAX_TOP_N,
    PROMOTION_RULE_VERSION,
    L3PromotionGate,
    PromotionDecision,
    PromotionResult,
)

__all__ += [
    "DEFAULT_QUOTA_KEY",
    "MAX_TOP_N",
    "PROMOTION_RULE_VERSION",
    "L3PromotionGate",
    "PromotionDecision",
    "PromotionResult",
]
