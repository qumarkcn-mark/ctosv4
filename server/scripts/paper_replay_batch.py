#!/usr/bin/env python3
"""Run intraday T paper replay batches and print a focused diagnostics report."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.domain.symbols import normalize_symbol
from server.engines.decision.intraday_t_profiles import intraday_t_profile_choices
from server.scripts.paper_decision_report import build_decision_report, print_report
from server.scripts.paper_replay_pool import print_summary, run_replay_pool


async def run_replay_batch(
    *,
    symbols: list[str],
    windows: list[tuple[str, str]],
    run_label: str = "batch",
    trigger_freq: str = "1",
    level_chain: dict[str, str] | None = None,
    user_id: int = 1,
    initial_cash: float = 1_000_000.0,
    base_qty: int = 1000,
    protected_base_qty: int = 700,
    available_qty: int = 1000,
    avg_cost: float = 0.0,
    default_t_qty: int = 100,
    strategy_profile: str = "strict",
    min_second_leg_bars: int | None = None,
    event_freshness_bars: int | None = None,
    min_divergence_strength: float | None = None,
    sell_first_min_distance_to_zg_atr: float | None = None,
    buy_first_max_distance_to_zd_atr: float | None = None,
    min_expected_edge_after_cost: float | None = None,
    expected_edge_atr_multiple: float | None = None,
    first_leg_confirmation_bars: int | None = None,
    second_leg_confirmation_bars: int | None = None,
    min_bars_before_window_end_for_first_leg: int | None = None,
    limit: int = 240,
    persist: bool = True,
    persist_feature_cache: bool = True,
    verbose_decisions: bool = False,
    kline_source: str | None = None,
    adjustflag: str = "2",
    detail_source: str | None = None,
    parent_level: str = "",
    parent_task: str = "",
    parent_leg_id: str = "",
    parent_allowed_first_side: str = "",
    parent_max_cycles: int = 0,
    auto_parent_context: bool = False,
    report_limit: int = 5000,
) -> dict[str, Any]:
    normalized_symbols = _normalize_symbols(symbols)
    all_summaries: list[dict[str, Any]] = []
    run_ids: list[str] = []

    for index, (start_date, end_date) in enumerate(windows, start=1):
        window_label = f"{run_label}_w{index}" if len(windows) > 1 else run_label
        summaries = await run_replay_pool(
            symbols=normalized_symbols,
            start_date=start_date,
            end_date=end_date,
            trigger_freq=trigger_freq,
            level_chain=level_chain,
            user_id=user_id,
            initial_cash=initial_cash,
            base_qty=base_qty,
            protected_base_qty=protected_base_qty,
            available_qty=available_qty,
            avg_cost=avg_cost,
            default_t_qty=default_t_qty,
            strategy_profile=strategy_profile,
            min_second_leg_bars=min_second_leg_bars,
            event_freshness_bars=event_freshness_bars,
            min_divergence_strength=min_divergence_strength,
            sell_first_min_distance_to_zg_atr=sell_first_min_distance_to_zg_atr,
            buy_first_max_distance_to_zd_atr=buy_first_max_distance_to_zd_atr,
            min_expected_edge_after_cost=min_expected_edge_after_cost,
            expected_edge_atr_multiple=expected_edge_atr_multiple,
            first_leg_confirmation_bars=first_leg_confirmation_bars,
            second_leg_confirmation_bars=second_leg_confirmation_bars,
            min_bars_before_window_end_for_first_leg=min_bars_before_window_end_for_first_leg,
            limit=limit,
            persist=persist,
            persist_feature_cache=persist_feature_cache,
            verbose_decisions=verbose_decisions,
            kline_source=kline_source,
            adjustflag=adjustflag,
            detail_source=detail_source,
            parent_level=parent_level,
            parent_task=parent_task,
            parent_leg_id=parent_leg_id,
            parent_allowed_first_side=parent_allowed_first_side,
            parent_max_cycles=parent_max_cycles,
            auto_parent_context=auto_parent_context,
            run_label=window_label,
        )
        all_summaries.extend(summaries)
        run_ids.extend(str(item["run_id"]) for item in summaries)

    report = build_decision_report(run_ids=run_ids, limit=report_limit) if persist and run_ids else {}
    return {
        "symbols": normalized_symbols,
        "windows": windows,
        "summaries": all_summaries,
        "run_ids": run_ids,
        "report": report,
    }


def print_batch_result(result: dict[str, Any]) -> None:
    print("\nPaper replay batch")
    print(f"symbols: {len(result['symbols'])}  windows: {len(result['windows'])}  runs: {len(result['run_ids'])}")
    if result["run_ids"]:
        print("run_ids:")
        for run_id in result["run_ids"][:20]:
            print(f"  {run_id}")
        if len(result["run_ids"]) > 20:
            print(f"  ... +{len(result['run_ids']) - 20}")
    print_summary(result["summaries"])
    if result.get("report"):
        print()
        print_report(result["report"])


def _normalize_symbols(symbols: list[str]) -> list[str]:
    result = []
    seen = set()
    for symbol in symbols:
        normalized = normalize_symbol(symbol)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _load_symbols(symbols: list[str] | None, symbols_file: str | None) -> list[str]:
    loaded = list(symbols or [])
    if symbols_file:
        path = Path(symbols_file)
        for line in path.read_text(encoding="utf-8").splitlines():
            item = line.split("#", 1)[0].strip()
            if item:
                loaded.append(item)
    if not loaded:
        raise ValueError("至少需要通过 --symbol 或 --symbols-file 提供股票池")
    return loaded


def _windows_from_args(args: argparse.Namespace) -> list[tuple[str, str]]:
    windows = list(args.window or [])
    if args.date:
        start_time = args.start_time or "09:31:00"
        end_time = args.end_time or "15:00:00"
        windows.extend((f"{date} {start_time}", f"{date} {end_time}") for date in args.date)
    if not windows:
        raise ValueError("至少需要提供 --window START END 或 --date")
    return [(str(start), str(end)) for start, end in windows]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CT-OS intraday T paper replay batches")
    parser.add_argument("--symbol", nargs="+", help="Symbols, e.g. sh.603893 sz.300724")
    parser.add_argument("--symbols-file", help="Text file with one symbol per line; # comments are ignored")
    parser.add_argument("--window", nargs=2, action="append", metavar=("START", "END"), help="Replay datetime window")
    parser.add_argument("--date", nargs="+", help="Trading dates; combined with --start-time/--end-time")
    parser.add_argument("--start-time", default="09:31:00")
    parser.add_argument("--end-time", default="15:00:00")
    parser.add_argument("--run-label", default="batch")
    parser.add_argument("--trigger-freq", default="1")
    parser.add_argument("--level-chain", nargs=3, metavar=("L0", "L1", "L2"))
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--initial-cash", type=float, default=1_000_000.0)
    parser.add_argument("--base-qty", type=int, default=1000)
    parser.add_argument("--protected-base-qty", type=int, default=700)
    parser.add_argument("--available-qty", type=int, default=1000)
    parser.add_argument("--avg-cost", type=float, default=0.0)
    parser.add_argument("--default-t-qty", type=int, default=100)
    parser.add_argument("--strategy-profile", choices=intraday_t_profile_choices(), default="strict")
    parser.add_argument("--min-second-leg-bars", type=int)
    parser.add_argument("--event-freshness-bars", type=int)
    parser.add_argument("--min-divergence-strength", type=float)
    parser.add_argument("--sell-first-min-distance-to-zg-atr", type=float)
    parser.add_argument("--buy-first-max-distance-to-zd-atr", type=float)
    parser.add_argument("--min-expected-edge-after-cost", type=float)
    parser.add_argument("--expected-edge-atr-multiple", type=float)
    parser.add_argument("--first-leg-confirmation-bars", type=int)
    parser.add_argument("--second-leg-confirmation-bars", type=int)
    parser.add_argument("--min-bars-before-window-end-for-first-leg", type=int)
    parser.add_argument("--limit", type=int, default=240)
    parser.add_argument("--report-limit", type=int, default=5000)
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--no-feature-cache-persist", action="store_true")
    parser.add_argument("--verbose-decisions", action="store_true")
    parser.add_argument("--kline-source", choices=["baostock", "qmt", "tdx"])
    parser.add_argument("--adjustflag", default="2")
    parser.add_argument("--detail-source", choices=["tdx_1m_replay"])
    parser.add_argument("--parent-level", default="")
    parser.add_argument("--parent-task", default="")
    parser.add_argument("--parent-leg-id", default="")
    parser.add_argument("--parent-allowed-first-side", choices=["BUY", "SELL"], default="")
    parser.add_argument("--parent-max-cycles", type=int, default=0)
    parser.add_argument("--auto-parent-context", action="store_true")
    return parser.parse_args(argv)


async def async_main() -> None:
    args = parse_args()
    result = await run_replay_batch(
        symbols=_load_symbols(args.symbol, args.symbols_file),
        windows=_windows_from_args(args),
        run_label=args.run_label,
        trigger_freq=args.trigger_freq,
        level_chain=(
            {"L0": args.level_chain[0], "L1": args.level_chain[1], "L2": args.level_chain[2]}
            if args.level_chain
            else None
        ),
        user_id=args.user_id,
        initial_cash=args.initial_cash,
        base_qty=args.base_qty,
        protected_base_qty=args.protected_base_qty,
        available_qty=args.available_qty,
        avg_cost=args.avg_cost,
        default_t_qty=args.default_t_qty,
        strategy_profile=args.strategy_profile,
        min_second_leg_bars=args.min_second_leg_bars,
        event_freshness_bars=args.event_freshness_bars,
        min_divergence_strength=args.min_divergence_strength,
        sell_first_min_distance_to_zg_atr=args.sell_first_min_distance_to_zg_atr,
        buy_first_max_distance_to_zd_atr=args.buy_first_max_distance_to_zd_atr,
        min_expected_edge_after_cost=args.min_expected_edge_after_cost,
        expected_edge_atr_multiple=args.expected_edge_atr_multiple,
        first_leg_confirmation_bars=args.first_leg_confirmation_bars,
        second_leg_confirmation_bars=args.second_leg_confirmation_bars,
        min_bars_before_window_end_for_first_leg=args.min_bars_before_window_end_for_first_leg,
        limit=args.limit,
        persist=not args.no_persist,
        persist_feature_cache=not args.no_feature_cache_persist,
        verbose_decisions=args.verbose_decisions,
        kline_source=args.kline_source,
        adjustflag=args.adjustflag,
        detail_source=args.detail_source,
        parent_level=args.parent_level,
        parent_task=args.parent_task,
        parent_leg_id=args.parent_leg_id,
        parent_allowed_first_side=args.parent_allowed_first_side,
        parent_max_cycles=args.parent_max_cycles,
        auto_parent_context=args.auto_parent_context,
        report_limit=args.report_limit,
    )
    print_batch_result(result)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
