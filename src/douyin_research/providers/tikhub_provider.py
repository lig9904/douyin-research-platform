"""Cached, provider-neutral TikHub Douyin adapter."""

from __future__ import annotations

import hashlib
from contextlib import nullcontext
from datetime import datetime, timedelta
from typing import Any, Callable, ContextManager, Iterable
from uuid import uuid4

from .endpoints import EndpointSpec, get_endpoint
from .fingerprint import request_fingerprint
from .normalizer import (
    extract_batch_detail_ids,
    extract_pagination,
    normalize_comment_samples,
    normalize_account_profile,
    normalize_video_observations,
    validate_tikhub_envelope,
)
from .store import ProviderStore, utcnow
from .transport import ProviderTransport, TikHubTransport
from .types import AccountRef, CommentSample, ProviderCallMeta, ProviderPage, VideoObservation
from .video_fetch_plan import plan_exact_video_brief, plan_video_fetches
from .cost_accounting import quote_call
from .errors import ProviderSchemaError, attach_provider_diagnostic, provider_failure_summary


class TikHubDouyinProvider:
    provider_name = "tikhub"
    platform_name = "douyin"
    video_batch_size = 50
    capabilities = frozenset({
        "discover.low_fan",
        "discover.creator_material",
        "search.videos",
        "video.batch_detail",
        "video.statistics",
        "account.posts",
        "account.profile",
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
        uncached_transport_guard: Callable[
            [EndpointSpec, tuple[str, ...] | None], ContextManager[None]
        ] | None = None,
        detail_strategy: str = "batch50",
    ) -> None:
        self.transport = transport
        self.store = store
        self.auth_scope = auth_scope
        self.before_external_call = before_external_call
        self.uncached_transport_guard = uncached_transport_guard
        if detail_strategy not in {"batch50", "cost_aware"}:
            raise ValueError("invalid detail strategy")
        self.detail_strategy = detail_strategy

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

    def fetch_account_profile(
        self, sec_user_id: str, *, force_refresh: bool = False,
    ) -> ProviderPage[AccountRef]:
        stable_id = sec_user_id.strip() if isinstance(sec_user_id, str) else ""
        if not stable_id or len(stable_id) > 256 or any(c.isspace() for c in stable_id):
            raise ValueError("sec_user_id must be a stable nonempty identifier")
        spec = get_endpoint("douyin.app.user_profile")

        def validate_profile(payload: dict[str, Any]) -> None:
            normalize_account_profile(
                payload, requested_sec_user_id=stable_id,
                raw_ref=None, observed_at=utcnow(),
            )

        payload, fp, cached, raw_ref, observed_at = self._call(
            spec, {"sec_user_id": stable_id},
            validate_payload=validate_profile, force_refresh=force_refresh,
        )
        account = normalize_account_profile(
            payload, requested_sec_user_id=stable_id,
            raw_ref=raw_ref, observed_at=observed_at,
        )
        return ProviderPage(
            items=[account], endpoint_key=spec.key,
            request_fingerprint=fp, cached=cached, raw_ref=raw_ref,
        )

    def fetch_videos(
        self,
        video_ids: Iterable[str],
        *,
        force_refresh: bool = False,
    ) -> list[VideoObservation]:
        return self._fetch_video_plan(
            self.plan_videos(video_ids), force_refresh=force_refresh,
        )

    def fetch_exact_video_ids(
        self, video_ids: Iterable[str], *, force_refresh: bool = False,
    ) -> ProviderPage[VideoObservation]:
        """Fetch a bounded, exact-ID discovery page without a keyword/date filter.

        Every returned video must be one of the requested IDs, and every ID
        must be present.  Callers can then ingest it through the ordinary
        discovery path rather than bypassing project candidate review.
        """
        ids = tuple(video_ids)
        if not 1 <= len(ids) <= 20 or len(set(ids)) != len(ids):
            raise ValueError("exact video IDs must be 1-20 distinct values")
        requests = plan_exact_video_brief(ids)
        items: list[VideoObservation] = []
        fingerprints: list[str] = []
        cached = True
        for request in requests:
            spec = get_endpoint(request.endpoint_key)
            kwargs = request.kwargs()
            def validate_exact_payload(payload: dict[str, Any]) -> None:
                observations = normalize_video_observations(
                    payload, endpoint_key=spec.key, raw_ref=None, observed_at=utcnow(),
                )
                returned_ids = [item.video.platform_video_id for item in observations]
                detail_ids = extract_batch_detail_ids(payload)
                duplicate_count = len(detail_ids) - len(set(detail_ids))
                if duplicate_count or set(returned_ids) != set(request.video_ids):
                    exc = ValueError("exact video response IDs do not match the request")
                    attach_provider_diagnostic(
                        exc,
                        exact_video_missing_positions=[
                            index for index, video_id in enumerate(ids)
                            if video_id not in returned_ids
                            and video_id in request.video_ids
                        ],
                        exact_video_unexpected_count=len(set(returned_ids) - set(request.video_ids)),
                        exact_video_duplicate_count=duplicate_count,
                    )
                    raise exc
            page = self._video_page(
                spec, kwargs,
                fingerprint_body=kwargs.get("body"),
                request_video_ids=request.video_ids,
                validate_payload=validate_exact_payload,
                force_refresh=force_refresh,
            )
            batch_ids = [item.video.platform_video_id for item in page.items]
            if len(batch_ids) != len(set(batch_ids)) or set(batch_ids) != set(request.video_ids):
                raise ValueError("exact video response IDs do not match the request")
            items.extend(page.items)
            fingerprints.append(page.request_fingerprint)
            cached = cached and page.cached
        ordered = {item.video.platform_video_id: item for item in items}
        fingerprint = hashlib.sha256(
            ("exact-video-ids-v1:" + ":".join(fingerprints)).encode("ascii")
        ).hexdigest()
        return ProviderPage(
            items=[ordered[video_id] for video_id in ids],
            endpoint_key="douyin.app.exact_video_ids",
            request_fingerprint=fingerprint,
            cached=cached,
        )

    def plan_videos(self, video_ids: Iterable[str]):
        return plan_video_fetches(video_ids, strategy=self.detail_strategy)

    def fetch_video_statistics(
        self, video_ids: Iterable[str], *, force_refresh: bool = False,
    ) -> list[VideoObservation]:
        """Explicit metrics-only route; never silently substitutes for details."""
        return self._fetch_video_plan(
            plan_video_fetches(video_ids, purpose="statistics"), force_refresh=force_refresh,
        )

    def fetch_exact_video_statistics(
        self, video_ids: Iterable[str], *, force_refresh: bool = False,
    ) -> ProviderPage[VideoObservation]:
        """Read one or two exact IDs, requiring a reported play count for each.

        This stricter project-review path does not accept partial or unrelated
        supplier rows as evidence. It retains the original response and call
        accounting through the ordinary provider store.
        """
        ids = tuple(video_ids)
        if not 1 <= len(ids) <= 2 or len(set(ids)) != len(ids):
            raise ValueError("statistics refresh requires one or two distinct IDs")
        request = plan_video_fetches(ids, purpose="statistics")
        if len(request) != 1 or request[0].endpoint_key != "douyin.app.video_statistics":
            raise ValueError("unexpected statistics request plan")
        spec = get_endpoint(request[0].endpoint_key)

        def validate_exact(payload: dict[str, Any]) -> None:
            observations = normalize_video_observations(
                payload, endpoint_key=spec.key, raw_ref=None, observed_at=utcnow(),
            )
            if {item.video.platform_video_id for item in observations} != set(ids) or (
                len(observations) != len(ids)
            ) or any(
                item.metrics is None or item.metrics.play_count is None
                or item.metrics.play_count < 0
                for item in observations
            ):
                raise ProviderSchemaError("exact statistics IDs or play counts are missing")

        page = self._video_page(
            spec, request[0].kwargs(), request_video_ids=ids,
            validate_payload=validate_exact, force_refresh=force_refresh,
        )
        indexed = {item.video.platform_video_id: item for item in page.items}
        return ProviderPage(
            items=[indexed[video_id] for video_id in ids],
            endpoint_key=page.endpoint_key,
            request_fingerprint=page.request_fingerprint,
            cached=page.cached, raw_ref=page.raw_ref,
        )

    def _fetch_video_plan(self, requests, *, force_refresh: bool):
        results: list[VideoObservation] = []
        for request in requests:
            spec = get_endpoint(request.endpoint_key)
            kwargs = request.kwargs()
            page = self._video_page(
                spec, kwargs,
                fingerprint_body=kwargs.get("body"),
                request_video_ids=tuple(request.video_ids),
                force_refresh=force_refresh,
            )
            # Do not ingest unrelated IDs if an upstream route returns extra data.
            allowed = set(request.video_ids)
            results.extend(item for item in page.items if item.video.platform_video_id in allowed)
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
                "cached_pages": sum(1 for page in pages if page.cached),
                "external_pages": sum(1 for page in pages if not page.cached),
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
        payload, fp, cached, raw_ref, observed_at = self._call(
            spec,
            kwargs,
            force_refresh=force_refresh,
        )
        items = normalize_comment_samples(
            payload,
            video_platform_id=video_id,
            endpoint_key=spec.key,
            observed_at=observed_at,
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
        request_video_ids: tuple[str, ...] | None = None,
        validate_payload: Callable[[dict[str, Any]], None] | None = None,
        force_refresh: bool = False,
    ) -> ProviderPage[VideoObservation]:
        payload, fp, cached, raw_ref, observed_at = self._call(
            spec,
            kwargs,
            fingerprint_body=fingerprint_body,
            request_video_ids=request_video_ids,
            validate_payload=validate_payload,
            force_refresh=force_refresh,
        )
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
        request_video_ids: tuple[str, ...] | None = None,
        validate_payload: Callable[[dict[str, Any]], None] | None = None,
        force_refresh: bool = False,
    ) -> tuple[dict[str, Any], str, bool, str | None, datetime]:
        started = utcnow()
        logical_call_id = str(uuid4())
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
                validate_tikhub_envelope(cached.payload)
                if validate_payload is not None:
                    try:
                        validate_payload(cached.payload)
                    except (TypeError, ValueError):
                        # Old or foreign malformed cache entries remain as raw
                        # evidence, but cannot block a corrected live response.
                        cached = None
            if cached is not None:
                finished = utcnow()
                estimate, actual, cost_meta = quote_call(spec, cached=True)
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
                        estimated_cost=estimate,
                        actual_cost=actual,
                        provider_request_id=cached.provider_request_id,
                        metadata={
                            "cache": "persistent",
                            **cost_meta, "logical_call_id": logical_call_id,
                        },
                    )
                )
                return cached.payload, fp, True, cached.raw_ref or f"cache:{fp}", cached.requested_at

        result = None
        call_attempted = False
        try:
            # This guard starts only after the persistent-cache decision.  It is
            # deliberately around both reservation and HTTP so a project gate can
            # validate the exact batch that is about to leave the process.
            guard = (
                self.uncached_transport_guard(spec, request_video_ids)
                if self.uncached_transport_guard is not None
                else nullcontext()
            )
            with guard:
                if self.before_external_call is not None:
                    # One reservation must correspond to at most one visible HTTP
                    # attempt. Check at the call boundary: transport configuration
                    # is mutable after provider construction.
                    if isinstance(self.transport, TikHubTransport) and (
                        self.transport.prefer_sdk or self.transport.max_retries > 1
                    ):
                        raise ValueError(
                            "guarded TikHub calls require single-attempt REST transport"
                        )
                    self.before_external_call(spec)
                call_attempted = True
                result = self.transport.call(spec, kwargs)
            payload = validate_tikhub_envelope(result.payload)
            if validate_payload is not None:
                validate_payload(payload)
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
            estimate, actual, cost_meta = quote_call(
                spec, successful_response=True, attempts=result.attempts,
            )
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
                    estimated_cost=estimate,
                    actual_cost=actual,
                    provider_request_id=result.provider_request_id,
                    retry_count=result.retry_count,
                    metadata={
                        "transport_mode": result.mode,
                        **cost_meta, "logical_call_id": logical_call_id,
                    },
                )
            )
            return payload, fp, False, raw_ref, requested_at
        except Exception as exc:
            # A project gate or budget refusal happened before transport.
            # It is not a paid provider attempt and must not enter the call
            # ledger as an unknown-cost failure.
            if not call_attempted:
                raise
            finished = utcnow()
            attempts = result.attempts if result is not None else getattr(exc, "provider_attempts", None)
            attach_provider_diagnostic(exc, logical_call_id=logical_call_id)
            estimate, actual, cost_meta = quote_call(
                spec, successful_response=result is not None and result.http_status == 200,
                attempts=attempts,
            )
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
                    estimated_cost=estimate,
                    actual_cost=actual,
                    http_status=(attempts[-1].get("http_status") if attempts else
                                 result.http_status if result is not None else None),
                    retry_count=max(0, len(attempts) - 1) if attempts is not None else None,
                    metadata={
                        "error_type": type(exc).__name__,
                        "failure": provider_failure_summary(
                            exc, stage="unknown", item_count=0,
                        ),
                        **cost_meta, "logical_call_id": logical_call_id,
                    },
                )
            )
            raise


# Backward-compatible technical alias. New code should use TikHubDouyinProvider.
TikHubProvider = TikHubDouyinProvider
