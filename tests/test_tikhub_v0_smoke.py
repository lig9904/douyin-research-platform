from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_smoke():
    path = Path("scripts/tikhub/v0_smoke.py")
    spec = importlib.util.spec_from_file_location("tikhub_v0_smoke", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_video_demo_summary_keeps_only_shape_evidence(tmp_path) -> None:
    smoke = _load_smoke()
    payload = {
        "code": 200,
        "request_id": "request-secret-not-returned",
        "router": "/demo/video",
        "data": {
            "aweme_detail": {
                "aweme_id": "123",
                "duration": 1000,
                "author": {"sec_uid": "sec-1"},
                "statistics": {"play_count": 0},
                "video": {},
            }
        },
    }

    summary = smoke.summarize_demo(
        "demo_douyin_app_video",
        payload,
        http_status=200,
        raw_path=tmp_path / "raw.json",
    )

    assert summary["request_id_present"] is True
    assert "request_id" not in summary
    assert summary["play_count_present"] is True
    assert summary["author_sec_uid_present"] is True


def test_search_demo_summary_verifies_pagination_and_stable_ids(tmp_path) -> None:
    smoke = _load_smoke()
    payload = {
        "code": 200,
        "request_id": "r1",
        "data": {
            "cursor": 20,
            "has_more": 1,
            "backtrace": "opaque",
            "data": [
                {"aweme_info": {"aweme_id": "v1"}},
                {"nested": [{"aweme_id": "v1"}, {"aweme_id": "v2"}]},
            ],
        },
    }

    summary = smoke.summarize_demo(
        "demo_douyin_search",
        payload,
        http_status=200,
        raw_path=tmp_path / "raw.json",
    )

    assert summary["card_count"] == 2
    assert summary["unique_aweme_id_count"] == 2
    assert summary["cursor"] == 20
    assert summary["backtrace_present"] is True


def test_cache_demo_allows_empty_cache(tmp_path) -> None:
    smoke = _load_smoke()
    payload = {
        "code": 200,
        "request_id": "r1",
        "data": {"total_cached_items": 0, "cache_items": []},
    }

    summary = smoke.summarize_demo(
        "demo_cache_status",
        payload,
        http_status=200,
        raw_path=tmp_path / "raw.json",
    )

    assert summary["total_cached_items"] == 0
    assert summary["cache_item_count"] == 0


def test_paid_summary_redacts_request_id_and_payload(tmp_path, monkeypatch) -> None:
    smoke = _load_smoke()
    monkeypatch.setattr(smoke, "OUT_DIR", tmp_path)
    payload = {
        "code": 200,
        "request_id": "paid-request-id-must-not-be-logged",
        "router": "/api/v1/paid",
        "data": [{"aweme_id": "private-sample-id"}],
    }

    summary = smoke.save_and_summarize("paid_probe", payload, "2.1.1")

    assert summary["request_id_present"] is True
    assert "request_id" not in summary
    assert "private-sample-id" not in str(summary)
    assert summary["first_list_count"] == 1
