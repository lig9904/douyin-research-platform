"""Database-only preparation, approval, and budget preview for L3 evidence."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from .evidence import (
    L3EvidenceAssembler,
    L3EvidenceBundle,
    L3_REVIEW_IDENTITY_SOURCE,
    assert_persisted_l3_privacy_review,
)
from .execution import L3_BUDGET_KEY

_ACTOR_RE = re.compile(r"[^\x00-\x1f\x7f]{1,200}\Z")
_IDEMPOTENCY_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}\Z")


def authorize_reviewer(
    end_user_email: str | None,
    reviewer_allowlist: str | None,
) -> str:
    """Return one trusted reviewer identity or fail closed.

    Callers must pass only the authenticated ``WM_END_USER_EMAIL`` value.  No
    fallback identity is accepted, and the comparison is exact after the
    required lower-case normalization of the static allowlist.
    """

    if not isinstance(end_user_email, str) or not end_user_email:
        raise PermissionError("authenticated end-user email is required")
    if (
        end_user_email != end_user_email.strip()
        or end_user_email != end_user_email.lower()
    ):
        raise PermissionError("authenticated end-user email must be lowercase")
    allowed = _parse_reviewer_allowlist(reviewer_allowlist)
    if end_user_email not in allowed:
        raise PermissionError("authenticated end-user is not an L3 reviewer")
    return end_user_email


def _parse_reviewer_allowlist(reviewer_allowlist: str | None) -> frozenset[str]:
    if not isinstance(reviewer_allowlist, str) or not reviewer_allowlist.strip():
        raise PermissionError("L3 reviewer allowlist is required")
    source = reviewer_allowlist.strip()
    if source.startswith("["):
        try:
            values = json.loads(source)
        except json.JSONDecodeError:
            raise PermissionError("L3 reviewer allowlist is invalid") from None
        if not isinstance(values, list) or not all(
            isinstance(item, str) for item in values
        ):
            raise PermissionError("L3 reviewer allowlist is invalid")
    else:
        values = re.split(r"[,\n]", source)
    normalized = []
    for value in values:
        email = value.strip()
        if not email or email != email.lower():
            raise PermissionError("L3 reviewer allowlist must contain lowercase emails")
        normalized.append(email)
    if not normalized:
        raise PermissionError("L3 reviewer allowlist is required")
    return frozenset(normalized)


class L3CandidateStaleError(ValueError):
    """The candidate shown to a reviewer is no longer the current snapshot."""


class L3IdempotencyConflictError(ValueError):
    """An idempotency key was previously used for different approval inputs."""


@dataclass(frozen=True, slots=True)
class L3EvidenceManifest:
    """Safe candidate identity; deliberately contains no evidence body."""

    video_id: UUID
    evidence_fingerprint: str
    evidence_version: str
    evidence_modalities: tuple[str, ...]
    privacy_review_version: str

    @classmethod
    def from_bundle(cls, bundle: L3EvidenceBundle) -> L3EvidenceManifest:
        review = bundle.evidence_bundle["privacy_review"]
        assert isinstance(review, Mapping)
        return cls(
            video_id=bundle.video_id,
            evidence_fingerprint=bundle.input_fingerprint,
            evidence_version=bundle.evidence_version,
            evidence_modalities=bundle.evidence_modalities,
            privacy_review_version=str(review["version"]),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "video_id": str(self.video_id),
            "evidence_fingerprint": self.evidence_fingerprint,
            "evidence_version": self.evidence_version,
            "evidence_modalities": list(self.evidence_modalities),
            "privacy_review_version": self.privacy_review_version,
        }


@dataclass(frozen=True, slots=True)
class L3ApprovalRequest:
    video_id: UUID | str
    actor: str
    idempotency_key: str
    evidence_fingerprint: str
    evidence_version: str
    evidence_modalities: tuple[str, ...]
    privacy_review_version: str


@dataclass(frozen=True, slots=True)
class L3ApprovalReceipt:
    status: str
    manifest: L3EvidenceManifest
    actor: str
    idempotency_key: str
    annotation_id: UUID
    approved_at: object
    idempotent_replay: bool

    def as_dict(self) -> dict[str, object]:
        value = self.manifest.as_dict()
        value.update(
            {
                "status": self.status,
                "actor": self.actor,
                "idempotency_key": self.idempotency_key,
                "approval_id": str(self.annotation_id),
                "approved_at": self.approved_at.isoformat(),
                "idempotent_replay": self.idempotent_replay,
                "external_calls": 0,
                "llm_calls": 0,
                "raw_evidence_included": False,
            }
        )
        return value


@dataclass(frozen=True, slots=True)
class L3BudgetPreviewRequest:
    video_id: UUID | str
    privacy_review_version: str
    evidence_fingerprint: str
    provider: str
    cost_currency: str
    estimated_llm_cost: Decimal | float | int | None


@dataclass(frozen=True, slots=True)
class L3BudgetPreview:
    status: str
    manifest: L3EvidenceManifest | None
    budget_date: date
    provider: str
    cost_currency: str
    estimated_llm_cost: Decimal | None
    max_cost: Decimal | None
    max_requests: int | None
    spent_cost: Decimal | None
    used_requests: int | None
    next_cost: Decimal | None
    next_requests: int | None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "status": self.status,
            "budget_date": self.budget_date.isoformat(),
            "provider": self.provider,
            "cost_currency": self.cost_currency,
            "estimated_llm_cost": _number_or_none(self.estimated_llm_cost),
            "max_cost": _number_or_none(self.max_cost),
            "max_requests": self.max_requests,
            "spent_cost": _number_or_none(self.spent_cost),
            "used_requests": self.used_requests,
            "next_cost": _number_or_none(self.next_cost),
            "next_requests": self.next_requests,
            "external_calls": 0,
            "llm_calls": 0,
            "db_writes": 0,
            "paid_execution_available": False,
            "raw_evidence_included": False,
        }
        if self.manifest is not None:
            result["manifest"] = self.manifest.as_dict()
        return result


class L3ReviewService:
    """The non-provider boundary used by future authenticated workflow adapters."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def prepare(
        self,
        video_id: UUID | str,
        *,
        privacy_review_version: str,
    ) -> L3EvidenceManifest:
        """Return only a stable manifest for the current review candidate."""

        bundle = L3EvidenceAssembler(self.dsn).prepare_for_privacy_review(
            video_id,
            privacy_review_version=privacy_review_version,
        )
        return L3EvidenceManifest.from_bundle(bundle)

    def approve(self, request: L3ApprovalRequest) -> L3ApprovalReceipt:
        normalized = _validate_approval_request(request)
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            lock_names = sorted(
                (
                    f"douyin_research:l3-review:key:{normalized.idempotency_key}",
                    "douyin_research:l3-review:candidate:"
                    f"{normalized.video_id}:{normalized.evidence_fingerprint}",
                )
            )
            # Acquire the shared candidate lock before establishing the
            # REPEATABLE READ snapshot. A waiter must see the winner's row.
            conn.autocommit = True
            for lock_name in lock_names:
                cur.execute(
                    "select pg_advisory_lock(hashtext(%s))",
                    (lock_name,),
                )
            conn.autocommit = False
            cur.execute("begin isolation level repeatable read")
            bundle = L3EvidenceAssembler(self.dsn).build_candidate(
                cur,
                normalized.video_id,
                normalized.privacy_review_version,
            )
            manifest = L3EvidenceManifest.from_bundle(bundle)
            _assert_manifest_matches(manifest, normalized)
            existing = self._load_idempotent_approval(cur, normalized.idempotency_key)
            if existing is not None:
                receipt = self._receipt_from_existing(existing, normalized)
                conn.commit()
                return receipt
            existing = self._load_candidate_approval(cur, manifest)
            if existing is not None:
                receipt = self._receipt_from_existing(
                    existing,
                    normalized,
                    allow_different_idempotency_key=True,
                )
                conn.commit()
                return receipt
            cur.execute(
                """
                insert into human_annotation(video_id, actor, annotation_type, value)
                values (%s,%s,'l3_privacy_review',%s)
                returning id, created_at
                """,
                (
                    manifest.video_id,
                    normalized.actor,
                    Jsonb(
                        {
                            "reviewed": True,
                            "version": manifest.privacy_review_version,
                            "evidence_fingerprint": manifest.evidence_fingerprint,
                            "evidence_version": manifest.evidence_version,
                            "evidence_modalities": list(manifest.evidence_modalities),
                            "idempotency_key": normalized.idempotency_key,
                            "reviewer_identity_source": L3_REVIEW_IDENTITY_SOURCE,
                        }
                    ),
                ),
            )
            annotation_id, approved_at = cur.fetchone()
            conn.commit()
        return L3ApprovalReceipt(
            status="approved",
            manifest=manifest,
            actor=normalized.actor,
            idempotency_key=normalized.idempotency_key,
            annotation_id=annotation_id,
            approved_at=approved_at,
            idempotent_replay=False,
        )

    def preview_budget(self, request: L3BudgetPreviewRequest) -> L3BudgetPreview:
        """Read current approval and budget capacity without any reservation."""

        normalized = _validate_budget_request(request)
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("begin isolation level repeatable read read only")
            cur.execute("select current_date")
            budget_date = cur.fetchone()[0]
            manifest = self._load_current_approval_manifest(cur, normalized.video_id)
            if manifest is None:
                return _empty_preview(
                    "approval_missing", normalized, budget_date=budget_date
                )
            bundle = L3EvidenceAssembler(self.dsn).build_candidate(
                cur, normalized.video_id, manifest.privacy_review_version
            )
            current = L3EvidenceManifest.from_bundle(bundle)
            if current != manifest:
                return _empty_preview(
                    "approval_stale",
                    normalized,
                    budget_date=budget_date,
                    manifest=current,
                )
            if (
                current.privacy_review_version != normalized.privacy_review_version
                or current.evidence_fingerprint != normalized.evidence_fingerprint
            ):
                return _empty_preview(
                    "candidate_stale",
                    normalized,
                    budget_date=budget_date,
                    manifest=current,
                )
            assert_persisted_l3_privacy_review(
                cur,
                current.video_id,
                current.privacy_review_version,
                evidence_fingerprint=current.evidence_fingerprint,
                evidence_version=current.evidence_version,
                evidence_modalities=current.evidence_modalities,
            )
            cur.execute(
                """
                select max_cost, max_requests, spent_cost, used_requests, cost_currency
                from daily_budget
                where budget_date=%s and provider=%s and budget_key=%s
                """,
                (budget_date, normalized.provider, L3_BUDGET_KEY),
            )
            row = cur.fetchone()
        if row is None:
            return _empty_preview(
                "budget_missing",
                normalized,
                budget_date=budget_date,
                manifest=current,
            )
        max_cost, max_requests, spent_cost, used_requests, configured_currency = row
        if configured_currency != normalized.cost_currency:
            return L3BudgetPreview(
                "budget_currency_mismatch", current, budget_date,
                normalized.provider, normalized.cost_currency,
                normalized.estimated_llm_cost,
                Decimal(max_cost) if max_cost is not None else None, max_requests,
                Decimal(spent_cost), used_requests, None, None,
            )
        next_cost = (
            Decimal(spent_cost) + normalized.estimated_llm_cost
            if normalized.estimated_llm_cost is not None
            else None
        )
        next_requests = used_requests + 1
        if normalized.estimated_llm_cost is None:
            status = "estimate_required"
        elif max_requests is not None and next_requests > max_requests:
            status = "budget_requests_exceeded"
        elif max_cost is not None and next_cost > Decimal(max_cost):
            status = "budget_cost_exceeded"
        else:
            status = "budget_capacity_preview_only"
        return L3BudgetPreview(
            status, current, budget_date, normalized.provider,
            normalized.cost_currency, normalized.estimated_llm_cost,
            Decimal(max_cost) if max_cost is not None else None, max_requests,
            Decimal(spent_cost), used_requests, next_cost, next_requests,
        )

    @staticmethod
    def _load_idempotent_approval(cur, idempotency_key: str):
        cur.execute(
            """
            select id, video_id, actor, value, created_at
            from human_annotation
            where annotation_type='l3_privacy_review'
              and value->>'idempotency_key'=%s
              and value->>'reviewer_identity_source'=%s
            order by created_at desc, id desc
            limit 1
            """,
            (idempotency_key, L3_REVIEW_IDENTITY_SOURCE),
        )
        return cur.fetchone()

    @staticmethod
    def _load_candidate_approval(cur, manifest: L3EvidenceManifest):
        cur.execute(
            """
            select id, video_id, actor, value, created_at
            from human_annotation
            where video_id=%s
              and annotation_type='l3_privacy_review'
              and value->>'reviewer_identity_source'=%s
              and value->>'version'=%s
              and value->>'evidence_fingerprint'=%s
            order by created_at desc, id desc
            limit 1
            """,
            (
                manifest.video_id,
                L3_REVIEW_IDENTITY_SOURCE,
                manifest.privacy_review_version,
                manifest.evidence_fingerprint,
            ),
        )
        return cur.fetchone()

    @staticmethod
    def _receipt_from_existing(
        row,
        request: L3ApprovalRequest,
        *,
        allow_different_idempotency_key: bool = False,
    ) -> L3ApprovalReceipt:
        annotation_id, video_id, actor, value, approved_at = row
        if not isinstance(value, Mapping):
            raise L3IdempotencyConflictError(
                "idempotency key has invalid stored approval"
            )
        manifest = _manifest_from_value(video_id, value)
        if (
            (not allow_different_idempotency_key and actor != request.actor)
            or manifest.video_id != request.video_id
            or manifest.evidence_fingerprint != request.evidence_fingerprint
            or manifest.evidence_version != request.evidence_version
            or manifest.evidence_modalities != request.evidence_modalities
            or manifest.privacy_review_version != request.privacy_review_version
        ):
            raise L3IdempotencyConflictError(
                "idempotency key was already used with different approval inputs"
            )
        return L3ApprovalReceipt(
            "approved", manifest, actor, request.idempotency_key,
            annotation_id, approved_at, True,
        )

    @staticmethod
    def _load_current_approval_manifest(cur, video_id: UUID):
        cur.execute(
            """
            select video_id, value
            from human_annotation
            where video_id=%s and annotation_type='l3_privacy_review'
              and value->>'reviewer_identity_source'=%s
            order by created_at desc, id desc
            limit 1
            """,
            (video_id, L3_REVIEW_IDENTITY_SOURCE),
        )
        row = cur.fetchone()
        if row is None or not isinstance(row[1], Mapping):
            return None
        try:
            return _manifest_from_value(row[0], row[1])
        except (KeyError, TypeError, ValueError):
            return None


