from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from douyin_research.l0l1.video_statistics import (
    _paid_video_guard,
    refresh_project_video_statistics,
)
from douyin_research.providers.transport import TransportResult


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
SCHEMA = Path(__file__).resolve().parents[1] / "db/schema.sql"
ACTOR = "statistics-owner@example.com"
IDS = ("7658347686323555610", "7681244536475077934")


class StatisticsTransport:
    def __init__(self, *, missing: bool = False, no_play: bool = False,
                 echo_missing: bool = False) -> None:
        self.calls = 0
        self.missing = missing
        self.no_play = no_play
        self.echo_missing = echo_missing

    def call(self, spec, kwargs):
        assert spec.key == "douyin.app.video_statistics"
        assert kwargs == {"aweme_ids": ",".join(IDS)}
        self.calls += 1
        rows = [
            {"aweme_id": video_id, "play_count": None if self.no_play else 1000 + index,
             "digg_count": 20 + index, "share_count": 3}
            for index, video_id in enumerate(IDS)
        ]
        if self.missing:
            rows.pop()
        data = {"statistics_list": rows}
        if self.echo_missing:
            data["request_echo"] = {"aweme_id": IDS[1], "play_count": 9999}
        return TransportResult(
            payload={"code": 200, "data": data},
            http_status=200, provider_request_id="statistics-fixture", mode="fake",
        )


def _fixture(dsn: str):
    namespace = f"video_statistics_{uuid4().hex}"
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
    scoped_dsn = make_conninfo(dsn, options=f"-c search_path={namespace}")
    try:
        with psycopg.connect(scoped_dsn, autocommit=True) as conn:
            conn.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
            organization_id = conn.execute(
                "insert into research_organization(slug,name) values ('statistics-org','资料组织') returning id"
            ).fetchone()[0]
            project_id = conn.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values (%s,'statistics-project','资料项目','active') returning id""",
                (organization_id,),
            ).fetchone()[0]
            conn.execute(
                """insert into research_project_member(project_id,actor_id,role)
                   values (%s,%s,'owner')""", (project_id, ACTOR),
            )
            for video_id in IDS:
                canonical = conn.execute(
                    """insert into source_video(platform,platform_video_id)
                       values ('douyin',%s) returning id""", (video_id,),
                ).fetchone()[0]
                conn.execute(
                    """insert into project_video_inclusion(project_id,video_id,source_type,status)
                       values (%s,%s,'manual','accepted')""", (project_id, canonical),
                )
        yield scoped_dsn, project_id
    finally:
        admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
        admin.close()


def test_exact_statistics_refresh_records_raw_snapshot_cost_and_cache():
    assert DSN
    for scoped_dsn, project_id in _fixture(DSN):
        transport = StatisticsTransport()
        first = refresh_project_video_statistics(
            dsn=scoped_dsn, project_id=project_id, video_platform_ids=IDS,
            actor=ACTOR, api_key="fixture-key", transport=transport,
        )
        second = refresh_project_video_statistics(
            dsn=scoped_dsn, project_id=project_id, video_platform_ids=IDS,
            actor=ACTOR, api_key="fixture-key", transport=transport,
        )
        assert first.play_counts == {IDS[0]: 1000, IDS[1]: 1001}
        assert first.snapshots_inserted == 2 and first.external_calls == 1
        assert first.estimated_api_cost_usd == pytest.approx(0.001)
        assert second.snapshots_inserted == 0 and second.cached_calls == 1
        assert second.external_calls == 0 and transport.calls == 1
        with psycopg.connect(scoped_dsn) as conn:
            rows = conn.execute(
                """select v.platform_video_id,m.play_count,m.source_endpoint,
                          m.raw_metrics->>'raw_ref'
                   from metric_snapshot m join source_video v on v.id=m.video_id
                   order by v.platform_video_id"""
            ).fetchall()
            assert [(row[0], row[1]) for row in rows] == [(IDS[0], 1000), (IDS[1], 1001)]
            assert all(row[2] == "douyin.app.video_statistics" for row in rows)
            assert all(row[3].startswith("external_api_response:") for row in rows)
            assert conn.execute("select count(*) from source_video").fetchone()[0] == 2


@pytest.mark.parametrize("variant", ["missing", "no_play", "echo_missing"])
def test_partial_statistics_are_not_promoted_but_failed_cost_is_recorded(variant):
    assert DSN
    for scoped_dsn, project_id in _fixture(DSN):
        transport = StatisticsTransport(
            missing=variant in {"missing", "echo_missing"},
            no_play=variant == "no_play", echo_missing=variant == "echo_missing",
        )
        with pytest.raises(RuntimeError, match="statistics refresh failed"):
            refresh_project_video_statistics(
                dsn=scoped_dsn, project_id=project_id, video_platform_ids=IDS,
                actor=ACTOR, api_key="fixture-key", transport=transport,
            )
        assert transport.calls == 1
        with psycopg.connect(scoped_dsn) as conn:
            assert conn.execute("select count(*) from metric_snapshot").fetchone()[0] == 0
            row = conn.execute(
                """select status,summary->>'error_type' from pipeline_run
                   where run_type='video_statistics_refresh'"""
            ).fetchone()
            assert row == ("failed", "ProviderSchemaError")
            raw_count = conn.execute(
                """select count(*) from external_api_response
                   where endpoint_key='douyin.app.video_statistics'"""
            ).fetchone()[0]
            assert raw_count == 1
            run = conn.execute(
                """select summary->>'api_cost_basis',
                          (summary->>'known_estimated_cost_usd')::numeric
                   from pipeline_run where run_type='video_statistics_refresh'"""
            ).fetchone()
            assert run[0] == "estimated" and float(run[1]) == pytest.approx(0.001)


def test_denied_project_does_not_call_provider_or_create_run():
    assert DSN
    for scoped_dsn, project_id in _fixture(DSN):
        with psycopg.connect(scoped_dsn) as conn:
            conn.execute(
                """update project_video_inclusion set status='archived'
                   where project_id=%s and video_id=(
                     select id from source_video where platform_video_id=%s)""",
                (project_id, IDS[1]),
            )
        transport = StatisticsTransport()
        with pytest.raises(PermissionError):
            refresh_project_video_statistics(
                dsn=scoped_dsn, project_id=project_id, video_platform_ids=IDS,
                actor=ACTOR, api_key="fixture-key", transport=transport,
            )
        assert transport.calls == 0


def test_paid_guard_blocks_a_newer_member_version_until_http_finishes():
    assert DSN
    for scoped_dsn, project_id in _fixture(DSN):
        with _paid_video_guard(scoped_dsn, project_id, IDS, ACTOR):
            with psycopg.connect(scoped_dsn) as competing:
                competing.execute("set lock_timeout='100ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    competing.execute(
                        """insert into research_project_member(
                             project_id,actor_id,role,status,effective_from)
                           values (%s,%s,'viewer','active',now()+interval '1 second')""",
                        (project_id, ACTOR),
                    )
