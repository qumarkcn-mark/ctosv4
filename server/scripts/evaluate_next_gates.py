#!/usr/bin/env python3
"""Evaluate center-breakout next-gate candidates from persisted CZSC snapshots.

This script is intentionally read-only. It tests the hypothesis that, after a
CZSC center breakout, nearby historical swing clusters can act as the next
pressure / support gate.

Usage examples:
  python -m server.scripts.evaluate_next_gates --symbol sh.600790 --level 30 --show-gates
  python -m server.scripts.evaluate_next_gates --level 30 day --samples 20
  python -m server.scripts.evaluate_next_gates --jsonl reports/next_gate_eval.jsonl
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db.database import DB_PATH
from server.domain.symbols import normalize_symbol
from server.engines.ai_native.czsc_snapshot_service import DEFAULT_COMPUTE_PROFILE
from server.engines.ai_native.structure_view_service import get_structure_view
from server.engines.structure.canonical_structure_service import get_latest_structure


DEFAULT_LEVELS = ["30", "day"]


def evaluate_next_gates(
    *,
    symbols: list[str] | None = None,
    levels: list[str] | None = None,
    limit: int = 200,
    horizon: int = 100,
    min_forward_bars: int = 12,
    gate_zone_pct: float = 0.008,
    cluster_pct: float = 0.012,
) -> dict[str, Any]:
    """Run next-gate evaluation against latest structure snapshots."""
    levels = levels or DEFAULT_LEVELS
    pairs = _load_snapshot_pairs(symbols=symbols, levels=levels, limit=limit)
    cases: list[dict[str, Any]] = []
    skipped = Counter()

    for pair in pairs:
        row = get_latest_structure(symbol=pair["symbol"], level=pair["level"], min_profile=DEFAULT_COMPUTE_PROFILE)
        view = get_structure_view(symbol=pair["symbol"], level=pair["level"], count=1200)
        if not row or not view:
            skipped["missing_view"] += 1
            continue
        klines = list((row.get("snapshot") or {}).get("klines") or [])[-1200:]
        bis = list(view.get("bis") or [])
        centers = list(view.get("centers") or [])
        if len(klines) < 160 or len(bis) < 8 or not centers:
            skipped["insufficient_structure"] += 1
            continue

        for center in centers[-8:]:
            if center.get("end_index") is None:
                skipped["center_missing_index"] += 1
                continue
            if len(klines) - int(center["end_index"]) < min_forward_bars + 2:
                skipped["insufficient_forward"] += 1
                continue
            breakout = _find_breakout_after_center(center, klines)
            if not breakout or len(klines) - breakout["index"] < min_forward_bars:
                skipped["no_breakout"] += 1
                continue
            gate = _nearest_gate(
                direction=breakout["direction"],
                klines=klines,
                bis=bis,
                center=center,
                breakout_price=breakout["price"],
                gate_zone_pct=gate_zone_pct,
                cluster_pct=cluster_pct,
            )
            if not gate:
                skipped["no_gate"] += 1
                continue
            outcome = _evaluate_gate(
                direction=breakout["direction"],
                klines=klines,
                breakout_index=breakout["index"],
                gate=gate,
                center=[_num(center.get("zd")), _num(center.get("zg"))],
                horizon=horizon,
                min_forward_bars=min_forward_bars,
            )
            cases.append(
                {
                    "symbol": pair["symbol"],
                    "level": pair["level"],
                    "center": [_num(center.get("zd")), _num(center.get("zg"))],
                    "center_end": center.get("end_bar_time") or center.get("end_time") or "",
                    "breakout": breakout,
                    "gate": gate,
                    **outcome,
                }
            )

    return {
        "pair_count": len(pairs),
        "case_count": len(cases),
        "skipped": dict(skipped),
        "summary": _summarize(cases),
        "cases": cases,
    }


def current_gate_ladder(
    *,
    symbol: str,
    level: str,
    max_gates: int = 8,
    gate_zone_pct: float = 0.008,
    cluster_pct: float = 0.012,
) -> dict[str, Any] | None:
    """Build current upside / downside gate ladders for a symbol and level."""
    canonical = normalize_symbol(symbol)
    row = get_latest_structure(symbol=canonical, level=level, min_profile=DEFAULT_COMPUTE_PROFILE)
    view = get_structure_view(symbol=canonical, level=level, count=1200)
    if not row or not view:
        return None
    klines = list((row.get("snapshot") or {}).get("klines") or [])[-1200:]
    if not klines:
        return None
    latest_close = _num(klines[-1].get("close"))
    centers = list(view.get("centers") or [])
    active_center = view.get("active_center") or (centers[-1] if centers else {})

    upside = _gate_ladder(
        direction="up",
        klines=klines,
        current_price=latest_close,
        gate_zone_pct=gate_zone_pct,
        cluster_pct=cluster_pct,
        max_gates=max_gates,
    )
    downside = _gate_ladder(
        direction="down",
        klines=klines,
        current_price=latest_close,
        gate_zone_pct=gate_zone_pct,
        cluster_pct=cluster_pct,
        max_gates=max_gates,
    )
    return {
        "symbol": canonical,
        "level": view.get("level") or level,
        "current_price": latest_close,
        "last_time": _bar_time(klines[-1]),
        "active_center": {
            "zd": _num(active_center.get("zd")),
            "zg": _num(active_center.get("zg")),
            "begin": active_center.get("begin_bar_time") or active_center.get("begin_time") or "",
            "end": active_center.get("end_bar_time") or active_center.get("end_time") or "",
        },
        "upside_gates": upside,
        "downside_gates": downside,
    }


def current_gate_focus(
    *,
    symbol: str,
    level: str,
    gate_zone_pct: float = 0.008,
    cluster_pct: float = 0.012,
) -> dict[str, Any] | None:
    """Pick only the gates that matter for the current center state.

    The coach does not need a full ladder. It needs one primary observation
    task, plus at most one fallback gate when price has already left a center.
    """
    ladder = current_gate_ladder(
        symbol=symbol,
        level=level,
        max_gates=4,
        gate_zone_pct=gate_zone_pct,
        cluster_pct=cluster_pct,
    )
    if not ladder:
        return None

    canonical = ladder["symbol"]
    row = get_latest_structure(symbol=canonical, level=level, min_profile=DEFAULT_COMPUTE_PROFILE)
    if not row:
        return None
    klines = list((row.get("snapshot") or {}).get("klines") or [])[-1200:]
    price = _num(ladder.get("current_price"))
    center = ladder.get("active_center") or {}
    zd = _num(center.get("zd"))
    zg = _num(center.get("zg"))
    state = _classify_center_state(price=price, zd=zd, zg=zg)
    overlap = _historical_overlap_gate(
        klines=klines,
        current_price=price,
        gate_zone_pct=gate_zone_pct,
        cluster_pct=cluster_pct,
    )

    upper_edge = _center_edge_gate(center, side="upper")
    lower_edge = _center_edge_gate(center, side="lower")
    upside = list(ladder.get("upside_gates") or [])
    downside = list(ladder.get("downside_gates") or [])
    primary: dict[str, Any] | None = None
    secondary: dict[str, Any] | None = None
    coach_task = ""

    if state == "breakout_up":
        primary = _tag_gate(upside[0], side="upper", role="pressure", reason="价格已离开当前中枢上方，优先观察上方最近历史压力区") if upside else upper_edge
        secondary = _tag_gate(upper_edge, side="lower", role="fallback_support", reason="若5分钟回落，先观察中枢上沿是否承接")
        if primary is upper_edge:
            primary = _tag_gate(primary, side="lower", role="fallback_support", reason="价格已离开当前中枢上方，但上方暂无可靠历史压力区；先观察中枢上沿回踩承接")
        coach_task = "观察5分钟在上方压力区前后的试压、消化、突破回踩或冲高失败。"
    elif state == "breakdown_down":
        primary = _tag_gate(downside[0], side="lower", role="support", reason="价格已跌破当前中枢下方，优先观察下方最近历史支撑区") if downside else lower_edge
        secondary = _tag_gate(lower_edge, side="upper", role="fallback_pressure", reason="若5分钟反抽，先观察中枢下沿是否转为压力")
        if primary is lower_edge:
            primary = _tag_gate(primary, side="upper", role="fallback_pressure", reason="价格已跌破当前中枢下方，但下方暂无可靠历史支撑区；先观察中枢下沿反抽压力")
        coach_task = "观察5分钟在下方支撑区附近的止跌、反抽、跌破后反压或重新拉回中枢。"
    elif state == "testing_upper_edge":
        primary = _tag_gate(overlap or upper_edge, side="upper", role="center_edge", reason="价格仍在中枢内，外部压力暂不优先；先看中枢上沿是否被有效试探")
        secondary = None
        coach_task = "观察5分钟是否能离开中枢上沿；未离开前不把外部压力当作主任务。"
    elif state == "testing_lower_edge":
        primary = _tag_gate(overlap or lower_edge, side="lower", role="center_edge", reason="价格仍在中枢内，外部支撑暂不优先；先看中枢下沿是否守住")
        secondary = None
        coach_task = "观察5分钟是否跌破中枢下沿；未跌破前不把外部支撑当作主任务。"
    else:
        primary = _tag_gate(overlap, side="overlap", role="historical_overlap", reason="价格在中枢内部震荡，但当前区间重合历史关口") if overlap else None
        secondary = None
        coach_task = "价格仍在中枢内部，主任务是等待靠近中枢边界；除非当前位置重合历史关口，否则不强调外部压力/支撑。"

    return {
        **ladder,
        "structure_state": state,
        "historical_overlap": overlap,
        "primary_gate": primary,
        "secondary_gate": secondary,
        "coach_task": coach_task,
    }


def current_coach_gate_focus(
    *,
    symbol: str,
    structure_level: str = "30",
    trigger_level: str = "5",
    gate_zone_pct: float = 0.008,
    cluster_pct: float = 0.012,
) -> dict[str, Any] | None:
    """Compose a coach-facing gate from a structure level and a lower trigger level."""
    structure = current_gate_focus(
        symbol=symbol,
        level=structure_level,
        gate_zone_pct=gate_zone_pct,
        cluster_pct=cluster_pct,
    )
    trigger = current_gate_focus(
        symbol=symbol,
        level=trigger_level,
        gate_zone_pct=gate_zone_pct,
        cluster_pct=cluster_pct,
    )
    if not structure:
        return None
    state = structure.get("structure_state")
    trigger_state = trigger.get("structure_state") if trigger else "unknown"
    primary = structure.get("primary_gate")
    secondary = structure.get("secondary_gate")
    task = structure.get("coach_task") or ""
    reason = "structure_state_only"

    if state == "breakout_up" and trigger_state in {"breakdown_down", "testing_lower_edge"}:
        pullback_support = _first_gate(structure.get("downside_gates")) or structure.get("secondary_gate")
        primary = _tag_gate(
            pullback_support,
            side="lower",
            role="pullback_support",
            reason="大级别已向上出中枢，但低一级正在回落；最大可能的下一步是先观察回落承接",
        )
        secondary = _tag_gate(
            _first_gate(structure.get("upside_gates")),
            side="upper",
            role="next_pressure",
            reason="只有低一级重新转强后，才把上方压力区作为下一关键分支",
        )
        task = "先观察低一级回落是否在下方支撑区承接；承接成立后，再观察上方压力区的试压与消化。"
        reason = "breakout_up_with_lower_level_pullback"
    elif state == "breakdown_down" and trigger_state in {"breakout_up", "testing_upper_edge"}:
        rebound_pressure = _first_gate(structure.get("upside_gates")) or structure.get("secondary_gate")
        primary = _tag_gate(
            rebound_pressure,
            side="upper",
            role="rebound_pressure",
            reason="大级别已跌破中枢，但低一级正在反抽；最大可能的下一步是先观察反抽压力",
        )
        secondary = _tag_gate(
            _first_gate(structure.get("downside_gates")),
            side="lower",
            role="next_support",
            reason="只有低一级反抽失败后，才把下方支撑区作为下一关键分支",
        )
        task = "先观察低一级反抽是否在上方压力区受阻；反抽失败后，再观察下方支撑区的止跌或跌破。"
        reason = "breakdown_down_with_lower_level_rebound"

    return {
        "version": "coach_gate_focus.v1",
        "symbol": structure["symbol"],
        "structure_level": structure_level,
        "trigger_level": trigger_level,
        "current_price": structure.get("current_price"),
        "last_time": structure.get("last_time"),
        "structure_state": state,
        "trigger_state": trigger_state,
        "active_center": structure.get("active_center"),
        "primary_gate": primary,
        "secondary_gate": secondary,
        "coach_task": task,
        "selection_reason": reason,
        "structure_focus": structure,
        "trigger_focus": trigger,
    }


def _load_snapshot_pairs(*, symbols: list[str] | None, levels: list[str], limit: int) -> list[dict[str, str]]:
    normalized_symbols = [normalize_symbol(item) for item in symbols or []]
    level_placeholders = ",".join("?" for _ in levels)
    params: list[Any] = list(levels)
    symbol_clause = ""
    if normalized_symbols:
        symbol_placeholders = ",".join("?" for _ in normalized_symbols)
        symbol_clause = f" AND symbol IN ({symbol_placeholders})"
        params.extend(normalized_symbols)
    params.append(int(limit))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"""
            SELECT symbol, level, MAX(updated_at) AS updated_at
              FROM structure_snapshots
             WHERE engine = 'czsc'
               AND compute_profile = ?
               AND level IN ({level_placeholders})
               {symbol_clause}
             GROUP BY symbol, level
             ORDER BY updated_at DESC
             LIMIT ?
            """,
            [DEFAULT_COMPUTE_PROFILE, *params],
        ).fetchall()
        return [{"symbol": row["symbol"], "level": row["level"]} for row in rows]
    finally:
        conn.close()


def _nearest_gate(
    *,
    direction: str,
    klines: list[dict[str, Any]],
    bis: list[dict[str, Any]],
    center: dict[str, Any],
    breakout_price: float,
    gate_zone_pct: float,
    cluster_pct: float,
) -> dict[str, Any] | None:
    begin = center.get("begin_index")
    if begin is None:
        return None
    begin_index = int(begin)
    zd = _num(center.get("zd"))
    zg = _num(center.get("zg"))
    height = max(abs(zg - zd), breakout_price * 0.01)
    if direction == "up":
        min_price = max(breakout_price * 1.012, zg + height * 0.75)
    else:
        min_price = min(breakout_price * 0.988, zd - height * 0.75)

    candidates: list[tuple[str, float, int, str]] = []
    for bi in bis:
        end_index = bi.get("end_index")
        if end_index is None or int(end_index) >= begin_index:
            continue
        price = _num(bi.get("end_price"))
        if direction == "up" and price >= min_price:
            candidates.append(("bi_endpoint", price, int(end_index), str(bi.get("end_time") or "")))
        if direction == "down" and 0 < price <= min_price:
            candidates.append(("bi_endpoint", price, int(end_index), str(bi.get("end_time") or "")))

    for kind, price, index, time in _local_extrema(klines[:begin_index], direction):
        if direction == "up" and price >= min_price:
            candidates.append((kind, price, index, time))
        if direction == "down" and 0 < price <= min_price:
            candidates.append((kind, price, index, time))

    clusters = _cluster_candidates(candidates, cluster_pct=cluster_pct)
    if not clusters:
        return None
    if direction == "up":
        chosen = min(clusters, key=lambda item: (item["price"] - breakout_price, -item["score"]))
    else:
        chosen = max(clusters, key=lambda item: (item["price"] - breakout_price, item["score"]))
    return _gate_from_cluster(chosen, direction=direction, gate_zone_pct=gate_zone_pct)


def _gate_ladder(
    *,
    direction: str,
    klines: list[dict[str, Any]],
    current_price: float,
    gate_zone_pct: float,
    cluster_pct: float,
    max_gates: int,
) -> list[dict[str, Any]]:
    candidates = [
        item
        for item in _local_extrema(klines, direction)
        if (item[1] > current_price * 1.003 if direction == "up" else 0 < item[1] < current_price * 0.997)
    ]
    clusters = _cluster_candidates(candidates, cluster_pct=cluster_pct)
    if direction == "up":
        clusters = [item for item in clusters if item["price"] > current_price * 1.003]
        clusters.sort(key=lambda item: item["price"])
    else:
        clusters = [item for item in clusters if 0 < item["price"] < current_price * 0.997]
        clusters.sort(key=lambda item: item["price"], reverse=True)
    return [
        {**_gate_from_cluster(item, direction=direction, gate_zone_pct=gate_zone_pct), "rank": rank}
        for rank, item in enumerate(clusters[:max_gates], start=1)
    ]


def _classify_center_state(*, price: float, zd: float, zg: float) -> str:
    if price <= 0 or zd <= 0 or zg <= 0 or zg <= zd:
        return "unknown"
    height = zg - zd
    if price > zg * 1.003:
        return "breakout_up"
    if price < zd * 0.997:
        return "breakdown_down"
    if price >= zg - height * 0.25:
        return "testing_upper_edge"
    if price <= zd + height * 0.25:
        return "testing_lower_edge"
    return "inside_center"


def _center_edge_gate(center: dict[str, Any], *, side: str) -> dict[str, Any] | None:
    price = _num(center.get("zg" if side == "upper" else "zd"))
    if price <= 0:
        return None
    return {
        "type": f"center_{side}_edge",
        "price": round(price, 4),
        "zone": _zone(price, 0.004),
        "source_time": center.get("end") or center.get("begin") or "",
        "hit_count": 1,
        "score": 1,
    }


def _historical_overlap_gate(
    *,
    klines: list[dict[str, Any]],
    current_price: float,
    gate_zone_pct: float,
    cluster_pct: float,
) -> dict[str, Any] | None:
    if current_price <= 0:
        return None
    candidates = _local_extrema(klines, "up") + _local_extrema(klines, "down")
    clusters = _cluster_candidates(candidates, cluster_pct=cluster_pct)
    overlaps = []
    for cluster in clusters:
        gate = _gate_from_cluster(cluster, direction="overlap", gate_zone_pct=gate_zone_pct)
        lower, upper = gate["zone"]
        if lower <= current_price <= upper:
            overlaps.append(gate)
    if not overlaps:
        return None
    return max(overlaps, key=lambda item: (item.get("score") or 0, item.get("hit_count") or 0))


def _tag_gate(gate: dict[str, Any] | None, *, side: str, role: str, reason: str) -> dict[str, Any] | None:
    if not gate:
        return None
    return {**gate, "side": side, "role": role, "reason": reason}


def _first_gate(gates: Any) -> dict[str, Any] | None:
    if isinstance(gates, list) and gates:
        return gates[0]
    return None


def _local_extrema(klines: list[dict[str, Any]], direction: str) -> list[tuple[str, float, int, str]]:
    items: list[tuple[str, float, int, str]] = []
    if len(klines) < 5:
        return items
    for index in range(2, len(klines) - 2):
        window = klines[index - 2 : index + 3]
        if direction == "up":
            value = _num(klines[index].get("high"))
            if value > 0 and value >= max(_num(item.get("high")) for item in window):
                items.append(("swing_high", value, index, _bar_time(klines[index])))
        else:
            value = _num(klines[index].get("low"))
            if value > 0 and value <= min(_num(item.get("low")) for item in window):
                items.append(("swing_low", value, index, _bar_time(klines[index])))
    return items


def _cluster_candidates(
    candidates: list[tuple[str, float, int, str]],
    *,
    cluster_pct: float,
) -> list[dict[str, Any]]:
    if not candidates:
        return []
    candidates = sorted(candidates, key=lambda item: item[1])
    groups: list[list[tuple[str, float, int, str]]] = []
    current: list[tuple[str, float, int, str]] = []
    for item in candidates:
        avg = sum(x[1] for x in current) / len(current) if current else item[1]
        if not current or abs(item[1] - avg) / avg <= cluster_pct:
            current.append(item)
        else:
            groups.append(current)
            current = [item]
    if current:
        groups.append(current)

    clusters = []
    for group in groups:
        prices = [item[1] for item in group]
        kinds = Counter(item[0] for item in group)
        score = len(group) + 2 * int(kinds.get("bi_endpoint", 0))
        last = max(group, key=lambda item: item[2])
        clusters.append(
            {
                "price": sum(prices) / len(prices),
                "hit_count": len(group),
                "score": score,
                "source_index": int(last[2]),
                "source_time": last[3],
                "types": dict(kinds),
            }
        )
    return clusters


def _gate_from_cluster(cluster: dict[str, Any], *, direction: str, gate_zone_pct: float) -> dict[str, Any]:
    price = _num(cluster.get("price"))
    return {
        "type": f"cluster_{direction}",
        "price": round(price, 4),
        "zone": _zone(price, gate_zone_pct),
        "source_index": int(cluster.get("source_index") or 0),
        "source_time": cluster.get("source_time") or "",
        "hit_count": int(cluster.get("hit_count") or 0),
        "score": int(cluster.get("score") or 0),
        "types": cluster.get("types") or {},
    }


def _find_breakout_after_center(center: dict[str, Any], klines: list[dict[str, Any]]) -> dict[str, Any] | None:
    end = center.get("end_index")
    if end is None:
        return None
    zd = _num(center.get("zd"))
    zg = _num(center.get("zg"))
    if zd <= 0 or zg <= 0:
        return None
    start = int(end) + 1
    for index in range(start, min(len(klines), start + 41)):
        close = _num(klines[index].get("close"))
        if close > zg * 1.003:
            return {"direction": "up", "index": index, "price": close, "time": _bar_time(klines[index])}
        if close < zd * 0.997:
            return {"direction": "down", "index": index, "price": close, "time": _bar_time(klines[index])}
    return None


def _evaluate_gate(
    *,
    direction: str,
    klines: list[dict[str, Any]],
    breakout_index: int,
    gate: dict[str, Any],
    center: list[float],
    horizon: int,
    min_forward_bars: int,
) -> dict[str, Any]:
    forward = klines[breakout_index + 1 : breakout_index + 1 + horizon]
    if len(forward) < min_forward_bars:
        return {"outcome": "insufficient_forward"}
    lower, upper = gate["zone"]
    center_zd, center_zg = center
    touch_index = None
    for offset, bar in enumerate(forward, start=1):
        if direction == "up" and _num(bar.get("high")) >= lower:
            touch_index = offset
            break
        if direction == "down" and _num(bar.get("low")) <= upper:
            touch_index = offset
            break
    if touch_index is None:
        return {"outcome": "not_touched", "bars_to_touch": None}

    after_touch = forward[touch_index - 1 :]
    if direction == "up":
        broke = any(_num(bar.get("close")) > upper for bar in after_touch)
        rejected = any(_num(bar.get("close")) < lower * 0.985 for bar in after_touch[:15])
        center_fail = any(_num(bar.get("close")) < center_zg for bar in after_touch[:35])
        flip = _support_flip_confirmed(after_touch, lower=lower, upper=upper, direction=direction) if broke else False
    else:
        broke = any(_num(bar.get("close")) < lower for bar in after_touch)
        rejected = any(_num(bar.get("close")) > upper * 1.015 for bar in after_touch[:15])
        center_fail = any(_num(bar.get("close")) > center_zd for bar in after_touch[:35])
        flip = _support_flip_confirmed(after_touch, lower=lower, upper=upper, direction=direction) if broke else False

    outcome = "touched_then_broke" if broke else ("touched_rejected" if rejected else "touched_absorbing")
    return {
        "outcome": outcome,
        "bars_to_touch": touch_index,
        "flip_confirmed": flip,
        "center_fail_after_touch": center_fail,
    }


def _support_flip_confirmed(after_touch: list[dict[str, Any]], *, lower: float, upper: float, direction: str) -> bool:
    if direction == "up":
        break_index = next((index for index, bar in enumerate(after_touch) if _num(bar.get("close")) > upper), None)
        if break_index is None:
            return False
        tail = after_touch[break_index + 1 :]
        return any(lower <= _num(bar.get("low")) <= upper and _num(bar.get("close")) >= lower for bar in tail[:35])
    break_index = next((index for index, bar in enumerate(after_touch) if _num(bar.get("close")) < lower), None)
    if break_index is None:
        return False
    tail = after_touch[break_index + 1 :]
    return any(lower <= _num(bar.get("high")) <= upper and _num(bar.get("close")) <= upper for bar in tail[:35])


def _summarize(cases: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = Counter(item.get("outcome") for item in cases)
    touched = [item for item in cases if item.get("bars_to_touch")]
    flips = [item for item in touched if item.get("flip_confirmed")]
    by_level: dict[str, dict[str, Any]] = {}
    for level in sorted({str(item.get("level")) for item in cases}):
        level_cases = [item for item in cases if str(item.get("level")) == level]
        level_touched = [item for item in level_cases if item.get("bars_to_touch")]
        by_level[level] = {
            "cases": len(level_cases),
            "touch_rate": _ratio(len(level_touched), len(level_cases)),
            "outcomes": dict(Counter(item.get("outcome") for item in level_cases)),
        }
    bars_to_touch = [int(item["bars_to_touch"]) for item in touched]
    return {
        "outcomes": dict(outcomes),
        "touch_rate": _ratio(len(touched), len(cases)),
        "flip_of_touched": _ratio(len(flips), len(touched)),
        "bars_to_touch_median": median(bars_to_touch) if bars_to_touch else None,
        "bars_to_touch_min": min(bars_to_touch) if bars_to_touch else None,
        "bars_to_touch_max": max(bars_to_touch) if bars_to_touch else None,
        "by_level": by_level,
    }


def _print_summary(result: dict[str, Any], *, samples: int) -> None:
    print(f"pairs={result['pair_count']} cases={result['case_count']} skipped={result['skipped']}")
    summary = result["summary"]
    print(f"outcomes={summary['outcomes']}")
    print(
        "touch_rate={touch_rate} flip_of_touched={flip_of_touched} "
        "bars_to_touch_median={bars_to_touch_median}".format(**summary)
    )
    for level, item in summary["by_level"].items():
        print(f"level={level} cases={item['cases']} touch_rate={item['touch_rate']} outcomes={item['outcomes']}")
    if samples > 0:
        print("\nsamples:")
        sample_cases = _sample_cases(result["cases"], samples)
        for item in sample_cases:
            print(json.dumps(item, ensure_ascii=False, separators=(",", ":")))


def _print_gate_ladder(ladder: dict[str, Any]) -> None:
    center = ladder.get("active_center") or {}
    print(
        f"\ncurrent gates {ladder['symbol']} {ladder['level']} "
        f"price={ladder['current_price']} time={ladder['last_time']} "
        f"center=({center.get('zd')}, {center.get('zg')})"
    )
    print("upside:")
    for item in ladder["upside_gates"]:
        print(f"  {item['rank']}. {item['zone'][0]}-{item['zone'][1]} price={item['price']} hits={item['hit_count']}")
    print("downside:")
    for item in ladder["downside_gates"]:
        print(f"  {item['rank']}. {item['zone'][0]}-{item['zone'][1]} price={item['price']} hits={item['hit_count']}")


def _print_gate_focus(focus: dict[str, Any]) -> None:
    center = focus.get("active_center") or {}
    print(
        f"\ncurrent focus {focus['symbol']} {focus['level']} "
        f"price={focus['current_price']} time={focus['last_time']} "
        f"state={focus.get('structure_state')} center=({center.get('zd')}, {center.get('zg')})"
    )
    print(f"primary={json.dumps(focus.get('primary_gate'), ensure_ascii=False, separators=(',', ':'))}")
    print(f"secondary={json.dumps(focus.get('secondary_gate'), ensure_ascii=False, separators=(',', ':'))}")
    print(f"task={focus.get('coach_task')}")


def _print_coach_gate_focus(focus: dict[str, Any]) -> None:
    print(
        f"\ncoach focus {focus['symbol']} structure={focus['structure_level']} trigger={focus['trigger_level']} "
        f"price={focus['current_price']} time={focus['last_time']} "
        f"state={focus.get('structure_state')} trigger_state={focus.get('trigger_state')} "
        f"reason={focus.get('selection_reason')}"
    )
    print(f"primary={json.dumps(focus.get('primary_gate'), ensure_ascii=False, separators=(',', ':'))}")
    print(f"secondary={json.dumps(focus.get('secondary_gate'), ensure_ascii=False, separators=(',', ':'))}")
    print(f"task={focus.get('coach_task')}")


def _sample_cases(cases: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for outcome in ("touched_rejected", "touched_absorbing", "touched_then_broke", "not_touched"):
        samples.extend([item for item in cases if item.get("outcome") == outcome][: max(1, limit // 4)])
    return samples[:limit]


def _write_jsonl(path: str, cases: list[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as file:
        for item in cases:
            file.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def _zone(price: float, pct: float) -> list[float]:
    return [round(price * (1 - pct), 4), round(price * (1 + pct), 4)]


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 3) if b else None


def _bar_time(bar: dict[str, Any]) -> str:
    return str(bar.get("time") or bar.get("date") or "")


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate CZSC center-breakout next-gate candidates")
    parser.add_argument("--symbol", nargs="*", help="Optional symbols, e.g. sh.600790 sz.300124")
    parser.add_argument("--level", nargs="*", default=DEFAULT_LEVELS, help="Levels to evaluate, default: 30 day")
    parser.add_argument("--limit", type=int, default=200, help="Max latest snapshot symbol/level pairs")
    parser.add_argument("--horizon", type=int, default=100, help="Forward bars used to evaluate touch/break/flip")
    parser.add_argument("--samples", type=int, default=16, help="Number of sample cases to print")
    parser.add_argument("--jsonl", help="Optional path to write evaluated cases as JSONL")
    parser.add_argument("--show-gates", action="store_true", help="Print current gate ladder for each requested symbol/level")
    parser.add_argument("--show-focus", action="store_true", help="Print current structure-state driven gate focus")
    parser.add_argument("--show-coach-focus", action="store_true", help="Print coach gate focus using structure level + 5m trigger")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = evaluate_next_gates(
        symbols=args.symbol,
        levels=args.level,
        limit=args.limit,
        horizon=args.horizon,
    )
    _print_summary(result, samples=args.samples)
    if args.jsonl:
        _write_jsonl(args.jsonl, result["cases"])
        print(f"\nwrote {len(result['cases'])} cases to {args.jsonl}")
    if args.show_gates:
        for symbol in args.symbol or []:
            for level in args.level:
                ladder = current_gate_ladder(symbol=symbol, level=level)
                if ladder:
                    _print_gate_ladder(ladder)
    if args.show_focus:
        for symbol in args.symbol or []:
            for level in args.level:
                focus = current_gate_focus(symbol=symbol, level=level)
                if focus:
                    _print_gate_focus(focus)
    if args.show_coach_focus:
        structure_levels = [level for level in args.level if level != "5"] or ["30"]
        for symbol in args.symbol or []:
            for level in structure_levels:
                focus = current_coach_gate_focus(symbol=symbol, structure_level=level, trigger_level="5")
                if focus:
                    _print_coach_gate_focus(focus)


if __name__ == "__main__":
    main()
