#!/usr/bin/env python3
"""Run the post-market TDX data flow.

This is the canonical after-hours path:
1. Read local TDX vipdoc `.day` files into `tdx/day/3` for the full market.
2. Refresh tracked symbols from local TDX history into `tdx/*/3`.
3. Try to import `tdx/day/2` from the TDX bridge and rebuild local qfq bars.
4. Enqueue formal CZSC snapshot jobs only for qfq (`adjustflag=2`) changes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.engines.ai_native.czsc_snapshot_service import prewarm_structure_snapshots
from server.services.tdx_daily_sync_service import resolve_vipdoc, sync_daily_files, vipdoc_status
from server.workers.kline_sync_worker import (
    ALL_FREQS,
    _get_all_tracked_symbols,
    _sync_all_symbols_from_tdx_local,
    enqueue_structure_jobs_for_changes,
)


def run_postmarket_sync(*, vipdoc: str | None = None, mode: str = "incremental", reset: bool = False) -> dict:
    root = Path(resolve_vipdoc(vipdoc))
    status = vipdoc_status(str(root))
    if not status.get("available"):
        return {
            "status": "error",
            "stage": "vipdoc",
            "message": f"TDX vipdoc 不可用: {root}",
            "vipdoc": status,
        }

    daily = sync_daily_files(root, mode=mode, reset=reset, job_id=None)
    symbols = _get_all_tracked_symbols()
    tracked = _sync_all_symbols_from_tdx_local(symbols, list(ALL_FREQS)) if symbols else {
        "total_symbols": 0,
        "updated_symbols": 0,
        "total_written": 0,
        "errors": 0,
        "changed": [],
    }
    structure_jobs = enqueue_structure_jobs_for_changes(
        tracked.get("changed") or [],
        priority=90,
        reason="tdx_postmarket_sync",
    )
    changed_symbols = sorted({item["symbol"] for item in tracked.get("changed", []) if item.get("symbol")})
    snapshot_prewarm = (
        prewarm_structure_snapshots(
            symbols=changed_symbols,
            levels=["week", "day", "30", "5"],
            priority=90,
            reason="tdx_postmarket_sync",
            force_rebuild=True,
        )
        if changed_symbols
        else {"count": 0, "items": [], "skipped": True, "reason": "NO_CHANGED_SYMBOLS"}
    )
    return {
        "status": "success" if tracked.get("errors", 0) == 0 else "partial",
        "vipdoc": status,
        "daily_raw": daily,
        "tracked": tracked,
        "structure_jobs": structure_jobs,
        "snapshot_prewarm": snapshot_prewarm,
    }


def print_summary(result: dict) -> None:
    print("TDX postmarket sync")
    print(f"status: {result.get('status')}")
    if result.get("status") == "error":
        print(f"stage: {result.get('stage')} message: {result.get('message')}")
        return
    daily = result.get("daily_raw") or {}
    tracked = result.get("tracked") or {}
    structure = result.get("structure_jobs") or {}
    snapshots = result.get("snapshot_prewarm") or {}
    print(
        "daily_raw: "
        f"symbols={daily.get('synced_symbols', 0)} rows={daily.get('written_rows', 0)} "
        f"files={daily.get('processed_files', 0)}/{daily.get('total_files', 0)}"
    )
    print(
        "tracked: "
        f"symbols={tracked.get('updated_symbols', 0)}/{tracked.get('total_symbols', 0)} "
        f"rows={tracked.get('total_written', 0)} errors={tracked.get('errors', 0)} "
        f"qfq_changes={len(tracked.get('changed') or [])}"
    )
    print(f"structure_jobs: count={structure.get('count', 0)}")
    print(f"snapshot_prewarm: count={snapshots.get('count', 0)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run post-market TDX raw/qfq/snapshot sync")
    parser.add_argument("--vipdoc", help="TDX vipdoc 挂载路径；默认按配置自动探测")
    parser.add_argument("--mode", choices=["incremental", "full"], default="incremental")
    parser.add_argument("--reset", action="store_true", help="重建 tdx/day/3 raw 日线")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_postmarket_sync(vipdoc=args.vipdoc, mode=args.mode, reset=args.reset)
    print_summary(result)
    if result.get("status") == "error":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
