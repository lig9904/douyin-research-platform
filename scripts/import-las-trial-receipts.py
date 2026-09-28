#!/usr/bin/env python3
"""Import reviewed LAS trial job results into project-private receipts.

The input file stays private and is never printed or copied into the DB. This
is a historical backfill, not proof that a future HTTP call was pre-recorded.
No supplier bill is imported by this command.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


_TASK = re.compile(r"^[A-Za-z0-9_-]{8,160}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_MODEL = "doubao-seed-2-0-lite-260428"
_OPERATOR = "las_video_understanding"
_OPERATOR_VERSION = "v1"
_TEMPLATE = "omni_video_audio_captioning@v1"


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _text(value: object, label: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} invalid")
    return value.strip()


def _uuid(value: object, label: str) -> UUID:
    try:
        return UUID(_text(value, label))
    except ValueError:
        raise ValueError(f"{label} invalid") from None


def _entry(raw: object) -> dict:
    if not isinstance(raw, dict) or set(raw) != {
        "platform_video_id", "submit_job_ref", "completion_job_ref",
        "submit_result", "completion_result", "estimated_cost", "executed_at",
    }:
        raise ValueError("LAS entry fields invalid")
    submit = raw["submit_result"]
    result = raw["completion_result"]
    if not isinstance(submit, dict) or not isinstance(result, dict):
        raise ValueError("LAS job result invalid")
    video_key = _text(raw["platform_video_id"], "platform_video_id", 160)
    task = _text(submit.get("task_id"), "submit task_id", 160)
    if not _TASK.fullmatch(task) or result.get("task_id") != task:
        raise ValueError("LAS task identity mismatch")
    if submit.get("platform_video_id") != video_key or result.get("platform_video_id") != video_key:
        raise ValueError("LAS video identity mismatch")
    if submit.get("external_paid_calls") != 1 or submit.get("actual_cost") is not None:
        raise ValueError("LAS submit cost claim invalid")
    if result.get("task_status") != "COMPLETED" or str(result.get("business_code")) != "0":
        raise ValueError("LAS completion not successful")
    if submit.get("model_name") != _MODEL:
        raise ValueError("LAS model mismatch")
    final = result.get("final_summary")
    if final is None:
        # Some Windmill result pages pretty-print literal newlines inside the
        # summary string, so a reviewed historical manifest may carry its
        # independently computed full-result and summary hashes instead.
        # This is explicitly a manual binding, not API authentication.
        result_sha = result.get("result_sha256")
        summary_sha = result.get("final_summary_sha256")
        summary_chars = result.get("final_summary_chars")
        if (not isinstance(result_sha, str) or not _SHA.fullmatch(result_sha) or
            not isinstance(summary_sha, str) or not _SHA.fullmatch(summary_sha) or
            type(summary_chars) is not int or summary_chars <= 0):
            raise ValueError("LAS reviewed result fingerprints invalid")
    else:
        try:
            parsed_summary = json.loads(final) if isinstance(final, str) else final
        except (ValueError, TypeError):
            raise ValueError("LAS final summary invalid") from None
        if not isinstance(parsed_summary, dict) or not parsed_summary:
            raise ValueError("LAS final summary unavailable")
        result_sha = _digest(result)
    asset_id = _uuid(submit.get("asset_id"), "asset_id")
    asset_sha = _text(submit.get("asset_sha256"), "asset_sha256", 64)
    if not _SHA.fullmatch(asset_sha):
        raise ValueError("LAS asset SHA-256 invalid")
    try:
        estimate = Decimal(_text(raw["estimated_cost"], "estimated_cost", 30))
    except InvalidOperation:
        raise ValueError("LAS estimate invalid") from None
    if estimate < 0 or estimate.as_tuple().exponent < -6:
        raise ValueError("LAS estimate invalid")
    executed = datetime.fromisoformat(_text(raw["executed_at"], "executed_at").replace("Z", "+00:00"))
    if executed.tzinfo is None or executed.utcoffset() is None:
        raise ValueError("LAS execution time must include timezone")
    token_usages = result.get("token_usages")
    if isinstance(token_usages, list) and token_usages:
        token_usages = {"models": token_usages}
    if not isinstance(token_usages, dict) or not token_usages:
        raise ValueError("LAS token usages invalid")
    submit_job = _uuid(raw["submit_job_ref"], "submit_job_ref")
    completion_job = _uuid(raw["completion_job_ref"], "completion_job_ref")
    if submit_job == completion_job:
        raise ValueError("LAS submit and completion jobs must differ")
    return {
        "video_key": video_key, "task": task, "asset_id": asset_id,
        "asset_sha": asset_sha, "submit_job": str(submit_job),
        "completion_job": str(completion_job), "result_sha": result_sha,
        "input_sha": _digest({"asset_sha256": asset_sha, "task_id": task,
                              "submit_job_ref": str(submit_job), "model": _MODEL,
                              "operator": _OPERATOR, "template": _TEMPLATE}),
        "token_usages": token_usages, "estimate": estimate,
        "executed_at": executed,
    }


def _manifest(raw: object) -> tuple[dict, list[dict]]:
    if not isinstance(raw, dict) or set(raw) != {
        "organization_slug", "project_slug", "authorized_by",
        "authorization_ref", "account_scope", "pricing_version", "entries",
    }:
        raise ValueError("LAS manifest fields invalid")
    entries = raw["entries"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 100:
        raise ValueError("LAS entries invalid")
    config = {key: _text(raw[key], key, 160 if key != "authorization_ref" else 512)
              for key in ("organization_slug", "project_slug", "authorized_by",
                          "authorization_ref", "account_scope", "pricing_version")}
    if "@" not in config["authorized_by"]:
        raise ValueError("authorized_by invalid")
    normalized = [_entry(item) for item in entries]
    if len({row["task"] for row in normalized}) != len(normalized):
        raise ValueError("duplicate LAS task in manifest")
    return config, normalized


def _import(conn: psycopg.Connection, config: dict, entries: list[dict]) -> tuple[int, int]:
    inserted = skipped = 0
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """select project.id from research_project project
               join research_organization org on org.id=project.organization_id
              where org.slug=%s and project.slug=%s and org.status='active'
                and project.status='active'""",
            (config["organization_slug"], config["project_slug"]),
        )
        project = cur.fetchone()
        if project is None:
            raise ValueError("target project unavailable")
        project_id = project["id"]
        cur.execute(
            """select member.role,member.status,
                      (member.effective_until is null or member.effective_until>now()) as unexpired
                 from research_project_member member
                where member.project_id=%s and member.actor_id=%s
                  and member.effective_from<=now()
                order by member.effective_from desc limit 1""",
            (project_id, config["authorized_by"]),
        )
        current_member = cur.fetchone()
        if (current_member is None or current_member["role"] not in ("owner", "admin")
            or current_member["status"] != "active" or not current_member["unexpired"]):
            raise ValueError("recorded authorizer is not an active project owner/admin")
        for entry in entries:
            cur.execute(
                """select video.id,asset.id as asset_id,asset.content_sha256
                     from source_video video
                     join project_video_inclusion inclusion_row
                       on inclusion_row.video_id=video.id and inclusion_row.project_id=%s
                     join media_asset asset on asset.video_id=video.id and asset.kind='video'
                    where video.platform='douyin' and video.platform_video_id=%s
                      and video.availability_status='available'
                      and inclusion_row.status='accepted'
                      and asset.id=%s and asset.content_sha256=%s""",
                (project_id, entry["video_key"], entry["asset_id"], entry["asset_sha"]),
            )
            source = cur.fetchone()
            if source is None:
                raise ValueError("accepted project video or source asset mismatch")
            task_key = f"las-trial:{project_id}:{entry['task']}"
            cur.execute(
                """select receipt.id,receipt.result_sha256,receipt.asset_sha256,
                          receipt.estimated_cost,receipt.project_id,receipt.video_id,
                          receipt.asset_id,receipt.provider,receipt.account_scope,
                          receipt.provider_task_ref,receipt.submit_job_ref,
                          receipt.completion_job_ref,receipt.executed_at,
                          receipt.pricing_version,receipt.model_id,receipt.operator_id,
                          receipt.operator_version,receipt.template_id,
                          attempt.authorization_ref,attempt.authorized_by,
                          attempt.input_fingerprint
                     from project_las_analysis_receipt receipt
                     join project_las_analysis_attempt attempt on attempt.id=receipt.attempt_id
                    where receipt.project_id=%s and receipt.task_key=%s
                      and receipt.attempt_no=1""",
                (project_id, task_key),
            )
            existing = cur.fetchone()
            if existing is not None:
                if (existing["result_sha256"] != entry["result_sha"] or
                    existing["asset_sha256"] != entry["asset_sha"] or
                    existing["estimated_cost"] != entry["estimate"] or
                    existing["video_id"] != source["id"] or
                    existing["asset_id"] != entry["asset_id"] or
                    existing["provider"] != "volcengine" or
                    existing["account_scope"] != config["account_scope"] or
                    existing["provider_task_ref"] != entry["task"] or
                    existing["submit_job_ref"] != entry["submit_job"] or
                    existing["completion_job_ref"] != entry["completion_job"] or
                    existing["executed_at"] != entry["executed_at"] or
                    existing["pricing_version"] != config["pricing_version"] or
                    existing["model_id"] != _MODEL or
                    existing["operator_id"] != _OPERATOR or
                    existing["operator_version"] != _OPERATOR_VERSION or
                    existing["template_id"] != _TEMPLATE or
                    existing["authorization_ref"] != config["authorization_ref"] or
                    existing["authorized_by"] != config["authorized_by"] or
                    existing["input_fingerprint"] != entry["input_sha"]):
                    raise ValueError("existing LAS receipt conflicts with import")
                skipped += 1
                continue
            cur.execute(
                """insert into project_las_analysis_attempt(
                     project_id,video_id,asset_id,asset_sha256,task_key,attempt_no,
                     provider,account_scope,cost_currency,provider_task_ref,
                     submit_job_ref,status,submission_count,authorization_ref,
                     authorized_by,record_mode,input_fingerprint,created_at)
                   values(%s,%s,%s,%s,%s,1,'volcengine',%s,'CNY',%s,%s,
                          'completed',1,%s,%s,'historical_backfill',%s,%s)
                   returning id""",
                (project_id, source["id"], entry["asset_id"], entry["asset_sha"],
                 task_key, config["account_scope"], entry["task"], entry["submit_job"],
                 config["authorization_ref"], config["authorized_by"],
                 entry["input_sha"], entry["executed_at"]),
            )
            attempt_id = cur.fetchone()["id"]
            cur.execute(
                """insert into project_las_analysis_receipt(
                     attempt_id,project_id,video_id,asset_id,asset_sha256,
                     task_key,attempt_no,provider,account_scope,provider_task_ref,
                     submit_job_ref,completion_job_ref,model_id,operator_id,
                     operator_version,template_id,status,business_code,
                     result_sha256,token_usages,estimated_cost,cost_currency,
                     pricing_version,executed_at,recorded_by,binding_basis)
                   values(%s,%s,%s,%s,%s,%s,1,'volcengine',%s,%s,%s,%s,%s,%s,
                          %s,%s,'completed',0,%s,%s,%s,'CNY',%s,%s,%s,
                          'reviewed_submission_and_result')""",
                (attempt_id, project_id, source["id"], entry["asset_id"],
                 entry["asset_sha"], task_key, config["account_scope"], entry["task"],
                 entry["submit_job"], entry["completion_job"], _MODEL, _OPERATOR,
                 _OPERATOR_VERSION, _TEMPLATE, entry["result_sha"],
                 Jsonb(entry["token_usages"]), entry["estimate"],
                 config["pricing_version"], entry["executed_at"],
                 config["authorized_by"]),
            )
            inserted += 1
    return inserted, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="private local JSON export")
    parser.add_argument("--apply", action="store_true", help="commit after review")
    args = parser.parse_args()
    dsn = os.environ.get("LAS_IMPORT_DATABASE_URL", "")
    if not dsn:
        raise SystemExit("LAS_IMPORT_DATABASE_URL is required")
    config, entries = _manifest(json.loads(args.manifest.read_text(encoding="utf-8")))
    with psycopg.connect(dsn) as conn:
        inserted, skipped = _import(conn, config, entries)
        if not args.apply:
            conn.rollback()
    print(f"LAS_RECEIPT_{'APPLIED' if args.apply else 'DRY_RUN'} inserted={inserted} skipped={skipped}")


if __name__ == "__main__":
    main()
