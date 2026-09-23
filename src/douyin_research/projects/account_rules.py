"""Fail-closed, project-scoped account domain rules.

``source_account`` remains the canonical public platform identity.  These
objects deliberately do not store credentials and do not infer business
ownership from a nickname, a relation label, or an identity link.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
import re
from typing import Iterable


class RelationKind(StrEnum):
    OFFICIAL = "official"
    IP_CHARACTER = "ip_character"
    EMPLOYEE_STORE = "employee_store"
    MANAGED_MATRIX = "managed_matrix"
    AUTHORIZED_PARTNER = "authorized_partner"
    UNVERIFIED_PARTNER = "unverified_partner"
    COMPETITOR = "competitor"
    MEDIA_REFERENCE = "media_reference"
    UGC_REFERENCE = "ugc_reference"
    OTHER = "other"


class ProjectAccountRole(StrEnum):
    PUBLISH_CHANNEL = "publish_channel"
    DISTRIBUTION_PARTNER = "distribution_partner"
    BENCHMARK_SAMPLE = "benchmark_sample"
    COMMENT_OBSERVER = "comment_observer"
    CONVERSION_ENTRY = "conversion_entry"
    RESEARCH_REFERENCE = "research_reference"


class AuthorizationPurpose(StrEnum):
    READ_METRICS = "read_metrics"
    ANALYZE_CONTENT = "analyze_content"
    PUBLISH = "publish"
    SYNC = "sync"


class VerificationStatus(StrEnum):
    PROPOSED = "proposed"
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"
    REVOKED = "revoked"


class AuthorizationStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


class IdentityLinkStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


def _identifier(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{name} must be a non-empty normalized string")
    return value


def _idempotency_key(value: str) -> str:
    """Validate the caller-supplied key used to make a create retry-safe."""
    normalized = _identifier("idempotency_key", value)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", normalized):
        raise ValueError("idempotency_key must be 8-128 safe characters")
    return normalized


def _aware(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _validate_window(
    *, starts_at: datetime, ends_at: datetime | None, label: str,
) -> None:
    _aware(f"{label}.starts_at", starts_at)
    if ends_at is not None:
        _aware(f"{label}.ends_at", ends_at)
        if ends_at <= starts_at:
            raise ValueError(f"{label}.ends_at must be later than starts_at")


@dataclass(frozen=True, slots=True)
class ProjectAccountRelation:
    """A business relationship, never a grant of provider permissions."""

    id: str
    idempotency_key: str
    organization_id: str
    project_id: str
    source_account_id: str
    kind: RelationKind
    roles: frozenset[ProjectAccountRole]
    evidence_ref: str
    verified_by: str | None
    starts_at: datetime
    ends_at: datetime | None = None
    subject_id: str | None = None
    verification_status: VerificationStatus = VerificationStatus.PROPOSED

    def __post_init__(self) -> None:
        for name in (
            "id", "organization_id", "project_id", "source_account_id", "evidence_ref",
        ):
            _identifier(name, getattr(self, name))
        _idempotency_key(self.idempotency_key)
        if self.subject_id is not None:
            _identifier("subject_id", self.subject_id)
        if not isinstance(self.kind, RelationKind):
            raise ValueError("kind must be a RelationKind")
        if not isinstance(self.verification_status, VerificationStatus):
            raise ValueError("verification_status must be a VerificationStatus")
        if self.verified_by is not None:
            _identifier("verified_by", self.verified_by)
        if self.verification_status is VerificationStatus.VERIFIED and self.verified_by is None:
            raise ValueError("verified relations require verified_by")
        if not self.roles or not all(isinstance(role, ProjectAccountRole) for role in self.roles):
            raise ValueError("roles must contain ProjectAccountRole values")
        _validate_window(starts_at=self.starts_at, ends_at=self.ends_at, label="relation")

    def is_active(self, at: datetime) -> bool:
        current = _aware("at", at)
        return (
            self.verification_status is VerificationStatus.VERIFIED
            and self.starts_at <= current
            and (self.ends_at is None or current < self.ends_at)
        )


@dataclass(frozen=True, slots=True)
class AccountAuthorization:
    """An explicit, narrow provider-data or publishing grant.

    ``credential_ref`` is an opaque reference to a controlled secret store. It is
    intentionally not a credential value and must never be shown to clients.
    """

    id: str
    idempotency_key: str
    organization_id: str
    project_id: str
    project_account_relation_id: str
    source_account_id: str
    provider: str
    purpose: AuthorizationPurpose
    allowed_fields: frozenset[str]
    allowed_operations: frozenset[str]
    granted_by: str
    evidence_ref: str
    credential_ref: str = field(repr=False)
    starts_at: datetime
    ends_at: datetime | None = None
    revoked_at: datetime | None = None
    status: AuthorizationStatus = AuthorizationStatus.DRAFT

    def __post_init__(self) -> None:
        for name in (
            "id", "organization_id", "project_id", "project_account_relation_id",
            "source_account_id", "provider", "granted_by", "evidence_ref", "credential_ref",
        ):
            _identifier(name, getattr(self, name))
        _idempotency_key(self.idempotency_key)
        if not re.fullmatch(r"(?:secret|vault|windmill)://[A-Za-z0-9][A-Za-z0-9/_-]*", self.credential_ref):
            raise ValueError("credential_ref must be an opaque controlled reference")
        if not isinstance(self.purpose, AuthorizationPurpose):
            raise ValueError("purpose must be an AuthorizationPurpose")
        if not isinstance(self.status, AuthorizationStatus):
            raise ValueError("status must be an AuthorizationStatus")
        if not self.allowed_fields or not all(isinstance(field, str) and field.strip() for field in self.allowed_fields):
            raise ValueError("allowed_fields must not be empty")
        if not self.allowed_operations or not all(isinstance(op, str) and op.strip() for op in self.allowed_operations):
            raise ValueError("allowed_operations must not be empty")
        _validate_window(starts_at=self.starts_at, ends_at=self.ends_at, label="authorization")
        if self.revoked_at is not None:
            _aware("authorization.revoked_at", self.revoked_at)
        if (self.status is AuthorizationStatus.REVOKED) != (self.revoked_at is not None):
            raise ValueError("authorization revoked status must match revoked_at")

    def is_active(self, at: datetime) -> bool:
        current = _aware("at", at)
        return (
            self.status is AuthorizationStatus.ACTIVE
            and self.starts_at <= current
            and (self.ends_at is None or current < self.ends_at)
            and (self.revoked_at is None or current < self.revoked_at)
        )


@dataclass(frozen=True, slots=True)
class AccountIdentityLink:
    """Verified cross-platform evidence about one real-world entity.

    It is not an authorization and does not merge platform metrics.
    """

    id: str
    idempotency_key: str
    left_source_account_id: str
    left_platform: str
    right_source_account_id: str
    right_platform: str
    evidence_ref: str
    verified_by: str | None
    starts_at: datetime
    ends_at: datetime | None = None
    revoked_at: datetime | None = None
    verification_status: VerificationStatus = VerificationStatus.PENDING
    status: IdentityLinkStatus = IdentityLinkStatus.ACTIVE

    def __post_init__(self) -> None:
        for name in (
            "id", "left_source_account_id", "left_platform", "right_source_account_id",
            "right_platform", "evidence_ref",
        ):
            _identifier(name, getattr(self, name))
        _idempotency_key(self.idempotency_key)
        if self.left_platform == self.right_platform:
            raise ValueError("identity links must join different platforms")
        if self.left_source_account_id == self.right_source_account_id:
            raise ValueError("identity links must join different source accounts")
        _validate_window(starts_at=self.starts_at, ends_at=self.ends_at, label="identity_link")
        if not isinstance(self.verification_status, VerificationStatus):
            raise ValueError("verification_status must be a VerificationStatus")
        if not isinstance(self.status, IdentityLinkStatus):
            raise ValueError("status must be an IdentityLinkStatus")
        if self.verified_by is not None:
            _identifier("verified_by", self.verified_by)
        if self.verification_status is VerificationStatus.VERIFIED and self.verified_by is None:
            raise ValueError("verified identity links require verified_by")
        if self.revoked_at is not None:
            _aware("identity_link.revoked_at", self.revoked_at)
        if (self.status is IdentityLinkStatus.REVOKED) != (self.revoked_at is not None):
            raise ValueError("identity_link revoked status must match revoked_at")

    def is_active(self, at: datetime) -> bool:
        current = _aware("at", at)
        return (
            self.status is IdentityLinkStatus.ACTIVE
            and self.verification_status is VerificationStatus.VERIFIED
            and self.starts_at <= current
            and (self.ends_at is None or current < self.ends_at)
            and (self.revoked_at is None or current < self.revoked_at)
        )


def make_identity_link(**kwargs: object) -> AccountIdentityLink:
    """Factory with the same validation path used by database adapters."""
    return AccountIdentityLink(**kwargs)  # type: ignore[arg-type]


def active_relations(
    relations: Iterable[ProjectAccountRelation], *, project_id: str, at: datetime,
) -> tuple[ProjectAccountRelation, ...]:
    """Return only relationships in the requested project and time window."""
    _identifier("project_id", project_id)
    current = _aware("at", at)
    return tuple(relation for relation in relations if relation.project_id == project_id and relation.is_active(current))


def can_use_project_account(
    relation: ProjectAccountRelation,
    *, project_id: str,
    source_account_id: str,
    required_role: ProjectAccountRole,
    at: datetime,
) -> bool:
    """Check a project relationship only; it deliberately grants no API access."""
    return (
        relation.project_id == project_id
        and relation.source_account_id == source_account_id
        and relation.is_active(at)
        and required_role in relation.roles
    )


def can_use_private_account_data(
    relation: ProjectAccountRelation,
    authorization: AccountAuthorization | None,
    *,
    organization_id: str,
    project_id: str,
    source_account_id: str,
    purpose: AuthorizationPurpose,
    required_field: str,
    required_operation: str,
    at: datetime,
) -> bool:
    """Fail closed unless relation and authorization scopes match exactly."""
    if authorization is None:
        return False
    if not relation.is_active(at) or not authorization.is_active(at):
        return False
    return (
        relation.organization_id == organization_id
        and relation.project_id == project_id
        and relation.source_account_id == source_account_id
        and authorization.organization_id == organization_id
        and authorization.project_id == project_id
        and authorization.project_account_relation_id == relation.id
        and authorization.source_account_id == source_account_id
        and authorization.purpose == purpose
        and required_field in authorization.allowed_fields
        and required_operation in authorization.allowed_operations
    )


def _dedupe_ids(ids: Iterable[str], *, label: str) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in ids:
        normalized = _identifier(label, value)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return tuple(result)


def dedupe_work_ids(work_ids: Iterable[str]) -> tuple[str, ...]:
    """Distinct source work IDs for multi-subject/project rollups."""
    return _dedupe_ids(work_ids, label="work_id")


def dedupe_external_call_ids(call_ids: Iterable[str]) -> tuple[str, ...]:
    """Distinct immutable supplier-call IDs; never sum one call per subject."""
    return _dedupe_ids(call_ids, label="external_call_id")
