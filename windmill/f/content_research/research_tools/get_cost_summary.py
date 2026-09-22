# py: ==3.14.*
from __future__ import annotations

from ..research_tool_lib.queries import get_cost_summary, research_db, safe_tool


def main(days: int = 30):
    return safe_tool(lambda: get_cost_summary(research_db(), days=days))
