#!/usr/bin/env python3
"""Run AI Native Radar calibration samples."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server import config
from server.db.database import init_db
from server.engines.ai_native.calibration import (
    render_calibration_markdown,
    summarize_reasoning_response,
)
from server.engines.ai_native.reasoning_orchestrator import build_ai_native_reasoning


DEFAULT_SYMBOLS = [
    "sh600519",
    "sz300394",
    "sz300502",
    "sz002273",
    "sh688008",
]


async def run_calibration(symbols: list[str], user_id: int, mode: str | None) -> list[dict]:
    config.AI_NATIVE_RADAR_ENABLED = True
    init_db()

    rows: list[dict] = []
    for symbol in symbols:
        response = await build_ai_native_reasoning(symbol=symbol, user_id=user_id, mode=mode)
        rows.append(summarize_reasoning_response(symbol, response))
    return rows


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AI Native Radar calibration samples")
    parser.add_argument("symbols", nargs="*", help="股票代码，例如 sh600519 sz300394")
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--mode", choices=["EMPTY", "HOLDING"], default=None)
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    return parser.parse_args(argv)


async def async_main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    symbols = args.symbols or DEFAULT_SYMBOLS
    rows = await run_calibration(symbols=symbols, user_id=args.user_id, mode=args.mode)
    if args.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(render_calibration_markdown(rows))
    return 0


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
