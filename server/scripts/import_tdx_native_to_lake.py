#!/usr/bin/env python3
"""Import TDX bridge native K-line periods into the TDX lake."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db.kline_lake import init_lake, upsert_klines
from server.domain.symbols import normalize_symbol
from server.services.tdx_bridge_client import fetch_tdx_klines


PERIODS: dict[str, str] = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "60m": "60",
    "1d": "day",
    "1w": "week",
}


async def import_tdx_native_to_lake(
    *,
    symbols: list[str],
    periods: list[str] | None = None,
    count: int = 5000,
    dividend_type: str = "front",
) -> list[dict]:
    init_lake()
    selected = periods or list(PERIODS)
    summaries = []
    for raw_symbol in symbols:
        symbol = normalize_symbol(raw_symbol)
        for period in selected:
            freq = PERIODS.get(period)
            if not freq:
                summaries.append({"symbol": symbol, "period": period, "imported": 0, "reason": "UNSUPPORTED_PERIOD"})
                continue
            rows = await fetch_tdx_klines(symbol, period=period, count=count, dividend_type=dividend_type)
            imported = upsert_klines(
                symbol,
                freq,
                rows,
                adjustflag="2",
                source="tdx",
            )
            summaries.append(
                {
                    "symbol": symbol,
                    "period": period,
                    "freq": freq,
                    "imported": imported,
                    "first": rows[0]["date"] if rows else "",
                    "last": rows[-1]["date"] if rows else "",
                    "dividend_type": dividend_type,
                    "reason": "" if rows else "NO_ROWS",
                }
            )
    return summaries


def print_summary(summaries: list[dict]) -> None:
    print("\nTDX native import summary")
    print("symbol       period freq rows first               last                reason")
    print("------------ ------ ---- ---- ------------------- ------------------- ----------------")
    for item in summaries:
        print(
            f"{item['symbol']:<12} "
            f"{item.get('period', ''):<6} "
            f"{item.get('freq', ''):<4} "
            f"{item.get('imported', 0):>4} "
            f"{item.get('first', ''):<19} "
            f"{item.get('last', ''):<19} "
            f"{item.get('reason', '')}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import TDX bridge native K-lines into CT-OS TDX lake")
    parser.add_argument("--symbol", nargs="+", required=True)
    parser.add_argument("--period", nargs="+", choices=sorted(PERIODS), default=list(PERIODS))
    parser.add_argument("--count", type=int, default=5000)
    parser.add_argument("--dividend-type", default="front")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print_summary(
        asyncio.run(
            import_tdx_native_to_lake(
                symbols=args.symbol,
                periods=args.period,
                count=args.count,
                dividend_type=args.dividend_type,
            )
        )
    )


if __name__ == "__main__":
    main()
