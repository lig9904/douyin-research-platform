from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from importlib import util
from pathlib import Path
from types import SimpleNamespace
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from douyin_research.media_storage import PrivateS3MediaStorage
from douyin_research.media_assets import MediaAssetStore
from douyin_research.project_analysis.las import ProjectLASService, video_manifest
from douyin_research.providers.volcengine_las import (
    LAS_MODEL_ID, LASPoll, LASSubmission,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "db/schema.sql"
DSN = os.getenv("TEST_DATABASE_URL")


class FakeStorage(PrivateS3MediaStorage):
    def __init__(self, *, actual_version: str = "test-version-A") -> None:
        self._config = SimpleNamespace(bucket="private")
        self.verified = 0
        self.signed = 0
        self.actual_version = actual_version

    def verified_version(self, stored, *, version_id: str | None = None) -> str:
        assert stored.sha256 == "a" * 64
        assert stored.size == 1024
        if version_id is not None and version_id != self.actual_version:
            raise ValueError("stored object version changed")
        self.verified += 1
        return self.actual_version

    def presigned_read_url(
        self, key: str, *, expires_in: int = 900, version_id: str | None = None
    ) -> str:
        assert key == "sha256/aa/" + "a" * 64
        assert expires_in == 3600
        assert version_id == "test-version-A"
        self.signed += 1
        return ("https://media.example/private/video.mp4"
                "?VersionId=test-version-A&X-Amz-Signature=private")


class FakeProvider:
    max_retries = 0

    def __init__(self, *, fail_submit: bool = False) -> None:
        self.submits = 0
        self.polls = 0
        self.fail_submit = fail_submit

    def submit(self, delivery) -> LASSubmission:
        self.submits += 1
        assert "VersionId=test-version-A" in delivery.url
        assert "X-Amz-Signature=private" in delivery.url
        if self.fail_submit:
            raise TimeoutError("private URL must not be logged")
        return LASSubmission("provider-task-one", "PENDING", "d" * 64)

    def poll(self, task_id: str) -> LASPoll:
        self.polls += 1
        assert task_id == "provider-task-one"
        return LASPoll(
            task_id, "COMPLETED", "0", "e" * 64,
            "[00:00-00:10] 画面和声音共同推进角色选择。",
            ({"model_name": LAS_MODEL_ID,
              "token_usage": {"prompt_tokens": 10, "completion_tokens": 2}},),
            datetime.now(timezone.utc).isoformat(),
        )


@contextmanager
def _database():
    assert DSN
    namespace = f"las_service_{uuid4().hex}"
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(SCHEMA.read_text(encoding="utf-8"), prepare=False)
            org = conn.execute(
                "insert into research_organization(slug,name) values('las-test','LAS') returning id"
            ).fetchone()[0]
            project = conn.execute(
                """insert into research_project(organization_id,slug,name,status)
                   values(%s,'whole-video','LAS','active') returning id""", (org,),
            ).fetchone()[0]
            conn.execute(
                """insert into research_project_member(project_id,actor_id,role,status)
                   values(%s,'owner@example.com','owner','active')""", (project,),
            )
            video = conn.execute(
                """insert into source_video(platform,platform_video_id,title)
                   values('douyin',%s,'Project LAS case') returning id""",
                (f"las-{uuid4().hex}",),
            ).fetchone()[0]
            conn.execute(
                """insert into project_video_inclusion(project_id,video_id,source_type,status)
                   values(%s,%s,'manual','accepted')""", (project, video),
            )
            asset = conn.execute(
                """insert into media_asset(video_id,kind,storage_location,bucket,object_key,
                     content_sha256,size_bytes,content_type)
                   values(%s,'video','s3','private',%s,%s,1024,'video/mp4') returning id""",
                (video, "sha256/aa/" + "a" * 64, "a" * 64),
            ).fetchone()[0]
            service_dsn = make_conninfo(DSN, options=f"-c search_path={namespace}")
            yield service_dsn, conn, project, video, asset
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))


def _services(dsn: str):
    human = ProjectLASService(dsn, delivery_origin="https://media.example",
                              account_scope="test-account")
    worker = ProjectLASService(dsn, delivery_origin="https://media.example",
                               trusted_worker_actor="worker@example.com",
                               trusted_storage_location="s3", trusted_bucket="private",
                               account_scope="test-account")
    return human, worker