def _validate_approval_request(request: L3ApprovalRequest) -> L3ApprovalRequest:
    actor = request.actor.strip() if isinstance(request.actor, str) else ""
    if not _ACTOR_RE.fullmatch(actor):
        raise ValueError("trusted L3 review actor is invalid")
    key = (
        request.idempotency_key.strip()
        if isinstance(request.idempotency_key, str)
        else ""
    )
    if not _IDEMPOTENCY_KEY_RE.fullmatch(key):
        raise ValueError("L3 review idempotency key is invalid")
    fingerprint = (
        request.evidence_fingerprint.strip()
        if isinstance(request.evidence_fingerprint, str)
        else ""
    )
    if not _FINGERPRINT_RE.fullmatch(fingerprint):
        raise ValueError("L3 evidence fingerprint is invalid")
    version = (
        request.evidence_version.strip()
        if isinstance(request.evidence_version, str)
        else ""
    )
    review_version = (
        request.privacy_review_version.strip()
        if isinstance(request.privacy_review_version, str)
        else ""
    )
    if not version or not review_version:
        raise ValueError("L3 evidence and review versions are required")
    modalities = tuple(request.evidence_modalities)
    if not modalities or any(
        not isinstance(item, str) or not item for item in modalities
    ):
        raise ValueError("L3 evidence modalities are invalid")
    if len(set(modalities)) != len(modalities):
        raise ValueError("L3 evidence modalities are invalid")
    return L3ApprovalRequest(
        UUID(str(request.video_id)),
        actor,
        key,
        fingerprint,
        version,
        modalities,
        review_version,
    )


