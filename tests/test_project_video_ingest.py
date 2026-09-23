from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from douyin_research.l0l1.ingest import DiscoveryContext, L0L1Store
from douyin_research.l0l1.research_briefs import _ensure_active_project
from douyin_research.providers.types import VideoObservation, VideoRef


DSN = os.getenv("TEST_DATABASE_URL")
SCHEMA = Path(__file__).resolve().parents[1] / "db/schema.sql"


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_same_canonical_video_has_separate_project_inclusions_and_novelty() -> None:
    assert DSN
    namespace = f"project_ingest_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
            organization_id = conn.execute(
                "insert into research_organization(slug, name) values ('org-test', 'Test') returning id"
            ).fetchone()[0]
            projects = [
                conn.execute(
                    "insert into research_project(organization_id, slug, name, status) values (%s, %s, %s, 'active') returning id",
                    (organization_id, f"project-{index}", f"Project {index}"),
                ).fetchone()[0]
                for index in (1, 2)
            ]
            scoped_dsn = make_conninfo(DSN, options=f"-c search_path={namespace}")
            _ensure_active_project(scoped_dsn, projects[0])
            store = L0L1Store(scoped_dsn)
            observation = VideoObservation(
                video=VideoRef(
                    provider="fixture", platform="douyin", platform_video_id="same-video",
                    title="Shared public video", observed_at=datetime.now(timezone.utc),
                ),
                account=None,
                metrics=None,
            )
            for project_id in projects:
                run_id = store.create_run(
                    "l0l1_discovery", "v1", "test-worker", platform="douyin",
                    project_id=project_id,
                )
                if project_id == projects[0]:
                    with pytest.raises(ValueError, match="does not match the run project"):
                        store.ingest(
                            [observation],
                            DiscoveryContext(
                                run_id=run_id, source_type="keyword", source_key="shared",
                                provider="fixture", request_fingerprint="wrong-scope",
                                project_id=projects[1],
                            ),
                        )
                    with pytest.raises(ValueError, match="does not match the run project"):
                        store.ingest(
                            [observation],
                            DiscoveryContext(
                                run_id=run_id, source_type="keyword", source_key="shared",
                                provider="fixture", request_fingerprint="missing-scope",
                            ),
                        )
                context = DiscoveryContext(
                    run_id=run_id, source_type="keyword", source_key="shared",
                    provider="fixture", request_fingerprint=f"fp-{project_id}",
                    project_id=project_id,
                )
                first = store.ingest([observation], context)
                replay = store.ingest([observation], context)
                assert len(first.new_project_video_ids) == 1
                assert replay.new_project_video_ids == []
            assert conn.execute("select count(*) from source_video").fetchone()[0] == 1
            assert conn.execute("select count(*) from project_video_inclusion").fetchone()[0] == 2
            assert conn.execute(
                "select count(distinct video_id) from project_video_inclusion"
            ).fetchone()[0] == 1
            conn.execute("update research_project set status = 'paused' where id = %s", (projects[0],))
            with pytest.raises(PermissionError, match="project is unavailable"):
                _ensure_active_project(scoped_dsn, projects[0])
            with pytest.raises(PermissionError, match="project is unavailable"):
                store.create_run(
                    "l0l1_discovery", "v1", "test-worker", platform="douyin",
                    project_id=projects[0],
                )
            with pytest.raises(PermissionError, match="project is unavailable"):
                store.ingest([observation], DiscoveryContext(
                    run_id=conn.execute(
                        "select id from pipeline_run where project_id = %s", (projects[0],)
                    ).fetchone()[0],
                    source_type="keyword", source_key="shared", provider="fixture",
                    request_fingerprint="paused-project", project_id=projects[0],
                ))
        finally:
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
