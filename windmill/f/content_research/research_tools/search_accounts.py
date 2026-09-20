from __future__ import annotations

from ..research_tool_lib.queries import research_db, safe_tool, search_accounts


def main(query: str = "", platform: str = "", limit: int = 10):
    return safe_tool(
        lambda: search_accounts(
            research_db(), query=query, platform=platform, limit=limit
        )
    )
