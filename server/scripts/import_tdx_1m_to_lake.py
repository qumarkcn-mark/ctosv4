#!/usr/bin/env python3
"""Import local TDX 1-minute bars into the replay lake."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db.kline_lake import init_lake, upsert_klines
from server.domain.symbols import normalize_symbol
from server.services.tdx_minute_service import read_tdx_1m_klines, tdx_minute_status


def import_tdx_1m_to_lake(
    *,
    symbols: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 5000,
    vipdoc: str | None = None,
) -> list[dict]:
    init_lake()
    summaries = []
    for raw_symbol in symbols:
        symbol = normalize_symbol(raw_symbol)
        status = tdx_minute_status(symbol, vipdoc=vipdoc) if vipdoc else tdx_minute_status(symbol)
        if not status.get("available"):
            summaries.append({"symbol": symbol, "imported": 0, "reason": status.get("reason", "UNAVAILABLE")})
            continue
        read_kwargs = {"start_date": start_date, "end_date": end_date, "limit": limit}
        if vipdoc:
            read_kwargs["vipdoc"] = vipdoc
        rows = read_tdx_1m_klines(symbol, **read_kwargs)
        imported = upsert_klines(
            symbol,
            "1",
            rows,
            adjustflag="3",
            source="qmt",
        )
        summaries.append(
            {
                "symbol": symbol,
                "imported": imported,
                "first": rows[0]["date"] if rows else "",
                "last": rows[-1]["date"] if rows else "",
                "reason": "",
            }
        )
    return summaries


def print_summary(summaries: list[dict]) -> None:
    print("\nTDX 1m import summary")
    print("symbol       rows first               last                reason")
    print("------------ ---- ------------------- ------------------- ----------------")
    for item in summaries:
        print(
            f"{item['symbol']:<12} "
            f"{item['imported']:>4} "
            f"{item.get('first', ''):<19} "
            f"{item.get('last', ''):<19} "
            f"{item.get('reason', '')}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import local TDX .lc1 bars into CT-OS replay lake")
    parser.add_argument("--symbol", nargs="+", required=True)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--vipdoc")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print_summary(
        import_tdx_1m_to_lake(
            symbols=args.symbol,
            start_date=args.start,
            end_date=args.end,
            limit=args.limit,
            vipdoc=args.vipdoc,
        )
    )


if __name__ == "__main__":
    main()
