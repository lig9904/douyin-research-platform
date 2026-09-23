# py: ==3.14.*
from __future__ import annotations


def main(limit: int = 20):
    # No verified actor is available to a shared MCP script token.
    return {"ok": False, "error": "MCP_IDENTITY_SCOPE_UNAVAILABLE"}
