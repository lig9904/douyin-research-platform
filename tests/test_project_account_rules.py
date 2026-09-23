from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

import pytest

from douyin_research.projects import (
    AccountAuthorization,
    AuthorizationStatus,
    AuthorizationPurpose,
    IdentityLinkStatus,
    ProjectAccountRelation,
    ProjectAccountRole,
    RelationKind,
    VerificationStatus,
    active_relations,
    can_use_private_account_data,
    can_use_project_account,
    dedupe_external_call_ids,
    dedupe_work_ids,
    make_identity_link,
)


NOW = datetime(2026, 9, 23, 6, tzinfo=timezone.utc)


def _relation(**changes: object) -> ProjectAccountRelation:
    values: dict[str, object] = {
        "id": "relation-yudao-official",
        "idempotency_key": "relation.yudao.official.001",
        "organization_id": "org-yudao",
        "project_id": "project-yudao",
        "source_account_id": "douyin-123",
        "kind": RelationKind.OFFICIAL,
        "roles": frozenset({ProjectAccountRole.PUBLISH_CHANNEL, ProjectAccountRole.CONVERSION_ENTRY}),
        "evidence_ref": "evidence://contract/1",
        "verified_by": "operator-1",
        "starts_at": NOW - timedelta(days=1),
        "verification_status": VerificationStatus.VERIFIED,
    }
    values.update(changes)
    return ProjectAccountRelation(**values)  # type: ignore[arg-type]


def _authorization(**changes: object) -> AccountAuthorization:
    values: dict[str, object] = {
        "id": "grant-yudao-metrics",
        "idempotency_key": "authorization.yudao.metrics.001",
        "organization_id": "org-yudao",
        "project_id": "project-yudao",
        "project_account_relation_id": "relation-yudao-official",
        "source_account_id": "douyin-123",
        "provider": "douyin",
        "purpose": AuthorizationPurpose.READ_METRICS,
        "allowed_fields": frozenset({"video.play_count"}),
        "allowed_operations": frozenset({"video.metrics.read"}),
        "granted_by": "account-owner",
        "evidence_ref": "evidence://oauth/1",
        "credential_ref": "secret://provider/douyin/yudao",
        "starts_at": NOW - timedelta(hours=1),
        "status": AuthorizationStatus.ACTIVE,
    }
    values.update(changes)
    return AccountAuthorization(**values)  # type: ignore[arg-type]


def test_one_account_can_have_different_active_roles_in_two_projects() -> None:
    yudao = _relation()
    competitor = _relation(
        id="relation-other-benchmark",
        project_id="project-other",
        kind=RelationKind.COMPETITOR,
        roles=frozenset({ProjectAccountRole.BENCHMARK_SAMPLE}),
    )

    assert active_relations([yudao, competitor], project_id="project-yudao", at=NOW) == (yudao,)
    assert can_use_project_account(
        competitor,
        project_id="project-other",
        source_account_id="douyin-123",
        required_role=ProjectAccountRole.BENCHMARK_SAMPLE,
        at=NOW,
    )


def test_relation_and_authorization_have_separate_fail_closed_scopes() -> None:
    relation = _relation()
    grant = _authorization()
    permitted = dict(
        organization_id="org-yudao",
        project_id="project-yudao",
        source_account_id="douyin-123",
        purpose=AuthorizationPurpose.READ_METRICS,
        required_field="video.play_count",
        required_operation="video.metrics.read",
        at=NOW,
    )

    assert can_use_private_account_data(relation, grant, **permitted)
    assert not can_use_private_account_data(relation, None, **permitted)
    assert not can_use_private_account_data(relation, _authorization(project_id="project-other"), **permitted)
    assert not can_use_private_account_data(
        relation,
        _authorization(status=AuthorizationStatus.REVOKED, revoked_at=NOW),
        **permitted,
    )
    assert not can_use_private_account_data(relation, grant, **(permitted | {"required_field": "audience.city"}))


def test_expiry_stops_relationship_and_data_use() -> None:
    relation = _relation(ends_at=NOW)
    grant = _authorization(ends_at=NOW)

    assert active_relations([relation], project_id="project-yudao", at=NOW) == ()
    assert not can_use_private_account_data(
        relation, grant,
        organization_id="org-yudao", project_id="project-yudao", source_account_id="douyin-123",
        purpose=AuthorizationPurpose.READ_METRICS,
        required_field="video.play_count", required_operation="video.metrics.read", at=NOW,
    )


