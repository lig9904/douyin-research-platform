"""Local, read-only MCP surface for canonical research records.

This package is deliberately independent of Windmill's MCP gateway.  It is a
stdio-only verification entry point and contains no provider, secret, budget
reservation, or write capability.
"""

from .readonly import CanonicalResearchQueries

__all__ = ("CanonicalResearchQueries",)
