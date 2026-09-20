"""Join downloaded public TikHub tariffs and OpenAPI; never call a paid API.

The output is a dated research artifact, not a runtime endpoint allowlist.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


GROUPS = {
    "billboard": """fetch_city_list fetch_content_tag fetch_hot_category_list
        fetch_hot_account_list fetch_hot_city_list fetch_hot_comment_word_list
        fetch_hot_item_trends_list fetch_hot_rise_list fetch_hot_total_high_fan_list
        fetch_hot_total_high_like_list fetch_hot_total_high_play_list
        fetch_hot_total_high_search_list fetch_hot_total_high_topic_list
        fetch_hot_total_hot_word_list fetch_hot_total_list fetch_hot_total_low_fan_list
        fetch_hot_total_search_list fetch_hot_total_topic_list fetch_hot_total_video_list
        fetch_hot_total_hot_word_detail_list fetch_hot_user_portrait_list
        fetch_hot_account_search_list fetch_hot_account_trends_list
        fetch_hot_account_item_analysis_list fetch_hot_account_fans_interest_account_list
        fetch_hot_account_fans_interest_search_list fetch_hot_account_fans_interest_topic_list
        fetch_hot_account_fans_portrait_list""".split(),
    "creator": """fetch_creator_hot_spot_billboard fetch_creator_hot_topic_billboard
        fetch_creator_hot_music_billboard fetch_creator_material_center_billboard
        fetch_creator_material_center_config fetch_creator_material_center_related
        fetch_creator_hot_props_billboard fetch_creator_hot_challenge_billboard""".split(),
    "index": """fetch_all_valid_date fetch_valid_date_for_relation fetch_all_area
        fetch_current_hot_topic fetch_hot_detail fetch_hot_words fetch_hot_trend_word
        fetch_keyword_valid_date fetch_multi_keyword_hot_trend
        fetch_multi_keyword_interpretation fetch_relation_word fetch_portrait
        fetch_encrypt_user_id fetch_daren_sug_great_user_list
        fetch_daren_compare_users_stable fetch_daren_similar_users
        fetch_daren_great_user_top_video fetch_daren_great_item_mile_info
        fetch_daren_great_user_fans_info fetch_item_filter_options fetch_item_sug
        fetch_item_query fetch_item_base fetch_item_index fetch_item_index_interpret
        fetch_item_analysis fetch_item_user_profile fetch_content_valid_date
        fetch_brand_hot_videos_time_scope fetch_content_creative_keywords
        fetch_content_creative_keyword_items fetch_content_creative_topic
        fetch_content_publish_trend fetch_content_creative_duration
        fetch_content_author_portrait fetch_content_consumer_portrait
        fetch_content_interact_trend fetch_content_consume_trend
        fetch_insight_recommend fetch_report_search fetch_report_detail
        fetch_insight_get_rec""".split(),
    "search": """fetch_user_search fetch_user_search_v2 fetch_video_search_v1
        fetch_video_search_v2 fetch_video_search_v3 fetch_video_search_v4
        fetch_video_search_v5 fetch_general_search_v2 fetch_challenge_search_v2
        fetch_search_suggest fetch_search_suggest_v2""".split(),
    "app/v3": """fetch_one_video fetch_one_video_v2 fetch_one_video_v3
        fetch_multi_video fetch_multi_video_v2 fetch_video_statistics
        fetch_multi_video_statistics fetch_user_post_videos handler_user_profile
        fetch_video_comments fetch_video_comment_replies
        fetch_video_high_quality_play_url fetch_multi_video_high_quality_play_url""".split(),
    "web": """fetch_one_video fetch_one_video_v2 fetch_multi_video
        handler_user_profile fetch_batch_user_profile_v1 fetch_batch_user_profile_v2
        fetch_user_post_videos fetch_video_comments fetch_video_comment_replies""".split(),
}
META = """get_endpoint_info get_all_endpoints_info calculate_price
    get_user_daily_usage get_tiered_discount_info""".split()
INTEGRATED = {
    "billboard/fetch_hot_total_low_fan_list", "app/v3/fetch_multi_video_v2",
    "creator/fetch_creator_material_center_billboard", "search/fetch_video_search_v2",
    "app/v3/fetch_user_post_videos", "app/v3/fetch_video_comments",
    "app/v3/fetch_video_comment_replies",
}
LIVE = {"billboard/fetch_hot_total_low_fan_list", "app/v3/fetch_multi_video_v2"}
CAPACITY = {
    "app/v3/fetch_one_video": 1, "app/v3/fetch_one_video_v2": 1,
    "app/v3/fetch_one_video_v3": 1, "app/v3/fetch_multi_video": 10,
    "app/v3/fetch_multi_video_v2": 50, "app/v3/fetch_video_statistics": 2,
    "app/v3/fetch_multi_video_statistics": 50,
    "app/v3/handler_user_profile": 1, "web/handler_user_profile": 1,
    "web/fetch_batch_user_profile_v1": 10, "web/fetch_batch_user_profile_v2": 50,
    "creator/fetch_creator_material_center_related": 100,
    "index/fetch_daren_compare_users_stable": 5,
    "search/fetch_video_search_v4": 12, "search/fetch_video_search_v5": 10,
}


def resolve(schema: dict, spec: dict) -> dict:
    if "$ref" in schema:
        target = spec
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        return target
    return schema


def shape(schema: dict, spec: dict, depth: int = 0) -> dict:
    """Keep only contract facts, not upstream descriptions or example payloads."""
    schema = resolve(schema, spec)
    keep = ("type", "format", "default", "enum", "minimum", "maximum",
            "minItems", "maxItems", "minLength", "maxLength", "required")
    result = {key: schema[key] for key in keep if key in schema}
    if depth < 5:
        if "properties" in schema:
            result["properties"] = {
                name: shape(value, spec, depth + 1)
                for name, value in schema["properties"].items()
            }
        if "items" in schema:
            result["items"] = shape(schema["items"], spec, depth + 1)
        for key in ("anyOf", "oneOf", "allOf"):
            if key in schema:
                result[key] = [shape(value, spec, depth + 1) for value in schema[key]]
    return result


def build(tariff_file: Path, spec_file: Path) -> dict:
    tariff = json.loads(tariff_file.read_text(encoding="utf-8"))
    spec = json.loads(spec_file.read_text(encoding="utf-8"))
    prices = {row["endpoint_uri"]: row for row in tariff["data"]}
    selected = [(group, name, f"/api/v1/douyin/{group}/{name}")
                for group, names in GROUPS.items() for name in names]
    selected += [("metadata", name, f"/api/v1/tikhub/user/{name}") for name in META]
    rows = []
    for group, name, path in selected:
        price = prices.get(path)
        operations = []
        for method, op in spec["paths"].get(path, {}).items():
            if method not in {"get", "post"}:
                continue
            params = [{"name": p["name"], "in": p["in"],
                       "required": p.get("required", False),
                       "schema": shape(p.get("schema", {}), spec)}
                      for p in op.get("parameters", [])]
            body = op.get("requestBody", {})
            operations.append({"method": method.upper(),
                "operation_id": op["operationId"], "parameters": params,
                "body_required": body.get("required", False),
                "body": {mime: shape(v.get("schema", {}), spec)
                         for mime, v in body.get("content", {}).items()}})
        key = f"{group}/{name}"
        rows.append({"group": group, "path": path,
            "documented_max_items": CAPACITY.get(key),
            "capacity_evidence": "reviewed_operation_description_or_schema" if key in CAPACITY else None,
            "status": "live_intake_verified" if key in LIVE else
                      "adapter_present" if key in INTEGRATED else
                      "metadata" if group == "metadata" else "candidate",
            "base_price_usd": price["endpoint_cost"] if price else None,
            "allow_discount": bool(price["allow_discount"]) if price else None,
            "allow_free_credit": bool(price["allow_free_credit"]) if price else None,
            "rate_limit": price["rate_limit"] if price else None,
            "contract_found": bool(operations), "operations": operations})
    return {"snapshot_date": "2026-09-20", "currency": "USD",
        "tariff_at_utc": datetime.fromtimestamp(tariff["time_stamp"], timezone.utc).isoformat(),
        "sources": {"prices": "https://api.tikhub.io/api/v1/tikhub/user/get_all_endpoints_info",
                    "contract": "https://api.tikhub.io/openapi.json",
                    "marketplace": "https://user.tikhub.io/dashboard/api-marketplace?platform=douyin"},
        "sha256": {"prices": hashlib.sha256(tariff_file.read_bytes()).hexdigest(),
                   "contract": hashlib.sha256(spec_file.read_bytes()).hexdigest()},
        "note": "Public price snapshot, not a bill or a runtime allowlist. Missing facts remain null.",
        "endpoints": rows}


def markdown(report: dict) -> str:
    out = ["# 业务候选接口逐项价格与请求契约", "", "日期：2026-09-20。由官方价表与当前 OpenAPI 离线关联生成。",
        "价格为 USD/供应商成功请求；默认值不代表最大返回量。候选不代表已启用。",
        "JSON 同名文件保留字段类型、枚举、默认值、必填项、约束与来源 SHA-256。",
        "True=支持、False=不支持、None=未核实；* 表示 query/path 必填；body 必填项见 JSON。",
        "live_intake_verified=已验收采集；adapter_present=有适配器；candidate=待接入/备选；metadata=费用查询。",
        "调用时机、输出用途与路由规则见 [业务调用策略](../API_BUSINESS_CALL_POLICY_V1.md)。", ""]
    for group in list(GROUPS) + ["metadata"]:
        out += [f"## {group}", "", "| 完整接口 | 方法/输入 | 单价 USD | 阶梯折扣 | 免费余额 | RPS 原值 | 状态 |",
                "| --- | --- | ---: | --- | --- | --- | --- |"]
        for row in report["endpoints"]:
            if row["group"] != group:
                continue
            inputs = []
            for op in row["operations"]:
                fields = [p["name"] + ("*" if p["required"] else "") for p in op["parameters"]]
                for schema in op["body"].values():
                    fields += list(schema.get("properties", {})) or ["body[]" if schema.get("type") == "array" else "body"]
                inputs.append(op["method"] + " " + ", ".join(fields))
            cost = "待核验" if row["base_price_usd"] is None else f'{row["base_price_usd"]:.3f}'
            out.append(f'| `{row["path"]}` | {"; ".join(inputs) or "契约缺失"} | {cost} | '
                       f'{row["allow_discount"]} | {row["allow_free_credit"]} | {row["rate_limit"]} | {row["status"]} |')
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tariffs", type=Path, required=True)
    parser.add_argument("--openapi", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build(args.tariffs, args.openapi)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    print(json.dumps({"endpoints": len(report["endpoints"]),
        "missing_prices": [r["path"] for r in report["endpoints"] if r["base_price_usd"] is None],
        "missing_contracts": [r["path"] for r in report["endpoints"] if not r["contract_found"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
