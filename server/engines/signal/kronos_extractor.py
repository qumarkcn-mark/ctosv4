"""Kronos 确定性提取器 — 从 KronosForecastResult 提取时间线和信封。

设计原则（kronos-repositioning-design.md）：
- Kronos 不再参与 LLM 推理，输出直接进入 SignalContext 的确定性字段
- 时间线：predicted_chan_structure.fenxings → estimated_confirmation_bars/date
- 信封：recursive_constraints.envelope → envelope_high/low + validation
- 全部是确定性计算，不经过 LLM
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 仅供参考声明
_CONFIDENCE_NOTE = "基于 Kronos 预测序列，仅供参考，不构成投资建议"


# ── Application 1: 时间线提取 ──────────────────────────────────────

def extract_timeline(
    kronos_forecast: dict,
    *,
    signal_code: str = "",
    current_date: Optional[str] = None,
) -> Optional[dict]:
    """从 predicted_chan_structure 提取结构确认时间线。

    回答用户最关心的问题："还要等多久？"

    逻辑：
    1. 从 predicted_chan_structure.fenxings 找到第一个与信号方向匹配的分型候选
    2. 分型的 step 就是预计确认的 K 线根数
    3. 结合当前日期算出预计确认日期

    Args:
        kronos_forecast: KronosForecastResult.model_dump() 或等效 dict
        signal_code: 当前信号短码（用于判断方向：bs* → 找 BOTTOM，ss* → 找 TOP）
        current_date: 当前日期字符串 (YYYY-MM-DD)，默认用今天

    Returns:
        kronos_timeline dict 或 None（数据不足时）
    """
    level_forecast = _select_signal_level_forecast(kronos_forecast, signal_code)
    predicted = level_forecast.get("predicted_chan_structure")
    if not predicted or not isinstance(predicted, dict):
        return None

    fenxings = predicted.get("fenxings") or []
    if not fenxings:
        return None

    # 根据信号方向确定要找的分型类型
    target_type = _target_fenxing_type(signal_code)

    # 找第一个匹配的分型候选
    matched = None
    for fx in fenxings:
        if not isinstance(fx, dict):
            continue
        fx_type = str(fx.get("type") or "").upper()
        if target_type and fx_type != target_type:
            continue
        # 无方向信息时取第一个分型
        matched = fx
        break

    if not matched:
        if target_type:
            return None
        # 无方向信号没有明确买卖偏置，取第一个分型作为通用时间估计
        matched = fenxings[0] if fenxings else None

    if not matched or not isinstance(matched, dict):
        return None

    step = _safe_int(matched.get("step"))
    if step <= 0:
        return None

    # 计算预计确认日期
    confirmation_date = _estimate_confirmation_date(step, current_date)

    # 生成趋势摘要
    trend_summary = predicted.get("trend_summary") or ""
    if not trend_summary:
        trend_summary = _build_simple_trend_summary(kronos_forecast)

    return {
        "level": _signal_kronos_level(signal_code),
        "estimated_confirmation_bars": step,
        "estimated_confirmation_date": confirmation_date,
        "predicted_fenxing": {
            "type": str(matched.get("type") or ""),
            "step": step,
            "price": round(_safe_float(matched.get("price")), 4),
            "confidence_note": _CONFIDENCE_NOTE,
        },
        "predicted_trend_summary": trend_summary,
    }


# ── Application 2: 信封提取 ────────────────────────────────────────

def extract_envelope(
    kronos_forecast: dict,
    *,
    signal_code: str = "",
    ai_buy_point: float = 0.0,
    target_day: int = 1,
) -> Optional[dict]:
    """从 recursive_constraints 提取日线信封，约束 30 分钟执行窗口。

    回答用户第二个问题："小级别在什么价格区间操作？"

    逻辑：
    1. 从 recursive_constraints 找到 parent=day 的约束
    2. 取其 envelope 中第 target_day 根 K 线的 high/low
    3. 如果提供了 ai_buy_point，做合理性校验

    Args:
        kronos_forecast: KronosForecastResult.model_dump() 或等效 dict
        signal_code: 当前信号短码
        ai_buy_point: AI 给出的小级别操作价格（0=不校验）
        target_day: 目标交易日（1=明天，2=后天）

    Returns:
        kronos_envelope dict 或 None（数据不足时）
    """
    constraints = kronos_forecast.get("recursive_constraints") or []
    if not constraints:
        # 如果没有 recursive_constraints，尝试从 forecast_mean 直接提取
        return _envelope_from_forecast_mean(kronos_forecast, target_day, ai_buy_point)

    # 找 parent=day 的约束（日线信封约束 30 分钟）
    day_constraint = None
    for constraint in constraints:
        if not isinstance(constraint, dict):
            continue
        parent = str(constraint.get("parent_level") or "")
        if parent in ("day", "d1"):
            day_constraint = constraint
            break

    # 如果没有 day→30 的约束，尝试任意有 envelope 的约束
    if not day_constraint:
        for constraint in constraints:
            if not isinstance(constraint, dict):
                continue
            if constraint.get("envelope"):
                day_constraint = constraint
                break

    if not day_constraint:
        return _envelope_from_forecast_mean(kronos_forecast, target_day, ai_buy_point)

    envelope_bars = day_constraint.get("envelope") or []
    if not envelope_bars:
        return _envelope_from_forecast_mean(kronos_forecast, target_day, ai_buy_point)

    # 取目标交易日的信封（target_day 从 1 开始）
    idx = max(0, target_day - 1)
    if idx >= len(envelope_bars):
        idx = len(envelope_bars) - 1

    bar = envelope_bars[idx]
    if not isinstance(bar, dict):
        return None

    envelope_high = _safe_float(bar.get("high"))
    envelope_low = _safe_float(bar.get("low"))

    if envelope_high <= 0 or envelope_low <= 0:
        return None

    # 校验小级别操作点
    validation = ""
    if ai_buy_point > 0:
        validation = _validate_sub_level_action(ai_buy_point, envelope_low, envelope_high)

    return {
        "target_day": f"Day{target_day}",
        "envelope_high": round(envelope_high, 4),
        "envelope_low": round(envelope_low, 4),
        "bar_direction": str(bar.get("direction") or "UNKNOWN"),
        "ai_buy_point": round(ai_buy_point, 4) if ai_buy_point > 0 else None,
        "validation": validation,
        "parent_level": str(day_constraint.get("parent_level") or "day"),
        "child_level": str(day_constraint.get("child_level") or "30"),
        "alignment": str(day_constraint.get("alignment") or ""),
        "confidence_note": _CONFIDENCE_NOTE,
    }


def _envelope_from_forecast_mean(
    kronos_forecast: dict,
    target_day: int,
    ai_buy_point: float,
) -> Optional[dict]:
    """没有 recursive_constraints 时，从 forecast_mean 的 OHLCV 直接提取信封。"""
    points = kronos_forecast.get("forecast_mean") or []
    if not points:
        return None

    idx = max(0, target_day - 1)
    if idx >= len(points):
        idx = len(points) - 1

    point = points[idx]
    if not isinstance(point, dict):
        return None

    high = _safe_float(point.get("high"))
    low = _safe_float(point.get("low"))
    if high <= 0 or low <= 0:
        return None

    open_price = _safe_float(point.get("open"))
    close_price = _safe_float(point.get("close"))
    if open_price > 0 and close_price > 0:
        bar_dir = "UP" if close_price > open_price * 1.001 else "DOWN" if close_price < open_price * 0.999 else "DOJI"
    else:
        bar_dir = "UNKNOWN"

    validation = ""
    if ai_buy_point > 0:
        validation = _validate_sub_level_action(ai_buy_point, low, high)

    return {
        "target_day": f"Day{target_day}",
        "envelope_high": round(high, 4),
        "envelope_low": round(low, 4),
        "bar_direction": bar_dir,
        "ai_buy_point": round(ai_buy_point, 4) if ai_buy_point > 0 else None,
        "validation": validation,
        "parent_level": "day",
        "child_level": "30",
        "alignment": "",
        "confidence_note": f"{_CONFIDENCE_NOTE}（直接从 forecast_mean 提取）",
    }


# ── 校验逻辑 ──────────────────────────────────────────────────────

def _validate_sub_level_action(
    ai_buy_point: float,
    envelope_low: float,
    envelope_high: float,
) -> str:
    """检查小级别操作点是否在日线信封内。"""
    if envelope_high <= 0 or envelope_low <= 0:
        return ""
    if ai_buy_point < envelope_low:
        return "CONFLICT: 买点低于日线预测最低价，可信度降低"
    if ai_buy_point > envelope_high:
        return "CONFLICT: 买点高于日线预测最高价，可信度降低"

    price_range = envelope_high - envelope_low
    if price_range <= 0:
        return "NEUTRAL: 信封区间过窄"

    range_pct = (ai_buy_point - envelope_low) / price_range
    if range_pct < 0.3:
        return "ALIGNED: 买点接近信封底部，与回踩判断一致"
    if range_pct > 0.7:
        return "WARNING: 买点接近信封顶部，追高风险"
    return "NEUTRAL: 买点在信封中段"


# ── 辅助函数 ──────────────────────────────────────────────────────

def _target_fenxing_type(signal_code: str) -> str:
    """根据信号短码判断需要找的分型类型。

    买入信号（bs1/bs2/bs3/bot_div）→ 找 BOTTOM 分型确认
    卖出信号（ss1/ss2/ss3/top_div）→ 找 TOP 分型确认

    短码格式: d1_bi5_bs3_strong — pattern 字段可能是单段（bs3）或双段（top_div, bot_div）
    """
    if not signal_code:
        return ""
    # 匹配完整短码中的 pattern 部分（可能含下划线如 top_div）
    code_lower = signal_code.lower()
    buy_patterns = ("bs1", "bs2", "bs3", "bot_div", "pullback")
    sell_patterns = ("ss1", "ss2", "ss3", "top_div")
    for pattern in buy_patterns:
        if pattern in code_lower:
            return "BOTTOM"
    for pattern in sell_patterns:
        if pattern in code_lower:
            return "TOP"
    return ""


def _signal_kronos_level(signal_code: str) -> str:
    """按 Signal 短码选择对应 Kronos level，不能依赖 fusion primary level。"""
    code = (signal_code or "").lower()
    if code.startswith(("w_", "week_")):
        return "week"
    if code.startswith(("d1_", "day_")):
        return "day"
    if code.startswith("m30_"):
        return "30"
    if code.startswith("m5_"):
        return "5"
    return ""


def _select_signal_level_forecast(kronos_forecast: dict, signal_code: str) -> dict:
    """从 level_forecasts 中选择与 Signal 级别一致的 Kronos payload。"""
    target_level = _signal_kronos_level(signal_code)
    level_forecasts = kronos_forecast.get("level_forecasts") or {}
    if isinstance(level_forecasts, dict) and target_level:
        payload = level_forecasts.get(target_level) or level_forecasts.get(_level_alias(target_level))
        if isinstance(payload, dict):
            return payload

    # 兼容测试和旧调用：若显式标注 top-level level，且与信号级别一致，才允许使用顶层结构。
    top_level = str(kronos_forecast.get("level") or kronos_forecast.get("interval") or "").lower()
    if target_level and top_level in {target_level, _level_alias(target_level)}:
        return kronos_forecast
    if not target_level and kronos_forecast.get("predicted_chan_structure"):
        return kronos_forecast
    return {}


def _level_alias(level: str) -> str:
    if level == "day":
        return "d1"
    if level == "d1":
        return "day"
    if level == "week":
        return "w"
    if level == "w":
        return "week"
    return level


def _estimate_confirmation_date(bars: int, current_date: Optional[str] = None) -> str:
    """估算确认日期（跳过周末）。"""
    try:
        if current_date:
            base = datetime.strptime(current_date[:10], "%Y-%m-%d")
        else:
            base = datetime.now()
    except (ValueError, TypeError):
        base = datetime.now()

    trading_days = 0
    current = base
    while trading_days < bars:
        current += timedelta(days=1)
        # 跳过周末（简单处理，不考虑节假日）
        if current.weekday() < 5:
            trading_days += 1

    return current.strftime("%Y-%m-%d")


def _build_simple_trend_summary(kronos_forecast: dict) -> str:
    """从 forecast_mean 构建简单趋势摘要。"""
    points = kronos_forecast.get("forecast_mean") or []
    if not points:
        return ""
    closes = []
    for p in points:
        if isinstance(p, dict):
            c = _safe_float(p.get("close"))
            if c > 0:
                closes.append(c)
    if len(closes) < 2:
        return ""
    change = (closes[-1] - closes[0]) / closes[0] * 100
    if change > 0.5:
        return f"预测前{len(closes)}根整体上行约{change:.1f}%"
    if change < -0.5:
        return f"预测前{len(closes)}根整体下行约{abs(change):.1f}%"
    return f"预测前{len(closes)}根整体横盘"


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
