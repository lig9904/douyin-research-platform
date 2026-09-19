"""Secret-free, machine-verifiable contracts for paid provider adapters."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit


EXECUTION_CONTRACT_VERSION = "provider-execution-contract-v1.0.0"
ASR_ASYNC_CAPABILITY = "asr_async"
L3_SYNC_CAPABILITY = "l3_sync"
_ALLOWED_CAPABILITIES = frozenset({ASR_ASYNC_CAPABILITY, L3_SYNC_CAPABILITY})
_ALLOWED_AUTH_SCHEMES = frozenset({"bearer", "header"})
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class VerifiedExecutionContract:
    """Non-secret facts an adapter must prove before a paid call is allowed."""

    provider: str
    capability: str
    contract_version: str
    model_id: str
    model_revision: str
    base_url: str
    auth_scheme: str
    auth_header_name: str
    submit_path: str
    status_path: str | None
    request_schema_version: str
    response_schema_version: str
    cost_currency: str
    polling_billed: bool
    max_retries: int
    timeout_seconds: int
    verified_source_fingerprint: str
    production_ready: bool


def validate_execution_contract(
    contract: VerifiedExecutionContract,
    *,
    expected_provider: str,
    expected_capability: str,
    expected_model_id: str,
    expected_model_revision: str,
    expected_currency: str,
) -> str:
    """Fail closed and return a stable fingerprint of the non-secret contract."""

    if not isinstance(contract, VerifiedExecutionContract):
        raise ValueError("provider must expose a VerifiedExecutionContract")

    for name in (
        "provider",
        "capability",
        "contract_version",
        "model_id",
        "model_revision",
        "base_url",
        "auth_scheme",
        "auth_header_name",
        "submit_path",
        "request_schema_version",
        "response_schema_version",
        "cost_currency",
        "verified_source_fingerprint",
    ):
        value = getattr(contract, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"provider contract field is required: {name}")

    if contract.contract_version != EXECUTION_CONTRACT_VERSION:
        raise ValueError("provider contract version is unsupported")
    if contract.capability not in _ALLOWED_CAPABILITIES:
        raise ValueError("provider contract capability is unsupported")
    if contract.auth_scheme not in _ALLOWED_AUTH_SCHEMES:
        raise ValueError("provider contract auth scheme is unsupported")
    if not _SHA256_RE.fullmatch(contract.verified_source_fingerprint):
        raise ValueError("verified source fingerprint must be lowercase SHA-256")
    if type(contract.polling_billed) is not bool:
        raise ValueError("provider contract polling_billed must be explicit")
    if type(contract.production_ready) is not bool or not contract.production_ready:
        raise ValueError("provider contract is not production ready")
    if type(contract.max_retries) is not int or contract.max_retries != 0:
        raise ValueError("provider contract retries must be disabled")
    if (
        type(contract.timeout_seconds) is not int
        or not 1 <= contract.timeout_seconds <= 300
    ):
        raise ValueError("provider contract timeout must be between 1 and 300 seconds")

    parsed = urlsplit(contract.base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("provider contract base URL must be credential-free HTTPS")

    _validate_relative_path("submit_path", contract.submit_path, required=True)
    _validate_relative_path(
        "status_path",
        contract.status_path,
        required=contract.capability == ASR_ASYNC_CAPABILITY,
    )
    if contract.capability == L3_SYNC_CAPABILITY and contract.status_path is not None:
        raise ValueError("synchronous L3 contract cannot define a status path")
    if contract.capability == ASR_ASYNC_CAPABILITY and contract.polling_billed:
        raise ValueError("billed ASR polling is not supported by the current budget model")

    expected = (
        expected_provider,
        expected_capability,
        expected_model_id,
        expected_model_revision,
        expected_currency,
    )
    actual = (
        contract.provider,
        contract.capability,
        contract.model_id,
        contract.model_revision,
        contract.cost_currency,
    )
    if actual != expected:
        raise ValueError("provider contract does not match execution request")

    payload = json.dumps(
        asdict(contract),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_relative_path(
    name: str,
    value: str | None,
    *,
    required: bool,
) -> None:
    if value is None:
        if required:
            raise ValueError(f"provider contract field is required: {name}")
        return
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError(f"provider contract {name} must be an absolute path")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError(f"provider contract {name} cannot be a URL or contain query data")
