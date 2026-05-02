#!/usr/bin/env python3
"""Summarize paper replay decisions and no-trade reasons."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db.database import get_connection


SELL_FIRST_BLOCKER_KEYS = (
    "fresh_event",
    "sell_first_trigger",
    "has_exit_plan",
    "first_leg_path_allowed",
    "parent_allows_sell_first",
    "sell_first_position_quality",
    "first_leg_confirmation_ok",
    "expected_edge_after_cost",
)
BUY_FIRST_BLOCKER_KEYS = (
    "fresh_event",
    "buy_first_trigger",
    "has_exit_plan",
    "first_leg_path_allowed",
    "parent_allows_buy_first",
    "buy_first_position_quality",
    "first_leg_confirmation_ok",
    "expected_edge_after_cost",
)
BUYBACK_BLOCKER_KEYS = (
    "second_leg_interval_ok",
    "second_leg_confirmation_ok",
    "fresh_event",
    "buy_first_trigger",
    "second_leg_timeout",
)
SELLBACK_BLOCKER_KEYS = (
    "second_leg_interval_ok",
    "second_leg_confirmation_ok",
    "fresh_event",
    "sell_first_trigger",
    "second_leg_timeout",
)


def build_decision_report(
    *,
    symbol: str | None = None,
    run_id: str | None = None,
    run_ids: list[str] | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    conn = get_connection()
    try:
        rows = _load_decisions(conn, symbol=symbol, run_id=run_id, run_ids=run_ids, limit=limit)
        runs = _load_runs(conn, symbol=symbol, run_id=run_id, run_ids=run_ids)
    finally:
        conn.close()
    return summarize_decisions(rows, runs)


def summarize_decisions(rows: list[dict[str, Any]], runs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    reason_counts = Counter()
    decision_counts = Counter()
    event_side_counts = Counter()
    path_counts = Counter()
    pattern_counts = Counter()
    blocker_counts = Counter()
    position_event_counts = Counter()
    symbol_counters: dict[str, Counter] = defaultdict(Counter)
    bars_since_values: list[int] = []
    divergence_values: list[float] = []

    for row in rows:
        symbol = str(row.get("symbol") or "")
        reason = row.get("reason") or ""
        decision = row.get("decision") or ""
        reason_counts[reason] += 1
        decision_counts[decision] += 1
        if symbol:
            symbol_counters[symbol]["decisions"] += 1
            symbol_counters[symbol][f"decision:{decision}"] += 1
            symbol_counters[symbol][f"reason:{reason}"] += 1
        evidence = _json(row.get("evidence_json"))
        latest_event = evidence.get("latest_event") or {}
        divergence = evidence.get("divergence") or {}
        paths = evidence.get("paths") or {}
        position_event = evidence.get("position_event") or {}
        position_event_name = position_event.get("name")
        if position_event_name:
            position_event_counts[str(position_event_name)] += 1
        event_side = latest_event.get("side")
        if event_side:
            event_side_counts[str(event_side)] += 1
        if latest_event.get("bars_since_event") is not None:
            try:
                bars_since_values.append(int(latest_event.get("bars_since_event")))
            except (TypeError, ValueError):
                pass
        if divergence.get("strength") is not None:
            try:
                divergence_values.append(float(divergence.get("strength")))
            except (TypeError, ValueError):
                pass
        if paths.get("main"):
            path_counts[str(paths.get("main"))] += 1
        for tag in evidence.get("pattern_tags") or []:
            pattern_counts[str(tag)] += 1
        blockers = _signal_blockers(evidence)
        blocker_counts.update(blockers)
        for blocker in blockers:
            if symbol:
                symbol_counters[symbol][f"blocker:{blocker}"] += 1

    return {
        "run_count": len(runs or []),
        "decision_count": len(rows),
        "decision_counts": dict(decision_counts),
        "reason_counts": dict(reason_counts),
        "blocker_counts": dict(blocker_counts),
        "position_event_counts": dict(position_event_counts),
        "event_side_counts": dict(event_side_counts),
        "path_counts": dict(path_counts),
        "pattern_counts": dict(pattern_counts),
        "bars_since_event": _stats(bars_since_values),
        "divergence_strength": _stats(divergence_values),
        "runs": _run_summaries(runs or []),
        "symbols": _symbol_summaries(symbol_counters, runs or []),
    }


def print_report(report: dict[str, Any]) -> None:
    print("Paper decision report")
    print(f"runs: {report['run_count']}  decisions: {report['decision_count']}")
    _print_counter("decisions", report["decision_counts"])
    _print_counter("reasons", report["reason_counts"])
    _print_counter("blockers", report["blocker_counts"])
    _print_counter("position_events", report["position_event_counts"])
    _print_counter("paths", report["path_counts"])
    _print_counter("patterns", report["pattern_counts"])
    print(f"bars_since_event: {report['bars_since_event']}")
    print(f"divergence_strength: {report['divergence_strength']}")
    if report["symbols"]:
        print("\nsymbols")
        for summary in report["symbols"][:10]:
            blockers = ", ".join(f"{k}={v}" for k, v in summary["top_blockers"].items()) or "-"
            print(
                f"{summary['symbol']} decisions={summary['decisions']} "
                f"fills={summary.get('filled_count', 0)} closed={summary.get('closed_t_count', 0)} "
                f"open={summary.get('open_t_count', 0)} maxRisk={summary.get('max_open_risk_bars', 0)} "
                f"netT={summary.get('net_t_pnl', 0)} fees={summary.get('total_fees', 0)} "
                f"slip={summary.get('slippage_cost', 0)} pnl={summary.get('realized_pnl', 0)} blockers={blockers}"
            )
    if report["runs"]:
        print("\nruns")
        for run in report["runs"][:10]:
            print(
                f"{run['run_id']} {run['symbol']} {run['status']} "
                f"fills={run.get('filled_count', 0)} closed={run.get('closed_t_count', 0)} "
                f"open={run.get('open_t_count', 0)} maxRisk={run.get('max_open_risk_bars', 0)} "
                f"netT={run.get('net_t_pnl', 0)} fees={run.get('total_fees', 0)} "
                f"slip={run.get('slippage_cost', 0)} pnl={run.get('realized_pnl', 0)}"
            )


def _load_decisions(
    conn,
    *,
    symbol: str | None,
    run_id: str | None,
    run_ids: list[str] | None = None,
    limit: int,
) -> list[dict[str, Any]]:
    conditions = []
    params: list[Any] = []
    if symbol:
        conditions.append("symbol = ?")
        params.append(symbol)
    if run_id:
        conditions.append("run_id = ?")
        params.append(run_id)
    if run_ids:
        placeholders = ",".join("?" for _ in run_ids)
        conditions.append(f"run_id IN ({placeholders})")
        params.extend(run_ids)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"""
        SELECT run_id, symbol, as_of, decision, decision_status, reason, evidence_json
          FROM paper_decisions
          {where}
         ORDER BY created_at DESC
         LIMIT ?
        """,
        (*params, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_runs(
    conn,
    *,
    symbol: str | None,
    run_id: str | None,
    run_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    conditions = []
    params: list[Any] = []
    if symbol:
        conditions.append("symbol = ?")
        params.append(symbol)
    if run_id:
        conditions.append("run_id = ?")
        params.append(run_id)
    if run_ids:
        placeholders = ",".join("?" for _ in run_ids)
        conditions.append(f"run_id IN ({placeholders})")
        params.extend(run_ids)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"""
        SELECT run_id, symbol, status, metrics_json
          FROM paper_replay_runs
          {where}
         ORDER BY started_at DESC
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _run_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        metrics = _json(row.get("metrics_json"))
        result.append({**row, **metrics})
    return result


