"""Historical slice evaluation for Radar A/B/C validation.

本模块只比较“切片时雷达边界”和“后续行情/结构事件”，用于回归测试。
它不参与实时交易决策，也不把后续数据泄露回实时雷达。
"""

from __future__ import annotations

from typing import Optional


RESULT_A_TRIGGERED = "A_TRIGGERED"
RESULT_A_PARTIAL_TRIGGERED = "A_PARTIAL_TRIGGERED"
RESULT_B_MAINTAINED = "B_MAINTAINED"
RESULT_C_TRIGGERED = "C_TRIGGERED"
RESULT_UNVERIFIED = "UNVERIFIED"


def evaluate_future_result(
    algorithm_output: dict,
    future_quote: Optional[dict] = None,
    future_events: Optional[list[dict]] = None,
) -> dict:
    """Evaluate which A/B/C branch was triggered after a historical slice.

    Args:
        algorithm_output: `build_radar_algorithm_v2` 输出或同形 contract。
        future_quote: 后续价格事实，如 {"last_price": 185.49, "high": 187.99}。
        future_events: 后续结构事件，如 L1 三卖确认。

    Returns:
        {"result": "...", "matched": [...], "unmatched": [...]}。
    """
    future_quote = future_quote or {}
    future_events = future_events or []
    boundaries = algorithm_output.get("boundaries") or {}

    invalidates = [
        item for item in boundaries.get("invalidate") or []
        if _boundary_triggered(item, future_quote)
    ]
    defense_events = [
        event for event in future_events
        if _is_defense_confirmation(event)
    ]
    if invalidates or defense_events:
        return {
            "result": RESULT_C_TRIGGERED,
            "matched": invalidates + defense_events,
            "unmatched": [],
        }

    confirms = boundaries.get("confirm") or []
    matched_confirms = [
        item for item in confirms
        if _boundary_triggered(item, future_quote)
    ]
    if matched_confirms:
        result = (
            RESULT_A_TRIGGERED
            if len(matched_confirms) == len(confirms)
            else RESULT_A_PARTIAL_TRIGGERED
        )
        return {
            "result": result,
            "matched": matched_confirms,
            "unmatched": [item for item in confirms if item not in matched_confirms],
        }

    maintains = [
        item for item in boundaries.get("maintain") or []
        if _boundary_triggered(item, future_quote)
    ]
    if maintains:
        return {
            "result": RESULT_B_MAINTAINED,
            "matched": maintains,
            "unmatched": [],
        }

    return {
        "result": RESULT_UNVERIFIED,
        "matched": [],
        "unmatched": confirms,
    }


def _boundary_triggered(boundary: dict, quote: dict) -> bool:
    value = _num(boundary.get("value"))
    if value <= 0:
        return False
    trigger = str(boundary.get("trigger") or "")
    last_price = _num(quote.get("last_price") or quote.get("close") or quote.get("price"))
    high = _num(quote.get("high")) or last_price
    low = _num(quote.get("low")) or last_price

    if trigger == "break_above":
        return last_price > value
    if trigger == "break_below":
        return last_price < value
    if trigger == "hold_above":
        return low >= value or last_price >= value
    if trigger == "stay_below":
        return high < value and last_price < value
    if trigger == "fail_below":
        return high < value
    return False


def _is_defense_confirmation(event: dict) -> bool:
    event_type = str(event.get("type") or "")
    if event_type not in {
        "third_sell_below_boundary",
        "second_sell_below_prior_high",
        "top_divergence_second_sell",
        "DOWNWARD_DEFENSE_CONFIRMED",
    }:
        return False
    event_price = _num(event.get("price"))
    boundary_value = _num(event.get("boundary_value"))
    return event_price > 0 and boundary_value > 0 and event_price < boundary_value


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
