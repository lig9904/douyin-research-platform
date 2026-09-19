#requirements:
#douyin-research-platform @ git+https://github.com/lig9904/douyin-research-platform@a4c72caa2d68324d802ea8f3bb7fad97d67d0f13

"""Preview-only Windmill entry point for a future paid ASR adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID

from douyin_research.l2 import (
    ASR_CONFIRMATION,
    ASRExecutionCoordinator,
    ASRExecutionRequest,
)


ADAPTER_STATUS = "blocked_unverified_contract"


@dataclass(frozen=True, slots=True)
class ManualASRPreviewRequest:
    provider: str
    model_id: str
    model_revision: str
    engine_version: str
    estimated_api_cost: float | None
    estimated_asr_cost: float | None
    cost_currency: str
    max_polls: int = 0
    execute: bool = False
    confirmation: str = ""


def _execute(
    request: ManualASRPreviewRequest,
    *,
    load_adapter: Callable[[], Any],
) -> dict[str, object]:
    """Return the core control-plane preview and fail closed on execution."""

    core = ASRExecutionRequest(
        video_id=UUID(int=0),
        task_key="windmill-asr-preview-only",
        provider=request.provider,
        model_id=request.model_id,
        model_revision=request.model_revision,
        engine_version=request.engine_version,
        source_fingerprint="preview-only",
        media_ref="preview-only-not-loaded",
        estimated_api_cost=request.estimated_api_cost,
        estimated_asr_cost=request.estimated_asr_cost,
        cost_currency=request.cost_currency,
        execute=False,
        max_polls=request.max_polls,
    )
    plan = ASRExecutionCoordinator("preview-only-no-database").run(
        core,
        provider_factory=load_adapter,
    )
    plan.update(
        {
            "adapter_status": ADAPTER_STATUS,
            "paid_execution_available": False,
        }
    )
    if not request.execute:
        return plan
    if request.confirmation != ASR_CONFIRMATION:
        raise PermissionError("exact paid-operation confirmation is required")
    raise RuntimeError(
        "ASR paid execution is disabled until the relay contract is verified"
    )


def _blocked_adapter_loader() -> Any:
    raise RuntimeError("ASR adapter is not configured")


def main(
    provider: str,
    model_id: str,
    model_revision: str,
    engine_version: str,
    estimated_api_cost: float | None = None,
    estimated_asr_cost: float | None = None,
    cost_currency: str = "CNY",
    max_polls: int = 0,
    execute: bool = False,
    confirmation: str = "",
):
    request = ManualASRPreviewRequest(
        provider=provider,
        model_id=model_id,
        model_revision=model_revision,
        engine_version=engine_version,
        estimated_api_cost=estimated_api_cost,
        estimated_asr_cost=estimated_asr_cost,
        cost_currency=cost_currency,
        max_polls=max_polls,
        execute=execute,
        confirmation=confirmation,
    )
    return _execute(request, load_adapter=_blocked_adapter_loader)
