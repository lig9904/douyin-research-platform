# py: ==3.14.*
#requirements:
#douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@586f9db5c49510c7f2be98c2bff22eaa9e96874b
#psycopg[binary]==3.3.6
#wmill==1.815.0

"""Project-bound verification of public video play counts via TikHub."""

from __future__ import annotations

import os
import re
from typing import Any, TypedDict
from uuid import UUID

from psycopg.conninfo import make_conninfo

from douyin_research.l0l1.video_statistics import (
    _accepted_videos,
    refresh_project_video_statistics,
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
_CONFIRMATION = "REFRESH_PUBLIC_VIDEO_STATISTICS_PAID"


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
    db: postgresql, project_id: str, video_platform_ids: list[str],
    action: str = "preview", confirmation: str = "",
) -> dict[str, Any]:
    if action not in {"preview", "execute"}:
        raise ValueError("unsupported video statistics action")
    if not isinstance(video_platform_ids, list) or not 1 <= len(video_platform_ids) <= 2 or any(
        not isinstance(item, str) or _VIDEO.fullmatch(item) is None
        for item in video_platform_ids
    ) or len(set(video_platform_ids)) != len(video_platform_ids):
        raise ValueError("one or two distinct exact Douyin video IDs are required")
    try:
        project = UUID(project_id)
    except (TypeError, ValueError, AttributeError):
        raise ValueError("project_id is invalid") from None
    actor = _actor()
    dsn = _dsn(db)
    try:
        import psycopg

        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            _accepted_videos(cur, project, tuple(video_platform_ids), actor)
        if action == "preview":
            return {
                "eligible": True, "external_calls": 0,
                "maximum_new_calls": 1, "estimated_base_price_usd": 0.001,
                "actual_charge_known": False,
                "message": "仅核验这些已接受视频的公开播放统计；缓存命中时不新发请求。",
            }
        if confirmation != _CONFIRMATION:
            raise ValueError("explicit paid statistics refresh confirmation is required")
        import wmill

        key = wmill.get_variable("f/content_research/tikhub_api_key")
        if not isinstance(key, str) or not key.strip():
            raise RuntimeError("TikHub secret is unavailable")
        result = refresh_project_video_statistics(
            dsn=dsn, project_id=project, video_platform_ids=video_platform_ids,
            actor=actor, api_key=key,
        )
        return {
            "status": "completed", "run_id": str(result.run_id),
            "play_counts": result.play_counts,
            "snapshots_inserted": result.snapshots_inserted,
            "external_calls": result.external_calls,
            "cached_calls": result.cached_calls,
            "estimated_api_cost_usd": result.estimated_api_cost_usd,
            "actual_charge_known": False,
        }
    except PermissionError:
        raise PermissionError("RESEARCH_PROJECT_VIDEO_STATISTICS_DENIED") from None
    except ValueError:
        raise
    except Exception:
        raise RuntimeError("RESEARCH_PROJECT_VIDEO_STATISTICS_UNAVAILABLE") from None
