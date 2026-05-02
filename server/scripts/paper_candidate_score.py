#!/usr/bin/env python3
"""Score observe-only intraday T candidates against future 1m bars."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db.database import get_connection
from server.db.kline_lake import query_klines
from server.engines.decision.intraday_t_position import _estimated_round_trip_cost
from server.engines.execution.paper_models import PaperRiskConfig


def build_candidate_score(
    *,
    run_id: str | None = None,
    run_ids: list[str] | None = None,
    run_label: str | None = None,
    symbol: str | None = None,
    horizons: tuple[int, ...] = (3, 5, 8),
    min_net_edge: float = 5.0,
    quantity: int = 100,
    kline_source: str = "qmt",
    adjustflag: str = "3",
) -> dict[str, Any]:
    conn = get_connection()
    try:
        if run_label and not run_ids:
            run_ids = _load_run_ids_by_label(conn, run_label)
        rows = _load_candidate_rows(conn, run_id=run_id, run_ids=run_ids, symbol=symbol)
    finally:
        conn.close()
    return score_candidate_rows(
        rows,
        horizons=horizons,
        min_net_edge=min_net_edge,
        quantity=quantity,
        kline_loader=lambda sym, day: query_klines(
            sym,
            "1",
            start_date=f"{day} 00:00:00",
            end_date=f"{day} 23:59:59",
            limit=1000,
            source=kline_source,  # type: ignore[arg-type]
            adjustflag=adjustflag,
        ),
    )


def score_candidate_rows(
    rows: list[dict[str, Any]],
    *,
    horizons: tuple[int, ...] = (3, 5, 8),
    min_net_edge: float = 5.0,
    quantity: int = 100,
    kline_loader: Callable[[str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    items = []
    for row in rows:
        evidence = _json(row.get("evidence_json"))
        position_event = evidence.get("position_event") or {}
        side = str(position_event.get("side") or "").upper()
        if side not in {"BUY", "SELL"}:
            continue
        symbol = str(row.get("symbol") or evidence.get("symbol") or "")
        as_of = str(row.get("as_of") or evidence.get("as_of") or "")
        day = as_of[:10]
        klines = kline_loader(symbol, day) if symbol and day else []
        item = _score_one(
            row=row,
            evidence=evidence,
            side=side,
            klines=klines,
            horizons=horizons,
            min_net_edge=min_net_edge,
            quantity=quantity,
        )
        if item:
            items.append(item)
    return {
        "candidate_count": len(items),
        "horizons": list(horizons),
        "min_net_edge": min_net_edge,
        "quantity": quantity,
        "summary": _summary(items, horizons),
        "items": items,
    }


def print_candidate_score(report: dict[str, Any], *, limit: int = 20) -> None:
    print("Paper candidate score")
    print(
        f"candidates: {report['candidate_count']}  "
        f"horizons: {','.join(str(item) for item in report['horizons'])}  "
        f"min_net_edge: {report['min_net_edge']}  qty: {report['quantity']}"
    )
    print("\nsummary")
    for horizon, stats in report["summary"].items():
        if not isinstance(stats, dict) or "pass_count" not in stats:
            continue
        print(
            f"  H{horizon}: pass={stats['pass_count']}/{stats['count']} "
            f"rate={stats['pass_rate']:.2%} medianNet={stats['median_net_edge']:.4f} "
            f"bestNet={stats['max_net_edge']:.4f} medianMAE={stats['median_adverse_edge']:.4f}"
        )
    print("\nitems")
    for item in report["items"][:limit]:
        horizon_bits = []
        for horizon in report["horizons"]:
            score = item["scores"].get(str(horizon), {})
            marker = "Y" if score.get("passed") else "n"
            horizon_bits.append(f"H{horizon}:{marker} net={score.get('net_edge', 0)} adv={score.get('adverse_edge', 0)}")
        print(
            f"  {item['symbol']} {item['as_of']} {item['side']} "
            f"{item['reason']} path={item['path'] or '-'} entry={item['entry_price']} "
            + " ".join(horizon_bits)
        )
    if len(report["items"]) > limit:
        print(f"  ... +{len(report['items']) - limit}")


def _score_one(
    *,
    row: dict[str, Any],
    evidence: dict[str, Any],
    side: str,
    klines: list[dict[str, Any]],
    horizons: tuple[int, ...],
    min_net_edge: float,
    quantity: int,
) -> dict[str, Any] | None:
    as_of = str(row.get("as_of") or evidence.get("as_of") or "")
    dates = [str(item.get("date") or "") for item in klines]
    try:
        signal_index = dates.index(as_of)
    except ValueError:
        return None
    entry_index = signal_index + 1
    if entry_index >= len(klines):
        return None
    entry = klines[entry_index]
    entry_price = _float(entry.get("open"))
    if entry_price <= 0:
        return None

    scores = {}
    for horizon in horizons:
        future = klines[entry_index : min(len(klines), entry_index + max(1, horizon))]
        if not future:
            continue
        if side == "SELL":
            favorable_price = min(_float(item.get("low")) for item in future)
            adverse_price = max(_float(item.get("high")) for item in future)
            gross_edge = max(0.0, entry_price - favorable_price) * quantity
            adverse_edge = max(0.0, adverse_price - entry_price) * quantity
            exit_price = favorable_price
        else:
            favorable_price = max(_float(item.get("high")) for item in future)
            adverse_price = min(_float(item.get("low")) for item in future)
            gross_edge = max(0.0, favorable_price - entry_price) * quantity
            adverse_edge = max(0.0, entry_price - adverse_price) * quantity
            exit_price = favorable_price
        estimated_cost = _estimated_round_trip_cost(entry_price, exit_price, quantity, side, PaperRiskConfig())
        net_edge = gross_edge - estimated_cost
        scores[str(horizon)] = {
            "bars": len(future),
            "favorable_price": _round4(favorable_price),
            "adverse_price": _round4(adverse_price),
            "gross_edge": _round4(gross_edge),
            "estimated_cost": _round4(estimated_cost),
            "net_edge": _round4(net_edge),
            "adverse_edge": _round4(adverse_edge),
            "passed": net_edge >= min_net_edge,
        }

    return {
        "run_id": row.get("run_id") or "",
        "symbol": row.get("symbol") or evidence.get("symbol") or "",
        "as_of": as_of,
        "decision": row.get("decision") or "",
        "status": row.get("decision_status") or "",
        "reason": row.get("reason") or "",
        "side": side,
        "entry_at": entry.get("date") or "",
        "entry_price": _round4(entry_price),
        "path": (evidence.get("paths") or {}).get("main") or "",
        "event": {
            "name": (evidence.get("position_event") or {}).get("name") or "",
            "code": (evidence.get("latest_event") or {}).get("code") or "",
            "bars_since_event": (evidence.get("latest_event") or {}).get("bars_since_event"),
            "divergence_direction": (evidence.get("divergence") or {}).get("direction") or "",
            "divergence_strength": (evidence.get("divergence") or {}).get("strength"),
        },
        "scores": scores,
    }


def _summary(items: list[dict[str, Any]], horizons: tuple[int, ...]) -> dict[str, Any]:
    result = {}
    for horizon in horizons:
        key = str(horizon)
        scores = [item["scores"][key] for item in items if key in item["scores"]]
        net_values = [float(score["net_edge"]) for score in scores]
        adverse_values = [float(score["adverse_edge"]) for score in scores]
        pass_count = sum(1 for score in scores if score.get("passed"))
        result[key] = {
            "count": len(scores),
            "pass_count": pass_count,
            "pass_rate": pass_count / len(scores) if scores else 0.0,
            "median_net_edge": _median(net_values),
            "max_net_edge": max(net_values) if net_values else 0.0,
            "median_adverse_edge": _median(adverse_values),
        }
    result["by_symbol"] = dict(Counter(str(item["symbol"]) for item in items))
    return result


def _load_candidate_rows(
    conn,
    *,
    run_id: str | None,
    run_ids: list[str] | None,
    symbol: str | None,
) -> list[dict[str, Any]]:
    conditions = ["evidence_json LIKE ?"]
    params: list[Any] = ['%"position_event"%']
    if run_id:
        conditions.append("run_id = ?")
        params.append(run_id)
    if run_ids:
        placeholders = ",".join("?" for _ in run_ids)
        conditions.append(f"run_id IN ({placeholders})")
        params.extend(run_ids)
    if symbol:
        conditions.append("symbol = ?")
        params.append(symbol)
    rows = conn.execute(
        f"""
        SELECT run_id, symbol, as_of, decision, decision_status, reason, evidence_json
          FROM paper_decisions
         WHERE {" AND ".join(conditions)}
         ORDER BY as_of ASC, decision_id ASC
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _load_run_ids_by_label(conn, run_label: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT run_id
          FROM paper_replay_runs
         WHERE run_id LIKE ?
         ORDER BY run_id ASC
        """,
        (f"%{run_label}%",),
    ).fetchall()
    return [str(row["run_id"]) for row in rows]


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


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _median(values: list[float]) -> float:
    return round(float(statistics.median(values)), 4) if values else 0.0


def _round4(value: float) -> float:
    return round(float(value or 0.0), 4)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score observe-only paper candidates against future 1m bars")
    parser.add_argument("--run-id")
    parser.add_argument("--run-ids", nargs="+")
    parser.add_argument("--run-label", help="Load persisted runs whose run_id contains this label")
    parser.add_argument("--symbol")
    parser.add_argument("--horizon", type=int, nargs="+", default=[3, 5, 8])
    parser.add_argument("--min-net-edge", type=float, default=5.0)
    parser.add_argument("--quantity", type=int, default=100)
    parser.add_argument("--kline-source", default="qmt")
    parser.add_argument("--adjustflag", default="3")
    parser.add_argument("--limit", type=int, default=20)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    report = build_candidate_score(
        run_id=args.run_id,
        run_ids=args.run_ids,
        run_label=args.run_label,
        symbol=args.symbol,
        horizons=tuple(args.horizon),
        min_net_edge=args.min_net_edge,
        quantity=args.quantity,
        kline_source=args.kline_source,
        adjustflag=args.adjustflag,
    )
    print_candidate_score(report, limit=args.limit)


if __name__ == "__main__":
    main()