def _validate_budget_request(request: L3BudgetPreviewRequest) -> L3BudgetPreviewRequest:
    provider = request.provider.strip() if isinstance(request.provider, str) else ""
    currency = (
        request.cost_currency.strip()
        if isinstance(request.cost_currency, str)
        else ""
    )
    if not provider or not currency:
        raise ValueError("L3 budget provider and currency are required")
    review_version = (
        request.privacy_review_version.strip()
        if isinstance(request.privacy_review_version, str)
        else ""
    )
    fingerprint = (
        request.evidence_fingerprint.strip()
        if isinstance(request.evidence_fingerprint, str)
        else ""
    )
    if not review_version or not _FINGERPRINT_RE.fullmatch(fingerprint):
        raise ValueError("current L3 candidate identity is invalid")
    return L3BudgetPreviewRequest(
        UUID(str(request.video_id)),
        review_version,
        fingerprint,
        provider,
        currency,
        _finite_nonnegative_decimal(request.estimated_llm_cost),
    )


def _finite_nonnegative_decimal(value: Decimal | float | int | None) -> Decimal | None:
    if value is None:
        return None
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("estimated L3 cost is invalid") from None
    if not decimal.is_finite() or decimal < 0:
        raise ValueError("estimated L3 cost must be finite and non-negative")
    return decimal


