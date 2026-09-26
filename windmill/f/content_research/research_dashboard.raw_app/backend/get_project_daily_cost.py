# py: ==3.14.*
#requirements:
#psycopg[binary]==3.3.6

"""Project-scoped daily research cost ledger.

Amounts are deliberately returned as *known* subtotals.  A task whose billing
basis is unknown (or whose component total has not been established) increases
``unknown_task_count`` and never becomes a misleading zero-valued amount.
Project-owned L0/L1 discovery runs are included without dividing global
supplier charges across projects.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TypedDict
from uuid import UUID

import psycopg
from psycopg.rows import dict_row


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


_EMAIL = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,120}$")
_REPORT_TIMEZONE = "Asia/Shanghai"
_MAX_DAYS = 90


def _actor() -> str:
    value = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if not _EMAIL.fullmatch(value) or len(value) > 254:
        raise PermissionError("RESEARCH_ACTION_IDENTITY_REQUIRED")
    return value


def _project_id(value: object) -> UUID:
    if not isinstance(value, str):
        raise ValueError("project_id is invalid")
    try:
        return UUID(value)
    except ValueError:
        raise ValueError("project_id is invalid") from None


def _day_count(value: object) -> int:
    # bool is an int subclass, but accepting it would turn a UI mistake into a
    # one-day billing report without an explicit user choice.
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _MAX_DAYS:
        raise ValueError(f"days must be an integer between 1 and {_MAX_DAYS}")
    return value


def _connect(db: postgresql):
    args: dict[str, Any] = {
        "host": db["host"], "port": int(db.get("port", 5432)),
        "user": db["user"], "password": db["password"],
        "dbname": db["dbname"], "sslmode": db.get("sslmode", "prefer"),
    }
    if db.get("options"):
        args["options"] = db["options"]
    return psycopg.connect(**args, row_factory=dict_row)


def _json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json(item) for item in value]
    return value


def main(db: postgresql, project_id: str, days: int = 30):
    """Return per-currency daily spend known to one authorized project member.

    ``known_amount`` sums completed ASR/L3 task totals and finished discovery
    run API costs only when their recorded billing basis is known.
    ``actual_amount``, ``estimated_amount``, and ``mixed_amount``
    retain their recorded billing basis; no conversion or cross-currency total
    is performed.  If ``unknown_task_count`` is non-zero, callers must present
    the day as partial rather than treating the known amount as total spend.
    """
    actor = _actor()
    project = _project_id(project_id)
    report_days = _day_count(days)
    try:
        with _connect(db) as conn, conn.cursor() as cur:
            cur.execute("set transaction isolation level repeatable read read only")
            cur.execute(
                """select p.id, p.name,
                          (select m.role from research_project_member m
                           where m.project_id=p.id and m.actor_id=%s
                             and m.status='active' and m.effective_from <= now()
                             and (m.effective_until is null or m.effective_until > now())
                             and not exists (
                               select 1 from research_project_member newer
                               where newer.project_id=m.project_id and newer.actor_id=m.actor_id
                                 and newer.effective_from <= now() and newer.effective_from > m.effective_from)
                           order by m.effective_from desc limit 1) as role
                   from research_project p
                   join research_organization o on o.id=p.organization_id
                   where p.id=%s and p.status='active' and o.status='active'
                     and project_actor_can_read(p.id,%s)""",
                (actor, project, actor),
            )
            project_row = cur.fetchone()
            if project_row is None or project_row["role"] is None:
                raise PermissionError("RESEARCH_PROJECT_ACCESS_DENIED")

            cur.execute(
                """with authorized_project as materialized (
                     select id from research_project
                     where id=%s and project_actor_can_read(id,%s)
                   ), cutoff as (
                     select ((((now() at time zone 'Asia/Shanghai')::date - (%s - 1))::timestamp)
                       at time zone 'Asia/Shanghai') as start_at
                   ), ledger as (
                     select cost.created_at,cost.cost_currency,cost.task_type,cost.cost_basis,
                            cost.total_cost,false as unbilled_job
                       from project_research_task_cost cost
                       join authorized_project project_row on project_row.id=cost.project_id
                      where cost.created_at >= (select start_at from cutoff)
                     union all
                     select job.created_at,job.cost_currency,'asr_transcription','unknown',
                            null::numeric,true
                       from project_asr_execution_job job
                       join authorized_project project_row on project_row.id=job.project_id
                      where job.created_at >= (select start_at from cutoff)
                        and not exists (select 1 from project_research_task_cost cost
                                        where cost.task_key=job.task_key)
                     union all
                     select job.created_at,job.cost_currency,'l3_structured_research','unknown',
                            null::numeric,true
                       from project_l3_execution_job job
                       join authorized_project project_row on project_row.id=job.project_id
                      where job.created_at >= (select start_at from cutoff)
                        and not exists (select 1 from project_research_task_cost cost
                                        where cost.task_key=job.task_key)
                     union all
                     select run.started_at,run.cost_currency,'l0l1_discovery',
                            case when run.status='success'
                                      and run.summary->>'api_cost_basis' in ('actual','estimated','mixed')
                                      and run.summary->>'unknown_cost_calls'='0'
                                 then run.summary->>'api_cost_basis' else 'unknown' end,
                            case when run.status='success'
                                      and run.summary->>'api_cost_basis' in ('actual','estimated','mixed')
                                      and run.summary->>'unknown_cost_calls'='0'
                                 then run.api_cost else null::numeric end,
                            run.status<>'success'
                       from pipeline_run run
                       join authorized_project project_row on project_row.id=run.project_id
                      where run.run_type='l0l1_discovery'
                        and run.started_at >= (select start_at from cutoff)
                   ), daily_cost as (
                     select (cost.created_at at time zone 'Asia/Shanghai')::date as cost_date,
                            cost.cost_currency,
                            count(*)::integer as task_count,
                            count(*) filter (where cost.task_type='asr_transcription')::integer as asr_task_count,
                            count(*) filter (where cost.task_type='l3_structured_research')::integer as l3_task_count,
                            count(*) filter (where cost.task_type='l0l1_discovery')::integer as discovery_run_count,
                            sum(cost.total_cost) filter (where cost.total_cost is not null) as known_amount,
                            sum(cost.total_cost) filter (
                              where cost.cost_basis='actual' and cost.total_cost is not null) as actual_amount,
                            sum(cost.total_cost) filter (
                              where cost.cost_basis='estimated' and cost.total_cost is not null) as estimated_amount,
                            sum(cost.total_cost) filter (
                              where cost.cost_basis='mixed' and cost.total_cost is not null) as mixed_amount,
                            count(*) filter (
                              where cost.cost_basis='unknown' or cost.total_cost is null
                            )::integer as unknown_task_count,
                            count(*) filter (where cost.unbilled_job)::integer as unbilled_job_count
                       from ledger cost
                      group by cost_date, cost.cost_currency
                   )
                   select cost_date, cost_currency, task_count, asr_task_count, l3_task_count,
                          discovery_run_count,
                          known_amount, actual_amount, estimated_amount, mixed_amount,
                          unknown_task_count, unbilled_job_count
                     from daily_cost
                    order by cost_date desc, cost_currency""",
                (project, actor, report_days),
            )
            records = [_json(dict(row)) for row in cur.fetchall()]
            for record in records:
                record["cost_status"] = "partial" if record["unknown_task_count"] else "complete"
            return {
                "project_id": str(project),
                "project_name": project_row["name"],
                "role": str(project_row["role"]),
                "report_timezone": _REPORT_TIMEZONE,
                "days": report_days,
                "records": records,
            }
    except (PermissionError, ValueError):
        raise
    except Exception:
        raise RuntimeError("RESEARCH_PROJECT_DAILY_COST_UNAVAILABLE") from None
