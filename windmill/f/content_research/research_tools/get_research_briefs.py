# py: ==3.14.*
from __future__ import annotations

from ..research_tool_lib.queries import get_research_briefs, research_db, safe_tool


def main(limit: int = 20):
    return safe_tool(lambda: get_research_briefs(research_db(), limit=limit))
