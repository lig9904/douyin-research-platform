"""Provider adapters and provider-neutral contracts."""

from .contracts import PlatformResearchProvider, require_capability
from .tikhub_provider import TikHubDouyinProvider, TikHubProvider

__all__ = [
    "PlatformResearchProvider",
    "TikHubDouyinProvider",
    "TikHubProvider",
    "require_capability",
]
