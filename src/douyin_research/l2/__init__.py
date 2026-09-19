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
