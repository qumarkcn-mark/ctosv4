"""Hydrate practical trading evidence for second-stage AI reasoning."""

from __future__ import annotations

import statistics
from typing import Any

from server.engines.ai_native.dynamics_hydrator import hydrate_dynamics


def hydrate_practical_evidence(
    snapshots: dict[str, dict[str, Any]],
    *,
    pressure_support: list[dict[str, Any]] | None = None,
    level_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """提取实盘关注的临界证据；只描述事实，不生成交易动作。"""
    names = level_names or {}
    current_price = _current_price(snapshots)
    by_level: dict[str, Any] = {}
    for level, row in snapshots.items():
        level_name = names.get(level, level)
        snap = row.get("snapshot") or {}
        klines = snap.get("klines") or []
        dynamics = hydrate_dynamics(klines)
        bis, unfinished_bi = _split_confirmed_and_unfinished_bis(snap)
        by_level[level_name] = {
            "bi_completion": _bi_completion_hint(bis, unfinished_bi, dynamics, klines),
            "divergence_evidence": _divergence_evidence(bis, unfinished_bi, klines),
            "fx_quality": _fx_quality(snap),
        }
    return {
        "version": "practical_evidence.v1",
        "by_level": by_level,
        "level_interaction": _level_interaction(current_price, pressure_support or []),
    }


def _bi_completion_hint(
    bis: list[dict[str, Any]],
    unfinished_bi: dict[str, Any] | None,
    dynamics: dict[str, Any],
    klines: list[dict[str, Any]],
) -> dict[str, Any]:
    if not unfinished_bi:
        return {"status": "no_unfinished_bi"}
    direction = str(unfinished_bi.get("direction") or "")
    bar_count = int(_num(unfinished_bi.get("bar_count")))
    same_direction_counts = [
        int(_num(item.get("bar_count")))
        for item in bis
        if str(item.get("direction") or "") == direction and _num(item.get("bar_count")) > 0
    ]
    avg_same_direction_bars = round(statistics.mean(same_direction_counts), 2) if same_direction_counts else None
    progress_ratio = (
        round(bar_count / avg_same_direction_bars, 2)
        if avg_same_direction_bars and avg_same_direction_bars > 0
        else None
    )
    extreme_state = _unfinished_extreme_state(direction, klines, bar_count)
    macd_momentum = str(dynamics.get("macd_momentum") or "unknown")
    if progress_ratio is not None and progress_ratio >= 0.85 and macd_momentum == "weakening":
        hint = "near_end"
    elif extreme_state in {"no_new_high", "no_new_low"} and macd_momentum in {"weakening", "neutral"}:
        hint = "near_end"
    elif extreme_state in {"fresh_high", "fresh_low"} and macd_momentum == "expanding":
        hint = "extending"
    else:
        hint = "developing"
    return {
        "status": "ongoing",
        "direction": direction,
        "bar_count": bar_count,
        "avg_same_direction_bars": avg_same_direction_bars,
        "progress_ratio": progress_ratio,
        "macd_momentum": macd_momentum,
        "extreme_state": extreme_state,
        "completion_hint": hint,
    }


def _divergence_evidence(
    bis: list[dict[str, Any]],
    unfinished_bi: dict[str, Any] | None,
    klines: list[dict[str, Any]],
) -> dict[str, Any]:
    candidate = unfinished_bi or (bis[-1] if bis else None)
    previous_pool = bis if unfinished_bi else bis[:-1]
    if not candidate:
        return {"status": "insufficient_bi"}
    direction = str(candidate.get("direction") or "")
    previous = next((item for item in reversed(previous_pool) if str(item.get("direction") or "") == direction), None)
    if not previous or len(klines) < 30:
        return {"status": "insufficient_comparison", "direction": direction}

    current_price_extreme = _bi_extreme(candidate, direction)
    previous_price_extreme = _bi_extreme(previous, direction)
    if current_price_extreme <= 0 or previous_price_extreme <= 0:
        return {"status": "insufficient_price", "direction": direction}

    current_bars = max(int(_num(candidate.get("bar_count"))), 3)
    previous_bars = max(int(_num(previous.get("bar_count"))), 3)
    hist = _macd_hist([_num(item.get("close")) for item in klines if _num(item.get("close")) > 0])
    volumes = [_num(item.get("volume") or item.get("vol")) for item in klines]
    current_area = _tail_abs_sum(hist, current_bars)
    previous_area = _tail_abs_sum(hist[: -current_bars] if len(hist) > current_bars else [], previous_bars)
    current_volume = _tail_mean(volumes, current_bars)
    previous_volume = _tail_mean(volumes[: -current_bars] if len(volumes) > current_bars else [], previous_bars)
    area_ratio = round(current_area / previous_area, 2) if previous_area > 0 else None
    volume_ratio = round(current_volume / previous_volume, 2) if previous_volume > 0 else None

    price_new_extreme = (
        current_price_extreme > previous_price_extreme
        if direction == "up"
        else current_price_extreme < previous_price_extreme
    )
    force_weaker = bool(
        price_new_extreme
        and area_ratio is not None
        and area_ratio < 0.85
        and (volume_ratio is None or volume_ratio < 1.05)
    )
    if not price_new_extreme:
        hint = "no_new_extreme"
    elif force_weaker and direction == "up":
        hint = "bearish_divergence_risk"
    elif force_weaker and direction == "down":
        hint = "bullish_divergence_risk"
    else:
        hint = "force_confirmed"
    return {
        "status": "ok",
        "direction": direction,
        "price_new_extreme": price_new_extreme,
        "current_extreme": round(current_price_extreme, 4),
        "previous_extreme": round(previous_price_extreme, 4),
        "macd_area_ratio": area_ratio,
        "volume_ratio": volume_ratio,
        "hint": hint,
        "impulse_exhaustion_context": _impulse_exhaustion_context(
            direction=direction,
            hint=hint,
            area_ratio=area_ratio,
            volume_ratio=volume_ratio,
            current_volume=current_volume,
            previous_volume=previous_volume,
        ),
    }


def _fx_quality(snapshot: dict[str, Any]) -> dict[str, Any]:
    raw = snapshot.get("chan_signals") or snapshot.get("signals") or {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            key_text = str(key or "")
            value_text = str(value or "")
            if "分型强弱" not in key_text or not value_text or value_text in {"任意", "无"}:
                continue
            return {
                "source": "czsc.signals",
                "key": key_text[:80],
                "value": value_text[:80],
                "strength": "strong" if "强" in value_text else "weak" if "弱" in value_text else "unknown",
                "mark": "top" if "顶" in value_text else "bottom" if "底" in value_text else "unknown",
                "with_center": "有中枢" in value_text,
            }
    return {"source": "none", "status": "unavailable"}


def _level_interaction(current_price: float, clusters: list[dict[str, Any]]) -> dict[str, Any]:
    if current_price <= 0:
        return {"status": "unknown"}
    nearest_pressure = None
    nearest_support = None
    for item in clusters:
        center = _cluster_center(item)
        if center <= 0:
            continue
        if center >= current_price and (nearest_pressure is None or center < _cluster_center(nearest_pressure)):
            nearest_pressure = item
        if center <= current_price and (nearest_support is None or center > _cluster_center(nearest_support)):
            nearest_support = item
    return {
        "current_price": current_price,
        "nearest_pressure": _interaction_item(current_price, nearest_pressure, "pressure"),
        "nearest_support": _interaction_item(current_price, nearest_support, "support"),
    }


def _interaction_item(current_price: float, cluster: dict[str, Any] | None, expected_type: str) -> dict[str, Any] | None:
    if not cluster:
        return None
    center = _cluster_center(cluster)
    distance_pct = round((center - current_price) / current_price * 100, 2)
    abs_distance = abs(distance_pct)
    if abs_distance <= 0.5:
        interaction = f"testing_{expected_type}"
    elif expected_type == "support" and center < current_price:
        interaction = "support_below"
    elif expected_type == "pressure" and center > current_price:
        interaction = "pressure_above"
    else:
        interaction = "crossed_level"
    return {
        "zone": cluster.get("zone"),
        "distance_pct": distance_pct,
        "interaction": interaction,
        "touch_count": cluster.get("hit_count"),
        "source_levels": cluster.get("source_levels") or [],
        "status": cluster.get("status") or "",
        "semantic": cluster.get("semantic") or "",
    }


def _impulse_exhaustion_context(
    *,
    direction: str,
    hint: str,
    area_ratio: float | None,
    volume_ratio: float | None,
    current_volume: float,
    previous_volume: float,
) -> dict[str, Any]:
    if direction == "down":
        prior_impulse = "high_volume_selloff" if previous_volume > 0 and current_volume / previous_volume >= 1.2 else "moderate"
        current_relief = "low_volume" if volume_ratio is not None and volume_ratio < 0.9 else "increasing" if volume_ratio and volume_ratio > 1.1 else "unknown"
        if hint == "bullish_divergence_risk":
            exhaustion_reading = "post_flush_relief" if prior_impulse == "high_volume_selloff" else "downside_force_weakening"
        elif hint == "force_confirmed":
            exhaustion_reading = "mid_impulse"
        else:
            exhaustion_reading = "unknown"
    elif direction == "up":
        prior_impulse = "upside_extension"
        current_relief = "low_volume_or_weakening" if (area_ratio is not None and area_ratio < 0.85) or (volume_ratio is not None and volume_ratio < 0.9) else "force_confirming"
        if hint in {"bearish_divergence_risk", "no_new_extreme"} or current_relief == "low_volume_or_weakening":
            exhaustion_reading = "upside_fatigue_or_high_level_digesting"
        else:
            exhaustion_reading = "upside_force_confirming"
    else:
        prior_impulse = "unknown"
        current_relief = "unknown"
        exhaustion_reading = "unknown"
    return {
        "prior_impulse": prior_impulse,
        "current_relief": current_relief,
        "exhaustion_reading": exhaustion_reading,
        "evidence": [
            f"direction={direction}",
            f"macd_area_ratio={area_ratio}",
            f"volume_ratio={volume_ratio}",
            f"current_volume={round(current_volume, 2) if current_volume else 0}",
            f"previous_volume={round(previous_volume, 2) if previous_volume else 0}",
        ],
        "note": "描述当前同向笔的动能释放上下文，不作为交易结论。",
    }


def _unfinished_extreme_state(direction: str, klines: list[dict[str, Any]], bar_count: int) -> str:
    if bar_count <= 0 or len(klines) < max(bar_count, 3):
        return "unknown"
    window = klines[-bar_count:]
    recent = klines[-min(3, len(window)) :]
    if direction == "up":
        high = max(_num(item.get("high")) for item in window)
        recent_high = max(_num(item.get("high")) for item in recent)
        return "fresh_high" if recent_high >= high else "no_new_high"
    if direction == "down":
        low = min(_num(item.get("low")) for item in window if _num(item.get("low")) > 0)
        recent_lows = [_num(item.get("low")) for item in recent if _num(item.get("low")) > 0]
        return "fresh_low" if recent_lows and min(recent_lows) <= low else "no_new_low"
    return "unknown"


def _bi_extreme(bi: dict[str, Any], direction: str) -> float:
    if direction == "up":
        return max(_num(bi.get("high")), _num(bi.get("end_price")), _num(bi.get("start_price")))
    if direction == "down":
        values = [_num(bi.get("low")), _num(bi.get("end_price")), _num(bi.get("start_price"))]
        values = [item for item in values if item > 0]
        return min(values) if values else 0.0
    return 0.0


def _macd_hist(closes: list[float]) -> list[float]:
    if len(closes) < 30:
        return []
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = [fast - slow for fast, slow in zip(ema12, ema26)]
    dea = _ema(dif, 9)
    return [(d - m) * 2 for d, m in zip(dif, dea)]


def _ema(values: list[float], span: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (span + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def _tail_abs_sum(values: list[float], size: int) -> float:
    if not values or size <= 0:
        return 0.0
    return sum(abs(item) for item in values[-size:])


def _tail_mean(values: list[float], size: int) -> float:
    values = [item for item in values if item > 0]
    if not values or size <= 0:
        return 0.0
    tail = values[-size:]
    return statistics.mean(tail) if tail else 0.0


def _cluster_center(cluster: dict[str, Any] | None) -> float:
    zone = (cluster or {}).get("zone") or []
    if len(zone) != 2:
        return 0.0
    return (_num(zone[0]) + _num(zone[1])) / 2


def _split_confirmed_and_unfinished_bis(snapshot: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    raw_bis = [item for item in (snapshot.get("bis") or []) if isinstance(item, dict)]
    raw_unfinished = snapshot.get("unfinished_bi") if isinstance(snapshot.get("unfinished_bi"), dict) else None
    if raw_unfinished:
        return raw_bis, raw_unfinished
    if raw_bis and _is_unfinished_bi(raw_bis[-1]):
        return raw_bis[:-1], raw_bis[-1]
    return raw_bis, None


def _is_unfinished_bi(item: dict[str, Any]) -> bool:
    return bool(item.get("is_sure") is False or item.get("source") == "czsc_ubi" or item.get("status") == "ongoing")


def _current_price(snapshots: dict[str, dict[str, Any]]) -> float:
    for level in ("day", "30", "5", "week"):
        snap = (snapshots.get(level) or {}).get("snapshot") or {}
        price = _num(snap.get("price"))
        if price > 0:
            return price
    return 0.0


def _num(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed == parsed else 0.0
