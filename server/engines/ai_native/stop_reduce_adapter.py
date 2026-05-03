"""Adapters from AI Native Radar output to stop/reduce shadow intents."""

from __future__ import annotations

from typing import Any

from server.engines.ai_native.schemas import AIReasoningResponse
from server.engines.ai_native.stop_reduce_feedback import StopReduceFeedback
from server.engines.ai_native.stop_reduce_training import (
    FundamentalVerdict,
    RebalanceIntent,
    StopReduceAction,
    StopReduceCondition,
    StopReduceConditions,
    apply_fundamental_constraint,
    build_stop_reduce_idempotency_key,
    validate_rebalance_intent,
)


def build_stop_reduce_intent_from_ai_response(
    *,
    user_id: int,
    symbol: str,
    response: AIReasoningResponse,
    as_of: str,
    fundamental_verdict: FundamentalVerdict = "中性",
    default_reduce_ratio: float = 0.5,
    feedback: StopReduceFeedback | None = None,
) -> RebalanceIntent | None:
    """Build a deterministic V1 intent from one AI Native Radar response.

    V1 intentionally uses only fields available in the response at ``as_of``.
    Future settlement bars are consumed later by the shadow loop, never here.
    """
    position = response.position_context
    if position is None or not position.is_holding:
        return None

    current_weight = _num(position.weight_pct)
    current_price = _num(position.current_price)
    risk_line = _nearest_price_line(position.nearest_risk_line, position.risk_lines)
    stop_price = _num(risk_line.get("value") or risk_line.get("price"))
    if current_weight <= 0 or current_price <= 0 or stop_price <= 0:
        return None

    raw_action = _infer_action(
        current_price=current_price,
        stop_price=stop_price,
        pnl_pct=_num(position.pnl_percentage),
        risk_flags=position.risk_flags,
        fundamental_verdict=fundamental_verdict,
    )
    action = apply_fundamental_constraint(
        _apply_feedback_action(
            raw_action,
            current_price=current_price,
            stop_price=stop_price,
            pnl_pct=_num(position.pnl_percentage),
            feedback=feedback,
        ),
        fundamental_verdict,
    )
    reduce_ratio = _feedback_reduce_ratio(default_reduce_ratio, feedback=feedback, fundamental_verdict=fundamental_verdict)
    target_weight = _target_weight(current_weight, action, reduce_ratio)
    primary_condition_id = _primary_condition_id(risk_line)
    conditions = _conditions_from_response(response, stop_price=stop_price, primary_condition_id=primary_condition_id)
    technical_run_id = response.run_id or "manual"
    intent_id = f"stop_reduce:{user_id}:{symbol}:{as_of}:{technical_run_id}:{primary_condition_id}"
    intent = RebalanceIntent(
        intent_type="STOP_REDUCE",
        intent_id=intent_id,
        idempotency_key=build_stop_reduce_idempotency_key(
            user_id=user_id,
            symbol=symbol,
            as_of=as_of,
            technical_run_id=technical_run_id,
            primary_condition_id=primary_condition_id,
        ),
        user_id=user_id,
        symbol=symbol,
        action=action,
        current_weight_pct=current_weight,
        target_weight_pct=target_weight,
        quantity_policy="reduce_to_target" if action == "REDUCE" else "exit_all" if action == "EXIT" else "observe",
        as_of=as_of,
        conditions=conditions,
        reason={
            "technical": _technical_reason(response, risk_line),
            "fundamental": fundamental_verdict,
            "memory_feedback": _feedback_reason(feedback),
            "position": {
                "state": position.state,
                "pnl_percentage": position.pnl_percentage,
                "risk_flags": position.risk_flags,
                "coach_focus": position.coach_focus,
            },
        },
        evidence_refs={
            "technical_run_id": technical_run_id,
            "gate_status": response.gate_status,
            "gate_score": response.gate_score,
            "primary_condition_id": primary_condition_id,
            "nearest_risk_line": risk_line,
            "case_memory_feedback": feedback.to_dict() if feedback else {},
        },
    )
    validate_rebalance_intent(intent)
    return intent


