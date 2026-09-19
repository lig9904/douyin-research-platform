from __future__ import annotations

import os
from typing import Any

import psycopg
import pytest

from douyin_research.providers.store import PostgresProviderStore
from douyin_research.providers.tikhub_provider import TikHubProvider
from douyin_research.providers.transport import TransportResult


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL not configured")


class FakeTransport:
    def __init__(self) -> None:
        self.calls = 0

    def call(self, spec, kwargs: dict[str, Any]) -> TransportResult:
        self.calls += 1
        return TransportResult(
            payload={
                "code": 200,
                "request_id": "pg-r1",
                "data": {
                    "aweme_list": [
                        {
                            "aweme_id": "pg-video-1",
                            "desc": "persistent cache",
                            "author": {"sec_uid": "pg-user-1"},
                            "statistics": {"digg_count": 12},
                        }
                    ]
                },
            },
            http_status=200,
            provider_request_id="pg-r1",
            mode="fake",
        )


def _clear() -> None:
    assert DSN
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("delete from external_api_call")
        cur.execute("delete from external_api_response")
        conn.commit()


def test_postgres_cache_persists_across_provider_instances() -> None:
    assert DSN
    _clear()

    first_transport = FakeTransport()
    first = TikHubProvider(
        transport=first_transport,
        store=PostgresProviderStore(DSN),
    )
    page1 = first.fetch_low_fan_billboard(page=1, page_size=5, date_window=24)
    assert page1.cached is False
    assert first_transport.calls == 1

    second_transport = FakeTransport()
    second = TikHubProvider(
        transport=second_transport,
        store=PostgresProviderStore(DSN),
    )
    page2 = second.fetch_low_fan_billboard(page=1, page_size=5, date_window=24)
    assert page2.cached is True
    assert second_transport.calls == 0

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select count(*), count(*) filter (where cached), count(*) filter (where not cached)
            from external_api_call
            """
        )
        total, cached_count, external_count = cur.fetchone()
        assert (total, cached_count, external_count) == (2, 1, 1)

        cur.execute(
            """
            select response_body->>'request_id', response_code
            from external_api_response
            order by id desc
            limit 1
            """
        )
        request_id, response_code = cur.fetchone()
        assert request_id == "pg-r1"
        assert response_code == "200"
