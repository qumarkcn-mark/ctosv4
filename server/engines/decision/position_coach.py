"""Position-aware Radar coaching rules.

本模块只消费 Radar 算法输出和持仓上下文，不读取数据库、不调用行情。
它把“股票结构”翻译成“这套结构对我当前仓位意味着什么”。
"""

from __future__ import annotations

from typing import Optional


DISCLAIMER = "仅供参考，不构成投资建议"

STATE_LABELS = {
    "EMPTY": "空仓",
    "PROBE": "观察仓",
    "NORMAL_HOLDING": "正常持仓",
    "PROFIT_HEAVY": "盈利重仓",
    "LOSS_HOLDING": "亏损持仓",
    "ANTI_TREND_ADD": "逆势加仓",
}

RADAR_STATE_LABELS = {
    "UPWARD_MAJOR_WAVE": "主升延续",
    "PULLBACK_IN_UPTREND": "上升回落验证",
    "THIRD_BUY_CONFIRMED": "三买确认",
    "HIGH_VOLATILITY_OSCILLATION": "高位震荡",
    "FAST_SELL_RISK": "快速卖点风险",
    "DOWNWARD_LEAVING": "向下离开",
    "BOTTOM_REVERSAL": "底部转折",
    "SMALL_TURN_BIG": "小转大修复",
    "NO_EDGE": "无优势路径",
}

ACTION_LABELS = {
    "WAIT": "等待",
    "WATCH_TRIGGER": "盯触发",
    "PRE_CHECK": "执行前复核",
    "PROBE_ALLOWED": "允许小仓试错",
    "PROBE_WITH_STOP": "试错带防守",
    "EXIT_IF_FAIL": "失败即撤",
    "HOLD": "持有",
    "HOLD_WATCH": "持有观察",
    "HOLD_RAISE_STOP": "防守上移",
    "HOLD_PROTECT": "保护持有",
    "REDUCE_OR_PROTECT": "减仓或强防守",
    "EXIT_OR_REDUCE": "退出或降暴露",
    "TRAIL_STOP": "移动止盈",
    "LOCK_PROFIT": "锁定利润",
    "STOP_LOSS_PRIORITY": "止损优先",
    "NO_ADD_WAIT": "禁止补仓",
    "REPAIR_WATCH": "修复观察",
    "BLOCK_ADD": "禁止加仓",
}


def build_position_context(
    holding: Optional[dict],
    algorithm: dict,
    account_value: float = 0.0,
    quote: Optional[dict] = None,
) -> dict:
    """Normalize raw holding data into a stable coaching context."""
    current_price = _current_price(algorithm, holding, quote=quote)
    structure_price = _structure_price(algorithm, holding)
    quote_price = _num((quote or {}).get("price"))
    price_source = "tencent_quote" if quote_price > 0 else "structure"
    if not holding:
        return {
            "state": "EMPTY",
            "label": STATE_LABELS["EMPTY"],
            "is_holding": False,
            "quantity": 0,
            "avg_cost": 0,
            "current_price": current_price,
            "structure_price": structure_price,
            "quote_price": quote_price,
            "price_source": price_source,
            "quote_time": (quote or {}).get("time") or "",
            "pnl_pct": None,
            "position_value": 0,
            "weight_pct": None,
            "risk_flags": [],
        }

    qty = _num(holding.get("qty") or holding.get("quantity"))
    cost = _num(holding.get("cost") or holding.get("avg_cost"))
    position_value = qty * current_price if current_price > 0 else qty * cost
    pnl_pct = ((current_price - cost) / cost * 100) if cost > 0 and current_price > 0 else None
    weight_pct = (position_value / account_value * 100) if account_value > 0 and position_value > 0 else None
    risk_flags = []
    state = "NORMAL_HOLDING"

    if pnl_pct is not None and pnl_pct <= -3:
        state = "LOSS_HOLDING"
    elif _is_probe_position(weight_pct, position_value, account_value):
        state = "PROBE"
    elif pnl_pct is not None and pnl_pct >= 10 and (weight_pct is None or weight_pct >= 12):
        state = "PROFIT_HEAVY"

    radar_state = infer_radar_state(algorithm)
    if state in {"NORMAL_HOLDING", "LOSS_HOLDING"} and radar_state in {"DOWNWARD_LEAVING", "FAST_SELL_RISK"}:
        risk_flags.append("STRUCTURE_AGAINST_POSITION")

    return {
        "state": state,
        "label": STATE_LABELS[state],
        "is_holding": True,
        "quantity": qty,
        "avg_cost": cost,
        "current_price": current_price,
        "structure_price": structure_price,
        "quote_price": quote_price,
        "price_source": price_source,
        "quote_time": (quote or {}).get("time") or "",
        "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
        "position_value": round(position_value, 2),
        "weight_pct": round(weight_pct, 2) if weight_pct is not None else None,
        "risk_flags": risk_flags,
        "strategy_type": holding.get("strategy_type") or "未知",
        "entry_date": holding.get("entry_date") or "",
        "stop_loss_price": holding.get("stop_loss_price"),
        "trailing_stop_price": holding.get("trailing_stop_price"),
        "m5_entry_zg": holding.get("m5_entry_zg"),
    }


