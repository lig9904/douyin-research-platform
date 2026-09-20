from __future__ import annotations

from ..research_tool_lib.queries import get_case_detail, research_db, safe_tool


def main(video_id: str):
    return safe_tool(lambda: get_case_detail(research_db(), video_id=video_id))
