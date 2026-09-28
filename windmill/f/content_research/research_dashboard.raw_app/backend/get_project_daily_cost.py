# py: ==3.14.*
#requirements:
#psycopg[binary]==3.3.6

"""Project-scoped daily research cost ledger.

Amounts are deliberately returned as *known* subtotals.  A task whose billing
basis is unknown (or whose component total has not been established) increases
``unknown_task_count`` and never becomes a misleading zero-valued amount.
Project-owned L0/L1 discovery and comment collection runs are included without
dividing global supplier charges across projects.
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

    ``known_amount`` sums recorded task amounts, including LAS estimates, only
    when their basis is known. LAS estimates remain partial until an exact
    supplier line is matched; an estimate never becomes supplier actual spend.
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
                            cost.total_cost,false as unbilled_job,false as pending_supplier_bill
                       from project_research_task_cost cost
                       join authorized_project project_row on project_row.id=cost.project_id
                      where cost.created_at >= (select start_at from cutoff)
                     union all
                     select job.created_at,job.cost_currency,'asr_transcription','unknown',
                            null::numeric,true,false
                       from project_asr_execution_job job
                       join authorized_project project_row on project_row.id=job.project_id
                      where job.created_at >= (select start_at from cutoff)
                        and not exists (select 1 from project_research_task_cost cost
                                        where cost.task_key=job.task_key)
                     union all
                     select job.created_at,job.cost_currency,'l3_structured_research','unknown',
                            null::numeric,true,false
                       from project_l3_execution_job job
                       join authorized_project project_row on project_row.id=job.project_id
                      where job.created_at >= (select start_at from cutoff)
                        and not exists (select 1 from project_research_task_cost cost
                                        where cost.task_key=job.task_key)
                     union all
                     select run.started_at,
                            case when run.status='success' then run.cost_currency
                                 when run.run_type in ('account_profile_refresh','video_statistics_refresh')
                                  and coalesce((run.summary->>'known_estimated_cost_usd')::numeric,0)>0
                                 then 'USD'
                                 else 'UNKNOWN' end,
                            run.run_type,
                            case when run.run_type in ('account_profile_refresh','video_statistics_refresh')
                                      and run.status<>'success'
                                      and coalesce((run.summary->>'known_estimated_cost_usd')::numeric,0)>0
                                 then 'estimated'
                                 when run.status='success'
                                      and run.summary->>'api_cost_basis' in ('actual','estimated','mixed')
                                      and run.summary->>'unknown_cost_calls'='0'
                                 then run.summary->>'api_cost_basis' else 'unknown' end,
                            case when run.run_type in ('account_profile_refresh','video_statistics_refresh')
                                      and run.status<>'success'
                                      and coalesce((run.summary->>'known_estimated_cost_usd')::numeric,0)>0
                                 then (run.summary->>'known_estimated_cost_usd')::numeric
                                 when run.status='success'
                                      and run.summary->>'api_cost_basis' in ('actual','estimated','mixed')
                                      and run.summary->>'unknown_cost_calls'='0'
                                 then run.api_cost else null::numeric end,
                            run.status<>'success',false
                       from pipeline_run run
                       join authorized_project project_row on project_row.id=run.project_id
                      where run.run_type in ('l0l1_discovery','comment_collection',
                                             'account_profile_refresh','video_statistics_refresh')
                        and run.started_at >= (select start_at from cutoff)
                     union all
                     select receipt.executed_at,receipt.cost_currency,
                            'las_whole_video_analysis',
                            case when receipt.estimated_cost is null then 'unknown'
                                 else 'estimated' end,
                            receipt.estimated_cost,false,true
                      from project_las_analysis_receipt receipt
                       join authorized_project project_row on project_row.id=receipt.project_id
                      where receipt.executed_at >= (select start_at from cutoff)
                     union all
                     select attempt.created_at,attempt.cost_currency,
                            'las_whole_video_analysis','unknown',null::numeric,true,false
                       from project_las_analysis_attempt attempt
                       join authorized_project project_row on project_row.id=attempt.project_id
                      where attempt.created_at >= (select start_at from cutoff)
                        and not exists (
                          select 1 from project_las_analysis_receipt receipt
                          where receipt.attempt_id=attempt.id)
                   ), daily_cost as (
                     select (cost.created_at at time zone 'Asia/Shanghai')::date as cost_date,
                            cost.cost_currency,
                            count(*)::integer as task_count,
                            count(*) filter (where cost.task_type='asr_transcription')::integer as asr_task_count,
                            count(*) filter (where cost.task_type='l3_structured_research')::integer as l3_task_count,
                            count(*) filter (where cost.task_type='las_whole_video_analysis')::integer as las_task_count,
                            count(*) filter (where cost.task_type='l0l1_discovery')::integer as discovery_run_count,
                            count(*) filter (where cost.task_type='comment_collection')::integer as comment_run_count,
                            count(*) filter (where cost.task_type='account_profile_refresh')::integer as profile_run_count,
                            count(*) filter (where cost.task_type='video_statistics_refresh')::integer as statistics_run_count,
                            sum(cost.total_cost) filter (
                              where cost.cost_basis in ('actual','estimated','mixed')
                                and cost.total_cost is not null) as known_amount,
                            sum(cost.total_cost) filter (
                              where cost.cost_basis='actual' and cost.total_cost is not null) as actual_amount,
                            sum(cost.total_cost) filter (
                              where cost.cost_basis='estimated' and cost.total_cost is not null) as estimated_amount,
                            sum(cost.total_cost) filter (
                              where cost.cost_basis='mixed' and cost.total_cost is not null) as mixed_amount,
                            count(*) filter (
                              where cost.cost_basis='unknown' or cost.total_cost is null
                                or cost.unbilled_job or cost.pending_supplier_bill
                            )::integer as unknown_task_count,
                            count(*) filter (where cost.unbilled_job)::integer as unbilled_job_count,
                            count(*) filter (where cost.pending_supplier_bill)::integer as las_pending_bill_count
                       from ledger cost
                      group by cost_date, cost.cost_currency
                   )
                   select cost_date, cost_currency, task_count, asr_task_count, l3_task_count, las_task_count,
                          discovery_run_count,comment_run_count,profile_run_count,statistics_run_count,
                          known_amount, actual_amount, estimated_amount, mixed_amount,
                          unknown_task_count, unbilled_job_count, las_pending_bill_count
                     from daily_cost
                    order by cost_date desc, cost_currency""",
                (project, actor, report_days),
            )
            records = [_json(dict(row)) for row in cur.fetchall()]
            for record in records:
                record["cost_status"] = "partial" if record["unknown_task_count"] else "complete"
            cur.execute(
                """select receipt.id,receipt.video_id,video.platform_video_id,video.title,
                          receipt.provider_task_ref,receipt.status,receipt.business_code,
                          receipt.model_id,receipt.operator_id,receipt.operator_version,
                          receipt.template_id,receipt.token_usages,receipt.estimated_cost,
                          receipt.cost_currency,receipt.pricing_version,receipt.executed_at,
                          receipt.result_sha256,receipt.binding_basis,
                          bill.line_count as supplier_line_count,
                          bill.signed_total as registered_supplier_amount,
                          bill.billing_dates as supplier_billing_dates,
                          (select item.reconciliation_status
                             from project_las_supplier_bill_item item
                            where item.receipt_id=receipt.id
                            order by item.reconciliation_version desc limit 1)
                             as supplier_reconciliation_status
                     from project_las_analysis_receipt receipt
                     join source_video video on video.id=receipt.video_id
                     left join lateral (
                       select count(*)::integer as line_count,
                              sum(item.signed_amount) as signed_total,
                              array_agg(distinct item.billing_date order by item.billing_date)
                                as billing_dates
                         from project_las_supplier_bill_item item
                        where item.receipt_id=receipt.id
                          and item.cost_currency=receipt.cost_currency
                     ) bill on true
                    where receipt.project_id=%s and project_actor_can_read(receipt.project_id,%s)
                      and receipt.executed_at >= (
                        (((now() at time zone 'Asia/Shanghai')::date - (%s - 1))::timestamp)
                          at time zone 'Asia/Shanghai')
                    order by receipt.executed_at desc,receipt.id desc
                    limit 501""",
                (project, actor, report_days),
            )
            las_rows = cur.fetchall()
            cur.execute(
                """select attempt.id,video.platform_video_id,video.title,
                          attempt.task_key,attempt.attempt_no,attempt.status,
                          attempt.provider_task_ref,attempt.submission_count,
                          attempt.error_code,attempt.cost_currency,attempt.created_at
                     from project_las_analysis_attempt attempt
                     join source_video video on video.id=attempt.video_id
                    where attempt.project_id=%s and project_actor_can_read(attempt.project_id,%s)
                      and attempt.created_at >= (
                        (((now() at time zone 'Asia/Shanghai')::date - (%s - 1))::timestamp)
                          at time zone 'Asia/Shanghai')
                      and not exists (select 1 from project_las_analysis_receipt receipt
                                      where receipt.attempt_id=attempt.id)
                    order by attempt.created_at desc,attempt.id desc
                    limit 501""",
                (project, actor, report_days),
            )
            open_attempts = cur.fetchall()
            cur.execute(
                """select bill.billing_date as cost_date,bill.billing_timezone,
                          bill.provider,bill.account_scope,bill.cost_currency,
                          count(*)::integer as supplier_line_count,
                          count(distinct bill.receipt_id)::integer as las_task_count,
                          sum(bill.signed_amount) as registered_line_amount
                     from project_las_supplier_bill_item bill
                    where bill.project_id=%s and project_actor_can_read(bill.project_id,%s)
                      and bill.billing_date >=
                        (now() at time zone 'Asia/Shanghai')::date - (%s - 1)
                    group by bill.billing_date,bill.billing_timezone,bill.provider,
                             bill.account_scope,bill.cost_currency
                    order by bill.billing_date desc,bill.provider,bill.account_scope,
                             bill.cost_currency""",
                (project, actor, report_days),
            )
            supplier_daily = [_json(dict(row)) for row in cur.fetchall()]
            return {
                "project_id": str(project),
                "project_name": project_row["name"],
                "role": str(project_row["role"]),
                "report_timezone": _REPORT_TIMEZONE,
                "days": report_days,
                "records": records,
                "las_receipts": [_json(dict(row)) for row in las_rows[:500]],
                "las_receipts_truncated": len(las_rows) > 500,
                "las_open_attempts": [_json(dict(row)) for row in open_attempts[:500]],
                "las_open_attempts_truncated": len(open_attempts) > 500,
                "las_supplier_daily": supplier_daily,
            }
    except (PermissionError, ValueError):
        raise
    except Exception:
        raise RuntimeError("RESEARCH_PROJECT_DAILY_COST_UNAVAILABLE") from None