def _assert_manifest_matches(
    manifest: L3EvidenceManifest,
    request: L3ApprovalRequest,
) -> None:
    if (
        manifest.video_id != request.video_id
        or manifest.evidence_fingerprint != request.evidence_fingerprint
        or manifest.evidence_version != request.evidence_version
        or manifest.evidence_modalities != request.evidence_modalities
        or manifest.privacy_review_version != request.privacy_review_version
    ):
        raise L3CandidateStaleError(
            "L3 evidence candidate is stale or does not match approval"
        )


def _manifest_from_value(
    video_id: UUID,
    value: Mapping[str, object],
) -> L3EvidenceManifest:
    fingerprint = value["evidence_fingerprint"]
    version = value["evidence_version"]
    modalities = value["evidence_modalities"]
    review_version = value["version"]
    if (
        value.get("reviewed") is not True
        or value.get("reviewer_identity_source") != L3_REVIEW_IDENTITY_SOURCE
        or not isinstance(fingerprint, str)
        or not _FINGERPRINT_RE.fullmatch(fingerprint)
        or not isinstance(version, str)
        or not isinstance(review_version, str)
        or not isinstance(modalities, list)
        or not all(isinstance(item, str) and item for item in modalities)
        or len(modalities) != len(set(modalities))
    ):
        raise ValueError("stored L3 privacy review is invalid")
    return L3EvidenceManifest(
        video_id, fingerprint, version, tuple(modalities), review_version
    )


def _empty_preview(
    status: str,
    request: L3BudgetPreviewRequest,
    *,
    budget_date: date,
    manifest: L3EvidenceManifest | None = None,
) -> L3BudgetPreview:
    return L3BudgetPreview(
        status,
        manifest,
        budget_date,
        request.provider,
        request.cost_currency,
        request.estimated_llm_cost,
        None,
        None,
        None,
        None,
        None,
        None,
    )


def _number_or_none(value: Decimal | None) -> float | None:
    return None if value is None else float(value)
