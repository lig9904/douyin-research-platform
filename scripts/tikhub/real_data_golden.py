#!/usr/bin/env python3
"""Safely collect one tiny public TikHub discovery page into local L0/L1.

Default is a dry-run and has no database, secret, or network dependency.
Live mode needs all three explicit gates:

  TIKHUB_GOLDEN_LIVE=YES TIKHUB_API_KEY=... DATABASE_URL=... \\
    uv run python scripts/tikhub/real_data_golden.py --live

The command prints only a plan or aggregate result. Provider payloads stay in
the local database cache and are never written to the repository.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from douyin_research.l0l1.real_data import make_plan, plan_dict, run_live


LIVE_GATE = "TIKHUB_GOLDEN_LIVE"
API_KEY_ENV = "TIKHUB_API_KEY"
DSN_ENV = "DATABASE_URL"


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--live", action="store_true", help="execute only with explicit environment gate")
    p.add_argument("--max-items", type=int, default=5)
    p.add_argument("--max-external-calls", type=int, default=2)
    p.add_argument("--max-cost-usd", type=float, default=0.01)
    p.add_argument("--date-window-hours", type=int, default=24)
    p.add_argument("--no-enrich-details", action="store_true")
    p.add_argument("--triggered-by", default="local-golden-operator")
    p.add_argument(
        "--force-refresh",
        action="store_true",
        help="bypass the discovery cache; still bounded by the same paid-call budget",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    plan = make_plan(
        dry_run=not args.live,
        max_items=args.max_items,
        max_external_calls=args.max_external_calls,
        max_cost_usd=args.max_cost_usd,
        date_window_hours=args.date_window_hours,
        enrich_details=not args.no_enrich_details,
        force_refresh=args.force_refresh,
    )
    if not args.live:
        print(json.dumps({"mode": "dry_run", "plan": plan_dict(plan)}, ensure_ascii=False))
        return 0
    if os.getenv(LIVE_GATE) != "YES":
        raise SystemExit(f"live mode is disabled; set {LIVE_GATE}=YES")
    result = run_live(
        dsn=os.getenv(DSN_ENV, ""),
        api_key=os.getenv(API_KEY_ENV, ""),
        plan=plan,
        triggered_by=args.triggered_by,
    )
    print(json.dumps({"mode": "live", "result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, RuntimeError) as exc:
        print(f"golden intake failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
