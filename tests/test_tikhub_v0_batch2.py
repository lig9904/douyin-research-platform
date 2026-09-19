from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_batch2():
    path = Path("scripts/tikhub/v0_batch2.py")
    spec = importlib.util.spec_from_file_location("tikhub_v0_batch2", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_call_budget_stops_before_eleventh_request(tmp_path, monkeypatch) -> None:
    probe = _load_batch2()
    monkeypatch.setattr(probe, "OUT_DIR", tmp_path)
    budget = probe.CallBudget(max_calls=10)

    def ok():
        return {"code": 200, "request_id": "hidden", "data": {}}

    for index in range(10):
        budget.call(f"call_{index}", ok)

    with pytest.raises(probe.ProbeFailure, match="budget exhausted"):
        budget.call("call_11", ok)

    assert budget.calls == 10


def test_choose_ordinary_user_prefers_target_follower_band() -> None:
    probe = _load_batch2()
    selected = probe.choose_ordinary_user(
        [
            {"sec_uid": "large", "follower_count": 2_000_000},
            {"sec_uid": "ordinary", "follower_count": 28_000},
            {"sec_uid": "tiny", "follower_count": 300},
        ]
    )

    assert selected["sec_uid"] == "ordinary"
    assert probe.follower_bucket(selected["follower_count"]) == "10k_100k"


def test_metric_coverage_treats_zero_as_present() -> None:
    probe = _load_batch2()
    coverage = probe.metric_coverage(
        [
            {
                "statistics": {
                    "play_count": 0,
                    "digg_count": 2,
                    "comment_count": None,
                    "share_count": 0,
                }
            }
        ]
    )

    assert coverage["play_count"] == 1
    assert coverage["digg_count"] == 1
    assert coverage["comment_count"] == 0
    assert coverage["share_count"] == 1
    assert coverage["collect_count"] == 0


def test_safe_shape_does_not_return_request_or_content_ids() -> None:
    probe = _load_batch2()
    payload = {
        "code": 200,
        "request_id": "request-id-must-not-appear",
        "router": "/safe/router",
        "data": [{"aweme_id": "video-id-must-not-appear"}],
    }

    summary = probe.safe_shape("detail", payload)
    rendered = str(summary)

    assert summary["request_id_present"] is True
    assert "request_id" not in summary
    assert "request-id-must-not-appear" not in rendered
    assert "video-id-must-not-appear" not in rendered


def test_usage_delta_summary_discloses_no_account_totals() -> None:
    probe = _load_batch2()
    before = {"data": {"request_count": 100, "balance": 99.25}}
    after = {"data": {"request_count": 108, "balance": 99.20}}

    summary = probe.usage_delta_summary(before, after)
    rendered = str(summary)

    assert summary["usage_change_detected"] is True
    assert summary["changed_numeric_field_count"] == 2
    assert "100" not in rendered
    assert "108" not in rendered
    assert "99.25" not in rendered
    assert "99.2" not in rendered


def test_user_candidates_parse_stringified_nested_json() -> None:
    probe = _load_batch2()
    payload = {
        "data": {
            "cards": [
                {
                    "payload": (
                        '{"user_info":{"sec_uid":"sec-hidden",'
                        '"follower_count":28000}}'
                    )
                }
            ]
        }
    }

    candidates = probe.user_candidates(payload)

    assert candidates == [{"sec_uid": "sec-hidden", "follower_count": 28000}]


def test_resume_budget_plus_prior_calls_never_exceeds_ten() -> None:
    probe = _load_batch2()
    resume = probe.CallBudget(max_calls=probe.RESUME_MAX_CALLS)

    assert resume.max_calls == 7
    assert 3 + resume.max_calls == 10