def _apply_feedback_action(
    action: StopReduceAction,
    *,
    current_price: float,
    stop_price: float,
    pnl_pct: float,
    feedback: StopReduceFeedback | None,
) -> StopReduceAction:
    if feedback is None or feedback.action_bias != "TIGHTEN_STOP":
        return action
    near_stop_pct = (current_price - stop_price) / current_price * 100 if current_price > 0 else 999
    if action == "HOLD" and pnl_pct < 0 and near_stop_pct <= 3.0:
        return "WATCH_EXIT"
    if action == "WATCH_EXIT" and pnl_pct < 0 and near_stop_pct <= 1.0:
        return "REDUCE"
    return action


def _feedback_reduce_ratio(
    default_reduce_ratio: float,
    *,
    feedback: StopReduceFeedback | None,
    fundamental_verdict: FundamentalVerdict,
) -> float:
    if feedback is None or feedback.action_bias != "WAIT_FOR_CONFIRMATION":
        return default_reduce_ratio
    if fundamental_verdict == "回避":
        return default_reduce_ratio
    # 过早减仓的错误记忆只降低减仓强度，不覆盖止损/风控动作。
    return min(default_reduce_ratio, 0.33)


def _feedback_reason(feedback: StopReduceFeedback | None) -> dict[str, Any]:
    if feedback is None:
        return {}
    return {
        "case_key": feedback.case_key,
        "action_bias": feedback.action_bias,
        "confidence": feedback.confidence,
        "total_count": feedback.total_count,
        "mistake_count": feedback.mistake_count,
        "latest_mistake_type": feedback.latest_mistake_type,
        "latest_lesson": feedback.latest_lesson,
    }


def _infer_action(
    *,
    current_price: float,
    stop_price: float,
    pnl_pct: float,
    risk_flags: list[str],
    fundamental_verdict: FundamentalVerdict,
) -> StopReduceAction:
    if fundamental_verdict == "回避" and current_price <= stop_price:
        return "EXIT"
    if current_price <= stop_price:
        return "REDUCE"
    near_stop_pct = (current_price - stop_price) / current_price * 100
    high_risk = bool(set(risk_flags) & {"STRUCTURE_AGAINST_POSITION", "STOP_LOSS_NEAR", "TRAILING_STOP_NEAR"})
    if near_stop_pct <= 3.0 or (pnl_pct < 0 and high_risk):
        return "WATCH_EXIT"
    return "HOLD"


def _conditions_from_response(
    response: AIReasoningResponse,
    *,
    stop_price: float,
    primary_condition_id: str,
) -> StopReduceConditions:
    cancel_if: list[StopReduceCondition] = []
    repair_price = _first_boundary_value(response.key_boundaries.confirm)
    if repair_price <= stop_price:
        repair_price = round(stop_price * 1.03, 4)
    cancel_if.append(
        StopReduceCondition(
            condition_id="close_above_repair",
            source="daily_close",
            field="close",
            op=">=",
            value=repair_price,
        )
    )
    return StopReduceConditions(
        activate_if=[
            StopReduceCondition(
                condition_id=primary_condition_id,
                source="daily_close",
                field="close",
                op="<=",
                value=stop_price,
            )
        ],
        cancel_if=cancel_if,
    )


def _nearest_price_line(nearest: dict[str, Any] | None, risk_lines: list[dict[str, Any]]) -> dict[str, Any]:
    if isinstance(nearest, dict) and _num(nearest.get("value") or nearest.get("price")) > 0:
        return dict(nearest)
    for item in risk_lines:
        if isinstance(item, dict) and _num(item.get("value") or item.get("price")) > 0:
            return dict(item)
    return {}


def _primary_condition_id(risk_line: dict[str, Any]) -> str:
    raw = str(risk_line.get("type") or risk_line.get("label") or "close_below_stop")
    normalized = "".join(ch.lower() if ch.isalnum() else "_" for ch in raw).strip("_")
    return f"close_below_{normalized or 'stop'}"


def _first_boundary_value(items: list[Any]) -> float:
    for item in items:
        value = _num(getattr(item, "value", None))
        if value > 0:
            return value
    return 0.0


def _target_weight(current_weight: float, action: StopReduceAction, reduce_ratio: float) -> float:
    if action == "EXIT":
        return 0.0
    if action == "REDUCE":
        ratio = min(1.0, max(0.0, reduce_ratio))
        return round(current_weight * (1 - ratio), 4)
    return current_weight


def _technical_reason(response: AIReasoningResponse, risk_line: dict[str, Any]) -> str:
    label = risk_line.get("label") or risk_line.get("type") or "nearest risk line"
    coach = response.position_context.coach_summary if response.position_context else ""
    return f"{label}: {coach}".strip()


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
