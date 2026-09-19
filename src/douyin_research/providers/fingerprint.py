"""Canonical request fingerprints used for cache/idempotency."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _normalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _normalize(value[k]) for k in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, set):
        return sorted(_normalize(v) for v in value)
    return value


def request_fingerprint(
    *,
    provider: str,
    endpoint_key: str,
    params: dict[str, Any] | None = None,
    body: Any = None,
    auth_scope: str = "default",
) -> str:
    """Return stable SHA-256 without including credentials."""
    canonical = {
        "provider": provider,
        "endpoint_key": endpoint_key,
        "params": _normalize(params or {}),
        "body": _normalize(body),
        "auth_scope": auth_scope,
    }
    encoded = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
