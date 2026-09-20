"""Offline research-artifact checks; no provider calls or credentials."""
from __future__ import annotations

import importlib.util
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pricing_inventory", ROOT / "scripts/tikhub/build_pricing_inventory.py"
)
assert SPEC is not None and SPEC.loader is not None
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


def test_snapshot_has_no_silent_missing_or_duplicate_endpoints():
    report = json.loads((ROOT / "docs/pricing/tikhub-20260920.json").read_text())
    rows = report["endpoints"]
    assert len(rows) == len({row["path"] for row in rows}) == 116
    assert all(row["contract_found"] for row in rows)
    assert [row["path"] for row in rows if row["base_price_usd"] is None] == [
        "/api/v1/douyin/search/fetch_search_suggest_v2"
    ]
    assert Counter(row["status"] for row in rows) == {
        "live_intake_verified": 2, "adapter_present": 5,
        "metadata": 5, "candidate": 104,
    }
    assert inventory.markdown(report) == (
        ROOT / "docs/pricing/tikhub-20260920.md"
    ).read_text()


def test_shape_preserves_request_constraints_not_response_examples():
    spec = {"components": {"schemas": {"Request": {
        "type": "object", "required": ["ids"], "properties": {
            "ids": {"type": "array", "maxItems": 50,
                    "items": {"type": "string", "minLength": 1}},
            "page": {"type": "integer", "default": 1, "minimum": 1,
                     "description": "do not copy upstream prose", "example": 9},
        },
    }}}}
    result = inventory.shape({"$ref": "#/components/schemas/Request"}, spec)
    assert result["required"] == ["ids"]
    assert result["properties"]["ids"]["maxItems"] == 50
    assert result["properties"]["page"] == {
        "type": "integer", "default": 1, "minimum": 1,
    }


def test_small_detail_batches_and_statistics_are_not_per_record_quotes():
    report = json.loads((ROOT / "docs/pricing/tikhub-20260920.json").read_text())
    rows = {row["path"].split("/douyin/")[-1]: row for row in report["endpoints"]}
    expected = {
        "app/v3/fetch_one_video": (1, "0.001"),
        "app/v3/fetch_multi_video": (10, "0.01"),
        "app/v3/fetch_multi_video_v2": (50, "0.05"),
        "app/v3/fetch_video_statistics": (2, "0.001"),
        "app/v3/fetch_multi_video_statistics": (50, "0.025"),
    }
    for key, (capacity, cost) in expected.items():
        assert rows[key]["documented_max_items"] == capacity
        assert Decimal(str(rows[key]["base_price_usd"])) == Decimal(cost)
    assert Decimal("0.001") + Decimal("0.05") == Decimal("0.051")
    assert 3 * Decimal("0.001") + Decimal("0.001") == Decimal("0.004")


def test_business_cost_examples_use_consistent_units():
    assert sum(map(Decimal, ["0.288", "0.072", "0.020", "0.100", "0.200",
                            "0.400", "0.160", "0.010", "0.020", "0.017"])) == Decimal("1.287")
    assert Decimal("0.00022") * 3600 == Decimal("0.792")
    assert Decimal("0.00008") * 3600 == Decimal("0.288")
    assert (8000 * Decimal("0.15") + 2000 * Decimal("1.5")) / 1_000_000 == Decimal("0.0042")
    assert (8000 * Decimal("0.8") + 2000 * Decimal("2")) / 1_000_000 == Decimal("0.0104")
    assert Decimal("0.288") + Decimal("0.64") * Decimal("0.8") == Decimal("0.8")
    assert Decimal("0.0042") + Decimal("0.65") * Decimal("0.012") == Decimal("0.012")
