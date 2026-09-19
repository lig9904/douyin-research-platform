"""Cached, provider-neutral TikHub Douyin adapter."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Callable, Iterable

from .endpoints import EndpointSpec, get_endpoint
from .fingerprint import request_fingerprint
from .normalizer import (
    extract_pagination,
    normalize_comment_samples,
    normalize_video_observations,
    validate_tikhub_envelope,
)
from .store import ProviderStore, utcnow
from .transport import ProviderTransport
from .types import CommentSample, ProviderCallMeta, ProviderPage, VideoObservation


class TikHubDouyinProvider:
    provider_name = "tikhub"
    platform_name = "douyin"
    video_batch_size = 50
    capabilities = frozenset({
        "discover.low_fan",
        "discover.creator_material",
        "search.videos",
        "video.batch_detail",
        "account.posts",
        "comments.list",
        "comments.replies",
    })

    def __init__(
        self,
        *,
        transport: ProviderTransport,
        store: ProviderStore,
        auth_scope: str = "default",
        before_external_call: Callable[[EndpointSpec], None] | None = None,
    ) -> None:
        self.transport = transport
        self.store = store
        self.auth_scope = auth_scope
        self.before_external_call = before_external_call

    def discover(
        self,
        kind: str,
        **kwargs: Any,
    ) -> ProviderPage[VideoObservation]:
        if kind == "low_fan":
            return self.fetch_low_fan_billboard(**kwargs)
        if kind in {"creator", "creator_material"}:
            return self.fetch_creator_material(**kwargs)
        raise ValueError(f"unsupported Douyin discovery kind: {kind}")

    def fetch_low_fan_billboard(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        date_window: int = 24,
        tags: list[Any] | None = None,
        force_refresh: bool = False,
    ) -> ProviderPage[VideoObservation]:
        spec = get_endpoint("douyin.billboard.low_fan")
        kwargs = {
            "page": page,
            "page_size": page_size,
            "date_window": date_window,
            "tags": tags or [],
        }
        return self._video_page(spec, kwargs, force_refresh=force_refresh)

    def search_videos(
        self,
        query: str,
        *,
        cursor: int = 0,
        sort_type: str = "0",
        publish_time: str = "0",
        filter_duration: str = "0",
        content_type: str = "1",
        search_id: str = "",
        backtrace: str = "",
        force_refresh: bool = False,
    ) -> ProviderPage[VideoObservation]:
        spec = get_endpoint("douyin.search.video_v2")
        kwargs = {
            "keyword": query,
            "cursor": cursor,
            "sort_type": sort_type,
            "publish_time": publish_time,
            "filter_duration": filter_duration,
            "content_type": content_type,
            "search_id": search_id,
            "backtrace": backtrace,
        }
        return self._video_page(spec, kwargs, force_refresh=force_refresh)

    def fetch_creator_material(
        self,
        *,
        billboard_tag: int | None = None,
        order_key: int | None = None,
        time_filter: int | None = None,
        force_refresh: bool = False,
    ) -> ProviderPage[VideoObservation]:
        spec = get_endpoint("douyin.creator.material")
        kwargs = {
            "billboard_tag": billboard_tag,
            "order_key": order_key,
            "time_filter": time_filter,
        }
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        return self._video_page(spec, kwargs, force_refresh=force_refresh)

    def fetch_videos(
        self,
        video_ids: Iterable[str],
        *,
        force_refresh: bool = False,
    ) -> list[VideoObservation]:
        ids = [str(x).strip() for x in video_ids if str(x).strip()]
        deduped = list(dict.fromkeys(ids))
        results: list[VideoObservation] = []
        spec = get_endpoint("douyin.app.multi_video_v2")
        for i in range(0, len(deduped), self.video_batch_size):
            chunk = deduped[i : i + self.video_batch_size]
            page = self._video_page(
                spec,
                {"body": chunk},
                fingerprint_body=chunk,
                force_refresh=force_refresh,
            )
            results.extend(page.items)
        return results

    def fetch_account_posts(
        self,
        sec_user_id: str,
        *,
        max_cursor: int = 0,
        count: int = 20,
        sort_type: int = 0,
        channel: str = "normal",
        force_refresh: bool = False,
    ) -> ProviderPage[VideoObservation]:
        if count > 20:
            raise ValueError("TikHub account-post count must not exceed 20")
        spec = get_endpoint("douyin.app.user_posts")
        kwargs = {
            "sec_user_id": sec_user_id,
            "max_cursor": max_cursor,
            "count": count,
            "sort_type": sort_type,
            "channel": channel,
        }
        return self._video_page(spec, kwargs, force_refresh=force_refresh)

    def fetch_comments_page(
        self,
        video_id: str,
        *,
        cursor: int | str = 0,
        count: int = 20,
        sample_reason: str = "top",
        force_refresh: bool = False,
    ) -> ProviderPage[CommentSample]:
        if not video_id.strip():
            raise ValueError("video_id is required")
        if count < 1 or count > 20:
            raise ValueError("TikHub comment count must be between 1 and 20")
        spec = get_endpoint("douyin.app.comments")
        return self._comment_page(
            spec,
            {
                "aweme_id": video_id,
                "cursor": cursor,
                "count": count,
            },
            video_id=video_id,
            sample_reason=sample_reason,
            force_refresh=force_refresh,
        )

    def fetch_comments(
        self,
        video_id: str,
        *,
        cursor: int | str = 0,
        count: int = 20,
        max_pages: int = 1,
        max_items: int = 20,
        sample_reason: str = "top",
        force_refresh: bool = False,
    ) -> ProviderPage[CommentSample]:
        """Fetch bounded pages and deduplicate stable comment IDs across pages."""
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")
        if max_items < 1:
            raise ValueError("max_items must be at least 1")

        items: list[CommentSample] = []
        seen: set[str] = set()
        duplicates_removed = 0
        current_cursor: int | str = cursor
        pages: list[ProviderPage[CommentSample]] = []

        for _ in range(max_pages):
            page = self.fetch_comments_page(
                video_id,
                cursor=current_cursor,
                count=count,
                sample_reason=sample_reason,
                force_refresh=force_refresh,
            )
            pages.append(page)
            for item in page.items:
                if item.platform_comment_id in seen:
                    duplicates_removed += 1
                    continue
                seen.add(item.platform_comment_id)
                items.append(item)
                if len(items) >= max_items:
                    break
            if len(items) >= max_items:
                break

            has_more = page.pagination.get("has_more")
            next_cursor = page.pagination.get("cursor")
            if next_cursor is None:
                next_cursor = page.pagination.get("max_cursor")
            if has_more in (None, False, 0, "0"):
                break
            if next_cursor in (None, "") or str(next_cursor) == str(current_cursor):
                break
            current_cursor = next_cursor

        first = pages[0]
        last = pages[-1]
        return ProviderPage(
            items=items,
            endpoint_key=first.endpoint_key,
            request_fingerprint=first.request_fingerprint,
            cached=all(page.cached for page in pages),
            pagination={
                **last.pagination,
                "pages_fetched": len(pages),
                "unique_items": len(items),
                "duplicates_removed": duplicates_removed,
            },
            raw_ref=last.raw_ref,
        )

    def fetch_comment_replies(
        self,
        video_id: str,
        comment_id: str,
        *,
        cursor: int | str = 0,
        count: int = 20,
        force_refresh: bool = False,
    ) -> ProviderPage[CommentSample]:
        if not video_id.strip() or not comment_id.strip():
            raise ValueError("video_id and comment_id are required")
        if count < 1 or count > 20:
            raise ValueError("TikHub reply count must be between 1 and 20")
        spec = get_endpoint("douyin.app.comment_replies")
        return self._comment_page(
            spec,
            {
                "item_id": video_id,
                "comment_id": comment_id,
                "cursor": cursor,
                "count": count,
            },
            video_id=video_id,
            sample_reason="thread_root",
            parent_comment_id=comment_id,
            force_refresh=force_refresh,
        )

    def _comment_page(
        self,
        spec: EndpointSpec,
        kwargs: dict[str, Any],
        *,
        video_id: str,
        sample_reason: str,
        force_refresh: bool,
        parent_comment_id: str | None = None,
    ) -> ProviderPage[CommentSample]:
        payload, fp, cached, raw_ref = self._call(
            spec,
            kwargs,
            force_refresh=force_refresh,
        )
        items = normalize_comment_samples(
            payload,
            video_platform_id=video_id,
            endpoint_key=spec.key,
            observed_at=utcnow(),
            sample_reason=sample_reason,
            parent_platform_comment_id=parent_comment_id,
            raw_ref=raw_ref,
        )
        return ProviderPage(
            items=items,
            endpoint_key=spec.key,
            request_fingerprint=fp,
            cached=cached,
            pagination=extract_pagination(payload),
            raw_ref=raw_ref,
        )

    def _video_page(
        self,
        spec: EndpointSpec,
        kwargs: dict[str, Any],
        *,
        fingerprint_body: Any = None,
        force_refresh: bool = False,
    ) -> ProviderPage[VideoObservation]:
        payload, fp, cached, raw_ref = self._call(
            spec,
            kwargs,
            fingerprint_body=fingerprint_body,
            force_refresh=force_refresh,
        )
        observed_at = utcnow()
        items = normalize_video_observations(
            payload,
            endpoint_key=spec.key,
            raw_ref=raw_ref,
            observed_at=observed_at,
        )
        return ProviderPage(
            items=items,
            endpoint_key=spec.key,
            request_fingerprint=fp,
            cached=cached,
            pagination=extract_pagination(payload),
            raw_ref=raw_ref,
        )

    def _call(
        self,
        spec: EndpointSpec,
        kwargs: dict[str, Any],
        *,
        fingerprint_body: Any = None,
        force_refresh: bool = False,
    ) -> tuple[dict[str, Any], str, bool, str | None]:
        started = utcnow()
        params_for_fp = kwargs if spec.request_style != "json" else {}
        body_for_fp = fingerprint_body if fingerprint_body is not None else (
            kwargs.get("body", kwargs) if spec.request_style == "json" else None
        )
        fp = request_fingerprint(
            provider=self.provider_name,
            endpoint_key=spec.key,
            params=params_for_fp,
            body=body_for_fp,
            auth_scope=self.auth_scope,
        )

        if not force_refresh:
            cached = self.store.get_cached(self.provider_name, self.platform_name, spec.key, fp, started)
            if cached is not None:
                finished = utcnow()
                self.store.record_call(
                    ProviderCallMeta(
                        provider=self.provider_name,
                        platform=self.platform_name,
                        endpoint_key=spec.key,
                        request_fingerprint=fp,
                        status="success",
                        cached=True,
                        started_at=started,
                        finished_at=finished,
                        actual_cost=0.0,
                        provider_request_id=cached.provider_request_id,
                        metadata={"cache": "persistent"},
                    )
                )
                validate_tikhub_envelope(cached.payload)
                return cached.payload, fp, True, cached.raw_ref or f"cache:{fp}"

        if self.before_external_call is not None:
            self.before_external_call(spec)

        try:
            result = self.transport.call(spec, kwargs)
            payload = validate_tikhub_envelope(result.payload)
            requested_at = utcnow()
            expires_at = requested_at + timedelta(seconds=spec.cache_ttl_seconds)
            raw_ref = self.store.save_response(
                provider=self.provider_name,
                platform=self.platform_name,
                endpoint_key=spec.key,
                fingerprint=fp,
                payload=payload,
                requested_at=requested_at,
                expires_at=expires_at,
                http_status=result.http_status,
                response_code=str(payload.get("code")),
                provider_request_id=result.provider_request_id,
            )
            finished = utcnow()
            self.store.record_call(
                ProviderCallMeta(
                    provider=self.provider_name,
                    platform=self.platform_name,
                    endpoint_key=spec.key,
                    request_fingerprint=fp,
                    status="success",
                    cached=False,
                    started_at=started,
                    finished_at=finished,
                    http_status=result.http_status,
                    estimated_cost=spec.unit_cost_usd,
                    actual_cost=spec.unit_cost_usd if spec.paid and spec.unit_cost_usd else None,
                    provider_request_id=result.provider_request_id,
                    retry_count=result.retry_count,
                    metadata={"transport_mode": result.mode},
                )
            )
            return payload, fp, False, raw_ref
        except Exception as exc:
            finished = utcnow()
            self.store.record_call(
                ProviderCallMeta(
                    provider=self.provider_name,
                    platform=self.platform_name,
                    endpoint_key=spec.key,
                    request_fingerprint=fp,
                    status="error",
                    cached=False,
                    started_at=started,
                    finished_at=finished,
                    estimated_cost=spec.unit_cost_usd,
                    metadata={"error_type": type(exc).__name__, "error": str(exc)[:500]},
                )
            )
            raise


# Backward-compatible technical alias. New code should use TikHubDouyinProvider.
TikHubProvider = TikHubDouyinProvider