def build_coach_action(position_context: dict, algorithm: dict, disclaimer: str = DISCLAIMER) -> dict:
    """Build one concise coach action from position context and Radar state."""
    radar_state = infer_radar_state(algorithm)
    position_state = position_context.get("state") or "EMPTY"
    action = _action_for(position_state, radar_state)
    tone = _tone_for(action)
    boundaries = _coach_boundaries(algorithm)
    risk_lines = _risk_lines(position_context, algorithm)
    nearest_risk_line = _nearest_risk_line(position_context, risk_lines)
    next_if = _next_if(algorithm)

    return {
        "version": "position_coach.v1",
        "position_state": position_state,
        "position_label": position_context.get("label") or STATE_LABELS.get(position_state, position_state),
        "radar_state": radar_state,
        "radar_label": RADAR_STATE_LABELS.get(radar_state, radar_state),
        "action": action,
        "label": ACTION_LABELS.get(action, action),
        "tone": tone,
        "priority": _priority_for(action),
        "summary": _summary(position_state, radar_state, action),
        "reason": _reason(position_context, algorithm),
        "focus": _focus_for(position_state, radar_state),
        "boundaries": boundaries,
        "risk_lines": risk_lines,
        "nearest_risk_line": nearest_risk_line,
        "next_if": next_if,
        "disclaimer": disclaimer,
    }


def infer_radar_state(algorithm: dict) -> str:
    """Infer the eight-state product vocabulary from algorithm output."""
    patterns = algorithm.get("patterns") or []
    pattern_codes = [item.get("code") for item in patterns if isinstance(item, dict)]
    transition = algorithm.get("transition") or {}
    transition_to = transition.get("to")
    path = algorithm.get("path")

    if "THIRD_BUY_FAST_SELL_RISK" in pattern_codes:
        return "FAST_SELL_RISK"
    if "THIRD_BUY_RETEST_UP" in pattern_codes:
        return "THIRD_BUY_CONFIRMED"
    if "SMALL_TURN_BIG_FAST_B2_B3" in pattern_codes or "BIG_CENTER_SMALL_CENTER_PULLBACK_REPAIR" in pattern_codes:
        return "SMALL_TURN_BIG"
    if transition_to in RADAR_STATE_LABELS:
        return transition_to
    if path in RADAR_STATE_LABELS:
        return path
    if path == "DOWNWARD_DEFENSE":
        return "DOWNWARD_LEAVING"
    if path in {"BOTTOM_REPAIR", "CENTER_REBOUND"}:
        return "BOTTOM_REVERSAL"
    return "NO_EDGE"


