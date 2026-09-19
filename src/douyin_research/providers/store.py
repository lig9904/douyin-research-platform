"""Persistent provider cache and call log.

The store only handles external-call bookkeeping.  Canonical video/account
upserts belong to the collector flow, not to this layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

import psycopg
from psycopg.types.json import Jsonb

from .types import ProviderCallMeta


@dataclass(slots=True)
class CachedPayload:
    payload: dict[str, Any]
    requested_at: datetime
    expires_at: datetime | None
    provider_request_id: str | None = None
    response_code: str | None = None


class ProviderStore(Protocol):
    def get_cached(
        self, provider: str, endpoint_key: str, fingerprint: str, now: datetime
    ) -> CachedPayload | None: ...

    def save_response(
        self,
        *,
        provider: str,
        endpoint_key: str,
        fingerprint: str,
        payload: dict[str, Any],
        requested_at: datetime,
        expires_at: datetime | None,
        http_status: int | None,
        response_code: str | None,
        provider_request_id: str | None,
    ) -> str: ...

    def record_call(self, call: ProviderCallMeta) -> None: ...


class MemoryProviderStore:
    """Deterministic store for unit tests and local pure-code checks."""

    def __init__(self) -> None:
        self.cache: dict[tuple[str, str, str], CachedPayload] = {}
        self.calls: list[ProviderCallMeta] = []
        self.responses: list[dict[str, Any]] = []

    def get_cached(
        self, provider: str, endpoint_key: str, fingerprint: str, now: datetime
    ) -> CachedPayload | None:
        item = self.cache.get((provider, endpoint_key, fingerprint))
        if item is None:
            return None
        if item.expires_at is not None and item.expires_at <= now:
            return None
        return item

    def save_response(
        self,
        *,
        provider: str,
        endpoint_key: str,
        fingerprint: str,
        payload: dict[str, Any],
        requested_at: datetime,
        expires_at: datetime | None,
        http_status: int | None,
        response_code: str | None,
        provider_request_id: str | None,
    ) -> str:
        cached = CachedPayload(
            payload=payload,
            requested_at=requested_at,
            expires_at=expires_at,
            provider_request_id=provider_request_id,
            response_code=response_code,
        )
        self.cache[(provider, endpoint_key, fingerprint)] = cached
        self.responses.append(
            {
                "provider": provider,
                "endpoint_key": endpoint_key,
                "fingerprint": fingerprint,
                "payload": payload,
                "http_status": http_status,
                "response_code": response_code,
            }
        )
        return f"memory:{len(self.responses)}"

    def record_call(self, call: ProviderCallMeta) -> None:
        self.calls.append(call)


class PostgresProviderStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def get_cached(
        self, provider: str, endpoint_key: str, fingerprint: str, now: datetime
    ) -> CachedPayload | None:
        sql = """
            select response_body, requested_at, expires_at, provider_request_id, response_code
            from external_api_response
            where provider = %s
              and endpoint_key = %s
              and request_fingerprint = %s
              and (expires_at is null or expires_at > %s)
              and response_code = '200'
            order by requested_at desc
            limit 1
        """
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(sql, (provider, endpoint_key, fingerprint, now))
            row = cur.fetchone()
        if row is None:
            return None
        return CachedPayload(
            payload=row[0],
            requested_at=row[1],
            expires_at=row[2],
            provider_request_id=row[3],
            response_code=row[4],
        )

    def save_response(
        self,
        *,
        provider: str,
        endpoint_key: str,
        fingerprint: str,
        payload: dict[str, Any],
        requested_at: datetime,
        expires_at: datetime | None,
        http_status: int | None,
        response_code: str | None,
        provider_request_id: str | None,
    ) -> str:
        sql = """
            insert into external_api_response(
                provider, endpoint_key, request_fingerprint, requested_at,
                http_status, response_code, response_body, provider_request_id, expires_at
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            returning id
        """
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    provider,
                    endpoint_key,
                    fingerprint,
                    requested_at,
                    http_status,
                    response_code,
                    Jsonb(payload),
                    provider_request_id,
                    expires_at,
                ),
            )
            response_id = cur.fetchone()[0]
            conn.commit()
        return f"external_api_response:{response_id}"

    def record_call(self, call: ProviderCallMeta) -> None:
        sql = """
            insert into external_api_call(
                provider, endpoint_key, request_fingerprint, status, http_status,
                cached, estimated_cost, actual_cost, cost_currency,
                started_at, finished_at, metadata
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        metadata = dict(call.metadata)
        if call.provider_request_id:
            metadata["provider_request_id"] = call.provider_request_id
        if call.retry_count is not None:
            metadata["retry_count"] = call.retry_count
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    call.provider,
                    call.endpoint_key,
                    call.request_fingerprint,
                    call.status,
                    call.http_status,
                    call.cached,
                    call.estimated_cost,
                    call.actual_cost,
                    call.currency,
                    call.started_at,
                    call.finished_at,
                    Jsonb(metadata),
                ),
            )
            conn.commit()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
