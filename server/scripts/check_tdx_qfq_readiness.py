#!/usr/bin/env python3
"""Check TDX qfq readiness for formal CZSC structure snapshots."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db.database import get_connection
from server.db.kline_lake import query_klines
from server.domain.symbols import normalize_symbol
from server.engines.structure.source_policy import resolve_structure_source_policy
from server.services.tdx_qfq_normalizer import rebuild_tdx_qfq_from_existing_factors

LEVELS = ("week", "day", "30", "5")


def tracked_symbols() -> list[str]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT symbol FROM positions WHERE quantity > 0
            UNION
            SELECT DISTINCT wi.symbol
              FROM watchlist_items wi
              JOIN watchlist_groups wg ON wg.id = wi.group_id
             ORDER BY symbol
            """
        ).fetchall()
    finally:
        conn.close()
    return [normalize_symbol(row[0]) for row in rows]


def check_symbol(symbol: str, *, rebuild: bool = False) -> dict:
    canonical = normalize_symbol(symbol)
    rebuild_result = None
    if rebuild:
        rebuild_result = rebuild_tdx_qfq_from_existing_factors(canonical, target_freqs=("week", "1", "5", "15", "30", "60"))

    counts = {}
    policies = {}
    for level in LEVELS:
        counts[level] = {
            "tdx_2": len(query_klines(canonical, level, limit=1, adjustflag="2", source="tdx")),
            "tdx_3": len(query_klines(canonical, level, limit=1, adjustflag="3", source="tdx")),
        }
        policy = resolve_structure_source_policy(symbol=canonical, level=level, limit=1200)
        selected = policy.get("selected") or {}
        policies[level] = {
            "source": selected.get("source"),
            "adjustflag": selected.get("adjustflag"),
            "usable": bool(selected.get("usable")),
            "reject_reason": selected.get("reject_reason") or "",
            "last_bar_at": selected.get("last_bar_at") or "",
        }

    ready = all(policies[level]["source"] == "tdx" and policies[level]["adjustflag"] == "2" and policies[level]["usable"] for level in LEVELS)
    missing = [level for level in LEVELS if not (policies[level]["source"] == "tdx" and policies[level]["adjustflag"] == "2" and policies[level]["usable"])]
    return {
        "symbol": canonical,
        "ready": ready,
        "missing_levels": missing,
        "counts": counts,
        "policies": policies,
        "rebuild": {
            "status": rebuild_result.status,
            "reason": rebuild_result.reason,
            "written": rebuild_result.written,
            "day_factor_count": rebuild_result.day_factor_count,
        } if rebuild_result else None,
    }


def print_report(items: list[dict]) -> None:
    print("symbol       ready missing      week        day         30          5           rebuild")
    print("------------ ----- ------------ ----------- ----------- ----------- ----------- ----------------")
    for item in items:
        policies = item["policies"]
        rebuild = item.get("rebuild") or {}
        print(
            f"{item['symbol']:<12} "
            f"{str(item['ready']):<5} "
            f"{','.join(item['missing_levels']):<12} "
            f"{_policy_text(policies['week']):<11} "
            f"{_policy_text(policies['day']):<11} "
            f"{_policy_text(policies['30']):<11} "
            f"{_policy_text(policies['5']):<11} "
            f"{rebuild.get('status', '')}:{rebuild.get('reason', '')}"
        )


def check_bridge(base_url: str, symbols: list[str]) -> None:
    url = base_url.rstrip("/")
    print(f"\nTDX bridge: {url}")
    health = _fetch_json(f"{url}/health")
    print(f"health: {json.dumps(health, ensure_ascii=False)[:500]}")
    if not symbols:
        return
    sample = symbols[0]
    parsed = sample.replace("sh.", "").replace("sz.", "")
    if sample.startswith("sh."):
        bridge_symbol = f"{parsed}.SH"
    elif sample.startswith("sz."):
        bridge_symbol = f"{parsed}.SZ"
    else:
        bridge_symbol = sample
    params = urllib.parse.urlencode({
        "symbol": bridge_symbol,
        "period": "1d",
        "count": 3,
        "dividend_type": "front",
        "refresh": "1",
    })
    kline = _fetch_json(f"{url}/kline?{params}")
    print(f"sample_1d_front[{bridge_symbol}]: {json.dumps(kline, ensure_ascii=False)[:500]}")


def _fetch_json(url: str) -> object:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            raw = response.read(4096).decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}
    except Exception as exc:
        return {"error": str(exc)}


def _policy_text(policy: dict) -> str:
    if not policy.get("usable"):
        return "none"
    return f"{policy.get('source')}/{policy.get('adjustflag')}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check TDX qfq readiness for tracked symbols")
    parser.add_argument("--symbol", nargs="*", help="默认检查持仓 + 盯盘/自选")
    parser.add_argument("--rebuild", action="store_true", help="先尝试用已有 tdx/day/2 因子重建分钟 qfq")
    parser.add_argument("--bridge-url", help="可选，顺带检查 TDX bridge /health 和样例 1d/front")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    symbols = [normalize_symbol(item) for item in args.symbol] if args.symbol else tracked_symbols()
    print_report([check_symbol(symbol, rebuild=args.rebuild) for symbol in symbols])
    if args.bridge_url:
        check_bridge(args.bridge_url, symbols)


if __name__ == "__main__":
    main()