def _symbol_summaries(symbol_counters: dict[str, Counter], runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    run_metrics: dict[str, Counter] = defaultdict(Counter)
    for row in runs:
        symbol = str(row.get("symbol") or "")
        if not symbol:
            continue
        metrics = _json(row.get("metrics_json"))
        run_metrics[symbol]["run_count"] += 1
        for key in ("filled_count", "closed_t_count"):
            try:
                run_metrics[symbol][key] += int(metrics.get(key) or 0)
            except (TypeError, ValueError):
                pass
        for key in ("open_t_count", "normal_second_leg_count", "forced_second_leg_count", "second_leg_watch_count"):
            try:
                run_metrics[symbol][key] += int(metrics.get(key) or 0)
            except (TypeError, ValueError):
                pass
        try:
            run_metrics[symbol]["max_open_risk_bars"] = max(
                int(run_metrics[symbol].get("max_open_risk_bars", 0)),
                int(metrics.get("max_open_risk_bars") or 0),
            )
        except (TypeError, ValueError):
            pass
        try:
            run_metrics[symbol]["realized_pnl"] += float(metrics.get("realized_pnl") or 0)
        except (TypeError, ValueError):
            pass
        for key in ("gross_t_pnl", "spread_t_pnl", "total_fees", "slippage_cost", "net_t_pnl"):
            try:
                run_metrics[symbol][key] += float(metrics.get(key) or 0)
            except (TypeError, ValueError):
                pass

    symbols = sorted(set(symbol_counters) | set(run_metrics))
    summaries = []
    for symbol in symbols:
        counters = symbol_counters.get(symbol, Counter())
        metrics = run_metrics.get(symbol, Counter())
        blockers = Counter(
            {key.removeprefix("blocker:"): value for key, value in counters.items() if key.startswith("blocker:")}
        )
        summaries.append(
            {
                "symbol": symbol,
                "decisions": int(counters.get("decisions", 0)),
                "run_count": int(metrics.get("run_count", 0)),
                "filled_count": int(metrics.get("filled_count", 0)),
                "closed_t_count": int(metrics.get("closed_t_count", 0)),
                "open_t_count": int(metrics.get("open_t_count", 0)),
                "normal_second_leg_count": int(metrics.get("normal_second_leg_count", 0)),
                "forced_second_leg_count": int(metrics.get("forced_second_leg_count", 0)),
                "second_leg_watch_count": int(metrics.get("second_leg_watch_count", 0)),
                "max_open_risk_bars": int(metrics.get("max_open_risk_bars", 0)),
                "realized_pnl": round(float(metrics.get("realized_pnl", 0)), 4),
                "gross_t_pnl": round(float(metrics.get("gross_t_pnl", 0)), 4),
                "spread_t_pnl": round(float(metrics.get("spread_t_pnl", 0)), 4),
                "total_fees": round(float(metrics.get("total_fees", 0)), 4),
                "slippage_cost": round(float(metrics.get("slippage_cost", 0)), 4),
                "net_t_pnl": round(float(metrics.get("net_t_pnl", 0)), 4),
                "top_blockers": dict(blockers.most_common(3)),
            }
        )
    return sorted(summaries, key=lambda item: (item["filled_count"], item["decisions"]), reverse=True)


def _signal_blockers(evidence: dict[str, Any]) -> list[str]:
    signals = evidence.get("signals") or {}
    if not isinstance(signals, dict):
        return []
    keys = _context_blocker_keys(evidence)
    if not keys and _is_no_actionable_event(evidence):
        return ["no_actionable_event"]
    if "fresh_event" in keys and _signal_matched(signals, "fresh_event") is False:
        return ["fresh_event"]
    blockers = [key for key in keys if _signal_matched(signals, key) is False]
    if not blockers and _is_no_actionable_event(evidence):
        return ["no_actionable_event"]
    return blockers


def _context_blocker_keys(evidence: dict[str, Any]) -> tuple[str, ...]:
    signals = evidence.get("signals") or {}
    latest_event = evidence.get("latest_event") or {}
    divergence = evidence.get("divergence") or {}
    if _signal_matched(signals, "waiting_second_leg") is True:
        if _signal_matched(signals, "second_leg_buyback_side") is True:
            return BUYBACK_BLOCKER_KEYS
        if _signal_matched(signals, "second_leg_sellback_side") is True:
            return SELLBACK_BLOCKER_KEYS
    side = latest_event.get("side")
    direction = divergence.get("direction")
    if side == "sell" or direction == "top":
        return SELL_FIRST_BLOCKER_KEYS
    if side == "buy" or direction == "bottom":
        return BUY_FIRST_BLOCKER_KEYS
    return ()


def _is_no_actionable_event(evidence: dict[str, Any]) -> bool:
    paths = evidence.get("paths") or {}
    latest_event = evidence.get("latest_event") or {}
    divergence = evidence.get("divergence") or {}
    return (
        paths.get("main") == "NO_EDGE"
        and not latest_event.get("side")
        and not divergence.get("direction")
    )


def _signal_matched(signals: dict[str, Any], key: str) -> bool | None:
    item = signals.get(key)
    if not isinstance(item, dict):
        return None
    matched = item.get("matched")
    return matched if isinstance(matched, bool) else None


def _stats(values: list[float | int]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    values_sorted = sorted(values)
    return {
        "count": len(values),
        "min": round(values_sorted[0], 4),
        "median": round(statistics.median(values_sorted), 4),
        "max": round(values_sorted[-1], 4),
        "avg": round(sum(values_sorted) / len(values_sorted), 4),
    }


def _json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(value)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _print_counter(title: str, data: dict[str, int]) -> None:
    print(f"\n{title}")
    if not data:
        print("  -")
        return
    for key, value in sorted(data.items(), key=lambda item: item[1], reverse=True):
        print(f"  {key}: {value}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize CT-OS paper replay decisions")
    parser.add_argument("--symbol")
    parser.add_argument("--run-id")
    parser.add_argument("--limit", type=int, default=1000)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = build_decision_report(symbol=args.symbol, run_id=args.run_id, limit=args.limit)
    print_report(report)


if __name__ == "__main__":
    main()
