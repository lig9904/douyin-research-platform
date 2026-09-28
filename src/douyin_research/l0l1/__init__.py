"""L0/L1 ingestion and deterministic scoring."""

from .budget import DailyBudgetGuard
from .comments import (
    CommentCollectionSummary,
    CommentCollector,
    CommentEvidenceStore,
    CommentIngestContext,
    CommentIngestResult,
)
from .ingest import DiscoveryContext, IngestResult, L0L1Store
from .runner import DiscoverySource, L0L1Runner
from .research_briefs import (
    ResearchBriefConfig,
    config_snapshot,
    depth_plan,
    discovery_source,
    make_config,
)
from .scoring import L1Scorer, RULE_VERSION
from .subject_relevance import RELEVANCE_RULE_VERSION, RelevanceDecision, SubjectRelevanceStore, SubjectTerms, classify

__all__ = [
    "CommentCollectionSummary",
    "CommentCollector",
    "CommentEvidenceStore",
    "CommentIngestContext",
    "CommentIngestResult",
    "DailyBudgetGuard",
    "DiscoveryContext",
    "DiscoverySource",
    "IngestResult",
    "L0L1Runner",
    "L0L1Store",
    "L1Scorer",
    "ResearchBriefConfig",
    "RULE_VERSION",
    "RELEVANCE_RULE_VERSION",
    "RelevanceDecision",
    "SubjectRelevanceStore",
    "SubjectTerms",
    "classify",
    "config_snapshot",
    "depth_plan",
    "discovery_source",
    "make_config",
]
