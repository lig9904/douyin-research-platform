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

from .transcripts import (
    ASR_EVIDENCE_VERSION,
    TranscriptEvidence,
    TranscriptEvidenceStore,
    TranscriptRecord,
    TranscriptSegment,
    TaskCost,
)

__all__ += [
    "ASR_EVIDENCE_VERSION",
    "TranscriptEvidence",
    "TranscriptEvidenceStore",
    "TranscriptRecord",
    "TranscriptSegment",
    "TaskCost",
]

from .asr_execution import (
    ASR_BUDGET_KEY,
    ASR_CONFIRMATION,
    MAX_POLLS_PER_RUN,
    ASRExecutionCoordinator,
    ASRExecutionRequest,
    ASRProvider,
    ASRProviderRequest,
    ASRProviderState,
)

__all__ += [
    "ASR_BUDGET_KEY",
    "ASR_CONFIRMATION",
    "MAX_POLLS_PER_RUN",
    "ASRExecutionCoordinator",
    "ASRExecutionRequest",
    "ASRProvider",
    "ASRProviderRequest",
    "ASRProviderState",
]