def _approve_and_prepare(human, project, video, asset, version: str):
    storage = FakeStorage()
    preview = human.preview(project_id=project, video_id=video, asset_id=asset,
                            review_version=version, storage=storage)
    assert preview["object_version_id"] == "test-version-A"
    assert preview["manifest_fingerprint"] == preview["asset_manifest_fingerprint"]
    approval = human.approve(
        project_id=project, video_id=video, asset_id=asset, review_version=version,
        storage=storage, object_version_id=preview["object_version_id"],
        expected_manifest_fingerprint=preview["manifest_fingerprint"],
        consent_statement="I approve this exact project video for whole-video LAS analysis.",
    )
    assert storage.verified == 2
    prepared = human.prepare(project_id=project, video_id=video,
                             review_id=approval["review_id"])
    return approval, prepared


def _authorize_machine_and_prepare(human, project, video, asset):
    storage = FakeStorage()
    preview = human.preview(project_id=project, video_id=video, asset_id=asset,
                            review_version="las-video-v2", storage=storage)
    authorization = human.authorize_machine_first(
        project_id=project, video_id=video, asset_id=asset,
        storage=storage, object_version_id=preview["object_version_id"],
        expected_manifest_fingerprint=preview["manifest_fingerprint"],
        consent_statement="I authorize machine analysis without claiming a human video review.",
    )
    prepared = human.prepare_machine_first(
        project_id=project, video_id=video,
        authorization_id=authorization["authorization_id"],
    )
    return authorization, prepared


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_las_machine_first_is_authorized_without_false_human_review(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    with _database() as (dsn, conn, project, video, asset):
        human, worker = _services(dsn)
        authorization, prepared = _authorize_machine_and_prepare(
            human, project, video, asset
        )
        assert authorization["status"] == "authorized"
        assert authorization["human_review_status"] == "not_asserted"
        assert conn.execute("select count(*) from project_las_video_review").fetchone()[0] == 0
        row = conn.execute(
            """select video_review_id,video_authorization_id,authorized_by,
                      object_version_id from project_las_analysis_attempt where id=%s""",
            (prepared["attempt_id"],),
        ).fetchone()
        assert row[0] is None
        assert str(row[1]) == authorization["authorization_id"]
        assert row[2:] == ("owner@example.com", "test-version-A")
        assert human.prepare_machine_first(
            project_id=project, video_id=video,
            authorization_id=authorization["authorization_id"],
        )["created"] is False
        assets = human.list_assets(project_id=project, video_id=video,
                                   review_version="las-video-v2")["assets"]
        assert assets[0]["review_status"] == "not_reviewed"
        assert assets[0]["machine_authorization_status"] == "authorized"
        spec = util.spec_from_file_location(
            "las_machine_scheduler_test",
            ROOT / "windmill/f/content_research/project_las/run_pending.py",
        )
        assert spec and spec.loader
        scheduler = util.module_from_spec(spec)
        spec.loader.exec_module(scheduler)
        assert scheduler._pending(dsn, "test-account") == ([prepared["attempt_id"]], [])
        provider = FakeProvider()
        sent = worker.dispatch(attempt_id=prepared["attempt_id"],
                               storage=FakeStorage(), provider=provider)
        assert sent["status"] == "submitted" and provider.submits == 1
        assert worker.dispatch(attempt_id=prepared["attempt_id"],
                               storage=FakeStorage(), provider=provider)["external_calls"] == 0
        result = worker.poll(attempt_id=prepared["attempt_id"], provider=provider)
        assert result["status"] == "completed" and provider.polls == 1
        receipt = conn.execute(
            """select binding_basis,object_version_id,estimated_cost
                 from project_las_analysis_receipt where attempt_id=%s""",
            (prepared["attempt_id"],),
        ).fetchone()
        assert receipt == ("authorized_machine_first_and_result", "test-version-A", None)
        visible = human.status(project_id=project, video_id=video)["runs"][0]
        assert visible["review_bound"] is False
        assert visible["machine_authorization_bound"] is True
        assert visible["provenance"] == "provider_machine_only"


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_las_machine_authorization_revocation_prevents_submit(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    with _database() as (dsn, conn, project, video, asset):
        human, worker = _services(dsn)
        authorization, prepared = _authorize_machine_and_prepare(
            human, project, video, asset
        )
        assert human.revoke_machine_authorization(
            project_id=project,
            authorization_id=authorization["authorization_id"],
        )["status"] == "revoked"
        assert conn.execute(
            "select status,error_code from project_las_analysis_attempt where id=%s",
            (prepared["attempt_id"],),
        ).fetchone() == ("cancelled", "authorization_revoked_before_dispatch")
        provider = FakeProvider()
        assert worker.dispatch(attempt_id=prepared["attempt_id"],
                               storage=FakeStorage(), provider=provider)["external_calls"] == 0
        assert provider.submits == 0
        assert conn.execute("select count(*) from project_las_video_review").fetchone()[0] == 0
        preview = human.preview(
            project_id=project, video_id=video, asset_id=asset,
            review_version="las-video-v2", storage=FakeStorage(),
        )
        renewed = human.authorize_machine_first(
            project_id=project, video_id=video, asset_id=asset,
            storage=FakeStorage(), object_version_id=preview["object_version_id"],
            expected_manifest_fingerprint=preview["manifest_fingerprint"],
            consent_statement="I authorize machine analysis without claiming a human video review.",
        )
        assert renewed["authorization_id"] != authorization["authorization_id"]
        assert conn.execute(
            """select status from project_las_video_authorization
               where id=%s""", (authorization["authorization_id"],),
        ).fetchone()[0] == "revoked"
        assert human.list_assets(
            project_id=project, video_id=video, review_version="las-video-v2",
        )["assets"][0]["machine_authorization_id"] == renewed["authorization_id"]


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_machine_authorization_rechecks_role_after_concurrent_revocation(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    with _database() as (dsn, conn, project, video, asset):
        human, _ = _services(dsn)
        preview = human.preview(
            project_id=project, video_id=video, asset_id=asset,
            review_version="las-video-v2", storage=FakeStorage(),
        )
        with psycopg.connect(dsn) as revoker:
            revoker.execute("select 1 from research_project where id=%s for update",
                            (project,))
            revoker.execute(
                """insert into research_project_member(project_id,actor_id,role,status)
                   values(%s,'owner@example.com','owner','revoked')""", (project,),
            )
            started = Event()

            def authorize():
                started.set()
                return human.authorize_machine_first(
                    project_id=project, video_id=video, asset_id=asset,
                    storage=FakeStorage(), object_version_id=preview["object_version_id"],
                    expected_manifest_fingerprint=preview["manifest_fingerprint"],
                    consent_statement=(
                        "I authorize machine analysis without claiming a human video review."
                    ),
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(authorize)
                assert started.wait(2)
                revoker.commit()
                with pytest.raises(PermissionError, match="project owner/admin"):
                    future.result(timeout=5)
        assert conn.execute(
            "select count(*) from project_las_video_authorization"
        ).fetchone()[0] == 0


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_machine_dispatch_and_revocation_serialize_without_replay(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    with _database() as (dsn, conn, project, video, asset):
        human, worker = _services(dsn)
        authorization, prepared = _authorize_machine_and_prepare(
            human, project, video, asset
        )
        entered_submit, release_submit = Event(), Event()

        class HeldProvider(FakeProvider):
            def submit(self, delivery):
                entered_submit.set()
                assert release_submit.wait(5)
                return super().submit(delivery)

        provider = HeldProvider()
        with ThreadPoolExecutor(max_workers=2) as pool:
            dispatched = pool.submit(
                worker.dispatch, attempt_id=prepared["attempt_id"],
                storage=FakeStorage(), provider=provider,
            )
            assert entered_submit.wait(5)
            revoked = pool.submit(
                human.revoke_machine_authorization,
                project_id=project,
                authorization_id=authorization["authorization_id"],
            )
            release_submit.set()
            assert dispatched.result(timeout=8)["status"] == "submitted"
            assert revoked.result(timeout=8)["status"] == "revoked"
        assert provider.submits == 1
        assert worker.dispatch(
            attempt_id=prepared["attempt_id"],
            storage=FakeStorage(), provider=provider,
        )["external_calls"] == 0
        assert conn.execute(
            "select status from project_las_analysis_attempt where id=%s",
            (prepared["attempt_id"],),
        ).fetchone()[0] == "submitted"


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_las_machine_unknown_submit_never_replays(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    with _database() as (dsn, conn, project, video, asset):
        human, worker = _services(dsn)
        _, prepared = _authorize_machine_and_prepare(human, project, video, asset)
        provider = FakeProvider(fail_submit=True)
        first = worker.dispatch(attempt_id=prepared["attempt_id"],
                                storage=FakeStorage(), provider=provider)
        assert first["status"] == "unknown" and first["external_calls"] == 1
        second = worker.dispatch(attempt_id=prepared["attempt_id"],
                                 storage=FakeStorage(), provider=provider)
        assert second["external_calls"] == 0 and provider.submits == 1
        assert conn.execute(
            """select status,submission_count,provider_task_ref
                 from project_las_analysis_attempt where id=%s""",
            (prepared["attempt_id"],),
        ).fetchone() == ("unknown", 1, None)


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_las_real_db_claim_submit_poll_and_private_receipt(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    with _database() as (dsn, conn, project, video, asset):
        human, worker = _services(dsn)
        approval, prepared = _approve_and_prepare(human, project, video, asset, "las-video-v2")
        assert human.list_assets(project_id=project, video_id=video,
                                 review_version="las-video-v2")["assets"][0]["review_status"] == "approved"
        assert prepared["status"] == "prepared"
        assert human.prepare(project_id=project, video_id=video,
                             review_id=approval["review_id"])["created"] is False
        changed_account = ProjectLASService(
            dsn, delivery_origin="https://media.example", account_scope="other-account",
        )
        with pytest.raises(PermissionError, match="different account scope"):
            changed_account.prepare(project_id=project, video_id=video,
                                    review_id=approval["review_id"])
        storage, provider = FakeStorage(), FakeProvider()
        sent = worker.dispatch(attempt_id=prepared["attempt_id"], storage=storage,
                               provider=provider)
        assert sent["status"] == "submitted" and provider.submits == 1
        submitted_at = conn.execute(
            "select provider_task_recorded_at from project_las_analysis_attempt where id=%s",
            (prepared["attempt_id"],),
        ).fetchone()[0]
        assert submitted_at is not None
        replay = worker.dispatch(attempt_id=prepared["attempt_id"], storage=storage,
                                 provider=provider)
        assert replay["external_calls"] == 0 and provider.submits == 1
        result = worker.poll(attempt_id=prepared["attempt_id"], provider=provider)
        assert result["status"] == "completed" and result["cost_basis"] == "unknown_until_supplier_bill"
        assert worker.poll(attempt_id=prepared["attempt_id"], provider=provider)["external_calls"] == 0
        assert provider.polls == 1
        row = conn.execute(
            """select a.status,a.submission_count,a.video_review_id,r.estimated_cost,
                      r.business_code,o.final_summary
                 from project_las_analysis_attempt a
                 join project_las_analysis_receipt r on r.attempt_id=a.id
                 join project_las_analysis_result o on o.receipt_id=r.id
                where a.id=%s""", (prepared["attempt_id"],),
        ).fetchone()
        assert row[0] == "completed" and row[1] == 1
        assert row[3] is None and row[4] == 0
        assert str(row[2]) == approval["review_id"]
        assert "角色选择" in row[5]
        assert storage.verified == 1 and storage.signed == 1
        assert conn.execute(
            "select object_version_id from project_las_analysis_attempt where id=%s",
            (prepared["attempt_id"],),
        ).fetchone()[0] == "test-version-A"
        assert conn.execute(
            "select object_version_id from project_las_analysis_receipt where attempt_id=%s",
            (prepared["attempt_id"],),
        ).fetchone()[0] == "test-version-A"
        visible = human.status(project_id=project, video_id=video)
        assert visible["runs"][0]["provenance"] == "provider_machine_only"
        assert visible["runs"][0]["record_mode"] == "live_pre_dispatch"
        assert visible["runs"][0]["review_bound"] is True
        assert "角色选择" in visible["runs"][0]["final_summary"]
        conn.execute(
            """insert into project_las_analysis_attempt(
                 project_id,video_id,asset_id,asset_sha256,task_key,attempt_no,
                 provider,account_scope,cost_currency,status,authorization_ref,
                 authorized_by,record_mode,input_fingerprint)
               values(%s,%s,%s,%s,%s,1,'volcengine_las','test-account','CNY',
                      'failed','historical-readback','owner@example.com',
                      'historical_backfill',%s)""",
            (project, video, asset, "a" * 64, f"historical-las:{uuid4().hex}", "f" * 64),
        )
        historical = next(run for run in human.status(project_id=project, video_id=video)["runs"]
                          if run["record_mode"] == "historical_backfill")
        assert historical["review_bound"] is False
        assert historical["final_summary"] is None
        monkeypatch.setenv("WM_END_USER_EMAIL", "outsider@example.com")
        with pytest.raises(PermissionError, match="result access denied"):
            human.status(project_id=project, video_id=video)


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_las_poll_rejects_wrong_account_and_uses_provider_task_age(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    with _database() as (dsn, conn, project, video, asset):
        human, worker = _services(dsn)
        storage = FakeStorage()
        preview = human.preview(project_id=project, video_id=video, asset_id=asset,
                                review_version="las-video-v2", storage=storage)
        approval = human.approve(
            project_id=project, video_id=video, asset_id=asset, review_version="las-video-v2",
            storage=storage, object_version_id=preview["object_version_id"],
            expected_manifest_fingerprint=preview["manifest_fingerprint"],
            consent_statement="I approve this exact project video for whole-video LAS analysis.",
        )
        prepared_id = conn.execute(
            """insert into project_las_analysis_attempt(
                 project_id,video_id,asset_id,asset_sha256,task_key,attempt_no,
                 provider,account_scope,cost_currency,status,authorization_ref,
                 authorized_by,record_mode,input_fingerprint,video_review_id,requested_by,
                 object_version_id,created_at)
               values(%s,%s,%s,%s,%s,1,'volcengine_las','test-account','CNY',
                      'prepared',%s,'owner@example.com','live_pre_dispatch',%s,%s,'owner@example.com',%s,
                      now()-interval '80 hours') returning id""",
            (project, video, asset, "a" * 64, f"project-las-v2:{uuid4().hex}",
             approval["review_id"], "f" * 64, approval["review_id"], "test-version-A"),
        ).fetchone()[0]
        provider = FakeProvider()
        worker.dispatch(attempt_id=prepared_id, storage=FakeStorage(),
                        provider=provider)
        recorded_at, created_at = conn.execute(
            """select provider_task_recorded_at,created_at
                 from project_las_analysis_attempt where id=%s""",
            (prepared_id,),
        ).fetchone()
        assert recorded_at - created_at > timedelta(hours=70)
        other_account = ProjectLASService(
            dsn, delivery_origin="https://media.example",
            trusted_worker_actor="worker@example.com", trusted_storage_location="s3",
            trusted_bucket="private", account_scope="other-account",
        )
        with pytest.raises(PermissionError, match="account scope mismatch"):
            other_account.poll(attempt_id=prepared_id, provider=provider)
        assert provider.polls == 0
        assert worker.poll(attempt_id=prepared_id, provider=provider)["status"] == "completed"
        assert provider.polls == 1


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_las_submit_timeout_remains_unknown_and_never_replays(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    with _database() as (dsn, conn, project, video, asset):
        human, worker = _services(dsn)
        _, prepared = _approve_and_prepare(human, project, video, asset, "las-video-v2")
        storage, provider = FakeStorage(), FakeProvider(fail_submit=True)
        first = worker.dispatch(attempt_id=prepared["attempt_id"], storage=storage,
                                provider=provider)
        assert first["status"] == "unknown" and first["external_calls"] == 1
        second = worker.dispatch(attempt_id=prepared["attempt_id"], storage=storage,
                                 provider=provider)
        assert second["external_calls"] == 0 and provider.submits == 1
        assert worker.poll(attempt_id=prepared["attempt_id"], provider=provider)[
            "needs_manual_reconciliation"] is True
        assert conn.execute(
            """select status,submission_count,provider_task_ref,error_code
                 from project_las_analysis_attempt where id=%s""",
            (prepared["attempt_id"],),
        ).fetchone() == ("unknown", 1, None, "submit_outcome_unknown")


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_las_revocation_cancels_prepared_task_before_paid_dispatch(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    with _database() as (dsn, conn, project, video, asset):
        human, worker = _services(dsn)
        approval, prepared = _approve_and_prepare(human, project, video, asset, "las-video-v2")
        assert human.revoke(project_id=project, review_id=approval["review_id"])["status"] == "revoked"
        assert conn.execute(
            "select status,error_code from project_las_analysis_attempt where id=%s",
            (prepared["attempt_id"],),
        ).fetchone() == ("cancelled", "review_revoked_before_dispatch")
        provider = FakeProvider()
        assert worker.dispatch(attempt_id=prepared["attempt_id"], storage=FakeStorage(),
                               provider=provider)["external_calls"] == 0
        assert provider.submits == 0


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_las_poll_survives_missing_storage_configuration(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    with _database() as (dsn, conn, project, video, asset):
        human, submit_worker = _services(dsn)
        _, prepared = _approve_and_prepare(human, project, video, asset, "las-video-v2")
        provider = FakeProvider()
        submit_worker.dispatch(attempt_id=prepared["attempt_id"], storage=FakeStorage(),
                               provider=provider)
        poll_worker = ProjectLASService(
            dsn, delivery_origin="https://poll-only.invalid",
            trusted_worker_actor="worker@example.com", account_scope="test-account",
        )
        assert poll_worker.poll(attempt_id=prepared["attempt_id"], provider=provider)[
            "status"] == "completed"
        assert provider.polls == 1


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_las_rejects_other_model_usage_without_false_receipt(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    with _database() as (dsn, conn, project, video, asset):
        human, worker = _services(dsn)
        _, prepared = _approve_and_prepare(human, project, video, asset, "las-video-v2")
        worker.dispatch(attempt_id=prepared["attempt_id"], storage=FakeStorage(),
                        provider=FakeProvider())

        class WrongModelProvider(FakeProvider):
            def poll(self, task_id: str) -> LASPoll:
                result = super().poll(task_id)
                return replace(result, token_usages=(
                    {"model_name": "different-model", "token_usage": {"total_tokens": 1}},
                ))

        wrong = WrongModelProvider()
        result = worker.poll(attempt_id=prepared["attempt_id"], provider=wrong)
        assert result["status"] == "unknown" and result["needs_manual_reconciliation"]
        assert conn.execute(
            "select count(*) from project_las_analysis_receipt where attempt_id=%s",
            (prepared["attempt_id"],),
        ).fetchone()[0] == 0


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_las_expired_unknown_task_is_flagged_once_without_provider_call(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    with _database() as (dsn, conn, project, video, asset):
        human, worker = _services(dsn)
        _, prepared = _approve_and_prepare(human, project, video, asset, "las-video-v2")
        worker.dispatch(attempt_id=prepared["attempt_id"], storage=FakeStorage(),
                        provider=FakeProvider())
        worker._mark_unknown(prepared["attempt_id"], "poll_outcome_unknown")
        # This isolated test schema owns its trigger; backdate the recorded
        # timestamp only to model a supplier task crossing the 70-hour window.
        conn.execute("alter table project_las_analysis_attempt disable trigger trg_project_las_provider_task_time")
        try:
            conn.execute(
                """update project_las_analysis_attempt
                      set provider_task_recorded_at=now()-interval '71 hours'
                    where id=%s""", (prepared["attempt_id"],),
            )
        finally:
            conn.execute("alter table project_las_analysis_attempt enable trigger trg_project_las_provider_task_time")
        provider = FakeProvider()
        result = worker.poll(attempt_id=prepared["attempt_id"], provider=provider)
        assert result["needs_manual_reconciliation"] and result["external_calls"] == 0
        assert provider.polls == 0
        assert conn.execute(
            "select error_code from project_las_analysis_attempt where id=%s",
            (prepared["attempt_id"],),
        ).fetchone()[0] == "provider_task_expiry_near"
        assert worker.poll(attempt_id=prepared["attempt_id"], provider=provider)[
            "external_calls"] == 0


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_las_legacy_review_cannot_prepare_new_paid_attempt(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    with _database() as (dsn, conn, project, video, asset):
        human, worker = _services(dsn)
        reference = MediaAssetStore(dsn).get(video, asset)
        assert reference is not None
        legacy_fingerprint = video_manifest(reference, "https://media.example")
        # Model a review written before migration 042. The migration preserves
        # these rows; its new-insert trigger rejects creation of further ones.
        conn.execute("alter table project_las_video_review disable trigger trg_project_las_review_version_binding")
        try:
            review_id = conn.execute(
                """insert into project_las_video_review(
                     project_id,video_id,asset_id,asset_sha256,asset_manifest_fingerprint,
                     delivery_origin,review_version,review_statement,binding_scheme)
                   values(%s,%s,%s,%s,%s,'https://media.example','las-video-v2',
                          %s::jsonb,'legacy_key_v1') returning id""",
                (project, video, asset, "a" * 64, legacy_fingerprint,
                 '{"consent_statement":"I approved this video before version binding."}'),
            ).fetchone()[0]
            conn.execute(
                """update project_las_video_review
                      set status='approved',reviewed_by='owner@example.com' where id=%s""",
                (review_id,),
            )
        finally:
            conn.execute("alter table project_las_video_review enable trigger trg_project_las_review_version_binding")
        assert human.list_assets(project_id=project, video_id=video,
                                 review_version="las-video-v2")["assets"][0][
                                     "review_status"] == "legacy_unbound"
        with pytest.raises((PermissionError, ValueError)):
            human.prepare(project_id=project, video_id=video, review_id=review_id)
        assert conn.execute(
            "select count(*) from project_las_analysis_attempt where video_review_id=%s",
            (review_id,),
        ).fetchone()[0] == 0
        # Simulate an already-submitted 041 request crossing the 042 cutover.
        # It may Poll its original provider task and settle a clearly legacy
        # receipt, but must never be resubmitted or assigned a fabricated ID.
        conn.execute("alter table project_las_analysis_attempt disable trigger trg_project_las_attempt_version_binding")
        try:
            old_attempt = conn.execute(
                """insert into project_las_analysis_attempt(
                     project_id,video_id,asset_id,asset_sha256,task_key,attempt_no,
                     provider,account_scope,cost_currency,status,authorization_ref,
                     authorized_by,record_mode,input_fingerprint,video_review_id,requested_by)
                   values(%s,%s,%s,%s,'legacy-submitted',1,'volcengine_las','test-account',
                          'CNY','prepared',%s,'owner@example.com','live_pre_dispatch',
                          %s,%s,'owner@example.com') returning id""",
                (project, video, asset, "a" * 64, str(review_id), "c" * 64, review_id),
            ).fetchone()[0]
            conn.execute(
                "update project_las_analysis_attempt set status='submitting',submission_count=1 "
                "where id=%s", (old_attempt,),
            )
            conn.execute(
                """update project_las_analysis_attempt
                      set status='submitted',provider_task_ref='provider-task-one',
                          submit_job_ref='old-worker-job'
                    where id=%s""", (old_attempt,),
            )
        finally:
            conn.execute("alter table project_las_analysis_attempt enable trigger trg_project_las_attempt_version_binding")
        provider = FakeProvider()
        settled = worker.poll(attempt_id=old_attempt, provider=provider)
        assert settled["status"] == "completed" and settled["external_calls"] == 1
        assert provider.polls == 1 and provider.submits == 0
        assert conn.execute(
            "select object_version_id from project_las_analysis_receipt where attempt_id=%s",
            (old_attempt,),
        ).fetchone()[0] is None


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_project_las_version_mismatch_makes_zero_provider_submits(monkeypatch) -> None:
    monkeypatch.setenv("WM_END_USER_EMAIL", "owner@example.com")
    with _database() as (dsn, conn, project, video, asset):
        human, worker = _services(dsn)
        _, prepared = _approve_and_prepare(human, project, video, asset, "las-video-v2")
        provider = FakeProvider()
        with pytest.raises((PermissionError, ValueError)):
            worker.dispatch(attempt_id=prepared["attempt_id"],
                            storage=FakeStorage(actual_version="test-version-B"),
                            provider=provider)
        assert provider.submits == 0
        assert conn.execute(
            "select submission_count from project_las_analysis_attempt where id=%s",
            (prepared["attempt_id"],),
        ).fetchone()[0] == 0
