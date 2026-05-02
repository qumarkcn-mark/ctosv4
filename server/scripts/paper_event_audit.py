#!/usr/bin/env python3
"""Audit paper replay buy/sell points against local 1m klines."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db.database import get_connection
from server.db.kline_lake import query_klines


def build_event_audit(
    *,
    run_id: str,
    symbol: str | None = None,
    before: int = 3,
    after: int = 3,
    kline_source: str = "qmt",
    adjustflag: str = "3",
) -> list[dict[str, Any]]:
    """Build audit rows for decisions that created a position event."""
    conn = get_connection()
    try:
        rows = _load_position_decisions(conn, run_id=run_id, symbol=symbol)
    finally:
        conn.close()
    return build_event_audit_from_rows(
        rows,
        before=before,
        after=after,
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


def build_event_audit_from_rows(
    rows: list[dict[str, Any]],
    *,
    before: int = 3,
    after: int = 3,
    kline_loader: Callable[[str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        evidence = _json(row.get("evidence_json"))
        position_event = evidence.get("position_event") or {}
        if not position_event:
            continue
        symbol = str(row.get("symbol") or evidence.get("symbol") or "")
        as_of = str(row.get("as_of") or evidence.get("as_of") or "")
        day = as_of[:10]
        klines = kline_loader(symbol, day) if symbol and day else []
        context = _kline_context(klines, as_of, before=before, after=after)
        result.append(
            {
                "run_id": row.get("run_id") or "",
                "symbol": symbol,
                "as_of": as_of,
                "decision": row.get("decision") or "",
                "reason": row.get("reason") or "",
                "event": {
                    "name": position_event.get("name") or "",
                    "side": position_event.get("side") or "",
                    "latest_code": (evidence.get("latest_event") or {}).get("code") or "",
                    "latest_side": (evidence.get("latest_event") or {}).get("side") or "",
                    "bars_since_event": (evidence.get("latest_event") or {}).get("bars_since_event"),
                    "divergence_direction": (evidence.get("divergence") or {}).get("direction") or "",
                    "divergence_strength": (evidence.get("divergence") or {}).get("strength"),
                    "path": (evidence.get("paths") or {}).get("main") or "",
                },
                "fill": {
                    "side": row.get("fill_side") or "",
                    "filled_at": row.get("filled_at") or "",
                    "fill_price": row.get("fill_price"),
                    "fill_status": row.get("fill_status") or "",
                },
                "audit_flags": _audit_flags(row, evidence, context),
                "klines": context,
            }
        )
    return result


def print_event_audit(items: list[dict[str, Any]]) -> None:
    print("Paper event audit")
    if not items:
        print("  -")
        return
    for item in items:
        event = item["event"]
        fill = item["fill"]
        flags = ",".join(item["audit_flags"]) or "ok"
        print(
            f"\n{item['symbol']} {item['as_of']} {event['name']} "
            f"decision={item['decision']} reason={item['reason']} flags={flags}"
        )
        print(
            f"  event code={event['latest_code'] or '-'} side={event['latest_side'] or '-'} "
            f"bars={event['bars_since_event']} div={event['divergence_direction']}/{event['divergence_strength']} "
            f"path={event['path'] or '-'}"
        )
        print(
            f"  fill side={fill['side'] or '-'} at={fill['filled_at'] or '-'} "
            f"price={_fmt(fill['fill_price'])} status={fill['fill_status'] or '-'}"
        )
        print("  klines")
        for kline in item["klines"]:
            marker = "*" if kline.get("is_signal_bar") else " "
            fill_marker = "F" if kline.get("is_fill_bar") else " "
            print(
                f"   {marker}{fill_marker} {kline['date']} "
                f"O={_fmt(kline['open'])} H={_fmt(kline['high'])} "
                f"L={_fmt(kline['low'])} C={_fmt(kline['close'])} V={_fmt(kline.get('volume'))}"
            )


def _load_position_decisions(conn, *, run_id: str, symbol: str | None) -> list[dict[str, Any]]:
    conditions = ["d.run_id = ?", "d.evidence_json LIKE ?"]
    params: list[Any] = [run_id, '%"position_event"%']
    if symbol:
        conditions.append("d.symbol = ?")
        params.append(symbol)
    rows = conn.execute(
        f"""
        SELECT d.run_id, d.symbol, d.as_of, d.decision, d.reason, d.intent_id, d.evidence_json,
               f.side AS fill_side, f.filled_at, f.fill_price, f.fill_status
          FROM paper_decisions d
          LEFT JOIN paper_fills f ON f.run_id = d.run_id AND f.intent_id = d.intent_id
         WHERE {" AND ".join(conditions)}
         ORDER BY d.as_of ASC, d.decision_id ASC
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _kline_context(klines: list[dict[str, Any]], as_of: str, *, before: int, after: int) -> list[dict[str, Any]]:
    dates = [str(item.get("date") or "") for item in klines]
    try:
        index = dates.index(as_of)
    except ValueError:
        return []
    start = max(0, index - before)
    end = min(len(klines), index + after + 1)
    context = []
    fill_index = index + 1 if index + 1 < len(klines) else -1
    for idx, item in enumerate(klines[start:end], start=start):
        context.append(
            {
                "date": item.get("date") or "",
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "close": item.get("close"),
                "volume": item.get("volume"),
                "is_signal_bar": idx == index,
                "is_fill_bar": idx == fill_index,
            }
        )
    return context


def _audit_flags(row: dict[str, Any], evidence: dict[str, Any], context: list[dict[str, Any]]) -> list[str]:
    flags = []
    signals = evidence.get("signals") or {}
    fresh = (signals.get("fresh_event") or {}).get("matched")
    if fresh is False:
        flags.append("stale_event")
    if row.get("intent_id") and not row.get("filled_at"):
        flags.append("missing_fill")
    if row.get("filled_at") and context:
        fill_bars = [item for item in context if item.get("is_fill_bar")]
        if fill_bars and row.get("filled_at") != fill_bars[0].get("date"):
            flags.append("fill_not_next_bar")
    return flags


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


def _fmt(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "-"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit CT-OS paper buy/sell points against 1m klines")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--symbol")
    parser.add_argument("--before", type=int, default=3)
    parser.add_argument("--after", type=int, default=3)
    parser.add_argument("--kline-source", default="qmt")
    parser.add_argument("--adjustflag", default="3")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    items = build_event_audit(
        run_id=args.run_id,
        symbol=args.symbol,
        before=args.before,
        after=args.after,
        kline_source=args.kline_source,
        adjustflag=args.adjustflag,
    )
    print_event_audit(items)


if __name__ == "__main__":
    main()
