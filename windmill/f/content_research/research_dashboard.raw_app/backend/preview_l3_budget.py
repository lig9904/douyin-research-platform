# py: ==3.14.*
#requirements:
#douyin-research-platform@git+https://github.com/lig9904/douyin-research-platform@1558677c40cc22239e660e269738619dfd05388d
#psycopg[binary]==3.3.6

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any, TypedDict

from psycopg.conninfo import make_conninfo

from douyin_research.l3 import (
    L3_BUDGET_KEY,
    L3BudgetPreviewRequest,
    L3ReviewService,
    authorize_reviewer,
)


class postgresql(TypedDict):
    host: str
    port: int
    user: str
    password: str
    dbname: str
    sslmode: str


_SAFE_CONFIG_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")


def _dsn(db: postgresql) -> str:
    return make_conninfo(
        host=db["host"],
        port=int(db.get("port", 5432)),
        user=db["user"],
        password=db["password"],
        dbname=db["dbname"],
        sslmode=db.get("sslmode", "prefer"),
    )


def _preview_config(raw: str | Mapping[str, Any]) -> dict[str, object]:
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("L3 budget preview configuration is invalid") from None
    else:
        value = raw
    if not isinstance(value, Mapping):
        raise ValueError("L3 budget preview configuration is invalid")
    required_text = (
        "provider",
        "model_id",
        "model_revision",
        "prompt_version",
        "pricing_version",
    )
    parsed: dict[str, object] = {}
    for field in required_text:
        item = value.get(field)
        if not isinstance(item, str) or not _SAFE_CONFIG_VALUE.fullmatch(item):
            raise ValueError("L3 budget preview configuration is invalid")
        parsed[field] = item
    currency = value.get("cost_currency")
    if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("L3 budget preview configuration is invalid")
    parsed["cost_currency"] = currency
    estimate = value.get("estimated_llm_cost")
    if estimate is None:
        parsed["estimated_llm_cost"] = None
    else:
        try:
            amount = Decimal(str(estimate))
        except (InvalidOperation, ValueError):
            raise ValueError("L3 budget preview configuration is invalid") from None
        if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -6:
            raise ValueError("L3 budget preview configuration is invalid")
        parsed["estimated_llm_cost"] = amount
    return parsed


def _remaining(limit: float | None, used: float | None) -> float | None:
    if limit is None or used is None:
        return None
    return float(Decimal(str(limit)) - Decimal(str(used)))


def _budget_configured(status: str) -> bool | None:
    if status == "budget_missing":
        return False
    if status in {
        "budget_currency_mismatch",
        "estimate_required",
        "budget_requests_exceeded",
        "budget_cost_exceeded",
        "budget_capacity_preview_only",
    }:
        return True
    return None


def main(
    db: postgresql,
    reviewer_allowlist: str,
    budget_preview_config: str,
    video_id: str,
    privacy_review_version: str,
    evidence_fingerprint: str,
):
    authorize_reviewer(os.environ.get("WM_END_USER_EMAIL"), reviewer_allowlist)
    try:
        config = _preview_config(budget_preview_config)
    except ValueError:
        raise RuntimeError("L3_BUDGET_PREVIEW_CONFIG_INVALID") from None
    try:
        preview = L3ReviewService(_dsn(db)).preview_budget(
            L3BudgetPreviewRequest(
                video_id=video_id,
                privacy_review_version=privacy_review_version,
                evidence_fingerprint=evidence_fingerprint,
                provider=str(config["provider"]),
                cost_currency=str(config["cost_currency"]),
                estimated_llm_cost=config["estimated_llm_cost"],
            )
        ).as_dict()
    except Exception:
        raise RuntimeError("L3_BUDGET_PREVIEW_FAILED") from None
    configured = _budget_configured(str(preview["status"]))
    remaining_cost = _remaining(preview["max_cost"], preview["spent_cost"])
    remaining_requests = (
        None
        if preview["max_requests"] is None or preview["used_requests"] is None
        else int(preview["max_requests"]) - int(preview["used_requests"])
    )
    remaining_after_estimate = _remaining(preview["max_cost"], preview["next_cost"])
    return {
        "status": preview["status"],
        "execute": False,
        "paid_execution_available": False,
        "maximum_external_calls": 0,
        "maximum_llm_calls": 0,
        "db_writes": 0,
        "raw_evidence_included": False,
        "planned_model": {
            "provider": config["provider"],
            "model_id": config["model_id"],
            "model_revision": config["model_revision"],
            "prompt_version": config["prompt_version"],
            "pricing_version": config["pricing_version"],
        },
        "budget": {
            "budget_key": L3_BUDGET_KEY,
            "date": preview["budget_date"],
            "configured": configured,
            "max_cost": preview["max_cost"],
            "spent_cost": preview["spent_cost"],
            "max_requests": preview["max_requests"],
            "used_requests": preview["used_requests"],
            "remaining_cost": remaining_cost,
            "remaining_requests": remaining_requests,
            "currency": preview["cost_currency"],
        },
        "estimated_llm_cost": preview["estimated_llm_cost"],
        "remaining_after_estimate": remaining_after_estimate,
        "cost_currency": preview["cost_currency"],
    }
