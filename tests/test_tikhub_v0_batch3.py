from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_batch3():
    module_dir = Path("scripts/tikhub").resolve()
    sys.path.insert(0, str(module_dir))
    path = module_dir / "v0_batch3.py"
    spec = importlib.util.spec_from_file_location("tikhub_v0_batch3", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_batch3_budget_stops_before_eleventh_request(tmp_path, monkeypatch) -> None:
    probe = _load_batch3()
    monkeypatch.setattr(probe, "OUT_DIR", tmp_path)
    budget = probe.Batch3Budget(max_calls=10)

    def ok():
        return {"code": 200, "request_id": "must-stay-private", "data": {}}

    for index in range(10):
        budget.call(f"call_{index}", ok)

    with pytest.raises(probe.ProbeFailure, match="budget exhausted"):
        budget.call("call_11", ok)

    assert budget.calls == 10


def test_page_relation_counts_overlap_without_disclosing_ids() -> None:
    probe = _load_batch3()
    summary = probe.page_relation(
        {"video-private-a", "video-private-b"},
        {"video-private-b", "video-private-c"},
    )
    rendered = str(summary)

    assert summary == {
        "page1_unique_count": 2,
        "page2_unique_count": 2,
        "cross_page_overlap_count": 1,
        "combined_unique_count": 3,
    }
    assert "video-private" not in rendered


def test_choose_reply_candidate_prefers_comment_with_replies() -> None:
    probe = _load_batch3()
    selected = probe.choose_reply_candidate(
        [
            {"comment_id": "private-first", "reply_count": 0},
            {"comment_id": "private-best", "reply_count": 4},
            {"comment_id": "private-mid", "reply_count": 2},
        ]
    )

    assert selected["comment_id"] == "private-best"


def test_comment_candidates_accepts_common_reply_count_fields() -> None:
    probe = _load_batch3()
    payload = {
        "data": {
            "comments": [
                {"cid": "private-a", "reply_comment_total": 3},
                {"comment_id": "private-b", "reply_count": 1},
            ]
        }
    }

    candidates = probe.comment_candidates(payload)

    assert len(candidates) == 2
    assert {item["reply_count"] for item in candidates} == {1, 3}


def test_stable_ids_are_used_only_for_internal_set_operations() -> None:
    probe = _load_batch3()
    payload = {
        "data": {
            "comments": [
                {"cid": "comment-private-a"},
                {"comment_id": "comment-private-b"},
            ]
        }
    }

    ids = probe.stable_ids(payload, ("cid", "comment_id"))
    summary = probe.page_relation(ids, {"comment-private-b"})

    assert len(ids) == 2
    assert "comment-private" not in str(summary)


def test_cursor_advanced_compares_without_exposing_cursor() -> None:
    probe = _load_batch3()

    assert probe.cursor_advanced("private-cursor-1", "private-cursor-2") is True
    assert probe.cursor_advanced("private-cursor-1", "private-cursor-1") is False
    assert probe.cursor_advanced(None, "private-cursor-2") is False


def test_require_next_page_fails_before_paid_followup() -> None:
    probe = _load_batch3()

    with pytest.raises(probe.ProbeFailure, match="missing cursor"):
        probe.require_next_page("page1", None, 1)

    with pytest.raises(probe.ProbeFailure, match="no second page"):
        probe.require_next_page("page1", 20, 0)
