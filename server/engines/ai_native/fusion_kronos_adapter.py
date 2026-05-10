"""Adapt Kronos service output into V4.5 Fusion forecast input."""

from __future__ import annotations

import logging

from server.engines.ai_native.fusion_schemas import (
    ChanAnalysisResult,
    EnvelopeBar,
    KronosForecastPoint,
    KronosForecastResult,
    KronosRecursiveConstraint,
)
from server.engines.ai_native.predicted_structure_analyzer import (
    analyze_predicted_structure,
)
from server.engines.ai_native.schemas import DISCLAIMER

_logger = logging.getLogger(__name__)


def build_kronos_forecast_from_service_result(
    service_result: dict | None,
    *,
    chan_analysis: ChanAnalysisResult | None = None,
    model_name: str = "",
) -> KronosForecastResult:
    """Convert current Kronos service dictionaries into Fusion contract.

    Kronos 在重定位后只提供预测序列、分型时间线和父级别信封。
    force_score / verdict / resonance_type / path_probabilities 均不进入
    Fusion contract，避免 LLM 重新解释模型摘要。
    """
    if not service_result:
        symbol = chan_analysis.symbol if chan_analysis else ""
        return KronosForecastResult(
            symbol=symbol,
            model_name=model_name,
            warnings=["kronos_unavailable"],
        )

    levels = _levels_payload(service_result)
    primary = _primary_level_payload(levels, service_result)
    symbol = str(service_result.get("symbol") or primary.get("symbol") or (chan_analysis.symbol if chan_analysis else ""))
    forecast_points = _forecast_points(primary)
    change_pct = _num(primary.get("change_pct"))
    warnings = []
    sample_count = _native_sample_count(service_result, primary)
    if not forecast_points:
        warnings.append("missing_forecast_sequence")
    if _has_path_probability_payload(service_result, primary):
        warnings.append("path_probabilities_deprecated_ignored")

    result = KronosForecastResult(
        symbol=symbol,
        model_name=model_name or str(service_result.get("model_name") or ""),
        generated_at=str(primary.get("last_date") or service_result.get("generated_at") or ""),
        levels=[level for level in ("week", "day", "30", "5") if level in levels] or ["day", "30"],
        lookback=_optional_int(primary.get("lookback")) or 0,
        horizon=len(forecast_points),
        sample_count=sample_count,
        forecast_mean=forecast_points,
        level_forecasts=_level_forecasts(levels),
        recursive_constraints=_recursive_constraints(levels),
        turning_windows=_turning_windows(forecast_points),
        volatility_state=_volatility_state(change_pct),
        regime_shift_score=_regime_shift_proxy(levels),
        signal_validation={
            "change_pct": change_pct,
            "shape_features": _shape_features(primary, service_result),
        },
        warnings=warnings,
        disclaimer=DISCLAIMER,
    )

    # P1: 对预测序列跑缠论分析，输出 predicted_chan_structure
    if forecast_points:
        try:
            predicted_structure = analyze_predicted_structure(result)
            result.predicted_chan_structure = predicted_structure.model_dump()
        except Exception as exc:
            _logger.warning("predicted_structure_analyzer failed: %s", exc)
            result.predicted_chan_structure = None

    return result


def _level_forecasts(levels: dict) -> dict[str, dict]:
    """保留各级别预测，避免 Signal 误用 fusion primary level。"""
    result = {}
    for level, payload in levels.items():
        if not isinstance(payload, dict):
            continue
        level_key = str(level)
        points = _forecast_points(payload)
        predicted_structure = None
        if points:
            try:
                level_result = KronosForecastResult(
                    symbol=str(payload.get("symbol") or ""),
                    generated_at=str(payload.get("last_date") or payload.get("generated_at") or ""),
                    levels=[level_key],  # type: ignore[list-item]
                    lookback=_optional_int(payload.get("lookback")) or 0,
                    horizon=len(points),
                    forecast_mean=points,
                    disclaimer=DISCLAIMER,
                )
                predicted_structure = analyze_predicted_structure(level_result).model_dump()
            except Exception as exc:
                _logger.warning("level predicted_structure_analyzer failed for %s: %s", level_key, exc)
        result[level_key] = {
            "level": level_key,
            "generated_at": str(payload.get("last_date") or payload.get("generated_at") or ""),
            "forecast_mean": [point.model_dump() for point in points],
            "predicted_chan_structure": predicted_structure,
            "change_pct": _optional_float(payload.get("change_pct")),
        }
    return result


def _levels_payload(service_result: dict) -> dict:
    levels = service_result.get("levels")
    if isinstance(levels, dict):
        return levels
    interval = service_result.get("interval")
    if interval:
        return {str(interval): service_result}
    return {"day": service_result}


