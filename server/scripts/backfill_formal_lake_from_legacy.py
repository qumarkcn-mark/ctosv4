#!/usr/bin/env python3
"""Backfill formal raw/qfq tables from existing legacy K-line rows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.services.formal_lake_backfill_service import DEFAULT_FREQS, backfill_formal_tables_from_legacy
from server.workers.kline_sync_worker import _get_all_tracked_symbols


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill raw_bars/adjusted_bars from legacy klines")
    parser.add_argument("--source", choices=["tdx", "baostock"], default="tdx")
    parser.add_argument("--symbols", help="Comma-separated CT-OS symbols, e.g. sh.600519,sz.000001")
    parser.add_argument("--tracked", action="store_true", help="Use current positions/watchlist/recent tracked symbols")
    parser.add_argument("--freqs", default=",".join(DEFAULT_FREQS), help="Comma-separated freqs")
    parser.add_argument("--batch-id", default="legacy_formal_backfill_v1")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.tracked:
        symbols = _get_all_tracked_symbols()
    elif args.symbols:
        symbols = [item.strip() for item in args.symbols.split(",") if item.strip()]
    else:
        symbols = None
    freqs = [item.strip() for item in args.freqs.split(",") if item.strip()]
    result = backfill_formal_tables_from_legacy(
        source=args.source,
        symbols=symbols,
        freqs=freqs,
        batch_id=args.batch_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
