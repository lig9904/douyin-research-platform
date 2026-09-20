"""Honest quote accounting: observed HTTP success is not supplier reconciliation."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .endpoints import EndpointSpec


def quote_call(
    spec: EndpointSpec, *, cached: bool = False, successful_response: bool = False,
    attempts: tuple[dict[str, Any], ...] | None = None,
) -> tuple[float | None, float | None, dict[str, Any]]:
    metadata: dict[str, Any] = {
        "price_source": spec.price_source, "pricing_version": spec.pricing_version,
        "http_attempt_count": len(attempts) if attempts is not None else None,
        "unknown_attempt_count": (sum(a.get("http_status") is None for a in attempts)
                                  if attempts is not None else None),
        "attempts": list(attempts) if attempts is not None else None,
    }
    if cached or not spec.paid:
        metadata.update(cost_basis="cache_zero" if cached else "free_endpoint",
                        billing_status="known_zero")
        if cached:
            metadata.update(http_attempt_count=0, unknown_attempt_count=0, attempts=[])
        return 0.0, 0.0, metadata
    if attempts is not None:
        successes = sum(a.get("http_status") == 200 for a in attempts)
        unknown = metadata["unknown_attempt_count"]
        # Unexpected redirects/1xx/2xx-other-than-200 are not proven non-billable here.
        unknown += sum(a.get("http_status") is not None and a.get("http_status") != 200
                       and a.get("http_status") < 400 for a in attempts)
        metadata["unknown_attempt_count"] = unknown
        if not successes and not unknown:
            metadata.update(cost_basis="nonbillable_http", billing_status="known_zero")
            return 0.0, 0.0, metadata
    else:
        successes = int(successful_response)
        unknown = None
    estimated = (float(Decimal(str(spec.unit_cost_usd)) * successes)
                 if spec.unit_cost_usd is not None and successes else None)
    metadata.update(
        cost_basis="estimated_unit_price" if estimated is not None else "unpriced",
        billing_status="estimated" if estimated is not None and unknown == 0 else "unknown",
        observed_successful_http_attempts=successes if attempts is not None else None,
    )
    # Unknown attempts are additional possible cost, not silently assigned a zero tariff.
    return estimated, None, metadata