def _primary_level_payload(levels: dict, service_result: dict) -> dict:
    for key in ("30", "day", "60", "5", "week"):
        value = levels.get(key)
        if isinstance(value, dict):
            return value
    return service_result


def _forecast_points(level_payload: dict) -> list[KronosForecastPoint]:
    points = []
    for idx, item in enumerate(level_payload.get("forecast_data") or [], start=1):
        if not isinstance(item, dict):
            continue
        points.append(
            KronosForecastPoint(
                step=idx,
                timestamp=str(item.get("date") or item.get("timestamp") or ""),
                open=_optional_float(item.get("open")),
                high=_optional_float(item.get("high")),
                low=_optional_float(item.get("low")),
                close=_optional_float(item.get("close")),
                volume=_optional_float(item.get("volume")),
            )
        )
    return points


def _native_sample_count(service_result: dict, primary: dict) -> int:
    candidates = [
        primary.get("sample_count"),
        service_result.get("sample_count"),
    ]
    monte_carlo = _as_dict(primary.get("monte_carlo")) or _as_dict(service_result.get("monte_carlo"))
    if monte_carlo:
        candidates.extend([monte_carlo.get("sample_count"), monte_carlo.get("samples")])
    for candidate in candidates:
        value = _optional_int(candidate)
        if value is not None:
            return max(value, 0)
    return 0


def _path_probability_payloads(service_result: dict, primary: dict) -> list:
    for source in (primary, service_result):
        direct = source.get("path_probabilities")
        if isinstance(direct, list):
            return direct
        monte_carlo = _as_dict(source.get("monte_carlo"))
        if monte_carlo and isinstance(monte_carlo.get("path_probabilities"), list):
            return monte_carlo["path_probabilities"]
    return []


def _has_path_probability_payload(service_result: dict, primary: dict) -> bool:
    return bool(_path_probability_payloads(service_result, primary))


def _turning_windows(points: list[KronosForecastPoint]) -> list[dict]:
    closes = [point.close for point in points if point.close is not None]
    if len(closes) < 3:
        return []
    windows = []
    for idx in range(1, len(closes) - 1):
        prev_close, current, next_close = closes[idx - 1], closes[idx], closes[idx + 1]
        if prev_close is None or current is None or next_close is None:
            continue
        if (current - prev_close) * (next_close - current) < 0:
            windows.append(
                {
                    "step": idx + 1,
                    "price": round(current, 4),
                    "type": "local_turning_candidate",
                }
            )
    return windows[:5]


def _recursive_constraints(levels: dict) -> list[KronosRecursiveConstraint]:
    constraints = []
    for parent, child in (("week", "day"), ("day", "30"), ("60", "30"), ("30", "5")):
        parent_payload = levels.get(parent)
        child_payload = levels.get(child)
        if not isinstance(parent_payload, dict) or not isinstance(child_payload, dict):
            continue
        parent_points = _forecast_points(parent_payload)
        child_points = _forecast_points(child_payload)
        parent_change = _level_expected_change_pct(parent_payload, parent_points)
        child_change = _level_expected_change_pct(child_payload, child_points)
        parent_direction = _direction(parent_change)
        child_direction = _direction(child_change)
        alignment = _alignment(parent_direction, child_direction)
        envelope = _build_envelope(parent_points)
        constraints.append(
            KronosRecursiveConstraint(
                parent_level=parent,  # type: ignore[arg-type]
                child_level=child,  # type: ignore[arg-type]
                parent_direction=parent_direction,
                child_direction=child_direction,
                alignment=alignment,
                parent_expected_change_pct=parent_change,
                child_expected_change_pct=child_change,
                parent_horizon=len(parent_points),
                child_horizon=len(child_points),
                envelope=envelope,
                constraint_summary=_constraint_summary(
                    parent,
                    child,
                    parent_direction,
                    child_direction,
                    alignment,
                    parent_change,
                    child_change,
                    envelope,
                ),
                fusion_instruction=_constraint_instruction(alignment, envelope),
                evidence=[
                    f"{parent}.change_pct={parent_change if parent_change is not None else 'unknown'}",
                    f"{child}.change_pct={child_change if child_change is not None else 'unknown'}",
                    f"{parent}.horizon={len(parent_points)}",
                    f"{child}.horizon={len(child_points)}",
                    *_envelope_evidence(envelope, parent),
                ],
            )
        )
    return constraints


def _level_expected_change_pct(level_payload: dict, points: list[KronosForecastPoint]) -> float | None:
    raw_change = level_payload.get("change_pct")
    if raw_change is not None:
        return round(_num(raw_change), 4)
    return _expected_change_pct(points)


