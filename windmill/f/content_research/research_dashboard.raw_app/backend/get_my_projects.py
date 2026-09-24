# py: ==3.14.*
#requirements:
#psycopg[binary]==3.3.6

"""Return only active research projects visible to the Windmill end user."""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
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


_ACTOR_RE = re.compile(r"^[^\s@]{1,128}@[^\s@]{1,120}$")
_LEGACY_ADMIN_ALLOWLIST_PATH = "f/content_research/research_action_writers"
_L3_REVIEWER_ALLOWLIST_PATH = "f/content_research/l3_privacy_reviewers"


def _actor() -> str:
    """Use the server-provided identity; never accept an actor from the client."""
    value = os.environ.get("WM_END_USER_EMAIL", "").strip().lower()
    if not _ACTOR_RE.fullmatch(value) or len(value) > 254:
        raise PermissionError("RESEARCH_ACTION_IDENTITY_REQUIRED")
    return value


def _legacy_admin(actor: str) -> bool:
    """Return whether this server-authenticated user is a legacy administrator.

    This is intentionally an optional capability signal, not an authorization
    input.  The list is obtained inside the Windmill worker, is never accepted
    from the caller, and a missing or malformed variable is treated as no
    administrator access.
    """
    try:
        import wmill

        raw = wmill.get_variable(_LEGACY_ADMIN_ALLOWLIST_PATH)
    except Exception:
        return False
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        values = json.loads(raw) if raw.lstrip().startswith("[") else re.split(r"[,\n]", raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(values, list):
        return False
    return any(
        isinstance(value, str)
        and _ACTOR_RE.fullmatch(value.strip().lower())
        and value.strip().lower() == actor
        for value in values
    )


def _central_l3_reviewer(actor: str) -> bool:
    """Return only a capability bit; never expose the reviewer roster."""
    try:
        import wmill

        raw = wmill.get_variable(_L3_REVIEWER_ALLOWLIST_PATH)
    except Exception:
        return False
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        values = json.loads(raw) if raw.strip().startswith("[") else re.split(r"[,\n]", raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(values, list) or not values or not all(isinstance(value, str) for value in values):
        return False
    # Mirror the backend's fail-closed lowercase-list contract. Malformed
    # entries invalidate the entire roster rather than granting a partial UI.
    normalized = [value.strip() for value in values]
    if any(not value or value != value.lower() or not _ACTOR_RE.fullmatch(value) for value in normalized):
        return False
    return actor in normalized


def _json(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json(item) for item in value]
    return value


def _connect(db: postgresql):
    args: dict[str, Any] = {
        "host": db["host"],
        "port": int(db.get("port", 5432)),
        "user": db["user"],
        "password": db["password"],
        "dbname": db["dbname"],
        "sslmode": db.get("sslmode", "prefer"),
    }
    # ``options`` is not configured in Windmill.  It makes the isolated
    # database test able to use a private PostgreSQL schema without changing
    # the production resource contract.
    if db.get("options"):
        args["options"] = db["options"]
    return psycopg.connect(**args, row_factory=dict_row)


def main(db: postgresql):
    actor = _actor()
    # Fetch independently of the roster query.  A Windmill-variable outage
    # must never make project membership data unavailable.
    legacy_admin = _legacy_admin(actor)
    central_l3_reviewer = _central_l3_reviewer(actor)
    try:
        with _connect(db) as conn, conn.cursor() as cur:
            cur.execute("set transaction read only")
            cur.execute(
                """
                with latest_membership as (
                  select distinct on (m.project_id)
                    m.project_id, m.role, m.status, m.effective_from, m.effective_until
                  from research_project_member m
                  where m.actor_id = %s
                    and m.effective_from <= now()
                  order by m.project_id, m.effective_from desc
                )
                select
                  p.id, p.slug, p.name, p.status,
                  o.id as organization_id, o.slug as organization_slug,
                  o.name as organization_name,
                  am.role as member_role, am.effective_from, am.effective_until
                from latest_membership am
                join research_project p on p.id = am.project_id
                join research_organization o on o.id = p.organization_id
                where am.status = 'active'
                  and (am.effective_until is null or am.effective_until > now())
                  and p.status = 'active' and o.status = 'active'
                order by o.name, p.name, p.id
                limit 100
                """,
                (actor,),
            )
            projects = [
                {**_json(dict(row)), "can_review_l3": central_l3_reviewer and row["member_role"] in {"owner", "admin"}}
                for row in cur.fetchall()
            ]
        return {"projects": projects, "read_only": True, "legacy_admin": legacy_admin}
    except PermissionError:
        raise
    except Exception:
        raise RuntimeError("RESEARCH_PROJECTS_UNAVAILABLE") from None
