#!/usr/bin/env python3
"""Import local TDX 1-minute bars into the replay lake."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db.kline_lake import init_lake, upsert_klines
from server.domain.symbols import normalize_symbol
from server.services.tdx_minute_service import (
    TDX_AGGREGATE_FREQ_LABELS,
    aggregate_tdx_1m_klines,
    read_tdx_1m_klines,
    tdx_minute_status,
)


def import_tdx_1m_to_lake(
    *,
    symbols: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 5000,
    vipdoc: str | None = None,
    freqs: list[str] | None = None,
) -> list[dict]:
    init_lake()
    summaries = []
    target_freqs = freqs or ["1", *TDX_AGGREGATE_FREQ_LABELS.keys()]
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
        by_freq = {}
        imported = 0
        if "1" in target_freqs:
            by_freq["1"] = (
                upsert_klines(
                    symbol,
                    "1",
                    rows,
                    adjustflag="3",
                    source="tdx",
                )
                if rows
                else 0
            )
            imported += by_freq["1"]
        for freq in target_freqs:
            if freq == "1":
                continue
            aggregated = aggregate_tdx_1m_klines(rows, freq)
            by_freq[freq] = (
                upsert_klines(
                    symbol,
                    freq,
                    aggregated,
                    adjustflag="3",
                    source="tdx",
                )
                if aggregated
                else 0
            )
            imported += by_freq[freq]
        summaries.append(
            {
                "symbol": symbol,
                "imported": imported,
                "by_freq": by_freq,
                "first": rows[0]["date"] if rows else "",
                "last": rows[-1]["date"] if rows else "",
                "reason": "",
            }
        )
    return summaries


def print_summary(summaries: list[dict]) -> None:
    print("\nTDX local minute import summary")
    print("symbol       rows by_freq                 first               last                reason")
    print("------------ ---- ----------------------- ------------------- ------------------- ----------------")
    for item in summaries:
        print(
            f"{item['symbol']:<12} "
            f"{item['imported']:>4} "
            f"{str(item.get('by_freq', {})):<23} "
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
    parser.add_argument("--freq", nargs="+", default=None, help="1 5 15 30 60；默认全部导入")
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
            freqs=args.freq,
        )
    )


if __name__ == "__main__":
    main()
