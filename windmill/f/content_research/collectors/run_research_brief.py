# /// script
# requires-python = "==3.14.*"
# dependencies = [
#   "douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@99ad6a0b7c02fa366f57091241380fc1110a43c9",
#   "psycopg[binary]==3.3.6",
#   "wmill==1.815.0",
# ]
# ///

"""Claim and execute one due research brief with fixed service credentials."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import re
from typing import Any, Iterator, Mapping, TypedDict
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from douyin_research.l0l1.research_briefs import make_config, run_live


DATABASE_PATH = "f/content_research/research_db"
IDENTITY_PATH = "f/content_research/automation_worker_identity"
API_KEY_PATH = "f/content_research/tikhub_api_key"
LOCK_NAME = "douyin_research:manual_golden_intake"
_SAFE_ERROR_TOKEN = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_SAFE_LOGICAL_CALL_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


def _resource() -> postgresql:
    import wmill

    return wmill.get_resource(DATABASE_PATH)


def _variable(path: str) -> str:
    import wmill

    return wmill.get_variable(path)


def _dsn(db: postgresql) -> str:
    return make_conninfo(
        host=db["host"], port=int(db.get("port", 5432)), user=db["user"],
        password=db["password"], dbname=db["dbname"],
        sslmode=db.get("sslmode", "prefer"),
    )


def _brief_id(value: object) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("brief_id is invalid") from None


def _project_id(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise RuntimeError("research brief project scope is invalid") from None


def _identity(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("worker identity is invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > 160 or not normalized.isprintable():
        raise ValueError("worker identity is invalid")
    return normalized


@contextmanager
def _single_paid_job(dsn: str) -> Iterator[bool]:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select pg_try_advisory_lock(hashtext(%s))", (LOCK_NAME,))
        acquired = bool(cur.fetchone()[0])
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            cur.execute("select pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))


def _claim(dsn: str, brief_id: UUID, actor: str) -> dict[str, Any] | None:
    with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
        cur.execute(
            "select pg_advisory_xact_lock(hashtext(%s))",
            (f"research_brief:{brief_id}",),
        )
        cur.execute(
            """
            select brief.id, brief.project_id, brief.platform, brief.source_type,
              brief.target, brief.time_window_hours, brief.max_items, brief.depth,
              brief.cadence_hours, brief.config_version, brief.next_due_at
            from research_brief as brief
            left join research_project as project on project.id = brief.project_id
            left join research_organization as organization
              on organization.id = project.organization_id
            where brief.id=%s and brief.status='active' and brief.next_due_at <= now()
              and (
                brief.project_id is null
                or (project.status='active' and organization.status='active')
              )
              and (brief.project_id is null or brief.depth='metadata')
            for update of brief
            """,
            (brief_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        snapshot = {
            "platform": row["platform"],
            "source_type": row["source_type"],
            "target": row["target"],
            "time_window_hours": row["time_window_hours"],
            "max_items": row["max_items"],
            "depth": row["depth"],
            "cadence_hours": row["cadence_hours"],
        }
        project_id = _project_id(row["project_id"])
        due_at: datetime = row["next_due_at"]
        dispatch_key = f"{brief_id}:{row['config_version']}:{due_at.isoformat()}"
        cur.execute(
            """
            insert into research_brief_run(
              brief_id, project_id, brief_version, dispatch_key, trigger_kind,
              triggered_by, status, config_snapshot
            ) values (%s,%s,%s,%s,'schedule',%s,'running',%s)
            on conflict(dispatch_key) do nothing returning id
            """,
            (
                brief_id, project_id, row["config_version"], dispatch_key, actor,
                Jsonb(snapshot),
            ),
        )
        run_row = cur.fetchone()
        if run_row is None:
            return None
        if row["cadence_hours"] is None:
            cur.execute(
                """
                update research_brief set status='paused', next_due_at=null,
                  last_dispatched_at=now(), updated_at=now()
                where id=%s
                """,
                (brief_id,),
            )
        else:
            cur.execute(
                """
                update research_brief set
                  next_due_at=now() + make_interval(hours => cadence_hours),
                  last_dispatched_at=now(), updated_at=now()
                where id=%s
                """,
                (brief_id,),
            )
        return {
            "brief_run_id": run_row["id"],
            "project_id": project_id,
            "config": snapshot,
        }


def _finish(
    dsn: str, brief_run_id: UUID, *, status: str,
    source_run_id: UUID | None = None, summary: Mapping[str, Any] | None = None,
    error_code: str | None = None,
) -> None:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            update research_brief_run set status=%s, source_run_id=%s,
              summary=%s, error_code=%s, finished_at=now()
            where id=%s and status='running'
            """,
            (status, source_run_id, Jsonb(dict(summary or {})), error_code, brief_run_id),
        )


