#!/usr/bin/env python3
"""Operator-only, explicit bootstrap for one research organization/project.

This is deliberately not a Raw App route.  A database operator supplies the
connection through an environment variable and must name the expected database
and exact organization/project before any write.  Existing projects are never
silently changed or granted a new owner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb


SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
EMAIL = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,120}$")


class ProvisionError(ValueError):
    """Safe, credential-free operator error."""


def _validate_slug(value: str, field: str) -> str:
    if not SLUG.fullmatch(value):
        raise ProvisionError(f"{field} must be a lowercase slug of 2-63 characters")
    return value


def _validate_name(value: str, field: str) -> str:
    normalized = " ".join(value.strip().split())
    if not 1 <= len(normalized) <= 160 or "\x00" in normalized:
        raise ProvisionError(f"{field} must contain 1-160 visible characters")
    return normalized


def _validate_email(value: str) -> str:
    normalized = value.strip().lower()
    if not EMAIL.fullmatch(normalized) or len(normalized) > 254:
        raise ProvisionError("owner email is invalid")
    return normalized


def _database_name(dsn: str) -> str:
    try:
        return str(conninfo_to_dict(dsn).get("dbname") or "")
    except Exception as exc:
        raise ProvisionError("database connection setting is invalid") from exc


def _payload(args: argparse.Namespace) -> dict[str, str]:
    return {
        "organization_slug": _validate_slug(args.organization_slug, "organization slug"),
        "organization_name": _validate_name(args.organization_name, "organization name"),
        "project_slug": _validate_slug(args.project_slug, "project slug"),
        "project_name": _validate_name(args.project_name, "project name"),
        "operator": _validate_email(args.operator_email),
        "owner": _validate_email(args.owner_email),
        "project_status": args.project_status,
    }


def provision(conn: psycopg.Connection, payload: dict[str, str], *, apply: bool) -> dict[str, str]:
    """Preview or atomically create exactly one project and its initial owner."""
    org_slug = payload["organization_slug"]
    project_slug = payload["project_slug"]
    with conn.transaction(), conn.cursor() as cur:
        # Serializes concurrent operator attempts at this exact destination.
        cur.execute(
            "select pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"research-project-provision:{org_slug}/{project_slug}",),
        )
        cur.execute(
            "select id,name,status from research_organization where slug=%s for update",
            (org_slug,),
        )
        org = cur.fetchone()
        if org is not None:
            if org[1] != payload["organization_name"] or org[2] != "active":
                raise ProvisionError("existing organization name/status differs; no changes made")
            cur.execute(
                "select id,name,status from research_project where organization_id=%s and slug=%s for update",
                (org[0], project_slug),
            )
            project = cur.fetchone()
            if project is not None:
                raise ProvisionError("project already exists; existing membership was not changed")

        result = {
            "operation": "create_project_in_existing_organization" if org else "create_organization_and_project",
            "organization_slug": org_slug,
            "project_slug": project_slug,
            "project_status": payload["project_status"],
            "owner": payload["owner"],
        }
        if not apply:
            return result

        if org is None:
            cur.execute(
                "insert into research_organization(slug,name,status) values(%s,%s,'active') returning id",
                (org_slug, payload["organization_name"]),
            )
            org_id = cur.fetchone()[0]
        else:
            org_id = org[0]
        cur.execute(
            """insert into research_project(organization_id,slug,name,status)
               values(%s,%s,%s,%s) returning id""",
            (org_id, project_slug, payload["project_name"], payload["project_status"]),
        )
        project_id = cur.fetchone()[0]
        cur.execute(
            """insert into research_project_member(project_id,actor_id,role,status)
               values(%s,%s,'owner','active')""",
            (project_id, payload["owner"]),
        )
        audit_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        audit_id = uuid4()
        cur.execute(
            """insert into research_user_action
               (idempotency_key,actor,action_type,payload_hash,outcome)
               values(%s,%s,'operator_project_provision',%s,%s)""",
            (
                audit_id,
                payload["operator"],
                hashlib.sha256(audit_payload.encode("utf-8")).hexdigest(),
                Jsonb({"organization_id": str(org_id), "project_id": str(project_id), "status": payload["project_status"]}),
            ),
        )
        return {**result, "project_id": str(project_id), "audit_id": str(audit_id)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn-env", default="RESEARCH_PROVISION_DSN")
    parser.add_argument("--expected-db", required=True)
    parser.add_argument("--organization-slug", required=True)
    parser.add_argument("--organization-name", required=True)
    parser.add_argument("--project-slug", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--operator-email", required=True)
    parser.add_argument("--owner-email", required=True)
    parser.add_argument("--project-status", choices=("draft", "active"), default="draft")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", help="required with --apply: exact organization/project slug")
    args = parser.parse_args(argv)
    try:
        payload = _payload(args)
        dsn = os.environ.get(args.dsn_env, "")
        if not dsn or _database_name(dsn) != args.expected_db:
            raise ProvisionError("database is missing or does not match --expected-db")
        exact_target = f"{payload['organization_slug']}/{payload['project_slug']}"
        if args.apply and args.confirm != exact_target:
            raise ProvisionError("--apply requires --confirm with the exact organization/project slug")
        if not args.apply and args.confirm:
            raise ProvisionError("--confirm is only used with --apply")
        with psycopg.connect(dsn) as conn:
            result = provision(conn, payload, apply=args.apply)
        print(json.dumps({"mode": "applied" if args.apply else "preview", **result}, ensure_ascii=False, sort_keys=True))
        return 0
    except ProvisionError as exc:
        print(f"provision refused: {exc}", file=sys.stderr)
        return 2
    except psycopg.Error:
        print("provision failed: database operation rejected; transaction rolled back", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
