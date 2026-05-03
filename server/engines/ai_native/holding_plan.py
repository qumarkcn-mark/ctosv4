"""Daily holding plan contracts for AI Native coaching."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from server.engines.ai_native.schemas import AIReasoningResponse
from server.engines.ai_native.stop_reduce_training import (
    FundamentalVerdict,
    StopReduceCondition,
)


PlanStatus = Literal["HOLD", "WATCH", "REDUCE_ALERT", "EXIT_ALERT"]
DISCLAIMER = "仅供参考，不构成投资建议"


@dataclass(frozen=True)
class AIHoldingPlan:
    plan_id: str
    user_id: int
    symbol: str
    trade_date: str
    as_of: str
    plan_status: PlanStatus
    current_script: str
    target_weight_pct: float
    max_position_pct: float
    defense_line: float
    repair_line: float
    trigger_conditions: list[StopReduceCondition] = field(default_factory=list)
    cancel_conditions: list[StopReduceCondition] = field(default_factory=list)
    observation_focus: list[str] = field(default_factory=list)
    evidence_refs: dict[str, Any] = field(default_factory=dict)
    raw_plan: dict[str, Any] = field(default_factory=dict)
    radar_run_id: int | None = None
    disclaimer: str = DISCLAIMER


def build_holding_plan_from_ai_response(
    *,
    user_id: int,
    symbol: str,
    response: AIReasoningResponse,
    as_of: str,
    fundamental_verdict: FundamentalVerdict = "中性",
) -> AIHoldingPlan | None:
    """Create the daily parent plan. Action intents are derived later."""
    position = response.position_context
    if position is None or not position.is_holding:
        return None
    current_weight = _num(position.weight_pct)
    current_price = _num(position.current_price)
    risk_line = _nearest_price_line(position.nearest_risk_line, position.risk_lines)
    defense_line = _num(risk_line.get("value") or risk_line.get("price"))
    if current_weight <= 0 or current_price <= 0:
        return None

    repair_line = _first_boundary_value(response.key_boundaries.confirm)
    if defense_line > 0 and repair_line <= defense_line:
        repair_line = round(defense_line * 1.03, 4)
    status = _plan_status(
        current_price=current_price,
        defense_line=defense_line,
        pnl_pct=_num(position.pnl_percentage),
        risk_flags=position.risk_flags,
        fundamental_verdict=fundamental_verdict,
    )
    target_weight = _target_weight(current_weight, status)
    max_position = 0.0 if fundamental_verdict == "回避" else current_weight
    primary_condition_id = _primary_condition_id(risk_line)
    trigger_conditions = []
    cancel_conditions = []
    if defense_line > 0:
        trigger_conditions.append(
            StopReduceCondition(primary_condition_id, "daily_close", "close", "<=", defense_line, as_of[:10])
        )
    if repair_line > 0:
        cancel_conditions.append(
            StopReduceCondition("close_above_repair", "daily_close", "close", ">=", repair_line, as_of[:10])
        )
    # 日级计划是 intent 的父对象，ID 必须在同一交易日内稳定。
    plan_id = f"holding_plan:{user_id}:{symbol}:{as_of[:10]}"
    return AIHoldingPlan(
        plan_id=plan_id,
        user_id=user_id,
        symbol=symbol,
        trade_date=as_of[:10],
        as_of=as_of,
        radar_run_id=_optional_int(response.run_id),
        plan_status=status,
        current_script=_script_for(status),
        target_weight_pct=target_weight,
        max_position_pct=max_position,
        defense_line=defense_line,
        repair_line=repair_line,
        trigger_conditions=trigger_conditions,
        cancel_conditions=cancel_conditions,
        observation_focus=[
            item for item in [
                position.coach_focus,
                position.coach_summary,
                "复核防守线是否触发",
            ] if item
        ],
        evidence_refs={
            "technical_run_id": response.run_id or "manual",
            "gate_status": response.gate_status,
            "gate_score": response.gate_score,
            "nearest_risk_line": risk_line,
            "fundamental_verdict": fundamental_verdict,
        },
        raw_plan={
            "position_state": position.state,
            "pnl_percentage": position.pnl_percentage,
            "risk_flags": position.risk_flags,
        },
    )


def _plan_status(
    *,
    current_price: float,
    defense_line: float,
    pnl_pct: float,
    risk_flags: list[str],
    fundamental_verdict: FundamentalVerdict,
) -> PlanStatus:
    if fundamental_verdict == "回避" and defense_line > 0 and current_price <= defense_line:
        return "EXIT_ALERT"
    if defense_line > 0 and current_price <= defense_line:
        return "REDUCE_ALERT"
    if defense_line > 0:
        near_defense_pct = (current_price - defense_line) / current_price * 100
        high_risk = bool(set(risk_flags) & {"STRUCTURE_AGAINST_POSITION", "STOP_LOSS_NEAR", "TRAILING_STOP_NEAR"})
        if near_defense_pct <= 3.0 or (pnl_pct < 0 and high_risk):
            return "WATCH"
    if fundamental_verdict == "回避":
        return "WATCH"
    return "HOLD"


def _target_weight(current_weight: float, status: PlanStatus) -> float:
    if status == "EXIT_ALERT":
        return 0.0
    if status == "REDUCE_ALERT":
        return round(current_weight * 0.5, 4)
    return current_weight


def _script_for(status: PlanStatus) -> str:
    return {
        "HOLD": "维持持仓计划，继续观察结构是否按原剧本推进。",
        "WATCH": "进入防守观察，重点盯防守线和修复线，不主动加仓。",
        "REDUCE_ALERT": "防守线已触发或接近触发，计划转为减仓复核。",
        "EXIT_ALERT": "技术失效叠加基本面回避，计划转为退出复核。",
    }[status]


def _nearest_price_line(nearest: dict[str, Any] | None, risk_lines: list[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(nearest, dict) and _num(nearest.get("value") or nearest.get("price")) > 0:
        return dict(nearest)
    for item in risk_lines:
        if isinstance(item, dict) and _num(item.get("value") or item.get("price")) > 0:
            return dict(item)
    return {}


def _primary_condition_id(risk_line: dict[str, Any]) -> str:
    raw = str(risk_line.get("type") or risk_line.get("label") or "close_below_defense")
    normalized = "".join(ch.lower() if ch.isalnum() else "_" for ch in raw).strip("_")
    return f"close_below_{normalized or 'defense'}"


def _first_boundary_value(items: list[Any]) -> float:
    for item in items:
        value = _num(getattr(item, "value", None))
        if value > 0:
            return value
    return 0.0


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
