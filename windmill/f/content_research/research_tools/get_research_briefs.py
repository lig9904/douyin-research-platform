# py: ==3.14.*
from __future__ import annotations

from ..research_tool_lib.queries import get_research_briefs


def main(limit: int = 20):
    # No verified actor is available to a shared MCP script token.
    return get_research_briefs({}, limit=limit)
