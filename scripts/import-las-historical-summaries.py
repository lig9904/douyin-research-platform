#!/usr/bin/env python3
"""Attach original LAS machine summaries to existing historical project receipts.

This never submits a LAS task, creates a receipt, or changes a human Case review.
It rejects any result whose original job, task, video, or hashes disagree with
the previously reviewed private manifest. Dry-run is the default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _private_json(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        raise ValueError("input must be a regular private file")
    info = path.stat()
    if stat.S_IMODE(info.st_mode) & 0o077 or info.st_size > 5_000_000:
        raise ValueError("input must be mode 0600 and at most 5 MB")
    return json.loads(path.read_text(encoding="utf-8"))


def _validated(manifest: object, raw_results: list[object]) -> tuple[dict, list[dict]]:
    if not isinstance(manifest, dict) or set(manifest) != {
        "organization_slug", "project_slug", "authorized_by", "authorization_ref",
        "account_scope", "pricing_version", "entries",
    } or not isinstance(manifest["entries"], list) or not 1 <= len(manifest["entries"]) <= 100:
        raise ValueError("historical manifest invalid")
    if not all(isinstance(manifest[name], str) and manifest[name].strip() for name in
               ("organization_slug", "project_slug", "authorized_by", "account_scope")):
        raise ValueError("historical manifest scope invalid")
    entries = {}
    for entry in manifest["entries"]:
        if (not isinstance(entry, dict) or
            not isinstance(entry.get("completion_result"), dict) or
            not isinstance(entry.get("submit_result"), dict)):
            raise ValueError("historical manifest entry invalid")
        video = entry.get("platform_video_id")
        if not isinstance(video, str) or not video.isdecimal() or video in entries:
            raise ValueError("historical manifest video identity invalid")
        entries[video] = entry
    if len(raw_results) != len(entries):
        raise ValueError("provide exactly one original result per manifest entry")
    normalized = []
    for result in raw_results:
        if not isinstance(result, dict):
            raise ValueError("original LAS result invalid")
        video = result.get("platform_video_id")
        entry = entries.pop(video, None)
        if entry is None:
            raise ValueError("original LAS result video is missing or duplicated")
        recorded = entry["completion_result"]
        task = result.get("task_id")
        if (not isinstance(task, str) or task != recorded.get("task_id") or
            task != entry.get("submit_result", {}).get("task_id") or
            result.get("task_status") != "COMPLETED" or str(result.get("business_code")) != "0"):
            raise ValueError("original LAS task identity or completion mismatch")
        summary = result.get("final_summary")
        if not isinstance(summary, str) or not 1 <= len(summary) <= 250_000:
            raise ValueError("original LAS summary invalid")
        try:
            parsed = json.loads(summary)
        except ValueError:
            raise ValueError("original LAS summary is not JSON") from None
        if not isinstance(parsed, dict) or not parsed:
            raise ValueError("original LAS summary structure invalid")
        summary_sha = hashlib.sha256(summary.encode("utf-8")).hexdigest()
        if (summary_sha != recorded.get("final_summary_sha256") or
            len(summary) != recorded.get("final_summary_chars") or
            _digest(result) != recorded.get("result_sha256") or
            result.get("token_usages") != recorded.get("token_usages")):
            raise ValueError("original LAS bytes disagree with reviewed manifest")
        normalized.append({"video": video, "task": task,
                           "completion_job": entry.get("completion_job_ref"),
                           "result_sha": recorded["result_sha256"],
                           "summary": summary, "summary_sha": summary_sha})
    return manifest, normalized


def _import(conn: psycopg.Connection, manifest: dict, results: list[dict]) -> tuple[int, int]:
    inserted = skipped = 0
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """select p.id from research_project p
               join research_organization o on o.id=p.organization_id
               where o.slug=%s and p.slug=%s and o.status='active' and p.status='active'""",
            (manifest["organization_slug"], manifest["project_slug"]),
        )
        project = cur.fetchone()
        if project is None:
            raise ValueError("target project unavailable")
        for result in results:
            cur.execute(
                """select receipt.id,receipt.project_id,receipt.video_id,
                          receipt.result_sha256,receipt.completion_job_ref,
                          receipt.account_scope,
                          attempt.record_mode,attempt.authorized_by,
                          source.platform_video_id,existing.final_summary,
                          existing.summary_sha256,existing.provenance
                   from project_las_analysis_receipt receipt
                   join project_las_analysis_attempt attempt on attempt.id=receipt.attempt_id
                   join source_video source on source.id=receipt.video_id
                   left join project_las_analysis_result existing on existing.receipt_id=receipt.id
                   where receipt.project_id=%s and receipt.provider='volcengine'
                     and receipt.provider_task_ref=%s and receipt.status='completed'
                   for key share of receipt""",
                (project["id"], result["task"]),
            )
            receipt = cur.fetchone()
            if (receipt is None or receipt["record_mode"] != "historical_backfill" or
                receipt["authorized_by"] != manifest["authorized_by"] or
                receipt["account_scope"] != manifest["account_scope"] or
                receipt["platform_video_id"] != result["video"] or
                receipt["completion_job_ref"] != result["completion_job"] or
                receipt["result_sha256"] != result["result_sha"]):
                raise ValueError("existing historical LAS receipt conflicts with original result")
            if receipt["final_summary"] is not None:
                if (receipt["final_summary"] != result["summary"] or
                    receipt["summary_sha256"] != result["summary_sha"] or
                    receipt["provenance"] != "provider_machine_only"):
                    raise ValueError("existing LAS machine result conflicts with import")
                skipped += 1
                continue
            cur.execute(
                """insert into project_las_analysis_result
                     (receipt_id,project_id,video_id,final_summary,summary_sha256,provenance)
                   values (%s,%s,%s,%s,%s,'provider_machine_only')""",
                (receipt["id"], receipt["project_id"], receipt["video_id"],
                 result["summary"], result["summary_sha"]),
            )
            inserted += 1
    return inserted, skipped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="private receipt manifest")
    parser.add_argument("results", type=Path, nargs="+", help="original Windmill result JSON files")
    parser.add_argument("--apply", action="store_true", help="commit instead of rollback")
    args = parser.parse_args()
    dsn = os.environ.get("LAS_IMPORT_DATABASE_URL", "")
    if not dsn:
        raise SystemExit("LAS_IMPORT_DATABASE_URL is required")
    manifest, results = _validated(_private_json(args.manifest),
                                   [_private_json(path) for path in args.results])
    with psycopg.connect(dsn) as conn:
        inserted, skipped = _import(conn, manifest, results)
        if not args.apply:
            conn.rollback()
    print(f"LAS_SUMMARY_{'APPLIED' if args.apply else 'DRY_RUN'} inserted={inserted} skipped={skipped}")


if __name__ == "__main__":
    main()
