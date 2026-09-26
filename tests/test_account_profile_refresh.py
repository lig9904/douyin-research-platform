from __future__ import annotations

import os
import json
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from douyin_research.l0l1.account_profiles import (
    _account_cache_lock,
    _paid_account_guard,
    refresh_project_account_profiles,
)
from douyin_research.providers.transport import TransportResult


DSN = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
SCHEMA = Path(__file__).resolve().parents[1] / "db/schema.sql"
ACTOR = "profile-owner@example.com"


class ProfileTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def call(self, spec, kwargs):
        assert spec.key == "douyin.app.user_profile"
        stable_id = kwargs["sec_user_id"]
        self.calls.append(stable_id)
        return TransportResult(
            payload={"code": 200, "data": {"user": {
                "sec_uid": stable_id, "nickname": "公开角色账号",
                "follower_count": 1755,
            }}}, http_status=200, provider_request_id="test-profile", mode="fake",
        )


class MismatchedSecondTransport(ProfileTransport):
    def call(self, spec, kwargs):
        result = super().call(spec, kwargs)
        if kwargs["sec_user_id"] == "sec-profile-second":
            result.payload["data"]["user"]["sec_uid"] = "wrong-account"
        return result


def test_project_profile_refresh_deduplicates_accounts_and_records_estimated_cost() -> None:
    assert DSN
    namespace = f"profile_refresh_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            scoped_dsn = make_conninfo(DSN, options=f"-c search_path={namespace}")
            with psycopg.connect(scoped_dsn, autocommit=True) as conn:
                conn.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
                organization_id = conn.execute(
                    "insert into research_organization(slug,name) values ('profile-org','资料组织') returning id"
                ).fetchone()[0]
                project_id = conn.execute(
                    """insert into research_project(organization_id,slug,name,status)
                       values (%s,'profile-project','资料项目','active') returning id""",
                    (organization_id,),
                ).fetchone()[0]
                conn.execute(
                    """insert into research_project_member(project_id,actor_id,role)
                       values (%s,%s,'owner')""", (project_id, ACTOR),
                )
                account_id = conn.execute(
                    """insert into source_account(platform,platform_account_id,nickname)
                       values ('douyin','sec-profile-test','公开账号') returning id"""
                ).fetchone()[0]
                video_ids = ("7658347686323555610", "7681244536475077934")
                for video_id in video_ids:
                    canonical_id = conn.execute(
                        """insert into source_video(platform,platform_video_id,account_id)
                           values ('douyin',%s,%s) returning id""",
                        (video_id, account_id),
                    ).fetchone()[0]
                    conn.execute(
                        """insert into project_video_inclusion(
                             project_id,video_id,source_type,status)
                           values (%s,%s,'manual','accepted')""",
                        (project_id, canonical_id),
                    )
                    fingerprint = f"video-evidence-{video_id}"
                    conn.execute(
                        """insert into external_api_response(
                             provider,platform,endpoint_key,request_fingerprint,
                             http_status,response_code,response_body)
                           values ('tikhub','douyin','douyin.app.multi_video',%s,
                             200,'200',%s::jsonb)""",
                        (fingerprint, json.dumps({"code": 200, "data": {
                            "aweme_list": [{"aweme_id": video_id, "author": {
                                "sec_uid": "sec-profile-test",
                            }}],
                        }})),
                    )
                    conn.execute(
                        """insert into discovery_event(
                             video_id,provider,source_type,source_key,
                             observation_key,metadata)
                           values (%s,'tikhub','manual','fixture',%s,%s::jsonb)""",
                        (canonical_id, fingerprint, json.dumps({
                            "request_fingerprint": fingerprint,
                        })),
                    )
            transport = ProfileTransport()
            first = refresh_project_account_profiles(
                dsn=scoped_dsn, project_id=project_id,
                video_platform_ids=video_ids, actor=ACTOR, api_key="fixture-key",
                transport=transport,
            )
            second = refresh_project_account_profiles(
                dsn=scoped_dsn, project_id=project_id,
                video_platform_ids=video_ids, actor=ACTOR, api_key="fixture-key",
                transport=transport,
            )
            assert first.requested_video_count == 2
            assert first.distinct_account_count == 1
            assert first.external_calls == 1 and first.cached_calls == 0
            assert first.snapshots_inserted == 1
            assert first.estimated_api_cost_usd == pytest.approx(0.001)
            assert second.external_calls == 0 and second.cached_calls == 1
            assert second.snapshots_inserted == 0
            assert second.estimated_api_cost_usd == 0
            assert transport.calls == ["sec-profile-test"]
            with _paid_account_guard(
                scoped_dsn, project_id, video_ids[0], "sec-profile-test", ACTOR,
            ):
                with psycopg.connect(scoped_dsn) as competing:
                    competing.execute("set lock_timeout='100ms'")
                    with pytest.raises(psycopg.errors.LockNotAvailable):
                        competing.execute(
                            """update project_video_inclusion set status='archived'
                               where project_id=%s and video_id=(
                                 select id from source_video where platform_video_id=%s)""",
                            (project_id, video_ids[0]),
                        )
            with _account_cache_lock(scoped_dsn, "sec-profile-test"):
                with psycopg.connect(scoped_dsn) as competing:
                    competing.execute("set lock_timeout='100ms'")
                    with pytest.raises(psycopg.errors.LockNotAvailable):
                        competing.execute(
                            "select pg_advisory_xact_lock(hashtextextended(%s,93017))",
                            ("sec-profile-test",),
                        )
            with psycopg.connect(scoped_dsn) as conn:
                conn.execute(
                    """update project_video_inclusion set status='archived'
                       where project_id=%s""", (project_id,),
                )
            with pytest.raises(PermissionError, match="accepted project video"):
                refresh_project_account_profiles(
                    dsn=scoped_dsn, project_id=project_id,
                    video_platform_ids=video_ids, actor=ACTOR, api_key="fixture-key",
                    transport=transport,
                )
            assert transport.calls == ["sec-profile-test"]
            with psycopg.connect(scoped_dsn) as conn:
                assert conn.execute(
                    """select count(*),max(follower_count)
                       from account_metric_snapshot where account_id=%s""",
                    (account_id,),
                ).fetchone() == (1, 1755)
                assert conn.execute(
                    """select count(*) from external_api_call
                       where endpoint_key='douyin.app.user_profile'"""
                ).fetchone()[0] == 2
                cost, currency, basis = conn.execute(
                    """select api_cost,cost_currency,summary->>'api_cost_basis'
                       from pipeline_run where id=%s""",
                    (first.run_id,),
                ).fetchone()
                assert float(cost) == pytest.approx(0.001)
                assert (currency, basis) == ("USD", "estimated")
                assert conn.execute(
                    """select api_cost,summary->>'api_cost_basis'
                       from pipeline_run where id=%s""",
                    (second.run_id,),
                ).fetchone() == (0, "actual")
                assert conn.execute(
                    """select metadata->>'pipeline_run_id',metadata->>'project_id'
                       from external_api_call where cached=false limit 1""",
                ).fetchone() == (str(first.run_id), str(project_id))
            with psycopg.connect(scoped_dsn) as conn:
                conn.execute(
                    """update project_video_inclusion set status='accepted'
                       where project_id=%s""", (project_id,),
                )
                conn.execute(
                    """update research_project_member set status='suspended'
                       where project_id=%s and actor_id=%s""", (project_id, ACTOR),
                )
            with pytest.raises(PermissionError, match="accepted project video"):
                refresh_project_account_profiles(
                    dsn=scoped_dsn, project_id=project_id,
                    video_platform_ids=[video_ids[0]], actor=ACTOR,
                    api_key="fixture-key", transport=transport,
                )
            with psycopg.connect(scoped_dsn) as conn:
                conn.execute(
                    """update research_project_member set status='active'
                       where project_id=%s and actor_id=%s""", (project_id, ACTOR),
                )
                conn.execute(
                    """update external_api_response set expires_at=now()-interval '1 second'
                       where endpoint_key='douyin.app.user_profile'""",
                )
                for stable_id, video_id, author in (
                    ("uid-only", "7683330565018794225", {"uid": "uid-only"}),
                    ("sec-profile-second", "7685956081940290981",
                     {"sec_uid": "sec-profile-second"}),
                ):
                    next_account = conn.execute(
                        """insert into source_account(platform,platform_account_id)
                           values ('douyin',%s) returning id""", (stable_id,),
                    ).fetchone()[0]
                    next_video = conn.execute(
                        """insert into source_video(platform,platform_video_id,account_id)
                           values ('douyin',%s,%s) returning id""",
                        (video_id, next_account),
                    ).fetchone()[0]
                    conn.execute(
                        """insert into project_video_inclusion(
                             project_id,video_id,source_type,status)
                           values (%s,%s,'manual','accepted')""",
                        (project_id, next_video),
                    )
                    fingerprint = f"video-evidence-{video_id}"
                    conn.execute(
                        """insert into external_api_response(
                             provider,platform,endpoint_key,request_fingerprint,
                             http_status,response_code,response_body)
                           values ('tikhub','douyin','douyin.app.multi_video',%s,
                             200,'200',%s::jsonb)""",
                        (fingerprint, json.dumps({"code": 200, "data": {
                            "aweme_list": [{"aweme_id": video_id, "author": author}],
                        }})),
                    )
                    conn.execute(
                        """insert into discovery_event(
                             video_id,provider,source_type,source_key,
                             observation_key,metadata)
                           values (%s,'tikhub','manual','fixture',%s,%s::jsonb)""",
                        (next_video, fingerprint, json.dumps({
                            "request_fingerprint": fingerprint,
                        })),
                    )
            with pytest.raises(PermissionError, match="verified sec_user_id"):
                refresh_project_account_profiles(
                    dsn=scoped_dsn, project_id=project_id,
                    video_platform_ids=["7683330565018794225"],
                    actor=ACTOR, api_key="fixture-key", transport=transport,
                )
            mismatch_transport = MismatchedSecondTransport()
            with pytest.raises(RuntimeError, match="account profile refresh failed"):
                refresh_project_account_profiles(
                    dsn=scoped_dsn, project_id=project_id,
                    video_platform_ids=[video_ids[0], "7685956081940290981"],
                    actor=ACTOR, api_key="fixture-key", transport=mismatch_transport,
                )
            assert mismatch_transport.calls == [
                "sec-profile-test", "sec-profile-second",
            ]
            with psycopg.connect(scoped_dsn) as conn:
                failed = conn.execute(
                    """select status,summary->>'known_estimated_cost_usd',
                              summary->>'unknown_cost_calls'
                       from pipeline_run where run_type='account_profile_refresh'
                         and status='failed'""",
                ).fetchone()
                assert failed == ("failed", "0.001", "1")
        finally:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
