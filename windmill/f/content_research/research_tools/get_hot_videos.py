# py: ==3.14.*
from __future__ import annotations

from ..research_tool_lib.queries import get_hot_videos, research_db, safe_tool


def main(platform: str = "", days: int = 7, limit: int = 10):
    return safe_tool(
        lambda: get_hot_videos(
            research_db(), platform=platform, days=days, limit=limit
        )
    )
