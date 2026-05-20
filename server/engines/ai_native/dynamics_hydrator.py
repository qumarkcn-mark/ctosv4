"""Hydrate lightweight momentum dynamics from persisted CZSC snapshot klines."""

from __future__ import annotations

import statistics
from typing import Any


def hydrate_dynamics(klines: list[dict[str, Any]]) -> dict[str, Any]:
    """从 K 线序列提取第二阶段推演需要的动力状态。"""
    closes = [_num(item.get("close")) for item in klines if _num(item.get("close")) > 0]
    volumes = [_num(item.get("volume") or item.get("vol")) for item in klines]
    highs = [_num(item.get("high")) for item in klines]
    lows = [_num(item.get("low")) for item in klines]
    if len(closes) < 30:
        return {"status": "insufficient_bars", "bar_count": len(closes)}

    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = [fast - slow for fast, slow in zip(ema12, ema26)]
    dea = _ema(dif, 9)
    hist = [(d - m) * 2 for d, m in zip(dif, dea)]
    volume_ratio = _volume_ratio(volumes)
    return {
        "macd_state": _macd_state(dif, dea),
        "macd_momentum": _macd_momentum(hist),
        "macd_zero_axis_tightness": round(_macd_tightness(dif, dea, closes), 5),
        "volume_state": _volume_state(volumes, volume_ratio),
        "volume_ratio_5_20": round(volume_ratio, 2),
        "ma_posture": _ma_posture(closes),
        "atr_volatility": _atr_state(highs, lows, closes),
        "atr_percentile": _atr_percentile(highs, lows, closes),
    }


def _ema(values: list[float], span: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (span + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def _macd_state(dif: list[float], dea: list[float]) -> str:
    if len(dif) < 2 or len(dea) < 2:
        return "unknown"
    if dif[-2] <= dea[-2] and dif[-1] > dea[-1]:
        return "golden_cross"
    if dif[-2] >= dea[-2] and dif[-1] < dea[-1]:
        return "dead_cross"
    if dif[-1] > 0 and dea[-1] > 0:
        return "above_zero"
    if dif[-1] < 0 and dea[-1] < 0:
        return "below_zero"
    return "crossing_zero"


def _macd_momentum(hist: list[float]) -> str:
    if len(hist) < 5:
        return "unknown"
    recent = hist[-5:]
    if abs(recent[-1]) > abs(recent[0]) * 1.25 and recent[-1] * recent[0] >= 0:
        return "expanding"
    if abs(recent[-1]) < abs(recent[0]) * 0.75:
        return "weakening"
    return "neutral"


def _macd_tightness(dif: list[float], dea: list[float], closes: list[float]) -> float:
    if len(dif) < 10 or len(dea) < 10 or len(closes) < 10:
        return 0.0
    numerator = statistics.mean(abs(d) + abs(m) for d, m in zip(dif[-10:], dea[-10:]))
    denominator = max(statistics.mean(closes[-10:]), 1e-9)
    return numerator / denominator


def _volume_ratio(volumes: list[float]) -> float:
    if len(volumes) < 20:
        return 0.0
    base = statistics.mean(volumes[-20:])
    return statistics.mean(volumes[-5:]) / base if base > 0 else 0.0


def _volume_state(volumes: list[float], ratio: float) -> str:
    if len(volumes) < 20:
        return "unknown"
    base = statistics.mean(volumes[-20:])
    if base > 0 and volumes[-1] > base * 2.2:
        return "abnormal_spike"
    if ratio > 1.25:
        return "expanding"
    if ratio < 0.75:
        return "shrinking"
    return "normal"


def _ma_posture(closes: list[float]) -> str:
    if len(closes) < 60:
        return "insufficient_bars"
    ma5 = statistics.mean(closes[-5:])
    ma20 = statistics.mean(closes[-20:])
    ma60 = statistics.mean(closes[-60:])
    price = closes[-1]
    if ma5 > ma20 > ma60:
        return "bullish_alignment"
    if ma5 < ma20 < ma60:
        return "bearish_alignment"
    if abs(ma5 - ma20) / price < 0.01 and abs(ma20 - ma60) / price < 0.02:
        return "tangled"
    return "mixed"


def _true_ranges(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    result = []
    end = min(len(highs), len(lows), len(closes))
    for index in range(1, end):
        result.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
        )
    return result


def _atr_percentile(highs: list[float], lows: list[float], closes: list[float]) -> float:
    ranges = _true_ranges(highs, lows, closes)
    if len(ranges) < 30:
        return 0.0
    atrs = [statistics.mean(ranges[index - 14:index]) for index in range(14, len(ranges) + 1)]
    current = atrs[-1]
    return round(sum(1 for item in atrs if item <= current) / len(atrs), 2)


def _atr_state(highs: list[float], lows: list[float], closes: list[float]) -> str:
    percentile = _atr_percentile(highs, lows, closes)
    if percentile <= 0:
        return "unknown"
    if percentile < 0.3:
        return "compressed"
    if percentile > 0.7:
        return "expanded"
    return "normal"


def _num(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed == parsed else 0.0
