#!/usr/bin/env python3
"""Credential-safe local contract smoke for Volcengine adapters.

This is deliberately not a paid smoke.  It verifies that environment wiring,
request envelopes and the permanent fail-closed boundary are intact without
sending credentials or opening a provider connection.
"""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal

from douyin_research.l3.execution import L3ProviderRequest
from douyin_research.providers.volcengine_ark_l3 import (
    RECOMMENDED_ARK_MODEL_FAMILY,
    VolcengineArkL3Provider,
    ark_request_body,
)
from douyin_research.providers.volcengine_asr import (
    VolcengineASRReadinessFacts,
    assess_volcengine_asr_readiness,
)


def _request() -> L3ProviderRequest:
    return L3ProviderRequest(
        task_key="local-contract-smoke", model_id=RECOMMENDED_ARK_MODEL_FAMILY,
        model_revision="local-contract-revision", prompt_version="l3-prompt-v1",
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("asr-readiness", "ark-contract"))
    args = parser.parse_args()
    result = asr_readiness() if args.mode == "asr-readiness" else ark_contract()
    # Do not add arbitrary environment values to this output.
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
