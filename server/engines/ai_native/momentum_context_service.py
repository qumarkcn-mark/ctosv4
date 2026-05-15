"""Lightweight momentum context for V5 Kline evidence layers.

This service only reads persisted CZSC snapshots. It does not compute CZSC
inline and it does not produce trading instructions.
"""

from __future__ import annotations

from typing import Any

from server.domain.symbols import normalize_symbol
from server.engines.ai_native.czsc_snapshot_service import (
    DEFAULT_COMPUTE_PROFILE,
    get_latest_snapshot,
)
from server.engines.ai_native.structure_view_service import get_structure_view
from server.engines.structure.structure_key import normalize_freq


def get_momentum_context(
    *,
    symbol: str,
    level: str,
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
    count: int = 1200,
) -> dict[str, Any] | None:
    """Return current-vs-previous same-direction leg momentum facts."""
    canonical = normalize_symbol(symbol)
    normalized_level = normalize_freq(level)
    structure_view = get_structure_view(
        symbol=canonical,
        level=normalized_level,
        compute_profile=compute_profile,
        count=count,
    )
    if not structure_view:
        return None

    snapshot_row = get_latest_snapshot(
        symbol=canonical,
        level=normalized_level,
        compute_profile=compute_profile,
    )
    snapshot = (snapshot_row or {}).get("snapshot") or {}
    klines = list(snapshot.get("klines") or [])
    if count > 0:
        klines = klines[-count:]

    bis = [item for item in structure_view.get("bis") or [] if item.get("is_sure", True)]
    if len(bis) < 2 or len(klines) < 5:
        return _insufficient(structure_view, reason="INSUFFICIENT_STRUCTURE")

    current = bis[-1]
    previous = _previous_same_direction(bis, current)
    if not previous:
        return _insufficient(structure_view, reason="NO_PREVIOUS_SAME_DIRECTION_LEG")

    macd = _macd_histogram([_num(item.get("close")) for item in klines])
    current_leg = _leg_metrics(current, klines, macd)
    previous_leg = _leg_metrics(previous, klines, macd)
    if not current_leg or not previous_leg:
        return _insufficient(structure_view, reason="LEG_OUT_OF_RANGE")

    comparison = _compare_legs(current_leg, previous_leg)
    verdict = _verdict(comparison)
    divergence = _divergence_hint(current_leg, previous_leg, comparison)

    return {
        "version": "momentum_context.v1",
        "symbol": canonical,
        "level": normalized_level,
        "engine": structure_view.get("engine") or "czsc",
        "compute_profile": structure_view.get("compute_profile") or compute_profile,
        "snapshot_id": structure_view.get("snapshot_id") or "",
        "data_signature": structure_view.get("data_signature") or "",
        "data_as_of": structure_view.get("data_as_of") or "",
        "updated_at": structure_view.get("updated_at") or "",
        "status": structure_view.get("status") or "fresh",
        "direction": current_leg["direction"],
        "current_leg": current_leg,
        "previous_leg": previous_leg,
        "comparison": comparison,
        "verdict": verdict,
        "divergence": divergence,
        "risk_boundary": "仅供结构观察，不构成投资建议",
    }


def _previous_same_direction(bis: list[dict[str, Any]], current: dict[str, Any]) -> dict[str, Any] | None:
    is_up = bool(current.get("is_up"))
    for item in reversed(bis[:-1]):
        if bool(item.get("is_up")) == is_up:
            return item
    return None


def _leg_metrics(bi: dict[str, Any], klines: list[dict[str, Any]], macd: list[float]) -> dict[str, Any] | None:
    start_index = _index(bi.get("start_index"))
    end_index = _index(bi.get("end_index"))
    if start_index is None or end_index is None:
        return None
    left = min(start_index, end_index)
    right = max(start_index, end_index)
    if left < 0 or right >= len(klines) or left == right:
        return None

    start_price = _num(bi.get("start_price"))
    end_price = _num(bi.get("end_price"))
    if start_price <= 0 or end_price <= 0:
        return None

    window = klines[left : right + 1]
    macd_window = macd[left : right + 1]
    bar_count = len(window)
    price_change_pct = (end_price - start_price) / start_price * 100
    volume_sum = sum(max(0.0, _num(item.get("volume"))) for item in window)
    volume_avg = volume_sum / bar_count if bar_count else 0.0
    macd_area = sum(abs(item) for item in macd_window)
    slope = (end_price - start_price) / max(1, bar_count - 1)

    return {
        "id": bi.get("id") or "",
        "direction": "up" if bool(bi.get("is_up")) else "down",
        "start_time": bi.get("start_time") or "",
        "end_time": bi.get("end_time") or "",
        "start_timestamp": bi.get("start_timestamp"),
        "end_timestamp": bi.get("end_timestamp"),
        "start_index": start_index,
        "end_index": end_index,
        "start_price": round(start_price, 4),
        "end_price": round(end_price, 4),
        "price_change_pct": round(price_change_pct, 3),
        "bar_count": bar_count,
        "macd_area": round(macd_area, 4),
        "volume_sum": round(volume_sum, 2),
        "volume_avg": round(volume_avg, 2),
        "slope": round(slope, 5),
    }