def _action_for(position_state: str, radar_state: str) -> str:
    matrix = {
        "EMPTY": {
            "UPWARD_MAJOR_WAVE": "WATCH_TRIGGER",
            "PULLBACK_IN_UPTREND": "WATCH_TRIGGER",
            "THIRD_BUY_CONFIRMED": "PRE_CHECK",
            "HIGH_VOLATILITY_OSCILLATION": "WAIT",
            "FAST_SELL_RISK": "WAIT",
            "DOWNWARD_LEAVING": "WAIT",
            "BOTTOM_REVERSAL": "WATCH_TRIGGER",
            "SMALL_TURN_BIG": "WATCH_TRIGGER",
        },
        "PROBE": {
            "UPWARD_MAJOR_WAVE": "EXIT_IF_FAIL",
            "PULLBACK_IN_UPTREND": "PROBE_ALLOWED",
            "THIRD_BUY_CONFIRMED": "PROBE_WITH_STOP",
            "HIGH_VOLATILITY_OSCILLATION": "EXIT_IF_FAIL",
            "FAST_SELL_RISK": "EXIT_IF_FAIL",
            "DOWNWARD_LEAVING": "EXIT_IF_FAIL",
            "BOTTOM_REVERSAL": "PROBE_WITH_STOP",
            "SMALL_TURN_BIG": "PROBE_WITH_STOP",
        },
        "NORMAL_HOLDING": {
            "UPWARD_MAJOR_WAVE": "HOLD",
            "PULLBACK_IN_UPTREND": "HOLD_WATCH",
            "THIRD_BUY_CONFIRMED": "HOLD_RAISE_STOP",
            "HIGH_VOLATILITY_OSCILLATION": "HOLD_PROTECT",
            "FAST_SELL_RISK": "REDUCE_OR_PROTECT",
            "DOWNWARD_LEAVING": "EXIT_OR_REDUCE",
            "BOTTOM_REVERSAL": "HOLD_WATCH",
            "SMALL_TURN_BIG": "HOLD_WATCH",
        },
        "PROFIT_HEAVY": {
            "UPWARD_MAJOR_WAVE": "TRAIL_STOP",
            "PULLBACK_IN_UPTREND": "TRAIL_STOP",
            "THIRD_BUY_CONFIRMED": "HOLD_RAISE_STOP",
            "HIGH_VOLATILITY_OSCILLATION": "LOCK_PROFIT",
            "FAST_SELL_RISK": "REDUCE_OR_PROTECT",
            "DOWNWARD_LEAVING": "EXIT_OR_REDUCE",
            "BOTTOM_REVERSAL": "LOCK_PROFIT",
            "SMALL_TURN_BIG": "TRAIL_STOP",
        },
        "LOSS_HOLDING": {
            "UPWARD_MAJOR_WAVE": "HOLD_WATCH",
            "PULLBACK_IN_UPTREND": "NO_ADD_WAIT",
            "THIRD_BUY_CONFIRMED": "REPAIR_WATCH",
            "HIGH_VOLATILITY_OSCILLATION": "NO_ADD_WAIT",
            "FAST_SELL_RISK": "STOP_LOSS_PRIORITY",
            "DOWNWARD_LEAVING": "STOP_LOSS_PRIORITY",
            "BOTTOM_REVERSAL": "REPAIR_WATCH",
            "SMALL_TURN_BIG": "REPAIR_WATCH",
        },
        "ANTI_TREND_ADD": {},
    }
    if position_state == "ANTI_TREND_ADD":
        return "BLOCK_ADD"
    return matrix.get(position_state, {}).get(radar_state, "WATCH_TRIGGER")


