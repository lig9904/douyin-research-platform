# py: ==3.14.*
from __future__ import annotations

from ..research_tool_lib.queries import get_account_videos, research_db, safe_tool


def main(account_id: str, days: int = 90, limit: int = 20):
    return safe_tool(
        lambda: get_account_videos(
            research_db(), account_id=account_id, days=days, limit=limit
        )
    )
