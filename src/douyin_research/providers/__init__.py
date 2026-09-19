"""Provider adapters and provider-neutral contracts."""

from .contracts import PlatformResearchProvider, require_capability
from .execution_contracts import (
    ASR_ASYNC_CAPABILITY,
    EXECUTION_CONTRACT_VERSION,
    L3_SYNC_CAPABILITY,
    VerifiedExecutionContract,
    validate_execution_contract,
)
from .tikhub_provider import TikHubDouyinProvider, TikHubProvider

__all__ = [
    "ASR_ASYNC_CAPABILITY",
    "EXECUTION_CONTRACT_VERSION",
    "L3_SYNC_CAPABILITY",
    "PlatformResearchProvider",
    "TikHubDouyinProvider",
    "TikHubProvider",
    "VerifiedExecutionContract",
    "require_capability",
    "validate_execution_contract",
]
