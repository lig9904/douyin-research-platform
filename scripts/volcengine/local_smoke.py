#!/usr/bin/env python3
"""Credential-safe local contract and deliberately gated Ark live smokes.

The default modes do not send credentials or open a provider connection.  The
``ark-live`` mode is intentionally separate and requires both an explicit CLI
flag and an environment confirmation before it sends one paid request.
"""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal

from douyin_research.l3.execution import L3ProviderRequest
from douyin_research.providers.volcengine_ark_l3 import (
    RECOMMENDED_ARK_MODEL_FAMILY,
    VerifiedLiveVolcengineArkL3Provider,
    VolcengineArkL3Provider,
    ark_request_body,
)
from douyin_research.providers.volcengine_asr import (
    VolcengineASRReadinessFacts,
    assess_volcengine_asr_readiness,
)


def _request(
    *,
    model_id: str = RECOMMENDED_ARK_MODEL_FAMILY,
    model_revision: str = "local-contract-revision",
) -> L3ProviderRequest:
    return L3ProviderRequest(
        task_key="local-contract-smoke", model_id=model_id,
        model_revision=model_revision, prompt_version="l3-prompt-v1",
        schema_version="l3-schema-v1", input_fingerprint="local-fingerprint",
        evidence_bundle={"modalities": ["metadata"], "privacy_review": {"reviewed": True, "version": "local-review-v1"}},
    )


def asr_readiness() -> dict[str, object]:
    report = assess_volcengine_asr_readiness(VolcengineASRReadinessFacts(
        secret_configured=bool(os.environ.get("VOLCENGINE_ASR_API_KEY")),
        model_enabled=False, media_delivery_verified=False,
        price_catalog_version=None, cost_reconciliation_version=None,
        polling_policy_version=None, polling_billed=None,
    ))
    return {"mode": "asr-readiness", "production_ready": report.production_ready,
            "review_complete": report.review_complete, "blockers": list(report.blockers),
            "secret_configured": report.facts.secret_configured}


def ark_contract() -> dict[str, object]:
    provider = VolcengineArkL3Provider(
        endpoint_id="local-contract-endpoint", model_revision="local-contract-revision",
        cost_currency="CNY", pricing_version="unconfigured-local-contract",
        input_cost_per_million_tokens=Decimal("0"), output_cost_per_million_tokens=Decimal("0"),
    )
    body = ark_request_body(provider, _request())
    return {"mode": "ark-contract", "production_ready": provider.contract.production_ready,
            "max_retries": provider.max_retries, "endpoint_in_body": body["model"] == "local-contract-endpoint",
            "structured_output": body["response_format"]["type"] == "json_schema",
            "ark_secret_configured": bool(os.environ.get("VOLCENGINE_ARK_API_KEY"))}


def ark_live() -> dict[str, object]:
    """Send exactly one request after two independent local gates.

    Only aggregate completion evidence is returned.  No prompt, response
    content, endpoint identifier, request id, or secret is printed.
    """
    if os.environ.get("ARK_LIVE_SMOKE") != "YES":
        raise PermissionError("set ARK_LIVE_SMOKE=YES before a paid Ark smoke")
    required = (
        "VOLCENGINE_ARK_API_KEY", "VOLCENGINE_ARK_ENDPOINT_ID",
        "VOLCENGINE_ARK_MODEL_REVISION", "VOLCENGINE_ARK_EXPECTED_RESPONSE_MODEL",
        "VOLCENGINE_ARK_PRICING_VERSION", "VOLCENGINE_ARK_INPUT_COST_PER_MILLION",
        "VOLCENGINE_ARK_OUTPUT_COST_PER_MILLION",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        # Names are safe; values are never returned.
        raise ValueError("Ark live smoke missing required configuration: " + ", ".join(missing))
    p = VerifiedLiveVolcengineArkL3Provider(
        api_key=os.environ["VOLCENGINE_ARK_API_KEY"],
        endpoint_id=os.environ["VOLCENGINE_ARK_ENDPOINT_ID"],
        model_id=os.environ.get("VOLCENGINE_ARK_MODEL_ID", RECOMMENDED_ARK_MODEL_FAMILY),
        model_revision=os.environ["VOLCENGINE_ARK_MODEL_REVISION"],
        expected_response_model=os.environ["VOLCENGINE_ARK_EXPECTED_RESPONSE_MODEL"],
        cost_currency="CNY", pricing_version=os.environ["VOLCENGINE_ARK_PRICING_VERSION"],
        input_cost_per_million_tokens=os.environ["VOLCENGINE_ARK_INPUT_COST_PER_MILLION"],
        output_cost_per_million_tokens=os.environ["VOLCENGINE_ARK_OUTPUT_COST_PER_MILLION"],
    )
    try:
        response = p.generate(_request(
            model_id=os.environ.get("VOLCENGINE_ARK_MODEL_ID", RECOMMENDED_ARK_MODEL_FAMILY),
            model_revision=os.environ["VOLCENGINE_ARK_MODEL_REVISION"],
        ))
    finally:
        p.close()
    return {
        "mode": "ark-live", "external_calls": 1, "result_valid": True,
        "cost_currency": response.cost.currency,
        "estimated_llm_cost": str(response.cost.llm_cost),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("asr-readiness", "ark-contract", "ark-live"))
    parser.add_argument("--live", action="store_true", help="permit the separately gated paid Ark smoke")
    args = parser.parse_args()
    if args.mode == "ark-live" and not args.live:
        parser.error("ark-live requires the explicit --live flag")
    if args.mode == "asr-readiness":
        result = asr_readiness()
    elif args.mode == "ark-contract":
        result = ark_contract()
    else:
        result = ark_live()
    # Do not add arbitrary environment values to this output.
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