def _summary(position_state: str, radar_state: str, action: str) -> str:
    position = STATE_LABELS.get(position_state, position_state)
    radar = RADAR_STATE_LABELS.get(radar_state, radar_state)
    action_label = ACTION_LABELS.get(action, action)
    templates = {
        "WAIT": f"{position} + {radar}：暂不参与，等待结构给出更清晰触发。",
        "WATCH_TRIGGER": f"{position} + {radar}：先盯触发条件，发生后再进入执行前复核。",
        "PRE_CHECK": f"{position} + {radar}：可以进入执行前复核，但仍需检查位置和防守距离。",
        "HOLD": f"{position} + {radar}：结构仍支持持有，继续盯失效线。",
        "HOLD_WATCH": f"{position} + {radar}：可以持有观察，等待回落或修复确认。",
        "HOLD_RAISE_STOP": f"{position} + {radar}：持有逻辑有效，防守线应上移。",
        "HOLD_PROTECT": f"{position} + {radar}：持有降级为保护模式，防利润回撤。",
        "REDUCE_OR_PROTECT": f"{position} + {radar}：卖点风险出现，减仓或收紧防守优先。",
        "EXIT_OR_REDUCE": f"{position} + {radar}：结构转弱，优先退出或降低暴露。",
        "TRAIL_STOP": f"{position} + {radar}：继续跟随，但必须移动保护利润。",
        "LOCK_PROFIT": f"{position} + {radar}：利润保护优先，先锁住一部分胜利。",
        "STOP_LOSS_PRIORITY": f"{position} + {radar}：止损看门狗优先，禁止用幻想替代纪律。",
        "NO_ADD_WAIT": f"{position} + {radar}：不补仓，先等结构重新确认。",
        "REPAIR_WATCH": f"{position} + {radar}：只按修复观察，不能把反弹直接当反转。",
        "BLOCK_ADD": f"{position} + {radar}：当前结构不支持加仓，先停止扩大风险。",
    }
    return templates.get(action, f"{position} + {radar}：{action_label}。")


def _reason(position_context: dict, algorithm: dict) -> str:
    pnl = position_context.get("pnl_pct")
    if pnl is None:
        return algorithm.get("summary") or "当前按结构推演管理。"
    return f"当前浮动盈亏约 {pnl:.2f}%，{algorithm.get('summary') or '按结构推演管理'}"


def _focus_for(position_state: str, radar_state: str) -> str:
    if position_state == "EMPTY":
        return "只看触发，不追中间价。"
    if position_state == "PROFIT_HEAVY":
        return "利润保护权重高于继续幻想空间。"
    if position_state == "LOSS_HOLDING":
        return "先判断原买入逻辑是否仍成立，禁止盲目补仓。"
    if radar_state in {"FAST_SELL_RISK", "DOWNWARD_LEAVING"}:
        return "风险优先，先看失效线和卖点确认。"
    return "看结构是否继续支持当前仓位。"


def _coach_boundaries(algorithm: dict) -> list[dict]:
    boundaries = algorithm.get("boundaries") or {}
    items = []
    for group, label in [
        ("confirm", "确认线"),
        ("maintain", "防守线"),
        ("invalidate", "失效线"),
        ("pressure", "压力观察"),
    ]:
        item = _first_valid_boundary(boundaries.get(group) or [])
        if item:
            items.append({
                "type": group,
                "label": label,
                "level": item.get("level"),
                "field": item.get("field"),
                "value": item.get("value"),
                "meaning": item.get("meaning") or "",
            })
    return items


def _risk_lines(position_context: dict, algorithm: dict) -> list[dict]:
    current_price = _num(position_context.get("current_price"))
    lines = []
    _append_risk_line(
        lines,
        source="position",
        type_="cost",
        label="成本线",
        value=position_context.get("avg_cost"),
        current_price=current_price,
        meaning="持仓盈亏分界线",
    )
    _append_risk_line(
        lines,
        source="watchdog",
        type_="stop_loss",
        label="原始止损",
        value=position_context.get("stop_loss_price"),
        current_price=current_price,
        meaning="交易计划中的硬止损价",
    )
    _append_risk_line(
        lines,
        source="watchdog",
        type_="trailing_stop",
        label="移动止盈",
        value=position_context.get("trailing_stop_price"),
        current_price=current_price,
        meaning="止损看门狗移动保护线",
    )
    _append_risk_line(
        lines,
        source="entry_structure",
        type_="m5_entry_zg",
        label="入场5分ZG",
        value=position_context.get("m5_entry_zg"),
        current_price=current_price,
        meaning="入场时记录的5分钟结构线",
    )

    boundaries = algorithm.get("boundaries") or {}
    for item in (boundaries.get("maintain") or [])[:2]:
        _append_boundary_risk_line(lines, item, current_price, "structure_defense", "结构防守")
    for item in (boundaries.get("invalidate") or [])[:2]:
        _append_boundary_risk_line(lines, item, current_price, "structure_invalidation", "结构失效")

    return sorted(
        lines,
        key=lambda item: (
            999 if item.get("distance_pct") is None else abs(item["distance_pct"]),
            item.get("value") or 0,
        ),
    )


