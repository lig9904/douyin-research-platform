from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
ASR_SCRIPT = (
    ROOT / "windmill/f/content_research/analysis/manual_asr_preview.py"
)
L3_SCRIPT = (
    ROOT / "windmill/f/content_research/analysis/manual_l3_preview.py"
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


asr = _load(ASR_SCRIPT, "manual_asr_preview")
l3 = _load(L3_SCRIPT, "manual_l3_preview")


def test_asr_preview_never_loads_adapter_and_preserves_unknown_costs() -> None:
    calls = []
    request = asr.ManualASRPreviewRequest(
        provider="synthetic-asr",
        model_id="synthetic-model",
        model_revision="revision-1",
        engine_version="engine-1",
        estimated_api_cost=None,
        estimated_asr_cost=None,
        cost_currency="CNY",
        max_polls=2,
    )

    result = asr._execute(
        request,
        load_adapter=lambda: calls.append("adapter"),
    )

    assert calls == []
    assert result == {
        "status": "preview",
        "execute": False,
        "maximum_external_calls": 3,
        "estimated_total_cost": None,
        "cost_currency": "CNY",
        "execution_ready": False,
        "sdk_retries": 0,
        "llm_calls": 0,
        "adapter_status": "blocked_unverified_contract",
        "paid_execution_available": False,
    }


def test_l3_preview_never_loads_evidence_or_adapter() -> None:
    calls = []
    request = l3.ManualL3PreviewRequest(
        provider="synthetic-llm",
        model_id="synthetic-model",
        model_revision="revision-1",
        prompt_version="prompt-v1",
        estimated_llm_cost=0.4,
        cost_currency="CNY",
    )

    result = l3._execute(
        request,
        load_evidence=lambda: calls.append("evidence") or {},
        load_adapter=lambda: calls.append("adapter"),
    )

    assert calls == []
    assert result == {
        "status": "preview",
        "execute": False,
        "maximum_external_calls": 1,
        "maximum_llm_calls": 1,
        "estimated_total_cost": 0.4,
        "price_known": True,
        "cost_currency": "CNY",
        "execution_ready": False,
        "sdk_retries": 0,
        "llm_calls": 0,
        "schema_version": "l3-research-v1.0.0",
        "adapter_status": "blocked_unverified_contract",
        "paid_execution_available": False,
    }


@pytest.mark.parametrize(
    ("module", "preview_request", "kwargs", "confirmation"),
    [
        (
            asr,
            asr.ManualASRPreviewRequest(
                provider="synthetic-asr",
                model_id="synthetic-model",
                model_revision="revision-1",
                engine_version="engine-1",
                estimated_api_cost=0.1,
                estimated_asr_cost=0.2,
                cost_currency="CNY",
                execute=True,
                confirmation="wrong",
            ),
            {"load_adapter": lambda: None},
            asr.ASR_CONFIRMATION,
        ),
        (
            l3,
            l3.ManualL3PreviewRequest(
                provider="synthetic-llm",
                model_id="synthetic-model",
                model_revision="revision-1",
                prompt_version="prompt-v1",
                estimated_llm_cost=0.4,
                cost_currency="CNY",
                execute=True,
                confirmation="wrong",
            ),
            {
                "load_evidence": lambda: {},
                "load_adapter": lambda: None,
            },
            l3.L3_CONFIRMATION,
        ),
    ],
)
def test_wrong_confirmation_is_rejected_without_loading_anything(
    module, preview_request, kwargs, confirmation
) -> None:
    calls = []
    safe_kwargs = {
        name: (lambda label=name: calls.append(label))
        for name in kwargs
    }
    with pytest.raises(PermissionError, match="exact paid-operation confirmation"):
        module._execute(preview_request, **safe_kwargs)
    assert calls == []

    values = {
        field: getattr(preview_request, field)
        for field in preview_request.__dataclass_fields__
    }
    values["confirmation"] = confirmation
    confirmed = type(preview_request)(**values)
    with pytest.raises(RuntimeError, match="disabled until"):
        module._execute(confirmed, **safe_kwargs)
    assert calls == []


def test_preview_flows_are_manual_non_sensitive_and_have_no_secret_lookup() -> None:
    asr_flow = (
        ROOT
        / "windmill/f/content_research/flows/manual_asr_preview.flow/flow.yaml"
    ).read_text()
    l3_flow = (
        ROOT
        / "windmill/f/content_research/flows/manual_l3_preview.flow/flow.yaml"
    ).read_text()
    asr_metadata = ASR_SCRIPT.with_suffix(".script.yaml").read_text()
    l3_metadata = L3_SCRIPT.with_suffix(".script.yaml").read_text()

    for flow in (asr_flow, l3_flow):
        lowered = flow.lower()
        assert "\nschedule:" not in lowered
        assert not lowered.startswith("schedule:")
        assert "\n  concurrent_limit: 1\n" in flow
        assert "default: false" in flow
        assert "$res:" not in flow
        assert "$var:" not in flow
        assert "video_id" not in flow
        assert "media_ref" not in flow
        assert "evidence_bundle" not in flow

    for source in (ASR_SCRIPT.read_text(), L3_SCRIPT.read_text()):
        assert "wmill.get_variable" not in source
        assert "import psycopg" not in source

    assert "maximum: 3" in asr_flow
    assert "concurrent_limit: 1" in asr_metadata
    assert "concurrent_limit: 1" in l3_metadata