def _safe_result(result: Mapping[str, Any], brief_run_id: UUID) -> dict[str, Any]:
    integer_fields = (
        "observations", "unique_platform_videos", "new_candidate_count",
        "scored_videos", "provider_call_count", "cached_call_count",
        "uncached_call_count", "max_external_calls", "sdk_retries",
    )
    values = {name: result.get(name) for name in integer_fields}
    if any(type(value) is not int or value < 0 for value in values.values()):
        raise RuntimeError("research brief returned an invalid summary")
    if values["provider_call_count"] != (
        values["cached_call_count"] + values["uncached_call_count"]
    ):
        raise RuntimeError("research brief returned an invalid summary")
    flags = {
        name: result.get(name)
        for name in (
            "collect_comments", "collect_media", "review_required",
            "auto_submit_asr", "auto_submit_l3",
        )
    }
    if any(type(value) is not bool for value in flags.values()):
        raise RuntimeError("research brief returned an invalid summary")
    if flags["auto_submit_asr"] or flags["auto_submit_l3"]:
        raise RuntimeError("research brief analysis boundary is invalid")
    return {
        "status": "completed",
        "brief_run_id": str(brief_run_id),
        "run_id": str(UUID(str(result["run_id"]))),
        **values,
        **flags,
        "external_calls": values["uncached_call_count"],
        "raw_provider_payload_included": False,
    }


def _safe_failure_summary(exc: BaseException) -> dict[str, Any]:
    """Keep Windmill results useful without copying provider exception text."""
    fallback = {
        "failure_schema": "provider_failure_v1",
        "status": "failed",
        "stage": "unknown",
        "item_count": 0,
        "error_type": type(exc).__name__,
    }
    candidate = getattr(exc, "research_failure_summary", None)
    if not isinstance(candidate, Mapping):
        return fallback
    stage = candidate.get("stage")
    item_count = candidate.get("item_count")
    if stage not in {"discovery", "detail_enrichment", "finalize", "unknown"}:
        return fallback
    if isinstance(item_count, bool) or not isinstance(item_count, int) or item_count < 0:
        return fallback
    safe = {
        "failure_schema": "provider_failure_v1",
        "status": "failed",
        "stage": stage,
        "item_count": item_count,
        "error_type": candidate.get("error_type")
        if isinstance(candidate.get("error_type"), str)
        and candidate["error_type"].isidentifier()
        and len(candidate["error_type"]) <= 128
        else type(exc).__name__,
    }
    if isinstance(candidate.get("http_status"), int) and 100 <= candidate["http_status"] <= 599:
        safe["http_status"] = candidate["http_status"]
    for key, limit in (("provider_error_code", 64), ("provider_request_id", 128)):
        value = candidate.get(key)
        if (
            isinstance(value, str)
            and len(value) <= limit
            and _SAFE_ERROR_TOKEN.fullmatch(value)
        ):
            safe[key] = value
    logical_call_id = candidate.get("ledger_logical_call_id")
    if isinstance(logical_call_id, str) and _SAFE_LOGICAL_CALL_ID.fullmatch(logical_call_id):
        safe["ledger_logical_call_id"] = logical_call_id.lower()
    return safe


def main(brief_id: str) -> dict[str, Any]:
    normalized_id = _brief_id(brief_id)
    try:
        dsn = _dsn(_resource())
        actor = _identity(_variable(IDENTITY_PATH))
    except Exception:
        raise RuntimeError("research brief configuration is unavailable") from None

    with _single_paid_job(dsn) as acquired:
        if not acquired:
            return {
                "status": "deferred", "external_calls": 0, "sdk_retries": 0,
                "collect_comments": False, "collect_media": False,
            }
        claim = _claim(dsn, normalized_id, actor)
        if claim is None:
            return {
                "status": "deferred", "external_calls": 0, "sdk_retries": 0,
                "collect_comments": False, "collect_media": False,
            }
        brief_run_id: UUID = claim["brief_run_id"]
        try:
            config = make_config(**claim["config"])
            api_key = (_variable(API_KEY_PATH) or "").strip()
            if not api_key:
                raise RuntimeError("TikHub secret is not configured")
            result = run_live(
                dsn=dsn,
                api_key=api_key,
                config=config,
                triggered_by=actor,
                project_id=claim["project_id"],
            )
            safe = _safe_result(result, brief_run_id)
            source_run_id = UUID(safe["run_id"])
            _finish(
                dsn, brief_run_id, status="success", source_run_id=source_run_id,
                summary=safe,
            )
            return safe
        except Exception as exc:
            _finish(
                dsn, brief_run_id, status="failed",
                error_code="RESEARCH_BRIEF_EXECUTION_FAILED",
                summary={
                    "external_calls": None,
                    "call_count_status": "use_external_api_call_ledger",
                    "raw_provider_payload_included": False,
                    **_safe_failure_summary(exc),
                },
            )
            raise RuntimeError("research brief execution failed") from None
