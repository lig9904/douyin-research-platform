# py: ==3.14.*
from __future__ import annotations

from ..research_tool_lib.queries import get_daily_briefing, research_db, safe_tool


def main(platform: str = "", hours: int = 24, limit: int = 10):
    return safe_tool(
        lambda: get_daily_briefing(
            research_db(), platform=platform, hours=hours, limit=limit
        )
    )
