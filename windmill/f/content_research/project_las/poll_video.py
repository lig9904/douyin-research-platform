# /// script
# requires-python = "==3.14.*"
# dependencies = ["douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@5b93ffa7681038ecbd355c5ffbbbb3b5cdb53180", "psycopg[binary]==3.3.6", "wmill==1.815.0"]
# ///
"""Poll only a previously bound LAS task ID; never submit a replacement."""
from __future__ import annotations

import os
import re
from uuid import UUID

from psycopg.conninfo import make_conninfo

from douyin_research.project_analysis.las import ProjectLASService
from douyin_research.providers.volcengine_las import VolcengineLASProvider


_SCOPE = re.compile(r"[A-Za-z0-9._:-]{1,160}\Z")


def main(attempt_id: str) -> dict:
    import wmill

    attempt = UUID(attempt_id)
    try:
        worker_email = wmill.get_variable("f/content_research/project_las_worker_email")
        if not isinstance(worker_email, str) or "@" not in worker_email:
            raise ValueError
        actual_actor = (os.environ.get("WM_END_USER_EMAIL") or os.environ.get("WM_EMAIL") or "").strip().lower()
        if actual_actor != worker_email.strip().lower():
            raise PermissionError("project LAS worker identity mismatch")
        db = wmill.get_resource("f/content_research/research_db")
        dsn = make_conninfo(
            host=db["host"], port=int(db.get("port", 5432)), user=db["user"],
            password=db["password"], dbname=db["dbname"],
            sslmode=db.get("sslmode", "prefer"),
        )
        api_key = wmill.get_variable("f/content_research/las_video_understanding_api_key")
        scope = wmill.get_variable("f/content_research/project_las_account_scope")
        if not isinstance(api_key, str) or not api_key:
            raise ValueError
        if not isinstance(scope, str) or not _SCOPE.fullmatch(scope):
            raise ValueError
    except Exception:
        raise RuntimeError("project LAS poll configuration unavailable") from None
    service = ProjectLASService(
        # Poll never reads or delivers media. Keep an inert constructor origin
        # so a storage-variable outage cannot strand an already paid task.
        dsn, delivery_origin="https://poll-only.invalid",
        trusted_worker_actor=worker_email,
        account_scope=scope,
    )
    with VolcengineLASProvider(api_key=api_key, timeout_seconds=30) as provider:
        return service.poll(attempt_id=attempt, provider=provider)
