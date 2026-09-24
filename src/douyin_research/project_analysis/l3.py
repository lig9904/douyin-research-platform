"""Project-private L3 evidence, review and one-shot execution.

This module intentionally has no fallback to the canonical ``transcript``,
``analysis_run`` or ``research_task_cost`` tables.  A caller must supply a
project and one project transcript; all persistence stays in migration 032's
project-private tables.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Callable, Mapping, Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from douyin_research.l2.transcripts import TaskCost
from douyin_research.l3.execution import L3Provider, L3ProviderRequest
from douyin_research.l3.review import authorize_reviewer
from douyin_research.l3.results import L3ResearchResult, _public_output, _validate_cost, _validate_result
from douyin_research.providers.execution_contracts import L3_SYNC_CAPABILITY, validate_execution_contract

from .identity import project_l3_task_key

PROJECT_L3_EVIDENCE_VERSION = "project-l3-evidence-v1.0.0"
PROJECT_L3_SCHEMA_VERSION = "l3-research-v1.0.0"
_MAX_TRANSCRIPT_CHARS = 30_000
_MAX_BUNDLE_BYTES = 128_000


@dataclass(frozen=True, slots=True)
class ProjectL3Evidence:
    project_id: UUID
    video_id: UUID
    transcript_id: UUID
    review_version: str
    fingerprint: str
    modalities: tuple[str, ...]
    bundle: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ProjectL3ReviewReceipt:
    review_id: UUID
    project_id: UUID
    video_id: UUID
    transcript_id: UUID
    review_version: str
    evidence_fingerprint: str
    idempotent_replay: bool


@dataclass(frozen=True, slots=True)
class ProjectL3ExecutionSelection:
    project_id: UUID | str
    video_id: UUID | str
    review_id: UUID | str
    transcript_id: UUID | str
    review_version: str
    evidence_fingerprint: str
    model_id: str
    model_revision: str
    prompt_version: str
    schema_version: str = PROJECT_L3_SCHEMA_VERSION
    cost_currency: str = "CNY"


class _ProviderFactory(Protocol):
    def __call__(self) -> L3Provider: ...


def _uuid(value: UUID | str, name: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{name} is invalid") from exc


def _text(value: object, name: str, limit: int = 160) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError(f"{name} is invalid")
    return value.strip()


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _output_fingerprint(result: L3ResearchResult) -> str:
    return _fingerprint(asdict(result))


class ProjectL3EvidenceService:
    """Rebuild an exact project-private candidate; never contacts a provider."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def prepare(
        self, *, project_id: UUID | str, video_id: UUID | str,
        transcript_id: UUID | str, review_version: str,
    ) -> ProjectL3Evidence:
        project, video, transcript = (_uuid(project_id, "project_id"), _uuid(video_id, "video_id"), _uuid(transcript_id, "transcript_id"))
        version = _text(review_version, "review_version", 80)
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("begin isolation level repeatable read read only")
            cur.execute(
                """select video.platform,video.title,video.description,video.published_at,video.duration_ms,
                          transcript.id,transcript.text_content,transcript.text_fingerprint,transcript.language,
                          transcript.audio_duration_ms,transcript.model_id,transcript.model_revision,transcript.engine_version
                   from research_project project
                   join research_organization organization on organization.id=project.organization_id
                   join project_video_inclusion inclusion on inclusion.project_id=project.id
                   join source_video video on video.id=inclusion.video_id
                   join project_transcript transcript
                     on transcript.project_id=inclusion.project_id and transcript.video_id=inclusion.video_id
                   where project.id=%s and project.status='active' and organization.status='active'
                     and inclusion.video_id=%s and inclusion.status='accepted'
                     and video.availability_status='available' and transcript.id=%s""",
                (project, video, transcript),
            )
            row = cur.fetchone()
            if row is None:
                raise PermissionError("PROJECT_L3_EVIDENCE_UNAVAILABLE")
            if len(row[6]) > _MAX_TRANSCRIPT_CHARS:
                raise ValueError("project transcript exceeds the L3 input limit")
            cur.execute(
                """select feature_version,sampled_comment_count,root_comment_count,sampled_reply_count,
                          source_observation_count,text_present_count,question_text_count,like_known_count,
                          like_sum,like_median,reply_known_count,reply_sum,reply_median,mean_text_length,
                          eligible_text_count,normalized_unique_text_count,duplicate_text_count,duplicate_group_count,
                          max_duplicate_group_size,url_text_count,mention_text_count,emoji_only_text_count,
                          repeated_char_text_count,short_text_count,template_like_text_count,char_bigram_count,
                          unique_char_bigram_count,top_char_bigram_count,top_char_bigram_share
                   from video_comment_feature_snapshot where video_id=%s
                   order by calculated_at desc,id desc limit 1""",
                (video,),
            )
            feature = cur.fetchone()
            if feature is None:
                raise ValueError("project L3 requires comment feature evidence")
        keys = ("feature_version","sampled_comment_count","root_comment_count","sampled_reply_count","source_observation_count","text_present_count","question_text_count","like_known_count","like_sum","like_median","reply_known_count","reply_sum","reply_median","mean_text_length","eligible_text_count","normalized_unique_text_count","duplicate_text_count","duplicate_group_count","max_duplicate_group_size","url_text_count","mention_text_count","emoji_only_text_count","repeated_char_text_count","short_text_count","template_like_text_count","char_bigram_count","unique_char_bigram_count","top_char_bigram_count","top_char_bigram_share")
        comments = dict(zip(keys, feature, strict=True))
        comments.update({"population_scope": "collected_sample_only", "semantic_inference": False, "raw_comment_text_included": False})
        bundle: dict[str, object] = {
            "evidence_version": PROJECT_L3_EVIDENCE_VERSION,
            "modalities": ["metadata", "comments", "transcript"],
            "project_scope": {"project_id": str(project), "video_id": str(video), "transcript_id": str(transcript)},
            "video_metadata": {"platform": row[0], "title": row[1], "description": row[2], "published_at": row[3], "duration_ms": row[4]},
            "comment_features": comments,
            "transcript": {"text": row[6], "text_fingerprint": row[7], "language": row[8], "audio_duration_ms": row[9], "model_id": row[10], "model_revision": row[11], "engine_version": row[12]},
            "privacy_review": {"reviewed": True, "version": version},
        }
        if len(_json_bytes(bundle)) > _MAX_BUNDLE_BYTES:
            raise ValueError("project L3 evidence bundle exceeds the byte limit")
        return ProjectL3Evidence(project, video, transcript, version, _fingerprint(bundle), ("metadata", "comments", "transcript"), bundle)


