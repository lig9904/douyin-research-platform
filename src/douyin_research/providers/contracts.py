"""Generic provider contracts shared by all content platforms."""

from __future__ import annotations

from typing import Any, Iterable, Protocol, runtime_checkable

from .types import AccountRef, CommentSample, ProviderPage, VideoObservation


@runtime_checkable
class PlatformResearchProvider(Protocol):
    """Business-facing platform adapter.

    Concrete adapters translate provider-specific APIs into shared research
    objects. A platform can expose only the capabilities it actually supports.
    """

    provider_name: str
    platform_name: str
    capabilities: frozenset[str]
    video_batch_size: int

    def discover(
        self,
        kind: str,
        **kwargs: Any,
    ) -> ProviderPage[VideoObservation]: ...

    def search_videos(
        self,
        query: str,
        **kwargs: Any,
    ) -> ProviderPage[VideoObservation]: ...

    def fetch_videos(
        self,
        video_ids: Iterable[str],
        **kwargs: Any,
    ) -> list[VideoObservation]: ...

    def fetch_exact_video_ids(
        self,
        video_ids: Iterable[str],
        **kwargs: Any,
    ) -> ProviderPage[VideoObservation]: ...

    def fetch_account_posts(
        self,
        account_id: str,
        **kwargs: Any,
    ) -> ProviderPage[VideoObservation]: ...

    def fetch_account_profile(
        self,
        sec_user_id: str,
        **kwargs: Any,
    ) -> ProviderPage[AccountRef]: ...

    def fetch_comments(
        self,
        video_id: str,
        **kwargs: Any,
    ) -> ProviderPage[CommentSample]: ...

    def fetch_comment_replies(
        self,
        video_id: str,
        comment_id: str,
        **kwargs: Any,
    ) -> ProviderPage[CommentSample]: ...


def require_capability(provider: PlatformResearchProvider, capability: str) -> None:
    if capability not in provider.capabilities:
        raise ValueError(
            f"{provider.provider_name}/{provider.platform_name} "
            f"does not expose capability: {capability}"
        )
