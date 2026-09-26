# py: ==3.14.*
#requirements:
#douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@44aa2ffd3a7384a792a1a17b41f3e5f312d9f749
#psycopg[binary]==3.3.6
#wmill==1.815.0

"""Project-manager initiated public account profile refresh.

The preview is database-only. Execution sends one stable sec_user_id to TikHub
at most once and returns no raw response or secret.
"""

from __future__ import annotations

import os
import re
from typing import Any, TypedDict
from uuid import UUID

from psycopg.conninfo import make_conninfo

from douyin_research.l0l1.account_profiles import (
    _accepted_account,
    refresh_project_account_profiles,
)


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


_EMAIL = re.compile(r"[^\s@]{1,128}@[^\s@]{1,120}\Z")
_VIDEO = re.compile(r"[0-9]{15,25}\Z")
_CONFIRMATION = "REFRESH_PUBLIC_ACCOUNT_PROFILE_PAID"


def _actor() -> str:
    value = os.environ.get("WM_END_USER_EMAIL", "")
    if value != value.strip().lower() or len(value) > 254 or _EMAIL.fullmatch(value) is None:
        raise PermissionError("RESEARCH_ACTION_IDENTITY_REQUIRED")
    return value


def _dsn(db: postgresql) -> str:
    args: dict[str, Any] = {
        key: db[key] for key in ("host", "port", "user", "password", "dbname", "sslmode")
        if key in db
    }
    if db.get("options"):
        args["options"] = db["options"]
    return make_conninfo(**args)


def main(
    db: postgresql, project_id: str, video_platform_id: str,
    action: str = "preview", confirmation: str = "",
) -> dict[str, Any]:
    if action not in {"preview", "execute"}:
        raise ValueError("unsupported account profile action")
    if not isinstance(video_platform_id, str) or _VIDEO.fullmatch(video_platform_id) is None:
        raise ValueError("exact Douyin video ID is required")
    try:
        project = UUID(project_id)
    except (TypeError, ValueError, AttributeError):
        raise ValueError("project_id is invalid") from None
    actor = _actor()
    dsn = _dsn(db)
    try:
        _accepted_account(dsn, project, video_platform_id, actor)
        if action == "preview":
            return {
                "eligible": True, "external_calls": 0,
                "maximum_new_calls": 1, "estimated_base_price_usd": 0.001,
                "actual_charge_known": False,
                "message": "仅核验这个已接受视频的公开账号资料；命中缓存时不新发请求。",
            }
        if confirmation != _CONFIRMATION:
            raise ValueError("explicit paid profile refresh confirmation is required")
        import wmill

        key = wmill.get_variable("f/content_research/tikhub_api_key")
        if not isinstance(key, str) or not key.strip():
            raise RuntimeError("TikHub secret is unavailable")
        result = refresh_project_account_profiles(
            dsn=dsn, project_id=project, video_platform_ids=[video_platform_id],
            actor=actor, api_key=key,
        )
        return {
            "status": "completed", "run_id": str(result.run_id),
            "requested_video_count": result.requested_video_count,
            "distinct_account_count": result.distinct_account_count,
            "snapshots_inserted": result.snapshots_inserted,
            "external_calls": result.external_calls,
            "cached_calls": result.cached_calls,
            "estimated_api_cost_usd": result.estimated_api_cost_usd,
            "actual_charge_known": False,
        }
    except PermissionError:
        raise PermissionError("RESEARCH_PROJECT_ACCOUNT_PROFILE_DENIED") from None
    except ValueError:
        raise
    except Exception:
        raise RuntimeError("RESEARCH_PROJECT_ACCOUNT_PROFILE_UNAVAILABLE") from None
