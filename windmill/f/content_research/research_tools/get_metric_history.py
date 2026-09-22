# py: ==3.14.*
from __future__ import annotations

from ..research_tool_lib.queries import get_metric_history, research_db, safe_tool


def main(video_id: str, days: int = 30, limit: int = 50):
    return safe_tool(
        lambda: get_metric_history(
            research_db(), video_id=video_id, days=days, limit=limit
        )
    )