def _nearest_risk_line(position_context: dict, risk_lines: list[dict]) -> Optional[dict]:
    if not position_context.get("is_holding"):
        return None
    below = [
        item for item in risk_lines
        if _num(item.get("value")) > 0 and item.get("side") in {"below", "at_or_below"}
    ]
    if below:
        return min(below, key=lambda item: abs(item.get("distance_pct") or 999))
    return risk_lines[0] if risk_lines else None


def _append_boundary_risk_line(
    lines: list[dict],
    item: dict,
    current_price: float,
    type_: str,
    label: str,
) -> None:
    field_label = f"{item.get('level') or ''}{item.get('field') or ''}"
    _append_risk_line(
        lines,
        source="structure",
        type_=type_,
        label=f"{label} {field_label}".strip(),
        value=item.get("value"),
        current_price=current_price,
        meaning=item.get("meaning") or "",
    )


def _append_risk_line(
    lines: list[dict],
    *,
    source: str,
    type_: str,
    label: str,
    value,
    current_price: float,
    meaning: str,
) -> None:
    price = _num(value)
    if price <= 0:
        return
    distance_pct = None
    side = "unknown"
    if current_price > 0:
        distance_pct = round((current_price - price) / current_price * 100, 2)
        if price < current_price:
            side = "below"
        elif price > current_price:
            side = "above"
        else:
            side = "at_or_below"
    lines.append({
        "source": source,
        "type": type_,
        "label": label,
        "value": price,
        "distance_pct": distance_pct,
        "side": side,
        "meaning": meaning,
    })


def _next_if(algorithm: dict) -> list[dict]:
    return [
        {
            "path": item.get("path"),
            "condition": item.get("condition"),
            "then": item.get("then"),
        }
        for item in (algorithm.get("trigger_playbook") or [])[:3]
    ]


def _current_price(algorithm: dict, holding: Optional[dict], quote: Optional[dict] = None) -> float:
    quote_price = _num((quote or {}).get("price"))
    if quote_price > 0:
        return quote_price
    return _structure_price(algorithm, holding)


def _structure_price(algorithm: dict, holding: Optional[dict]) -> float:
    for role in ("L2", "L1", "L0"):
        price = _num(((algorithm.get("atoms") or {}).get(role) or {}).get("price"))
        if price > 0:
            return price
    if holding:
        return _num(holding.get("current_price"))
    return 0.0


def _first_valid_boundary(items: list[dict]) -> Optional[dict]:
    for item in items:
        if _num(item.get("value")) > 0:
            return item
    return None


def _is_probe_position(weight_pct: Optional[float], position_value: float, account_value: float) -> bool:
    if weight_pct is not None:
        return 0 < weight_pct <= 5
    if account_value <= 0:
        return False
    return 0 < position_value <= account_value * 0.05


def _tone_for(action: str) -> str:
    if action in {"PRE_CHECK", "HOLD", "HOLD_RAISE_STOP", "PROBE_ALLOWED", "PROBE_WITH_STOP"}:
        return "confirm"
    if action in {"WATCH_TRIGGER", "HOLD_WATCH", "TRAIL_STOP", "REPAIR_WATCH"}:
        return "watch"
    if action in {"HOLD_PROTECT", "LOCK_PROFIT", "NO_ADD_WAIT"}:
        return "warning"
    if action in {"REDUCE_OR_PROTECT", "EXIT_OR_REDUCE", "STOP_LOSS_PRIORITY", "BLOCK_ADD", "EXIT_IF_FAIL"}:
        return "danger"
    return "neutral"


def _priority_for(action: str) -> str:
    if action in {"STOP_LOSS_PRIORITY", "BLOCK_ADD", "EXIT_OR_REDUCE", "REDUCE_OR_PROTECT"}:
        return "HIGH"
    if action in {"LOCK_PROFIT", "HOLD_PROTECT", "NO_ADD_WAIT", "TRAIL_STOP"}:
        return "MEDIUM_HIGH"
    return "MEDIUM"


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
