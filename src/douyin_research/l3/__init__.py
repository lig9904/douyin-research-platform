"""Versioned L3 research result contracts and persistence."""

from .evidence import (
    ELIGIBLE_TRANSCRIPT_QUALITY,
    L3_EVIDENCE_VERSION,
    L3EvidenceAssembler,
    L3EvidenceBundle,
)
from .execution import (
    L3_BUDGET_KEY,
    L3_CONFIRMATION,
    L3ExecutionCoordinator,
    L3ExecutionRequest,
    L3Provider,
    L3ProviderRequest,
    L3ProviderResponse,
)
from .results import (
    EVIDENCE_MODALITIES,
    L3_ANALYSIS_TYPE,
    L3_SCHEMA_VERSION,
    L3ResearchRecord,
    L3ResearchResult,
    L3ResearchStore,
)

__all__ = [
    "EVIDENCE_MODALITIES",
    "ELIGIBLE_TRANSCRIPT_QUALITY",
    "L3_ANALYSIS_TYPE",
    "L3_BUDGET_KEY",
    "L3_CONFIRMATION",
    "L3_EVIDENCE_VERSION",
    "L3_SCHEMA_VERSION",
    "L3EvidenceAssembler",
    "L3EvidenceBundle",
    "L3ExecutionCoordinator",
    "L3ExecutionRequest",
    "L3Provider",
    "L3ProviderRequest",
    "L3ProviderResponse",
    "L3ResearchRecord",
    "L3ResearchResult",
    "L3ResearchStore",
]
