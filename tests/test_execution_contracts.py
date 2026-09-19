from __future__ import annotations

from dataclasses import replace

import pytest

from douyin_research.providers import (
    ASR_ASYNC_CAPABILITY,
    EXECUTION_CONTRACT_VERSION,
    L3_SYNC_CAPABILITY,
    VerifiedExecutionContract,
    validate_execution_contract,
)


def _contract(**overrides) -> VerifiedExecutionContract:
    values = {
        "provider": "synthetic-provider",
        "capability": ASR_ASYNC_CAPABILITY,
        "contract_version": EXECUTION_CONTRACT_VERSION,
        "model_id": "synthetic-model",
        "model_revision": "revision-1",
        "base_url": "https://relay.example.test/v1",
        "auth_scheme": "bearer",
        "auth_header_name": "Authorization",
        "submit_path": "/jobs",
        "status_path": "/jobs/{task_ref}",
        "request_schema_version": "request-v1",
        "response_schema_version": "response-v1",
        "cost_currency": "CNY",
        "polling_billed": False,
        "max_retries": 0,
        "timeout_seconds": 30,
        "verified_source_fingerprint": "a" * 64,
        "production_ready": True,
    }
    values.update(overrides)
    return VerifiedExecutionContract(**values)


def _validate(contract: VerifiedExecutionContract) -> str:
    return validate_execution_contract(
        contract,
        expected_provider="synthetic-provider",
        expected_capability=contract.capability,
        expected_model_id="synthetic-model",
        expected_model_revision="revision-1",
        expected_currency="CNY",
    )


def test_contract_fingerprint_is_stable_and_sensitive() -> None:
    contract = _contract()
    assert _validate(contract) == _validate(contract)
    assert _validate(replace(contract, timeout_seconds=31)) != _validate(contract)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"base_url": "https://token@example.test"}, "credential-free HTTPS"),
        ({"base_url": "https://example.test?v=1"}, "credential-free HTTPS"),
        ({"submit_path": "jobs"}, "absolute path"),
        ({"status_path": "https://example.test/jobs/1"}, "absolute path"),
        ({"verified_source_fingerprint": "not-a-sha"}, "lowercase SHA-256"),
        ({"production_ready": False}, "not production ready"),
        ({"max_retries": 1}, "retries must be disabled"),
        ({"polling_billed": True}, "billed ASR polling"),
    ],
)
def test_contract_rejects_unsafe_or_unverified_values(changes, message) -> None:
    with pytest.raises(ValueError, match=message):
        _validate(_contract(**changes))


def test_contract_must_match_execution_request() -> None:
    contract = _contract()
    with pytest.raises(ValueError, match="does not match execution request"):
        validate_execution_contract(
            contract,
            expected_provider="different-provider",
            expected_capability=ASR_ASYNC_CAPABILITY,
            expected_model_id="synthetic-model",
            expected_model_revision="revision-1",
            expected_currency="CNY",
        )


def test_synchronous_l3_contract_has_no_polling_endpoint() -> None:
    contract = _contract(
        capability=L3_SYNC_CAPABILITY,
        status_path=None,
        polling_billed=False,
    )
    assert len(_validate(contract)) == 64

    with pytest.raises(ValueError, match="cannot define a status path"):
        _validate(replace(contract, status_path="/jobs/{task_ref}"))