class ProjectL3ReviewService:
    """Persist only a project-bound approval after rebuilding the candidate."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def prepare(self, *, actor: str, reviewer_allowlist: str, **kwargs: object) -> ProjectL3Evidence:
        project = _uuid(kwargs.get("project_id"), "project_id")
        video = _uuid(kwargs.get("video_id"), "video_id")
        reviewer = authorize_reviewer(actor, reviewer_allowlist)
        self._assert_manager(project, video, reviewer)
        return ProjectL3EvidenceService(self.dsn).prepare(**kwargs)  # type: ignore[arg-type]

    def approve(self, *, actor: str, reviewer_allowlist: str, evidence: ProjectL3Evidence) -> ProjectL3ReviewReceipt:
        reviewer = authorize_reviewer(actor, reviewer_allowlist)
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            self._assert_manager(evidence.project_id, evidence.video_id, reviewer, cur=cur)
            cur.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"project-l3-review:{evidence.project_id}:{evidence.video_id}:{evidence.fingerprint}",))
            current = ProjectL3EvidenceService(self.dsn).prepare(project_id=evidence.project_id, video_id=evidence.video_id, transcript_id=evidence.transcript_id, review_version=evidence.review_version)
            if current.fingerprint != evidence.fingerprint:
                raise ValueError("PROJECT_L3_REVIEW_CANDIDATE_STALE")
            cur.execute(
                """select id,transcript_id,reviewed_by from project_l3_privacy_review
                   where project_id=%s and video_id=%s and review_version=%s and evidence_fingerprint=%s and status='approved'
                   for update""",
                (evidence.project_id, evidence.video_id, evidence.review_version, evidence.fingerprint),
            )
            existing = cur.fetchone()
            if existing is not None:
                if existing[1] != evidence.transcript_id or existing[2] != reviewer:
                    raise PermissionError("PROJECT_L3_REVIEW_ALREADY_APPROVED")
                return ProjectL3ReviewReceipt(existing[0], evidence.project_id, evidence.video_id, evidence.transcript_id, evidence.review_version, evidence.fingerprint, True)
            manifest = {"evidence_version": PROJECT_L3_EVIDENCE_VERSION, "evidence_modalities": list(evidence.modalities), "transcript_id": str(evidence.transcript_id), "raw_evidence_stored": False}
            cur.execute(
                """insert into project_l3_privacy_review(project_id,video_id,transcript_id,review_version,evidence_fingerprint,evidence_manifest)
                   values(%s,%s,%s,%s,%s,%s) returning id""",
                (evidence.project_id, evidence.video_id, evidence.transcript_id, evidence.review_version, evidence.fingerprint, Jsonb(manifest)),
            )
            review_id = cur.fetchone()[0]
            cur.execute("update project_l3_privacy_review set status='approved',reviewed_by=%s where id=%s", (reviewer, review_id))
            return ProjectL3ReviewReceipt(review_id, evidence.project_id, evidence.video_id, evidence.transcript_id, evidence.review_version, evidence.fingerprint, False)

    def _assert_manager(self, project: UUID, video: UUID, actor: str, *, cur=None) -> None:
        if cur is None:
            with psycopg.connect(self.dsn) as conn, conn.cursor() as owned:
                self._assert_manager(project, video, actor, cur=owned)
            return
        cur.execute(
            """select 1 from research_project project
               join research_organization organization on organization.id=project.organization_id
               join lateral (select role,status,effective_until from research_project_member
                 where project_id=project.id and actor_id=%s and effective_from<=now()
                 order by effective_from desc limit 1) member on true
               where project.id=%s and project.status='active' and organization.status='active'
                 and member.status='active' and member.role in ('owner','admin')
                 and (member.effective_until is null or member.effective_until>now())
                 and project_video_can_read(project.id,%s,%s)""",
            (actor, project, actor, video),
        )
        if cur.fetchone() is None:
            raise PermissionError("PROJECT_L3_REVIEW_ACCESS_DENIED")


class ProjectL3ExecutionService:
    """One project-private L3 call.  Revalidates approval before provider construction."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def run(self, selection: ProjectL3ExecutionSelection, *, provider_factory: _ProviderFactory) -> dict[str, object]:
        project, video, review, transcript = (_uuid(selection.project_id, "project_id"), _uuid(selection.video_id, "video_id"), _uuid(selection.review_id, "review_id"), _uuid(selection.transcript_id, "transcript_id"))
        for name in ("review_version", "model_id", "model_revision", "prompt_version", "schema_version", "cost_currency"):
            _text(getattr(selection, name), name)
        evidence = ProjectL3EvidenceService(self.dsn).prepare(project_id=project, video_id=video, transcript_id=transcript, review_version=selection.review_version)
        if evidence.fingerprint != selection.evidence_fingerprint:
            return _blocked("evidence_changed_since_review")
        task_key = project_l3_task_key(project_id=project, video_id=video, review_id=review, review_version=selection.review_version, evidence_fingerprint=evidence.fingerprint, model_id=selection.model_id, model_revision=selection.model_revision, prompt_version=selection.prompt_version, schema_version=selection.schema_version)
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """select 1 from project_l3_privacy_review review
                   join research_project project on project.id=review.project_id
                   join research_organization organization on organization.id=project.organization_id
                   join project_video_inclusion inclusion on inclusion.project_id=review.project_id and inclusion.video_id=review.video_id
                   join source_video source on source.id=review.video_id
                   where review.id=%s and review.project_id=%s and review.video_id=%s and review.transcript_id=%s
                     and review.review_version=%s and review.evidence_fingerprint=%s and review.status='approved'
                     and project.status='active' and organization.status='active'
                     and inclusion.status='accepted' and source.availability_status='available' for update""",
                (review, project, video, transcript, selection.review_version, evidence.fingerprint),
            )
            if cur.fetchone() is None:
                return _blocked("project_review_missing_or_revoked")
            cur.execute("select id,status from project_l3_execution_job where task_key=%s for update", (task_key,))
            existing = cur.fetchone()
            if existing is not None:
                return {"status": existing[1], "created": False, "external_calls": 0, "llm_calls": 0, "task_key": task_key}
            cur.execute(
                """insert into project_l3_execution_job(task_key,project_id,video_id,privacy_review_id,review_version,evidence_fingerprint,provider,model_id,model_revision,prompt_version,schema_version,input_fingerprint,status,attempt_count,cost_currency,metadata)
                   values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'running',1,%s,%s) returning id""",
                (task_key, project, video, review, selection.review_version, evidence.fingerprint, "configured_provider", selection.model_id, selection.model_revision, selection.prompt_version, selection.schema_version, evidence.fingerprint, selection.cost_currency, Jsonb({"raw_evidence_stored": False, "project_private": True})),
            )
            job = cur.fetchone()[0]
        # The transaction trigger checked approval at transition.  Rebuild and
        # query it again after commit, immediately before Provider construction.
        fresh = ProjectL3EvidenceService(self.dsn).prepare(project_id=project, video_id=video, transcript_id=transcript, review_version=selection.review_version)
        if fresh.fingerprint != evidence.fingerprint or not self._review_current(review, project, video, transcript, fresh):
            self._fail(job, task_key, project, video, selection, "approval_revoked_or_stale")
            return _blocked("approval_revoked_or_stale")
        try:
            provider = provider_factory()
            provider_name = _text(getattr(provider, "provider_name", None), "provider_name")
            contract_fingerprint = validate_execution_contract(provider.contract, expected_provider=provider_name, expected_capability=L3_SYNC_CAPABILITY, expected_model_id=selection.model_id, expected_model_revision=selection.model_revision, expected_currency=selection.cost_currency)
            if provider.max_retries != 0:
                raise ValueError("project L3 provider retries must be disabled")
        except Exception:
            self._fail(job, task_key, project, video, selection, "provider_unavailable")
            return _blocked("provider_unavailable")
        try:
            response = provider.generate(L3ProviderRequest(task_key=task_key, model_id=selection.model_id, model_revision=selection.model_revision, prompt_version=selection.prompt_version, schema_version=selection.schema_version, input_fingerprint=fresh.fingerprint, evidence_bundle=fresh.bundle))
            result, cost = response.result, response.cost
            _validate_result(result)
            costs = _validate_cost(cost)
            if result.input_fingerprint != fresh.fingerprint or result.model_id != selection.model_id or result.model_revision != selection.model_revision or result.prompt_version != selection.prompt_version or result.schema_version != selection.schema_version or not result.privacy_reviewed:
                raise ValueError("project L3 provider response does not bind reviewed input")
            return self._complete(job, task_key, project, video, review, result, cost, costs, contract_fingerprint)
        except Exception:
            self._fail(job, task_key, project, video, selection, "generation_failed")
            return {"status": "failed", "created": True, "external_calls": 1, "llm_calls": 1, "task_key": task_key}
        finally:
            close = getattr(locals().get("provider"), "close", None)
            if callable(close):
                try: close()
                except Exception: pass

    def _review_current(self, review: UUID, project: UUID, video: UUID, transcript: UUID, evidence: ProjectL3Evidence) -> bool:
        with psycopg.connect(self.dsn) as conn:
            return bool(conn.execute("""select 1 from project_l3_privacy_review review
                join research_project project_row on project_row.id=review.project_id
                join research_organization organization on organization.id=project_row.organization_id
                join project_video_inclusion inclusion on inclusion.project_id=review.project_id and inclusion.video_id=review.video_id
                join source_video source on source.id=review.video_id
                where review.id=%s and review.project_id=%s and review.video_id=%s and review.transcript_id=%s
                  and review.review_version=%s and review.evidence_fingerprint=%s and review.status='approved'
                  and project_row.status='active' and organization.status='active'
                  and inclusion.status='accepted' and source.availability_status='available'""", (review, project, video, transcript, evidence.review_version, evidence.fingerprint)).fetchone())

    def _fail(self, job: UUID, task_key: str, project: UUID, video: UUID, selection: ProjectL3ExecutionSelection, code: str) -> None:
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("""insert into project_research_task_cost(task_key,project_id,video_id,task_type,task_version,status,input_fingerprint,api_cost,asr_cost,llm_cost,cost_currency,cost_basis,metadata)
                    values(%s,%s,%s,'l3_structured_research',%s,'failed',%s,0,0,null,%s,'unknown',%s) on conflict(task_key) do nothing returning id""", (task_key, project, video, selection.schema_version, selection.evidence_fingerprint, selection.cost_currency, Jsonb({"error_code": code})))
                cost = cur.fetchone()
                cur.execute("update project_l3_execution_job set status='failed',error_code=%s,task_cost_id=coalesce(task_cost_id,%s) where id=%s", (code, cost[0] if cost else None, job))

    def _complete(self, job: UUID, task_key: str, project: UUID, video: UUID, review: UUID, result: L3ResearchResult, cost: TaskCost, costs: tuple[Decimal | None, Decimal | None, Decimal | None], contract_fingerprint: str) -> dict[str, object]:
        output_fingerprint = _output_fingerprint(result)
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("""insert into project_research_task_cost(task_key,project_id,video_id,task_type,task_version,status,input_fingerprint,output_fingerprint,api_cost,asr_cost,llm_cost,cost_currency,cost_basis,metadata)
                values(%s,%s,%s,'l3_structured_research',%s,'completed',%s,%s,%s,%s,%s,%s,%s,%s) returning id,total_cost""", (task_key, project, video, result.schema_version, result.input_fingerprint, output_fingerprint, *costs, cost.currency, cost.basis, Jsonb({"project_private": True, "provider_contract_fingerprint": contract_fingerprint, "raw_evidence_stored": False})))
            cost_id, total = cur.fetchone()
            cur.execute("update project_l3_execution_job set status='completed',task_cost_id=%s where id=%s", (cost_id, job))
            cur.execute("""insert into project_l3_analysis_result(project_id,video_id,execution_job_id,privacy_review_id,output,output_fingerprint,task_cost_id)
                values(%s,%s,%s,%s,%s,%s,%s) returning id""", (project, video, job, review, Jsonb(_public_output(result)), output_fingerprint, cost_id))
            result_id = cur.fetchone()[0]
        return {"status": "completed", "created": True, "external_calls": 1, "llm_calls": 1, "project_result_id": str(result_id), "task_cost_id": str(cost_id), "total_cost": float(total) if total is not None else None, "cost_currency": cost.currency, "task_key": task_key}


def _blocked(reason: str) -> dict[str, object]:
    return {"status": "blocked", "created": False, "external_calls": 0, "llm_calls": 0, "sdk_retries": 0, "reason": reason}
