# py: ==3.14.*
#requirements:
#douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@a4c72caa2d68324d802ea8f3bb7fad97d67d0f13

"""Preview-only Windmill entry point for a future paid L3 model adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping
from uuid import UUID

from douyin_research.l3 import (
    L3_CONFIRMATION,
    L3_SCHEMA_VERSION,
    L3ExecutionCoordinator,
    L3ExecutionRequest,
)


ADAPTER_STATUS = "blocked_unverified_contract"


@dataclass(frozen=True, slots=True)
class ManualL3PreviewRequest:
    provider: str
    model_id: str
    model_revision: str
    prompt_version: str
    estimated_llm_cost: float | None
    cost_currency: str
    execute: bool = False
    confirmation: str = ""


def _execute(
    request: ManualL3PreviewRequest,
    *,
    load_evidence: Callable[[], Mapping[str, object]],
    load_adapter: Callable[[], Any],
) -> dict[str, object]:
    """Return the core control-plane preview and fail closed on execution."""

    core = L3ExecutionRequest(
        video_id=UUID(int=0),
        task_key="windmill-l3-preview-only",
        provider=request.provider,
        model_id=request.model_id,
        model_revision=request.model_revision,
        prompt_version=request.prompt_version,
        schema_version=L3_SCHEMA_VERSION,
        input_fingerprint="preview-only",
        estimated_llm_cost=request.estimated_llm_cost,
        cost_currency=request.cost_currency,
        execute=False,
    )
    plan = L3ExecutionCoordinator("preview-only-no-database").run(
        core,
        evidence_factory=load_evidence,
        provider_factory=load_adapter,
    )
    plan.update(
        {
            "schema_version": L3_SCHEMA_VERSION,
            "adapter_status": ADAPTER_STATUS,
            "execution_ready": False,
            "paid_execution_available": False,
        }
    )
    if not request.execute:
        return plan
    if request.confirmation != L3_CONFIRMATION:
        raise PermissionError("exact paid-operation confirmation is required")
    raise RuntimeError(
        "L3 paid execution is disabled until the model contract is verified"
    )


def _blocked_evidence_loader() -> Mapping[str, object]:
    raise RuntimeError("L3 evidence loading is disabled in preview-only mode")


def _blocked_adapter_loader() -> Any:
    raise RuntimeError("L3 adapter is not configured")


def main(
    provider: str,
    model_id: str,
    model_revision: str,
    prompt_version: str,
    estimated_llm_cost: float | None = None,
    cost_currency: str = "CNY",
    execute: bool = False,
    confirmation: str = "",
):
    request = ManualL3PreviewRequest(
        provider=provider,
        model_id=model_id,
        model_revision=model_revision,
        prompt_version=prompt_version,
        estimated_llm_cost=estimated_llm_cost,
        cost_currency=cost_currency,
        execute=execute,
        confirmation=confirmation,
    )
    return _execute(
        request,
        load_evidence=_blocked_evidence_loader,
        load_adapter=_blocked_adapter_loader,
    )
