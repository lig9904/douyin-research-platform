from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from douyin_research.l0l1.research_briefs import (
    _project_detail_transport_guard, _project_uncached_guard,
)
from douyin_research.providers.endpoints import get_endpoint
from douyin_research.providers.store import MemoryProviderStore
from douyin_research.providers.tikhub_provider import TikHubDouyinProvider


SCHEMA = Path(__file__).parents[1] / "db/schema.sql"
DSN = os.getenv("TEST_DATABASE_URL")


def test_project_uncached_guard_requires_exact_ids_on_detail_routes() -> None:
    guard = _project_uncached_guard("unused", uuid4(), uuid4())
    with pytest.raises(PermissionError, match="missing exact video IDs"):
        guard(get_endpoint("douyin.app.multi_video_v2"), None)
    with pytest.raises(PermissionError, match="non-detail endpoint"):
        guard(get_endpoint("douyin.billboard.low_fan"), ("detail-001",))
    assert guard(get_endpoint("douyin.billboard.low_fan"), None) is not None


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_uncached_project_detail_guard_is_exact_and_serializes_review() -> None:
    assert DSN
    namespace = f"detail_transport_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            admin.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            admin.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
            org = admin.execute(
                "insert into research_organization(slug,name) values ('detail-org','Detail Org') returning id"
            ).fetchone()[0]
            project = admin.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values (%s,'detail-project','Detail Project','active') returning id""", (org,)
            ).fetchone()[0]
            subject = admin.execute(
                """insert into research_subject(project_id,name,subject_type,status)
                   values (%s,'九九','ip','active') returning id""", (project,)
            ).fetchone()[0]
            run = admin.execute(
                """insert into pipeline_run(run_type,platform,project_id)
                   values ('l0l1_discovery','douyin',%s) returning id""", (project,)
            ).fetchone()[0]
            video = admin.execute(
                """insert into source_video(platform,platform_video_id,availability_status)
                   values ('douyin','detail-001','available') returning id"""
            ).fetchone()[0]
            admin.execute(
                """insert into project_video_inclusion(project_id,video_id,source_run_id,source_type)
                   values (%s,%s,%s,'pipeline_run')""", (project, video, run),
            )
            admin.execute(
                """insert into project_video_subject_relevance(
                     project_id,video_id,subject_id,run_id,decision,decision_source,rule_version)
                   values (%s,%s,%s,%s,'relevant','rule','subject-relevance-v1.0.0')""",
                (project, video, subject, run),
            )
            scoped_dsn = make_conninfo(DSN, options=f"-c search_path={namespace}")

            with _project_detail_transport_guard(scoped_dsn, project, subject, ("detail-001",)):
                reviewer_dsn = make_conninfo(DSN, options=f"-c search_path={namespace} -c lock_timeout=100ms")
                with psycopg.connect(reviewer_dsn) as reviewer:
                    with pytest.raises(psycopg.errors.LockNotAvailable):
                        reviewer.execute(
                            """update project_video_subject_relevance set decision='irrelevant'
                               where project_id=%s and video_id=%s and subject_id=%s""",
                            (project, video, subject),
                        )

            project_guard = _project_uncached_guard(scoped_dsn, project, subject)
            with project_guard(get_endpoint("douyin.billboard.low_fan"), None):
                with psycopg.connect(reviewer_dsn) as reviewer:
                    with pytest.raises(psycopg.errors.LockNotAvailable):
                        reviewer.execute(
                            "update research_project set status='paused' where id=%s", (project,)
                        )

            with pytest.raises(PermissionError, match="no longer eligible"):
                with _project_detail_transport_guard(scoped_dsn, project, subject, ("detail-001", "other")):
                    pass
            admin.execute(
                """update project_video_subject_relevance set decision='irrelevant'
                   where project_id=%s and video_id=%s and subject_id=%s""",
                (project, video, subject),
            )
            with pytest.raises(PermissionError, match="no longer eligible"):
                with _project_detail_transport_guard(scoped_dsn, project, subject, ("detail-001",)):
                    pass
            class NoTransport:
                def __init__(self) -> None:
                    self.calls = 0

                def call(self, _spec, _kwargs):
                    self.calls += 1
                    raise AssertionError("ineligible detail reached transport")

            transport = NoTransport()
            provider_store = MemoryProviderStore()
            reserved: list[str] = []
            provider = TikHubDouyinProvider(
                transport=transport, store=provider_store,
                before_external_call=lambda spec: reserved.append(spec.key),
                uncached_transport_guard=_project_uncached_guard(scoped_dsn, project, subject),
            )
            with pytest.raises(PermissionError, match="no longer eligible"):
                provider.fetch_videos(["detail-001"])
            assert transport.calls == 0
            assert reserved == []
            assert provider_store.calls == []
            admin.execute(
                """update project_video_subject_relevance set decision='relevant'
                   where project_id=%s and video_id=%s and subject_id=%s""",
                (project, video, subject),
            )
            admin.execute("update source_video set availability_status='unavailable' where id=%s", (video,))
            with pytest.raises(PermissionError, match="no longer eligible"):
                with _project_detail_transport_guard(scoped_dsn, project, subject, ("detail-001",)):
                    pass
            admin.execute("update research_project set status='paused' where id=%s", (project,))
            with pytest.raises(PermissionError, match="unavailable"):
                with project_guard(get_endpoint("douyin.billboard.low_fan"), None):
                    pass
        finally:
            admin.execute("set search_path to public")
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