def test_identity_link_requires_verified_cross_platform_evidence_and_never_grants_data_access() -> None:
    link = make_identity_link(
        id="identity-1", idempotency_key="identity.yudao.001",
        left_source_account_id="douyin-123", left_platform="douyin",
        right_source_account_id="kuaishou-456", right_platform="kuaishou",
        evidence_ref="evidence://brand/1", verified_by="operator-1", starts_at=NOW,
        verification_status=VerificationStatus.VERIFIED,
        status=IdentityLinkStatus.ACTIVE,
    )
    assert link.is_active(NOW)
    with pytest.raises(ValueError, match="different platforms"):
        make_identity_link(
            id="identity-bad", idempotency_key="identity.yudao.bad.001",
            left_source_account_id="douyin-123", left_platform="douyin",
            right_source_account_id="douyin-456", right_platform="douyin",
            evidence_ref="evidence://brand/1", verified_by="operator-1", starts_at=NOW,
        )


def test_cross_subject_and_cross_project_rollups_dedupe_canonical_work_and_call_ids() -> None:
    assert dedupe_work_ids(["work-1", "work-1", "work-2"]) == ("work-1", "work-2")
    assert dedupe_external_call_ids(["call-1", "call-1", "call-2"]) == ("call-1", "call-2")
    with pytest.raises(ValueError, match="external_call_id"):
        dedupe_external_call_ids(["call-1", " "])


def test_invalid_windows_and_empty_roles_are_rejected() -> None:
    with pytest.raises(ValueError, match="roles"):
        _relation(roles=frozenset())
    with pytest.raises(ValueError, match="later"):
        _relation(ends_at=NOW - timedelta(days=2))
    with pytest.raises(ValueError, match="credential_ref"):
        _authorization(credential_ref="inline-access-key")
    assert "secret://provider/douyin/yudao" not in repr(_authorization())


def test_idempotency_and_revocation_contracts_fail_closed() -> None:
    with pytest.raises(ValueError, match="idempotency_key"):
        _relation(idempotency_key="retry")
    with pytest.raises(ValueError, match="revoked status"):
        _authorization(revoked_at=NOW)
    with pytest.raises(ValueError, match="revoked status"):
        make_identity_link(
            id="identity-revocation", idempotency_key="identity.yudao.revocation.001",
            left_source_account_id="douyin-123", left_platform="douyin",
            right_source_account_id="kuaishou-456", right_platform="kuaishou",
            evidence_ref="evidence://brand/revocation", verified_by="operator-1", starts_at=NOW,
            verification_status=VerificationStatus.VERIFIED, revoked_at=NOW,
        )
    revoked = _authorization(status=AuthorizationStatus.REVOKED, revoked_at=NOW)
    assert not revoked.is_active(NOW)
    future_revoked = _authorization(
        status=AuthorizationStatus.REVOKED,
        starts_at=NOW + timedelta(days=1),
        revoked_at=NOW,
    )
    assert not future_revoked.is_active(NOW)


def _quoted_values(source: str) -> set[str]:
    return set(re.findall(r"'([^']+)'", source))


def test_domain_enums_match_migration_021() -> None:
    migration = (Path(__file__).parents[1] / "db/migrations/021_project_account_foundation.sql").read_text(encoding="utf-8")
    relation_table = migration.split("create table if not exists project_account_relation", 1)[1].split(
        "create table if not exists account_group", 1,
    )[0]
    authorization_table = migration.split("create table if not exists account_authorization", 1)[1].split(
        "create unique index", 1,
    )[0]
    relation_types = re.search(r"relation_type in \((.*?)\)", relation_table, flags=re.DOTALL)
    task_roles = re.search(r"task_roles <@ array\[(.*?)\]::text\[\]", relation_table, flags=re.DOTALL)
    purposes = re.search(r"purpose in \((.*?)\)", authorization_table, flags=re.DOTALL)

    assert relation_types and task_roles and purposes
    assert _quoted_values(relation_types.group(1)) == {item.value for item in RelationKind}
    assert _quoted_values(task_roles.group(1)) == {item.value for item in ProjectAccountRole}
    assert _quoted_values(purposes.group(1)) == {item.value for item in AuthorizationPurpose}


def test_proposed_relations_and_draft_grants_cannot_be_used() -> None:
    proposed = _relation(verification_status=VerificationStatus.PROPOSED, verified_by=None)
    draft = _authorization(status=AuthorizationStatus.DRAFT)

    assert not can_use_project_account(
        proposed,
        project_id="project-yudao",
        source_account_id="douyin-123",
        required_role=ProjectAccountRole.PUBLISH_CHANNEL,
        at=NOW,
    )
    assert not can_use_private_account_data(
        _relation(), draft,
        organization_id="org-yudao", project_id="project-yudao", source_account_id="douyin-123",
        purpose=AuthorizationPurpose.READ_METRICS,
        required_field="video.play_count", required_operation="video.metrics.read", at=NOW,
    )
