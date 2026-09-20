from __future__ import annotations

from ..research_tool_lib.queries import (
    get_blackhorse_videos,
    research_db,
    safe_tool,
)


def main(
    platform: str = "",
    min_score: float = 60,
    limit: int = 10,
):
    return safe_tool(
        lambda: get_blackhorse_videos(
            research_db(), platform=platform, min_score=min_score, limit=limit
        )
    )