def _compare_legs(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    current_move = abs(_num(current.get("price_change_pct")))
    previous_move = abs(_num(previous.get("price_change_pct")))
    current_slope = abs(_num(current.get("slope")))
    previous_slope = abs(_num(previous.get("slope")))
    current_area = abs(_num(current.get("macd_area")))
    previous_area = abs(_num(previous.get("macd_area")))
    current_volume = abs(_num(current.get("volume_sum")))
    previous_volume = abs(_num(previous.get("volume_sum")))
    return {
        "price_change_ratio": _ratio(current_move, previous_move),
        "macd_area_ratio": _ratio(current_area, previous_area),
        "volume_ratio": _ratio(current_volume, previous_volume),
        "slope_ratio": _ratio(current_slope, previous_slope),
        "price_makes_extreme": _price_makes_extreme(current, previous),
    }


def _verdict(comparison: dict[str, Any]) -> dict[str, Any]:
    area_ratio = _num(comparison.get("macd_area_ratio"))
    slope_ratio = _num(comparison.get("slope_ratio"))
    volume_ratio = _num(comparison.get("volume_ratio"))
    if area_ratio <= 0 or slope_ratio <= 0:
        return {"state": "insufficient_data", "label": "不足判断", "confidence": 0.0}

    score = area_ratio * 0.45 + slope_ratio * 0.35 + volume_ratio * 0.20
    if area_ratio >= 1.12 and slope_ratio >= 0.92 and score >= 1.05:
        return {"state": "strengthening", "label": "力度增强", "confidence": _confidence(score - 1.0)}
    if area_ratio <= 0.82 or slope_ratio <= 0.78:
        weakness = max(1.0 - area_ratio, 1.0 - slope_ratio)
        return {"state": "weakening", "label": "力度衰减", "confidence": _confidence(weakness)}
    return {"state": "neutral", "label": "力度接近", "confidence": 0.45}


def _divergence_hint(
    current: dict[str, Any],
    previous: dict[str, Any],
    comparison: dict[str, Any],
) -> dict[str, Any]:
    price_makes_extreme = bool(comparison.get("price_makes_extreme"))
    area_ratio = _num(comparison.get("macd_area_ratio"))
    slope_ratio = _num(comparison.get("slope_ratio"))
    is_weaker = area_ratio > 0 and slope_ratio > 0 and (area_ratio <= 0.82 or slope_ratio <= 0.78)
    if not (price_makes_extreme and is_weaker):
        return {"type": "", "is_valid": False, "reason": "未同时满足价格创新极值与力度衰减"}
    div_type = "顶背驰" if current.get("direction") == "up" else "底背驰"
    return {
        "type": div_type,
        "is_valid": True,
        "reason": "当前同向段价格创新极值，但 MACD 面积或斜率低于上一同向段",
    }


def _macd_histogram(closes: list[float]) -> list[float]:
    if not closes:
        return []
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = _ema(dif, 9)
    return [(d - e) * 2 for d, e in zip(dif, dea)]


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(value * alpha + result[-1] * (1 - alpha))
    return result


def _price_makes_extreme(current: dict[str, Any], previous: dict[str, Any]) -> bool:
    current_end = _num(current.get("end_price"))
    previous_end = _num(previous.get("end_price"))
    if current.get("direction") == "up":
        return current_end >= previous_end
    return current_end <= previous_end


def _insufficient(structure_view: dict[str, Any], *, reason: str) -> dict[str, Any]:
    return {
        "version": "momentum_context.v1",
        "symbol": structure_view.get("symbol") or "",
        "level": structure_view.get("level") or "",
        "engine": structure_view.get("engine") or "czsc",
        "compute_profile": structure_view.get("compute_profile") or DEFAULT_COMPUTE_PROFILE,
        "snapshot_id": structure_view.get("snapshot_id") or "",
        "data_signature": structure_view.get("data_signature") or "",
        "data_as_of": structure_view.get("data_as_of") or "",
        "updated_at": structure_view.get("updated_at") or "",
        "status": "insufficient_data",
        "direction": "",
        "current_leg": {},
        "previous_leg": {},
        "comparison": {},
        "verdict": {"state": "insufficient_data", "label": "不足判断", "confidence": 0.0},
        "divergence": {"type": "", "is_valid": False, "reason": reason},
        "risk_boundary": "仅供结构观察，不构成投资建议",
    }


def _ratio(a: float, b: float) -> float:
    if b <= 0:
        return 0.0
    return round(a / b, 3)


def _confidence(value: float) -> float:
    return round(max(0.1, min(0.95, value)), 3)


def _index(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _num(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed == parsed else 0.0
