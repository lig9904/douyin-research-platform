from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_batch4():
    module_dir = Path("scripts/tikhub").resolve()
    sys.path.insert(0, str(module_dir))
    path = module_dir / "v0_batch4.py"
    spec = importlib.util.spec_from_file_location("tikhub_v0_batch4", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _envelope(data: object) -> dict[str, object]:
    return {"code": 200, "request_id": "must-not-print", "data": data}


class _FakeTikHub:
    instances: list["_FakeTikHub"] = []
    page1_has_more = 1

    def __init__(self, *, api_key: str, max_retries: int):
        self.api_key = api_key
        self.max_retries = max_retries
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.tikhub_user = self.User(self)
        self.douyin_billboard = self.Billboard(self)
        self.douyin_search = self.Search(self)
        self.douyin_app_v3 = self.App(self)
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    class User:
        def __init__(self, parent):
            self.parent = parent

        def get_user_daily_usage(self):
            self.parent.calls.append(("usage", {}))
            return _envelope({"calls": len(self.parent.calls)})

        def calculate_price(self, *, endpoint: str, request_per_day: int):
            self.parent.calls.append(("price", {"endpoint": endpoint, "request_per_day": request_per_day}))
            return _envelope({"price": 0.001})

    class Billboard:
        def __init__(self, parent):
            self.parent = parent

        def fetch_hot_total_low_fan_list(self, *, page: int, page_size: int, date_window: int, tags: list[object]):
            self.parent.calls.append(("billboard", {"page": page, "page_size": page_size, "date_window": date_window, "tags": tags}))
            return _envelope({"cursor": page * 10, "has_more": self.parent.page1_has_more if page == 1 else 0, "items": [_video(f"billboard-{page}")]})

    class Search:
        def __init__(self, parent):
            self.parent = parent

        def fetch_video_search_v2(self, *, keyword: str, cursor: object, sort_type: str, publish_time: str, filter_duration: str, content_type: str, search_id: str, backtrace: str):
            self.parent.calls.append(("search", {"keyword": keyword, "cursor": cursor, "search_id": search_id, "backtrace": backtrace}))
            next_cursor = 20 if cursor == 0 else 40
            return _envelope({"cursor": next_cursor, "has_more": 1 if cursor == 0 else 0, "search_id": "private-search-token", "backtrace": "private-backtrace-token", "items": [_video(f"search-{next_cursor}")]})

    class App:
        def __init__(self, parent):
            self.parent = parent

        def fetch_multi_video_v2(self, *, body: list[str]):
            self.parent.calls.append(("detail", {"body": body}))
            return _envelope({"items": [_video(identifier) for identifier in body]})


def _video(identifier: str) -> dict[str, object]:
    return {
        "aweme_id": identifier,
        "statistics": {
            "play_count": 1,
            "digg_count": 1,
            "comment_count": 1,
            "share_count": 1,
            "collect_count": 1,
        },
    }


def _install_fake_sdk(monkeypatch) -> None:
    _FakeTikHub.instances.clear()
    _FakeTikHub.page1_has_more = 1
    monkeypatch.setitem(sys.modules, "tikhub", types.SimpleNamespace(TikHub=_FakeTikHub, __version__="2.1.1"))


def test_batch4_fixed_ten_call_plan_has_zero_retries_and_redacted_stdout(tmp_path, monkeypatch, capsys) -> None:
    probe = _load_batch4()
    _install_fake_sdk(monkeypatch)
    monkeypatch.setattr(probe, "OUT_DIR", tmp_path)
    monkeypatch.setenv(probe.GATE_ENV, probe.GATE_VALUE)
    monkeypatch.setenv(probe.API_KEY_ENV, "private-api-key")

    assert probe.run() == 0

    client = _FakeTikHub.instances[-1]
    assert len(client.calls) == 10
    assert client.max_retries == 0
    assert [name for name, _ in client.calls] == [
        "usage", "price", "price", "price", "billboard", "billboard", "search", "search", "detail", "usage",
    ]
    assert len(client.calls[8][1]["body"]) == 4
    assert client.calls[7][1] == {
        "keyword": "秦皇岛旅游",
        "cursor": 20,
        "search_id": "private-search-token",
        "backtrace": "private-backtrace-token",
    }
    rendered = capsys.readouterr().out
    assert "private-api-key" not in rendered
    assert "billboard-1" not in rendered
    assert "must-not-print" not in rendered
    assert "private-search-token" not in rendered
    assert "private-backtrace-token" not in rendered


def test_batch4_stops_before_second_billboard_page_only_when_has_more_is_false(tmp_path, monkeypatch) -> None:
    probe = _load_batch4()
    _install_fake_sdk(monkeypatch)
    _FakeTikHub.page1_has_more = 0
    monkeypatch.setattr(probe, "OUT_DIR", tmp_path)
    monkeypatch.setenv(probe.GATE_ENV, probe.GATE_VALUE)
    monkeypatch.setenv(probe.API_KEY_ENV, "private-api-key")

    with pytest.raises(probe.ProbeFailure, match="no second page"):
        probe.run()

    assert [name for name, _ in _FakeTikHub.instances[-1].calls] == [
        "usage", "price", "price", "price", "billboard"
    ]


def test_raw_evidence_files_are_private(tmp_path, monkeypatch) -> None:
    probe = _load_batch4()
    monkeypatch.setattr(probe, "OUT_DIR", tmp_path)

    path = probe.save_raw("private", {"code": 200, "data": {}})

    assert path.stat().st_mode & 0o777 == 0o600
    assert tmp_path.stat().st_mode & 0o777 == 0o700


def test_page_relation_and_selected_detail_ids_do_not_need_public_identifiers() -> None:
    probe = _load_batch4()
    first = {"private-a", "private-b"}
    second = {"private-b", "private-c"}

    assert probe.page_relation(first, second) == {
        "page1_unique_video_count": 2,
        "page2_unique_video_count": 2,
        "cross_page_overlap_count": 1,
        "combined_unique_video_count": 3,
    }
    assert "private-" not in str(probe.page_relation(first, second))
    selected = probe.select_detail_sources(
        ({"code": 200, "data": {"items": [_video("private-a"), _video("private-b")] }},),
        ({"code": 200, "data": {"items": [_video("private-b"), _video("private-c")] }},),
    )
    assert selected == {"billboard": ["private-a", "private-b"], "search": ["private-c"]}
    summary = probe.detail_source_summary(selected, {"code": 200, "data": {"items": [_video("private-a"), _video("private-c")]}})
    assert summary == {
        "billboard_requested_count": 2,
        "billboard_returned_count": 1,
        "billboard_not_returned_count": 1,
        "search_requested_count": 1,
        "search_returned_count": 1,
        "search_not_returned_count": 0,
    }


def test_environment_and_sdk_version_are_exact(monkeypatch) -> None:
    probe = _load_batch4()
    _install_fake_sdk(monkeypatch)

    monkeypatch.delenv(probe.GATE_ENV, raising=False)
    monkeypatch.delenv(probe.API_KEY_ENV, raising=False)

    with pytest.raises(probe.ProbeFailure, match="must equal"):
        probe.require_environment()
    with pytest.raises(probe.ProbeFailure, match="must equal"):
        probe.run()
    assert _FakeTikHub.instances == []
    with pytest.raises(probe.ProbeFailure, match="must equal"):
        probe.require_sdk_version("2.1.2")


def test_budget_rejects_eleventh_request_before_calling_provider(tmp_path, monkeypatch) -> None:
    probe = _load_batch4()
    monkeypatch.setattr(probe, "OUT_DIR", tmp_path)
    budget = probe.Batch4Budget(max_calls=10)
    invoked = 0

    def ok():
        nonlocal invoked
        invoked += 1
        return _envelope({})

    for index in range(10):
        budget.call(f"call_{index}", ok)
    with pytest.raises(probe.ProbeFailure, match="budget exhausted"):
        budget.call("call_11", ok)
    assert invoked == 10


def test_search_second_page_requires_all_opaque_tokens() -> None:
    probe = _load_batch4()

    with pytest.raises(probe.ProbeFailure, match="missing search_id"):
        probe.require_search_next_page("search", 20, "", "backtrace", 1)
    with pytest.raises(probe.ProbeFailure, match="missing backtrace"):
        probe.require_search_next_page("search", 20, "search-id", None, 1)
    with pytest.raises(probe.ProbeFailure, match="no second page"):
        probe.require_search_next_page("search", 20, "search-id", "backtrace", 0)
    with pytest.raises(probe.ProbeFailure, match="no second page"):
        probe.require_search_next_page("search", 20, "search-id", "backtrace", "0")


def test_billboard_second_page_does_not_require_cursor_or_has_more_presence() -> None:
    probe = _load_batch4()

    probe.require_billboard_second_page(None)
    with pytest.raises(probe.ProbeFailure, match="no second page"):
        probe.require_billboard_second_page(False)


def test_detail_selection_rejects_single_source_and_caps_each_source_at_ten() -> None:
    probe = _load_batch4()
    billboard = {"code": 200, "data": {"items": [_video(f"billboard-{index}") for index in range(15)]}}
    search = {"code": 200, "data": {"items": [_video(f"search-{index}") for index in range(15)]}}

    selected = probe.select_detail_sources((billboard,), (search,))
    assert len(selected["billboard"]) == 10
    assert len(selected["search"]) == 10
    assert len(selected["billboard"] + selected["search"]) == 20
    with pytest.raises(probe.ProbeFailure, match="Billboard and one Search"):
        probe.select_detail_sources((billboard,), ({"code": 200, "data": {"items": []}},))
