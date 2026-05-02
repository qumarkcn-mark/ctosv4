#!/usr/bin/env python3
"""Run intraday T paper replay for a small historical sample pool.

Example:
  python -m server.scripts.paper_replay_pool \
    --symbol sh.603893 sh.603986 \
    --start 2026-04-01 \
    --end 2026-04-29
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db.database import get_connection, init_db
from server.db.kline_lake import query_klines
from server.domain.symbols import normalize_symbol
from server.engines.execution.paper_models import (
    PaperAccount,
    PaperPosition,
    PaperRiskConfig,
)
from server.engines.decision.intraday_t_profiles import build_intraday_t_risk_config, intraday_t_profile_choices
from server.engines.execution.paper_feature_cache import ReplayFeatureCache, SQLiteReplayFeatureCache
from server.engines.execution.paper_replay import ReplayResult, replay_intraday_t_steps
from server.engines.decision.intraday_t_strategy import IntradayTParentContext
from server.engines.execution.paper_replay_source import (
    build_replay_steps_from_klines,
)
from server.engines.execution.paper_store import save_replay_result


async def run_replay_pool(
    *,
    symbols: list[str],
    start_date: str,
    end_date: str,
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
    run_label: str = "",
) -> list[dict]:
    """Run replay for each symbol and optionally persist to paper tables."""
    init_db()
    config = build_intraday_t_risk_config(
        profile=strategy_profile,
        default_t_qty=default_t_qty,
        protected_base_qty=protected_base_qty,
        overrides=_profile_overrides(
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
        ),
    )
    cache_conn = get_connection() if persist_feature_cache else None
    feature_cache = SQLiteReplayFeatureCache(conn=cache_conn) if cache_conn is not None else ReplayFeatureCache()
    summaries = []

    conn = get_connection() if persist else None
    try:
        for symbol in [normalize_symbol(item) for item in symbols]:
            cache_before = feature_cache.stats()
            account = _account_for_symbol(
                symbol=symbol,
                user_id=user_id,
                initial_cash=initial_cash,
                base_qty=base_qty,
                protected_base_qty=protected_base_qty,
                available_qty=available_qty,
                avg_cost=avg_cost,
                ref_end_date=start_date,
                kline_source=kline_source,
                adjustflag=adjustflag,
            )
            steps = await build_replay_steps_from_klines(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                trigger_freq=trigger_freq,
                level_chain=level_chain,
                limit=limit,
                feature_cache=feature_cache,
                detail_source=detail_source,
                kline_loader=_kline_loader(kline_source, adjustflag),
            )
            parent_context = _parent_context(
                parent_level=parent_level,
                parent_task=parent_task,
                parent_leg_id=parent_leg_id or f"{symbol}:{start_date}:{end_date}",
                allowed_first_side=parent_allowed_first_side,
                max_cycles=parent_max_cycles,
            )
            result = replay_intraday_t_steps(
                account,
                steps,
                config,
                parent_context=parent_context,
                auto_parent_context=auto_parent_context and parent_context is None,
                parent_max_cycles=parent_max_cycles or 1,
            )
            run_id = _unique_run_id(
                conn,
                _make_run_id(
                    user_id=user_id,
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    run_label=run_label,
                ),
            )
            if verbose_decisions:
                print_decisions(symbol, result)
            if persist and conn is not None:
                save_replay_result(
                    conn,
                    run_id=run_id,
                    start_account=account,
                    result=result,
                    symbol=symbol,
                    config={
                        "start_date": start_date,
                        "end_date": end_date,
                        "trigger_freq": trigger_freq,
                        "level_chain": level_chain or {},
                        "default_t_qty": default_t_qty,
                        "protected_base_qty": protected_base_qty,
                        "strategy_profile": config.profile,
                        "min_second_leg_bars": config.min_second_leg_bars,
                        "event_freshness_bars": config.event_freshness_bars,
                        "min_divergence_strength": config.min_divergence_strength,
                        "sell_first_min_distance_to_zg_atr": config.sell_first_min_distance_to_zg_atr,
                        "buy_first_max_distance_to_zd_atr": config.buy_first_max_distance_to_zd_atr,
                        "min_expected_edge_after_cost": config.min_expected_edge_after_cost,
                        "expected_edge_atr_multiple": config.expected_edge_atr_multiple,
                        "first_leg_confirmation_bars": config.first_leg_confirmation_bars,
                        "second_leg_confirmation_bars": config.second_leg_confirmation_bars,
                        "min_bars_before_window_end_for_first_leg": config.min_bars_before_window_end_for_first_leg,
                        "observe_only": config.observe_only,
                        "buyback_timeout_bars": config.buyback_timeout_bars,
                        "limit": limit,
                        "feature_cache": _cache_delta(cache_before, feature_cache.stats()),
                        "kline_source": kline_source or "",
                        "adjustflag": adjustflag,
                        "detail_source": detail_source or "",
                        "parent_context": _parent_context_config(parent_context),
                        "auto_parent_context": auto_parent_context and parent_context is None,
                        "run_label": run_label,
                    },
                )
            summaries.append(_summary(symbol, run_id, steps, result, _cache_delta(cache_before, feature_cache.stats())))
    finally:
        if conn is not None:
            conn.close()
        if cache_conn is not None:
            cache_conn.close()

    return summaries


def _profile_overrides(
    *,
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
) -> dict:
    overrides = {}
    if min_second_leg_bars is not None:
        overrides["min_second_leg_bars"] = min_second_leg_bars
    if event_freshness_bars is not None:
        overrides["event_freshness_bars"] = event_freshness_bars
    if min_divergence_strength is not None:
        overrides["min_divergence_strength"] = min_divergence_strength
    if sell_first_min_distance_to_zg_atr is not None:
        overrides["sell_first_min_distance_to_zg_atr"] = sell_first_min_distance_to_zg_atr
    if buy_first_max_distance_to_zd_atr is not None:
        overrides["buy_first_max_distance_to_zd_atr"] = buy_first_max_distance_to_zd_atr
    if min_expected_edge_after_cost is not None:
        overrides["min_expected_edge_after_cost"] = min_expected_edge_after_cost
    if expected_edge_atr_multiple is not None:
        overrides["expected_edge_atr_multiple"] = expected_edge_atr_multiple
    if first_leg_confirmation_bars is not None:
        overrides["first_leg_confirmation_bars"] = first_leg_confirmation_bars
    if second_leg_confirmation_bars is not None:
        overrides["second_leg_confirmation_bars"] = second_leg_confirmation_bars
    if min_bars_before_window_end_for_first_leg is not None:
        overrides["min_bars_before_window_end_for_first_leg"] = min_bars_before_window_end_for_first_leg
    return overrides


def _make_run_id(*, user_id: int, symbol: str, start_date: str, end_date: str, run_label: str = "") -> str:
    raw = f"paper_run_{user_id}_{symbol.replace('.', '')}_{start_date}_{end_date}"
    if run_label:
        raw = f"{raw}_{run_label}"
    return _safe_run_id(raw)


def _safe_run_id(value: str) -> str:
    chars = []
    for char in value.strip():
        if char.isalnum() or char in {"_", "-", "."}:
            chars.append(char)
        elif char.isspace() or char in {":", "/", "\\"}:
            chars.append("_")
    safe = "".join(chars).strip("_")
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe or "paper_run"


def _unique_run_id(conn, base_run_id: str) -> str:
    if conn is None:
        return base_run_id
    if not _run_exists(conn, base_run_id):
        return base_run_id
    suffix = 2
    while _run_exists(conn, f"{base_run_id}_v{suffix}"):
        suffix += 1
    return f"{base_run_id}_v{suffix}"


def _run_exists(conn, run_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM paper_replay_runs WHERE run_id=? LIMIT 1", (run_id,)).fetchone()
    return row is not None


def _account_for_symbol(
    *,
    symbol: str,
    user_id: int,
    initial_cash: float,
    base_qty: int,
    protected_base_qty: int,
    available_qty: int,
    avg_cost: float,
    ref_end_date: str | None = None,
    kline_source: str | None = None,
    adjustflag: str = "2",
) -> PaperAccount:
    last_price = _latest_close(symbol, source=kline_source, adjustflag=adjustflag, end_date=ref_end_date) or avg_cost
    effective_avg_cost = avg_cost if avg_cost > 0 else last_price
    return PaperAccount(
        paper_account_id=f"paper_{user_id}_{symbol.replace('.', '')}",
        user_id=user_id,
        cash=initial_cash,
        positions={
            symbol: PaperPosition(
                symbol=symbol,
                total_qty=base_qty,
                available_qty=available_qty,
                protected_base_qty=protected_base_qty,
                avg_cost=effective_avg_cost,
                last_price=last_price,
            )
        },
    )


def _latest_close(symbol: str, source: str | None = None, adjustflag: str = "2", end_date: str | None = None) -> float:
    rows = query_klines(symbol, "1", end_date=end_date, limit=1, source=source, adjustflag=adjustflag)
    if not rows:
        rows = query_klines(symbol, "5", end_date=end_date, limit=1, source=source, adjustflag=adjustflag)
    if not rows and source is not None:
        rows = query_klines(symbol, "5", end_date=end_date, limit=1)
    if not rows:
        return 0.0
    try:
        return float(rows[-1].get("close") or 0)
    except (TypeError, ValueError):
        return 0.0


def _kline_loader(source: str | None, adjustflag: str):
    def load(symbol: str, freq: str, start_date=None, end_date=None, limit=240):
        return query_klines(
            symbol,
            freq,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            source=source,
            adjustflag=adjustflag,
        )

    return load


def _parent_context(
    *,
    parent_level: str,
    parent_task: str,
    parent_leg_id: str,
    allowed_first_side: str,
    max_cycles: int,
) -> IntradayTParentContext | None:
    if not (allowed_first_side or max_cycles > 0 or parent_task or parent_level):
        return None
    return IntradayTParentContext(
        parent_level=parent_level,
        parent_task=parent_task,
        parent_leg_id=parent_leg_id,
        allowed_first_side=allowed_first_side.upper(),
        max_cycles=max_cycles,
    )


def _parent_context_config(parent: IntradayTParentContext | None) -> dict:
    if parent is None:
        return {}
    return {
        "parent_level": parent.parent_level,
        "parent_task": parent.parent_task,
        "parent_leg_id": parent.parent_leg_id,
        "allowed_first_side": parent.allowed_first_side,
        "max_cycles": parent.max_cycles,
    }


def _summary(symbol: str, run_id: str, steps: list, result: ReplayResult, cache_stats: dict | None = None) -> dict:
    return {
        "run_id": run_id,
        "symbol": symbol,
        "steps": len(steps),
        "feature_cache": cache_stats or {},
        **result.metrics,
    }


def _cache_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    keys = set(before) | set(after) | {"hits", "misses", "size"}
    delta = {}
    for key in keys:
        if key == "size":
            delta[key] = int(after.get(key, 0))
        else:
            delta[key] = int(after.get(key, 0)) - int(before.get(key, 0))
    return delta


def print_summary(summaries: list[dict]) -> None:
    print("\nPaper replay summary")
    print("symbol       steps fills closed open closure maxRisk gross    fees     slip     netT     pnl      cache")
    print("------------ ----- ----- ------ ---- ------- ------- -------- -------- -------- -------- -------- -----------")
    for item in summaries:
        cache = item.get("feature_cache") or {}
        cache_text = f"{cache.get('hits', 0)}/{cache.get('misses', 0)}"
        print(
            f"{item['symbol']:<12} "
            f"{item['steps']:>5} "
            f"{item['filled_count']:>5} "
            f"{item['closed_t_count']:>6} "
            f"{item.get('open_t_count', 0):>4} "
            f"{item['t_closure_rate']:>7.2%} "
            f"{item.get('max_open_risk_bars', 0):>7} "
            f"{item.get('gross_t_pnl', 0):>8.2f} "
            f"{item.get('total_fees', 0):>8.2f} "
            f"{item.get('slippage_cost', 0):>8.2f} "
            f"{item.get('net_t_pnl', 0):>8.2f} "
            f"{item['realized_pnl']:>8.2f} "
            f"{cache_text:>11}"
        )


def print_decisions(symbol: str, result: ReplayResult) -> None:
    print(f"\nDecisions {symbol}")
    fills_by_intent = {fill.intent_id: fill for fill in result.fills}
    for decision in result.decisions:
        evidence = decision.evidence or {}
        latest_event = evidence.get("latest_event") or {}
        divergence = evidence.get("divergence") or {}
        paths = evidence.get("paths") or {}
        as_of = evidence.get("as_of") or (decision.intent.created_at if decision.intent else "")
        event = latest_event.get("code") or latest_event.get("side") or "-"
        bars_since = latest_event.get("bars_since_event", "-")
        direction = divergence.get("direction") or "-"
        strength = _fmt_num(divergence.get("strength"))
        path = paths.get("main") or "-"
        intent_text = ""
        if decision.intent is not None:
            intent_text = f" intent={decision.intent.side} qty={decision.intent.quantity}"
        print(
            f"{as_of} {decision.decision} {decision.reason}"
            f" event={event} bars={bars_since} div={direction}/{strength} path={path}{intent_text}"
        )
        blockers = _signal_blockers(evidence)
        if blockers:
            print(f"  BLOCKERS {', '.join(blockers)}")
        if decision.intent is not None and decision.intent.intent_id in fills_by_intent:
            fill = fills_by_intent[decision.intent.intent_id]
            print(
                f"  FILL {fill.status} {fill.side} qty={fill.quantity} "
                f"price={_fmt_num(fill.fill_price)} at={fill.filled_at} reason={fill.reason or '-'}"
            )


def _signal_blockers(evidence: dict) -> list[str]:
    signals = evidence.get("signals") or {}
    if not isinstance(signals, dict):
        return []
    latest_event = evidence.get("latest_event") or {}
    divergence = evidence.get("divergence") or {}
    if _signal_matched(signals, "waiting_second_leg"):
        if _signal_matched(signals, "first_leg_sell"):
            keys = ["buy_first_trigger", "second_leg_confirmation_ok", "second_leg_timeout"]
        elif _signal_matched(signals, "first_leg_buy"):
            keys = ["sell_first_trigger", "second_leg_confirmation_ok", "second_leg_timeout"]
        else:
            keys = ["second_leg_timeout"]
    elif latest_event.get("side") == "sell" or divergence.get("direction") == "top":
        keys = [
            "fresh_event",
            "sell_first_trigger",
            "first_leg_path_allowed",
            "parent_allows_sell_first",
            "sell_first_position_quality",
            "first_leg_confirmation_ok",
            "expected_edge_after_cost",
            "has_exit_plan",
        ]
    elif latest_event.get("side") == "buy" or divergence.get("direction") == "bottom":
        keys = [
            "fresh_event",
            "buy_first_trigger",
            "first_leg_path_allowed",
            "parent_allows_buy_first",
            "buy_first_position_quality",
            "first_leg_confirmation_ok",
            "expected_edge_after_cost",
            "has_exit_plan",
        ]
    else:
        keys = ["fresh_event", "first_leg_path_allowed", "has_exit_plan"]
    blockers = []
    for key in keys:
        item = signals.get(key) or {}
        if item.get("matched") is False:
            blockers.append(_format_signal_blocker(key, item.get("evidence") or {}))
    return blockers[:4]


def _signal_matched(signals: dict, key: str) -> bool:
    item = signals.get(key) or {}
    return bool(item.get("matched"))


def _format_signal_blocker(key: str, evidence: dict) -> str:
    if key == "fresh_event":
        return f"{key}=false bars={evidence.get('bars_since_event', '-')}>{evidence.get('max_bars', '-')}"
    if key == "first_leg_path_allowed":
        return f"{key}=false path={evidence.get('path', '-')}"
    if key in {"parent_allows_sell_first", "parent_allows_buy_first"}:
        return f"{key}=false allowed={evidence.get('allowed_first_side', '-') or '-'}"
    if key == "sell_first_position_quality":
        return f"{key}=false zg_atr={_fmt_num(evidence.get('distance_to_zg_atr'))}"
    if key == "buy_first_position_quality":
        return f"{key}=false zd_atr={_fmt_num(evidence.get('distance_to_zd_atr'))}"
    if key == "second_leg_timeout":
        return f"{key}=false bars={evidence.get('bars_since_first_leg', '-')}/{evidence.get('timeout_bars', '-')}"
    if key == "second_leg_interval_ok":
        return f"{key}=false bars={evidence.get('bars_since_first_leg', '-')}/{evidence.get('min_second_leg_bars', '-')}"
    if key == "second_leg_confirmation_ok":
        return f"{key}=false event_bars={evidence.get('bars_since_event', '-')}/{evidence.get('confirmation_bars', '-')}"
    if key == "first_leg_confirmation_ok":
        return (
            f"{key}=false event_bars={evidence.get('bars_since_event', '-')}/"
            f"{evidence.get('confirmation_bars', '-')} price_ok={evidence.get('price_confirmed', '-')}"
        )
    if key == "expected_edge_after_cost":
        return (
            f"{key}=false net={_fmt_num(evidence.get('net_edge'))}"
            f"<{_fmt_num(evidence.get('threshold'))}"
        )
    return f"{key}=false"


def _fmt_num(value) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "-"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CT-OS intraday T paper replay pool")
    parser.add_argument("--symbol", nargs="+", required=True, help="Symbols, e.g. sh.603893 sz.000001")
    parser.add_argument("--start", required=True, help="Start datetime/date")
    parser.add_argument("--end", required=True, help="End datetime/date")
    parser.add_argument("--trigger-freq", default="1", help="Replay trigger/fill bar frequency, e.g. 1 or 5")
    parser.add_argument(
        "--level-chain",
        nargs=3,
        metavar=("L0", "L1", "L2"),
        help="Radar level chain, e.g. --level-chain 30 15 5",
    )
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
    parser.add_argument("--run-label", default="", help="Optional replay label appended to the persisted run_id")
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--no-feature-cache-persist", action="store_true")
    parser.add_argument("--verbose-decisions", action="store_true", help="Print every replay decision and fill reason")
    parser.add_argument("--kline-source", choices=["baostock", "qmt", "tdx"], help="Replay trigger/fill kline lake source")
    parser.add_argument("--adjustflag", default="2", help="Replay trigger/fill adjustment flag")
    parser.add_argument(
        "--detail-source",
        choices=["tdx_1m_replay"],
        help="Replay detail data routing. tdx_1m_replay reads level 1 from qmt lake adjustflag=3.",
    )
    parser.add_argument("--parent-level", default="")
    parser.add_argument("--parent-task", default="")
    parser.add_argument("--parent-leg-id", default="")
    parser.add_argument("--parent-allowed-first-side", choices=["BUY", "SELL"], default="")
    parser.add_argument("--parent-max-cycles", type=int, default=0)
    parser.add_argument(
        "--auto-parent-context",
        action="store_true",
        help="Infer parent task/budget from L0 last bi in Radar features.",
    )
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    summaries = await run_replay_pool(
        symbols=args.symbol,
        start_date=args.start,
        end_date=args.end,
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
        run_label=args.run_label,
    )
    print_summary(summaries)


if __name__ == "__main__":
    asyncio.run(async_main())
