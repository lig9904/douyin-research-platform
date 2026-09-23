from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db/migrations/021_project_account_foundation.sql"
SCHEMA = ROOT / "db/schema.sql"
DSN = os.getenv("TEST_DATABASE_URL")


def test_project_account_foundation_is_present_in_schema_and_migration() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    schema = SCHEMA.read_text(encoding="utf-8")
    for source in (migration, schema):
        assert "create table if not exists research_organization" in source
        assert "create table if not exists research_project" in source
        assert "create table if not exists research_project_member" in source
        assert "create table if not exists research_subject" in source
        assert "create table if not exists project_account_relation" in source
        assert "create table if not exists account_group" in source
        assert "create table if not exists account_group_member" in source
        assert "create table if not exists account_identity_link" in source
        assert "create table if not exists account_authorization" in source
        assert "foreign key (subject_id, project_id)" in source
        assert "references research_subject(id, project_id)" in source
        assert "unique (id, project_id, source_account_id)" in source
        assert "foreign key (project_account_relation_id, project_id, source_account_id)" in source
        assert "idempotency_key text not null" in source
        assert "uq_project_account_relation_idempotency" in source
        assert "uq_account_identity_link_idempotency" in source
        assert "uq_account_authorization_idempotency" in source
        assert "where id = new.project_account_relation_id for share" in source
        assert "idx_research_project_member_actor_project_effective" in source
        assert "revoked_at timestamptz" in source
        assert "trg_revoke_authorizations_for_inactive_relation" in source
        assert "trg_enforce_authorization_relation_not_inactive" in source
        assert "create or replace view effective_account_authorization" in source
        assert "cardinality(task_roles) > 0" in source
        assert "cardinality(field_allowlist) > 0" in source
        assert "cardinality(operation_allowlist) > 0" in source
        assert "credential_ref is an opaque controlled-backend reference" in source
        assert "insert into project_account_relation" not in source.lower()
        assert "update source_account" not in source.lower()


@pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is required")
def test_021_keeps_project_subject_and_authorization_boundaries_in_database() -> None:
    assert DSN
    namespace = f"migration_021_{uuid4().hex}"
    migration = MIGRATION.read_text(encoding="utf-8")
    bootstrap = """
        create extension if not exists pgcrypto;
        create table source_account(
          id uuid primary key default gen_random_uuid(),
          platform text not null default 'douyin'
        );
    """
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(sql.SQL("create schema {}").format(sql.Identifier(namespace)))
        try:
            conn.execute(sql.SQL("set search_path to {}").format(sql.Identifier(namespace)))
            conn.execute(bootstrap, prepare=False)
            conn.execute(migration, prepare=False)
            org_a = conn.execute(
                "insert into research_organization(slug, name) values ('org-a', '组织A') returning id"
            ).fetchone()[0]
            org_b = conn.execute(
                "insert into research_organization(slug, name) values ('org-b', '组织B') returning id"
            ).fetchone()[0]
            project_a = conn.execute(
                "insert into research_project(organization_id, slug, name) values (%s, 'project-a', '项目A') returning id",
                (org_a,),
            ).fetchone()[0]
            project_b = conn.execute(
                "insert into research_project(organization_id, slug, name) values (%s, 'project-b', '项目B') returning id",
                (org_b,),
            ).fetchone()[0]
            subject_b = conn.execute(
                "insert into research_subject(project_id, name, subject_type) values (%s, '对象B', 'brand') returning id",
                (project_b,),
            ).fetchone()[0]
            account = conn.execute("insert into source_account default values returning id").fetchone()[0]
            other_account = conn.execute("insert into source_account default values returning id").fetchone()[0]

            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                conn.execute(
                    "insert into research_subject(project_id, parent_subject_id, name, subject_type) values (%s, %s, '错误父对象', 'product')",
                    (project_a, subject_b),
                )
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                conn.execute(
                    """insert into project_account_relation(
                         project_id, idempotency_key, subject_id, source_account_id, relation_type, task_roles, evidence_ref
                       ) values (%s, 'relation.cross.project.001', %s, %s, 'official', array['publish_channel'], 'evidence://cross-project')""",
                    (project_a, subject_b, account),
                )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    """insert into project_account_relation(
                         project_id, idempotency_key, source_account_id, relation_type, task_roles, evidence_ref
                       ) values (%s, 'relation.empty.role.001', %s, 'official', '{}', 'evidence://candidate/empty-role')""",
                    (project_a, account),
                )

            relation = conn.execute(
                """insert into project_account_relation(
                     project_id, idempotency_key, source_account_id, relation_type, task_roles, evidence_ref
                   ) values (%s, 'relation.primary.001', %s, 'official', array['publish_channel'], 'evidence://relation/1')
                   returning id""",
                (project_a, account),
            ).fetchone()[0]
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    """insert into account_authorization(
                         organization_id, project_id, idempotency_key, project_account_relation_id, source_account_id,
                         provider, authorization_kind, purpose, field_allowlist, operation_allowlist,
                         credential_ref, granted_by, evidence_ref
                       ) values (
                         %s, %s, 'authorization.inline.001', %s, %s, 'douyin', 'project_account', 'read_metrics',
                         array['video.play_count'], array['video.metrics.read'],
                         'inline-access-key', 'owner-1', 'evidence://grant/inline'
                       )""",
                    (org_a, project_a, relation, account),
                )
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                conn.execute(
                    """insert into account_authorization(
                         organization_id, project_id, idempotency_key, project_account_relation_id, source_account_id,
                         provider, authorization_kind, purpose, field_allowlist, operation_allowlist,
                         credential_ref, granted_by, evidence_ref
                       ) values (
                         %s, %s, 'authorization.other.account.001', %s, %s, 'douyin', 'project_account', 'read_metrics',
                         array['video.play_count'], array['video.metrics.read'],
                         'secret://controlled/reference', 'owner-1', 'evidence://grant/1'
                       )""",
                    (org_a, project_a, relation, other_account),
                )

            # Retries must collide on a caller-owned stable key, not a freshly
            # evaluated now() value.  A different payload with the same key is
            # deliberately rejected for adapter-level conflict handling.
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    """insert into project_account_relation(
                         project_id, idempotency_key, source_account_id, relation_type, task_roles, evidence_ref
                       ) values (%s, 'relation.primary.001', %s, 'competitor',
                         array['benchmark_sample'], 'evidence://relation/retry')""",
                    (project_a, account),
                )
            with pytest.raises(psycopg.errors.RaiseException, match="only be active for a verified relation"):
                conn.execute(
                    """insert into account_authorization(
                         organization_id, project_id, idempotency_key, project_account_relation_id, source_account_id,
                         provider, authorization_kind, purpose, field_allowlist, operation_allowlist,
                         credential_ref, status, granted_by, evidence_ref
                       ) values (%s, %s, 'authorization.proposed.active.001', %s, %s,
                         'douyin', 'project_account', 'read_metrics', array['video.play_count'],
                         array['video.metrics.read'], 'secret://controlled/reference', 'active',
                         'owner-1', 'evidence://grant/proposed-active')""",
                    (org_a, project_a, relation, account),
                )
            conn.execute(
                """update project_account_relation
                   set verification_status = 'verified', verified_by = 'owner-1'
                   where id = %s""",
                (relation,),
            )
            authorization = conn.execute(
                """insert into account_authorization(
                     organization_id, project_id, idempotency_key, project_account_relation_id, source_account_id,
                     provider, authorization_kind, purpose, field_allowlist, operation_allowlist,
                     credential_ref, status, granted_by, evidence_ref
                   ) values (%s, %s, 'authorization.primary.001', %s, %s,
                     'douyin', 'project_account', 'read_metrics', array['video.play_count'],
                     array['video.metrics.read'], 'secret://controlled/reference', 'active',
                     'owner-1', 'evidence://grant/active') returning id""",
                (org_a, project_a, relation, account),
            ).fetchone()[0]
            assert authorization
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    """insert into account_authorization(
                         organization_id, project_id, idempotency_key, project_account_relation_id, source_account_id,
                         provider, authorization_kind, purpose, field_allowlist, operation_allowlist,
                         credential_ref, granted_by, evidence_ref
                       ) values (%s, %s, 'authorization.primary.001', %s, %s,
                         'douyin', 'project_account', 'sync', array['video.play_count'],
                         array['video.metrics.read'], 'secret://controlled/reference',
                         'owner-1', 'evidence://grant/retry')""",
                    (org_a, project_a, relation, account),
                )
            future_authorization = conn.execute(
                """insert into account_authorization(
                     organization_id, project_id, idempotency_key, project_account_relation_id, source_account_id,
                     provider, authorization_kind, purpose, field_allowlist, operation_allowlist,
                     credential_ref, status, effective_from, granted_by, evidence_ref
                   ) values (%s, %s, 'authorization.future.draft.001', %s, %s,
                     'douyin', 'project_account', 'sync', array['video.play_count'],
                     array['video.metrics.read'], 'secret://controlled/reference', 'draft',
                     now() + interval '1 day', 'owner-1', 'evidence://grant/future-draft') returning id""",
                (org_a, project_a, relation, account),
            ).fetchone()[0]
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    """insert into account_authorization(
                         organization_id, project_id, idempotency_key, project_account_relation_id, source_account_id,
                         provider, authorization_kind, purpose, field_allowlist, operation_allowlist,
                         credential_ref, status, revoked_at, granted_by, evidence_ref
                       ) values (%s, %s, 'authorization.bad.revoked.001', %s, %s,
                         'douyin', 'project_account', 'sync', array['video.play_count'],
                         array['video.metrics.read'], 'secret://controlled/reference', 'active', now(),
                         'owner-1', 'evidence://grant/bad-revocation')""",
                    (org_a, project_a, relation, account),
                )
            conn.execute(
                "update project_account_relation set verification_status = 'pending' where id = %s",
                (relation,),
            )
            grant_status, grant_revoked_at = conn.execute(
                "select status, revoked_at from account_authorization where id = %s", (authorization,)
            ).fetchone()
            assert grant_status == "revoked"
            assert grant_revoked_at is not None
            conn.execute(
                "update project_account_relation set verification_status = 'verified' where id = %s",
                (relation,),
            )
            with pytest.raises(psycopg.errors.RaiseException, match="cannot be reactivated"):
                conn.execute(
                    "update account_authorization set status = 'active', revoked_at = null where id = %s",
                    (authorization,),
                )
            conn.execute(
                "update project_account_relation set verification_status = 'pending' where id = %s",
                (relation,),
            )
            future_status, future_revoked_at, future_effective_from = conn.execute(
                "select status, revoked_at, effective_from from account_authorization where id = %s",
                (future_authorization,),
            ).fetchone()
            assert future_status == "revoked"
            assert future_revoked_at is not None and future_revoked_at < future_effective_from
            with pytest.raises(psycopg.errors.RaiseException, match="only be active for a verified relation"):
                conn.execute(
                    """insert into account_authorization(
                         organization_id, project_id, idempotency_key, project_account_relation_id, source_account_id,
                         provider, authorization_kind, purpose, field_allowlist, operation_allowlist,
                         credential_ref, status, granted_by, evidence_ref
                       ) values (%s, %s, 'authorization.after.relation.revoked.001', %s, %s,
                         'douyin', 'project_account', 'sync', array['video.play_count'],
                         array['video.metrics.read'], 'secret://controlled/reference', 'active',
                         'owner-1', 'evidence://grant/after-relation-revoked')""",
                    (org_a, project_a, relation, account),
                )
            conn.execute(
                "update project_account_relation set verification_status = 'revoked' where id = %s",
                (relation,),
            )
            with pytest.raises(psycopg.errors.RaiseException, match="cannot be draft"):
                conn.execute(
                    """insert into account_authorization(
                         organization_id, project_id, idempotency_key, project_account_relation_id, source_account_id,
                         provider, authorization_kind, purpose, field_allowlist, operation_allowlist,
                         credential_ref, status, granted_by, evidence_ref
                       ) values (%s, %s, 'authorization.after.relation.revoked.draft.001', %s, %s,
                         'douyin', 'project_account', 'sync', array['video.play_count'],
                         array['video.metrics.read'], 'secret://controlled/reference', 'draft',
                         'owner-1', 'evidence://grant/after-relation-revoked-draft')""",
                    (org_a, project_a, relation, account),
                )

            bounded_relation = conn.execute(
                """insert into project_account_relation(
                     project_id, idempotency_key, source_account_id, relation_type,
                     task_roles, evidence_ref, verification_status, verified_by,
                     effective_from, effective_until
                   ) values (%s, 'relation.bounded.001', %s, 'official',
                     array['publish_channel'], 'evidence://relation/bounded', 'verified',
                     'owner-1', now(), now() + interval '2 days') returning id""",
                (project_a, account),
            ).fetchone()[0]
            with pytest.raises(psycopg.errors.RaiseException, match="within its relation window"):
                conn.execute(
                    """insert into account_authorization(
                         organization_id, project_id, idempotency_key,
                         project_account_relation_id, source_account_id, provider,
                         authorization_kind, purpose, field_allowlist, operation_allowlist,
                         credential_ref, status, granted_by, evidence_ref
                       ) values (%s, %s, 'authorization.unbounded.001', %s, %s,
                         'douyin', 'project_account', 'read_metrics', array['video.play_count'],
                         array['video.metrics.read'], 'secret://controlled/reference',
                         'active', 'owner-1', 'evidence://grant/unbounded')""",
                    (org_a, project_a, bounded_relation, account),
                )
            bounded_grant = conn.execute(
                """insert into account_authorization(
                     organization_id, project_id, idempotency_key,
                     project_account_relation_id, source_account_id, provider,
                     authorization_kind, purpose, field_allowlist, operation_allowlist,
                     credential_ref, status, effective_until, granted_by, evidence_ref
                   ) values (%s, %s, 'authorization.bounded.001', %s, %s,
                     'douyin', 'project_account', 'read_metrics', array['video.play_count'],
                     array['video.metrics.read'], 'secret://controlled/reference',
                     'active', now() + interval '1 day', 'owner-1',
                     'evidence://grant/bounded') returning id""",
                (org_a, project_a, bounded_relation, account),
            ).fetchone()[0]
            conn.execute("update research_project set status = 'active' where id = %s", (project_a,))
            assert conn.execute(
                "select count(*) from effective_account_authorization where id = %s",
                (bounded_grant,),
            ).fetchone()[0] == 1
            conn.execute(
                "update project_account_relation set effective_until = now() where id = %s",
                (bounded_relation,),
            )
            assert conn.execute(
                "select status from account_authorization where id = %s", (bounded_grant,)
            ).fetchone()[0] == "revoked"
            assert conn.execute(
                "select count(*) from effective_account_authorization where id = %s",
                (bounded_grant,),
            ).fetchone()[0] == 0
            future_relation = conn.execute(
                """insert into project_account_relation(
                     project_id, idempotency_key, source_account_id, relation_type,
                     task_roles, evidence_ref, verification_status, verified_by,
                     effective_from
                   ) values (%s, 'relation.future.001', %s, 'official',
                     array['publish_channel'], 'evidence://relation/future', 'verified',
                     'owner-1', now() + interval '1 day') returning id""",
                (project_a, account),
            ).fetchone()[0]
            with pytest.raises(psycopg.errors.RaiseException, match="effective window"):
                conn.execute(
                    """insert into account_authorization(
                         organization_id, project_id, idempotency_key,
                         project_account_relation_id, source_account_id, provider,
                         authorization_kind, purpose, field_allowlist, operation_allowlist,
                         credential_ref, status, granted_by, evidence_ref
                       ) values (%s, %s, 'authorization.future.early.001', %s, %s,
                         'douyin', 'project_account', 'read_metrics', array['video.play_count'],
                         array['video.metrics.read'], 'secret://controlled/reference',
                         'active', 'owner-1', 'evidence://grant/future-early')""",
                    (org_a, project_a, future_relation, account),
                )

            cross_left = conn.execute(
                "insert into source_account(platform) values ('douyin') returning id"
            ).fetchone()[0]
            cross_right = conn.execute(
                "insert into source_account(platform) values ('kuaishou') returning id"
            ).fetchone()[0]
            left, right = sorted((cross_left, cross_right))
            conn.execute(
                """insert into account_identity_link(
                     idempotency_key, left_account_id, right_account_id, relation_type,
                     evidence_ref, verification_status, verified_by
                   ) values ('identity.cross.platform.001', %s, %s, 'same_brand',
                     'evidence://identity/1', 'verified', 'owner-1')""",
                (left, right),
            )
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    """insert into account_identity_link(
                         idempotency_key, left_account_id, right_account_id, relation_type, evidence_ref
                       ) values ('identity.cross.platform.001', %s, %s, 'same_person',
                         'evidence://identity/retry')""",
                    (left, right),
                )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    """insert into account_identity_link(
                         idempotency_key, left_account_id, right_account_id, relation_type,
                         evidence_ref, status, revoked_at
                       ) values ('identity.bad.revoked.001', %s, %s, 'same_person',
                         'evidence://identity/bad-revocation', 'active', now())""",
                    (left, right),
                )
        finally:
            conn.execute("set search_path to public")
            conn.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(namespace)))