def _expected_change_pct(points: list[KronosForecastPoint]) -> float | None:
    closes = [point.close for point in points if point.close is not None]
    if len(closes) < 2 or closes[0] == 0:
        return None
    return round((closes[-1] - closes[0]) / closes[0] * 100, 4)


def _direction(change_pct: float | None) -> str:
    if change_pct is None:
        return "UNKNOWN"
    if change_pct >= 0.8:
        return "UP"
    if change_pct <= -0.8:
        return "DOWN"
    return "SIDEWAYS"


def _alignment(parent_direction: str, child_direction: str) -> str:
    if "UNKNOWN" in {parent_direction, child_direction}:
        return "INSUFFICIENT_DATA"
    if parent_direction == "SIDEWAYS" or child_direction == "SIDEWAYS":
        return "ALIGNED" if parent_direction == child_direction else "DIVERGENT"
    return "ALIGNED" if parent_direction == child_direction else "DIVERGENT"


def _build_envelope(parent_points: list[KronosForecastPoint]) -> list[EnvelopeBar]:
    """从父级别预测序列构建信封约束——每根 K 线的 High/Low 就是子级别的价格边界。"""
    envelope = []
    for point in parent_points:
        if point.high is None or point.low is None:
            continue
        # 单根 K 线方向判断
        if point.open is not None and point.close is not None:
            if point.close > point.open * 1.001:
                bar_dir = "UP"
            elif point.close < point.open * 0.999:
                bar_dir = "DOWN"
            else:
                bar_dir = "DOJI"
        else:
            bar_dir = "UNKNOWN"

        envelope.append(EnvelopeBar(
            step=point.step,
            timestamp=point.timestamp,
            high=round(point.high, 4),
            low=round(point.low, 4),
            open=round(point.open, 4) if point.open is not None else None,
            close=round(point.close, 4) if point.close is not None else None,
            direction=bar_dir,
        ))
    return envelope


def _envelope_evidence(envelope: list[EnvelopeBar], parent_level: str) -> list[str]:
    """为 evidence 列表生成信封摘要。"""
    if not envelope:
        return []
    highs = [bar.high for bar in envelope]
    lows = [bar.low for bar in envelope]
    return [
        f"{parent_level}.envelope_range=[{min(lows):.4f}, {max(highs):.4f}]",
        f"{parent_level}.envelope_bars={len(envelope)}",
    ]


def _constraint_summary(
    parent: str,
    child: str,
    parent_direction: str,
    child_direction: str,
    alignment: str,
    parent_change: float | None,
    child_change: float | None,
    envelope: list[EnvelopeBar] | None = None,
) -> str:
    base = (
        f"{parent} 预测 {parent_direction}({parent_change if parent_change is not None else '--'}%)，"
        f"{child} 预测 {child_direction}({child_change if child_change is not None else '--'}%)，"
        f"递归关系 {alignment}。"
    )
    if envelope:
        highs = [bar.high for bar in envelope]
        lows = [bar.low for bar in envelope]
        base += f" 信封约束：{child} 走势应在 [{min(lows):.4f}, {max(highs):.4f}] 范围内。"
    return base


def _constraint_instruction(alignment: str, envelope: list[EnvelopeBar] | None = None) -> str:
    envelope_note = ""
    if envelope:
        envelope_note = " 父级别信封（每根预测 K 线的 High/Low）约束子级别价格上下限——子级别走势突破信封边界时，递归预测一致性降低，需降级处理。"

    if alignment == "ALIGNED":
        return f"高低周期方向一致，可作为时间窗口和价格区间参考，但仍不得替代结构触发。{envelope_note}"
    if alignment == "DIVERGENT":
        return f"高低周期方向冲突，只提示时间/价格参考的不确定性，不得输出强动作。{envelope_note}"
    return f"递归预测证据不足，只保留预测序列和信封边界作为参考。{envelope_note}"


def _volatility_state(change_pct: float) -> str:
    magnitude = abs(change_pct)
    if magnitude >= 4:
        return "expanding"
    if magnitude <= 0.8:
        return "contracting_or_neutral"
    return "normal"


def _regime_shift_proxy(levels: dict) -> float:
    scores = []
    for item in levels.values():
        if isinstance(item, dict):
            scores.append(_num(item.get("change_pct")))
    if len(scores) < 2:
        return 0.0
    spread = max(scores) - min(scores)
    return round(min(abs(spread) / 20.0, 1.0), 4)


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if value:
        return [str(value)]
    return []


def _num(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _shape_features(primary: dict, service_result: dict) -> dict:
    """提取 Kronos service 输出中的 shape_features（P2 升级后才有）。"""
    for source in (primary, service_result):
        features = source.get("shape_features")
        if isinstance(features, dict) and features.get("pattern"):
            return features
    return {}
