"""Thin AI Native rebalance engine.

The engine translates single-symbol Fusion outcomes into conditioned portfolio
intents. It does not place trades and does not decide final execution for users.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from server.engines.ai_native.market_time import CN_TZ, valid_until_for_refresh_trigger
from server.engines.ai_native.schemas import DISCLAIMER
from server.engines.ai_native.rebalance_schemas import (
    PortfolioState,
    RebalanceAction,
    RebalanceConditions,
    RebalanceContract,
    RebalanceEvidence,
    RebalanceIntent,
    RebalanceIntentType,
    RebalanceMemory,
    RebalanceRisk,
    RebalanceSummary,
    RebalanceSymbolRef,
    RebalanceUrgency,
    RecommendedAction,
    RefreshTrigger,
)

class RebalanceEngineInputItem(BaseModel):
    symbol: str
    name: str = ""
    is_holding: bool = False
    quantity: Optional[float] = None
    weight_pct: Optional[float] = None
    avg_cost: Optional[float] = None
    current_price: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None
    radar: dict[str, Any] = Field(default_factory=dict)
    kronos: dict[str, Any] = Field(default_factory=dict)
    ai_fusion: dict[str, Any] = Field(default_factory=dict)
    memory: dict[str, Any] = Field(default_factory=dict)


def build_rebalance_contract(
    items: list[RebalanceEngineInputItem | dict[str, Any]],
    *,
    user_id: int,
    portfolio_state: PortfolioState | dict[str, Any] | None = None,
    generated_at: str | None = None,
    valid_until: str | None = None,
    refresh_trigger: RefreshTrigger = "NEXT_30M_CLOSE",
    run_id: str | None = None,
) -> RebalanceContract:
    """Build a conditioned rebalance contract from Fusion-ready symbol inputs."""
    now = datetime.now(CN_TZ)
    expiry_base = _parse_generated_at(generated_at) or now
    generated = generated_at or now.isoformat(timespec="seconds")
    valid = valid_until or _default_valid_until(expiry_base, refresh_trigger)
    normalized_items = [
        item if isinstance(item, RebalanceEngineInputItem) else RebalanceEngineInputItem(**item)
        for item in items
    ]

    intents = [_build_intent(item, generated) for item in normalized_items]
    return RebalanceContract(
        run_id=run_id or f"rebalance_{now.strftime('%Y%m%d_%H%M%S')}",
        user_id=user_id,
        generated_at=generated,
        valid_until=valid,
        refresh_trigger=refresh_trigger,
        portfolio_state=_portfolio_state(portfolio_state, normalized_items),
        intents=intents,
        summary=_summary(intents),
    )


def _build_intent(item: RebalanceEngineInputItem, generated_at: str) -> RebalanceIntent:
    action = _extract_action(item)
    intent_type = _intent_type(action, item.is_holding)
    memory = _memory(item, generated_at, action)
    urgency = _urgency(action, item, memory)
    symbol_ref = RebalanceSymbolRef(
        symbol=item.symbol,
        name=item.name,
        is_holding=item.is_holding,
        quantity=item.quantity,
        weight_pct=item.weight_pct,
        avg_cost=item.avg_cost,
        current_price=item.current_price,
        unrealized_pnl_pct=item.unrealized_pnl_pct,
    )

    return RebalanceIntent(
        intent_id=f"rb_{item.symbol}_{generated_at[:10].replace('-', '')}",
        intent_type=intent_type,
        urgency=urgency,
        source=symbol_ref,
        target=None,
        recommended_action=_recommended_action(action, item),
        conditions=_conditions(action, item),
        risk=_risk(action, item),
        evidence=RebalanceEvidence(
            radar=item.radar,
            kronos=item.kronos,
            ai_fusion=item.ai_fusion,
            fusion_status=_fusion_status(item.ai_fusion),
            notes=_evidence_notes(memory),
        ),
        memory=memory,
    )


def _fusion_status(ai_fusion: dict[str, Any]) -> dict[str, Any]:
    fallback_reason = str(ai_fusion.get("fallback_reason") or "").strip()
    primary_path = str(ai_fusion.get("primary_path_id") or ai_fusion.get("primary_path") or "")
    is_fallback = bool(fallback_reason) or primary_path.startswith("fallback-")
    return {
        "state": "FALLBACK" if is_fallback else "AI_READY",
        "fallback_reason": fallback_reason or None,
        "primary_path_id": primary_path or None,
    }


def _extract_action(item: RebalanceEngineInputItem) -> RebalanceAction:
    if _is_fusion_fallback(item.ai_fusion):
        return "NO_ACTION"
    candidates = [
        (item.ai_fusion.get("action_playbook") or {}).get("action")
        if isinstance(item.ai_fusion.get("action_playbook"), dict)
        else None,
        item.ai_fusion.get("action"),
        (item.ai_fusion.get("recommended_action") or {}).get("action")
        if isinstance(item.ai_fusion.get("recommended_action"), dict)
        else None,
    ]
    for candidate in candidates:
        if candidate in {"EXIT", "REDUCE", "HOLD", "OBSERVE", "TEST", "ADD", "NO_ACTION"}:
            return candidate

    primary_path = str(item.ai_fusion.get("primary_path") or item.ai_fusion.get("primary_path_id") or "")
    risk_level = str(item.radar.get("risk_level") or item.ai_fusion.get("risk_level") or "")
    if item.is_holding and (primary_path.startswith("C") or risk_level == "HIGH"):
        return "REDUCE"
    if item.is_holding:
        return "HOLD"
    return "OBSERVE"


def _intent_type(action: RebalanceAction, is_holding: bool) -> RebalanceIntentType:
    if action in {"EXIT", "REDUCE"}:
        return "REDUCE_OR_EXIT"
    if action == "HOLD":
        return "HOLD_WITH_DEFENSE"
    if action == "TEST":
        return "TEST_ENTRY"
    if action == "ADD":
        return "ADD_ON_CONFIRMATION"
    if action == "OBSERVE" and not is_holding:
        return "WATCH_REPLACEMENT"
    return "NO_ACTION"


def _urgency(action: RebalanceAction, item: RebalanceEngineInputItem, memory: RebalanceMemory) -> RebalanceUrgency:
    if _is_fusion_fallback(item.ai_fusion):
        return "WATCH_ONLY"
    primary_path = str(item.ai_fusion.get("primary_path") or item.ai_fusion.get("primary_path_id") or "")
    if memory.urgency_escalated and action in {"EXIT", "REDUCE"}:
        return "IMMEDIATE"
    if action == "EXIT" or (action == "REDUCE" and primary_path.startswith("C")):
        return "IMMEDIATE"
    if action == "REDUCE":
        return "NEXT_SESSION"
    if action in {"TEST", "ADD"}:
        return "CONDITIONAL_WAIT"
    return "WATCH_ONLY"


def _memory(item: RebalanceEngineInputItem, generated_at: str, action: RebalanceAction) -> RebalanceMemory:
    previous_count = int(item.memory.get("previous_intent_count") or 0)
    last_response = item.memory.get("last_user_response")
    escalated = bool(item.memory.get("urgency_escalated") or False)
    if _should_escalate_from_memory(action, item.is_holding, previous_count, last_response):
        escalated = True
    return RebalanceMemory(
        previous_intent_count=previous_count,
        first_seen_at=str(item.memory.get("first_seen_at") or generated_at),
        last_user_response=last_response,
        urgency_escalated=escalated,
    )


def _should_escalate_from_memory(
    action: RebalanceAction,
    is_holding: bool,
    previous_count: int,
    last_response: Any,
) -> bool:
    if not is_holding or action not in {"EXIT", "REDUCE"} or previous_count < 2:
        return False
    if last_response in {"EXECUTED", "INVALIDATED"}:
        return False
    return True


def _evidence_notes(memory: RebalanceMemory) -> list[str]:
    notes = ["调仓意图来自单票 Fusion/Radar/Kronos 证据汇总，不自动执行交易。"]
    if memory.previous_intent_count > 0:
        notes.append(f"该标的此前已有 {memory.previous_intent_count} 次调仓意图记录，已纳入本次紧急度判断。")
    if memory.urgency_escalated:
        notes.append("历史上多次提示但未执行/仍继续观察，本次紧急度上调为优先复核。")
    return notes


def _recommended_action(action: RebalanceAction, item: RebalanceEngineInputItem) -> RecommendedAction:
    playbook = item.ai_fusion.get("action_playbook") if isinstance(item.ai_fusion.get("action_playbook"), dict) else {}
    label_map = {
        "EXIT": "退出或降到极小观察仓",
        "REDUCE": "降低风险暴露",
        "HOLD": "持有但守防线",
        "OBSERVE": "观察等待确认",
        "TEST": "满足条件后试仓",
        "ADD": "确认后再加仓",
        "NO_ACTION": "无动作",
    }
    if _is_fusion_fallback(item.ai_fusion):
        reason = _fallback_rebalance_reason(item.ai_fusion)
    else:
        reason = (
            str(playbook.get("primary_reason") or item.ai_fusion.get("reason") or item.ai_fusion.get("current_judgement") or "")
            or "当前只生成调仓意图，最终动作必须回到条件和人工复核。"
        )
    return RecommendedAction(
        action=action,
        action_label=str(playbook.get("action_label") or label_map[action]),
        position_delta=_position_delta(action),
        max_after_weight_pct=_playbook_max_weight(playbook) or _max_after_weight(action, item),
        reason=f"{reason}。{DISCLAIMER}",
    )


def _conditions(action: RebalanceAction, item: RebalanceEngineInputItem) -> RebalanceConditions:
    playbook = item.ai_fusion.get("action_playbook") if isinstance(item.ai_fusion.get("action_playbook"), dict) else {}
    wait_for = _as_list(item.ai_fusion.get("wait_for"))
    invalidation = _as_list(item.ai_fusion.get("invalidation"))
    defense_line = _defense_line(item)

    if _is_fusion_fallback(item.ai_fusion):
        execute_if = []
        delay_if = [_fallback_rebalance_reason(item.ai_fusion)]
        invalidate_if = ["重新生成 AI Fusion 并得到 AI_READY 状态后，再评估是否导入调仓动作。"]
    elif action in {"EXIT", "REDUCE"}:
        condition_key = "exit_conditions" if action == "EXIT" else "reduce_conditions"
        execute_if = _as_list(playbook.get(condition_key)) or wait_for or ["Fusion 维持 REDUCE/EXIT，且闭合分钟 K 未重新修复结构。"]
        delay_if = ["闭合 30 分钟 K 重新站回关键结构线，并由 Fusion 降级为 HOLD/OBSERVE。"]
    elif action in {"TEST", "ADD"}:
        condition_key = "test_conditions" if action == "TEST" else "add_conditions"
        execute_if = _as_list(playbook.get(condition_key)) or wait_for or ["闭合 5/30 分钟 K 满足 Fusion 触发条件。"]
        delay_if = ["实时价触碰但分钟 K 未闭合确认。"]
    elif action == "HOLD":
        execute_if = _as_list(playbook.get("hold_conditions")) or ["防线未破，结构仍维持，按计划持有观察。"]
        delay_if = ["出现价格触线但未形成闭合 K 结构确认。"]
    else:
        execute_if = []
        delay_if = wait_for or ["等待更清晰的结构确认。"]

    if not _is_fusion_fallback(item.ai_fusion):
        invalidate_if = invalidation or [
            f"跌破防线 {defense_line}" if defense_line is not None else "Fusion/Radar 给出结构失效。"
        ]
    return RebalanceConditions(
        execute_if=execute_if,
        delay_if=delay_if,
        invalidate_if=invalidate_if,
        recheck_at=_playbook_recheck(playbook),
    )


def _risk(action: RebalanceAction, item: RebalanceEngineInputItem) -> RebalanceRisk:
    risk_level = str(item.radar.get("risk_level") or item.ai_fusion.get("risk_level") or "UNKNOWN")
    if risk_level not in {"LOW", "MEDIUM", "HIGH", "UNKNOWN"}:
        risk_level = "UNKNOWN"
    if _is_fusion_fallback(item.ai_fusion):
        failure = "AI Fusion 处于结构兜底状态，若把兜底当作正式调仓信号，容易误把低确定性判断放大为动作。"
    else:
        failure = {
            "EXIT": "若继续拖延，可能从结构失效演变成被动持仓。",
            "REDUCE": "若不降低暴露，弱结构波动可能继续放大账户回撤。",
            "HOLD": "若防线失守但继续按持有处理，纪律锚会失效。",
            "TEST": "若未等确认就试仓，容易把观察机会变成追涨。",
            "ADD": "若未等确认就加仓，容易在震荡中扩大风险。",
            "OBSERVE": "若把观察当成买入信号，会绕过结构确认。",
            "NO_ACTION": "无明确优势时强行动作会增加噪音交易。",
        }[action]
    return RebalanceRisk(
        defense_line=_defense_line(item),
        risk_level=risk_level,  # type: ignore[arg-type]
        failure_mode=f"{failure}{DISCLAIMER}",
    )


def _portfolio_state(
    state: PortfolioState | dict[str, Any] | None,
    items: list[RebalanceEngineInputItem],
) -> PortfolioState:
    if isinstance(state, PortfolioState):
        return state
    if isinstance(state, dict):
        return PortfolioState(**state)
    holdings = [item for item in items if item.is_holding]
    max_weight = max((item.weight_pct or 0 for item in holdings), default=0)
    return PortfolioState(
        position_count=len(holdings),
        max_position_weight_pct=round(max_weight, 2),
        risk_posture="DEFENSIVE" if max_weight >= 20 or len(holdings) > 8 else "BALANCED",
        summary=f"基于当前输入生成调仓意图，释放资金默认等待确认。{DISCLAIMER}",
    )


def _summary(intents: list[RebalanceIntent]) -> RebalanceSummary:
    return RebalanceSummary(
        immediate_count=sum(1 for item in intents if item.urgency == "IMMEDIATE"),
        next_session_count=sum(1 for item in intents if item.urgency == "NEXT_SESSION"),
        conditional_wait_count=sum(1 for item in intents if item.urgency == "CONDITIONAL_WAIT"),
        watch_only_count=sum(1 for item in intents if item.urgency == "WATCH_ONLY"),
        coach_message=f"调仓只输出条件化意图；先处理高紧急度，再等待候选确认。{DISCLAIMER}",
    )


def _position_delta(action: RebalanceAction) -> str:
    return {
        "EXIT": "REDUCE_TO_ZERO_OR_TINY_TRACKING",
        "REDUCE": "REDUCE_RISK_EXPOSURE",
        "HOLD": "KEEP_WITH_DEFENSE",
        "OBSERVE": "NO_POSITION_CHANGE",
        "TEST": "SMALL_TEST_ONLY_AFTER_CONFIRMATION",
        "ADD": "ADD_ONLY_AFTER_CONFIRMATION",
        "NO_ACTION": "NO_POSITION_CHANGE",
    }[action]


def _max_after_weight(action: RebalanceAction, item: RebalanceEngineInputItem) -> Optional[float]:
    if action == "EXIT":
        return 0.5
    if action == "REDUCE":
        current = item.weight_pct or 0
        return round(max(current * 0.5, 0.5), 2)
    if action == "TEST":
        return 3.0
    if action == "ADD":
        return 8.0
    return item.weight_pct


def _defense_line(item: RebalanceEngineInputItem) -> Optional[float]:
    for source in (item.ai_fusion, item.radar):
        raw = source.get("defense_line")
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _playbook_max_weight(playbook: dict[str, Any]) -> Optional[float]:
    try:
        value = playbook.get("max_position_weight_pct")
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _playbook_recheck(playbook: dict[str, Any]) -> RefreshTrigger:
    value = str(playbook.get("recheck_trigger") or "NEXT_30M_CLOSE")
    if value in {"NEXT_5M_CLOSE", "NEXT_30M_CLOSE", "NEXT_DAILY_CLOSE", "PRICE_TOUCH", "MANUAL_REFRESH", "POSITION_CHANGE"}:
        return value  # type: ignore[return-value]
    return "NEXT_30M_CLOSE"


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if value:
        return [str(value)]
    return []


def _is_fusion_fallback(ai_fusion: dict[str, Any]) -> bool:
    return _fusion_status(ai_fusion).get("state") == "FALLBACK"


def _fallback_rebalance_reason(ai_fusion: dict[str, Any]) -> str:
    status = _fusion_status(ai_fusion)
    reason = str(status.get("fallback_reason") or "").strip()
    if not reason:
        reason = "AI Fusion 处于结构兜底状态"
    return f"{reason}，本轮只加入人工复核，不生成减仓/加仓动作"


def _default_valid_until(now: datetime, trigger: RefreshTrigger) -> str:
    return valid_until_for_refresh_trigger(now, trigger)


def _parse_generated_at(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
