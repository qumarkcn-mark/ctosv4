"""Baseline intraday T strategy for paper simulation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from server.engines.decision.intraday_t_features import IntradayTFeatures
from server.engines.decision.intraday_t_position import (
    IntradayTEvent,
    build_intraday_t_position_plan,
    build_intraday_t_signals,
    signal_evidence,
)
from server.engines.execution.paper_models import PaperAccount, PaperIntent, PaperRiskConfig


TDecisionType = Literal[
    "NO_TRADE",
    "SELL_THEN_BUY_BACK",
    "BUY_THEN_SELL_BACK",
    "SECOND_LEG_WATCHING",
    "BUYBACK_TIMEOUT",
]


@dataclass(frozen=True)
class IntradayTState:
    waiting_second_leg: bool = False
    first_leg_side: str = ""
    first_leg_intent_id: str = ""
    first_leg_filled_at: str = ""
    bars_since_first_leg: int = 0
    pending_first_leg_side: str = ""
    pending_first_leg_condition_id: str = ""
    pending_first_leg_event_key: str = ""
    pending_first_leg_event_price: float = 0.0
    pending_first_leg_bars: int = 0


@dataclass(frozen=True)
class IntradayTParentContext:
    parent_level: str = ""
    parent_task: str = ""
    parent_leg_id: str = ""
    allowed_first_side: str = ""
    max_cycles: int = 0
    used_cycles: int = 0
    consumed_event_keys: frozenset[str] = frozenset()


@dataclass(frozen=True)
class IntradayTDecision:
    decision: TDecisionType
    status: str
    reason: str
    intent: PaperIntent | None = None
    evidence: dict = field(default_factory=dict)


def evaluate_intraday_t(
    features: IntradayTFeatures,
    account: PaperAccount,
    config: PaperRiskConfig,
    state: IntradayTState | None = None,
    parent: IntradayTParentContext | None = None,
) -> IntradayTDecision:
    state = state or IntradayTState()
    plan = build_intraday_t_position_plan(
        features,
        config,
        max_cycles=parent.max_cycles if parent and parent.max_cycles > 0 else 1,
    )
    signals = build_intraday_t_signals(features, account, config, state, plan)

    if features.is_stale:
        return _no_trade("stale_structure", features, signals=signals, plan=plan)

    position = account.positions.get(features.symbol)
    if position is None or position.protected_base_qty <= 0:
        return _no_trade("no_protected_base_position", features, signals=signals, plan=plan)

    if state.waiting_second_leg:
        return _evaluate_second_leg(features, account, config, state, plan, signals)

    if not _event_is_fresh(features, config):
        return _no_trade("event_not_fresh", features, signals=signals, plan=plan)

    pending = _pending_first_leg_decision(features, account, config, state, signals, plan)
    if pending is not None:
        return pending

    event = plan.select_event(signals)
    if event and event.side == "SELL":
        parent_block = _parent_first_leg_block(features, "SELL", parent, signals, plan)
        if parent_block:
            return parent_block
        if position.available_t_qty < config.default_t_qty:
            return _no_trade("insufficient_sellable_t_quantity", features, signals=signals, plan=plan)
        intent = _intent(features, account, config, "SELL", event.condition_id, state if state.waiting_second_leg else None)
        if _first_leg_confirmation_enabled(config):
            return _pending_first_leg_candidate(event, features, signals, plan)
        if config.observe_only:
            return _candidate_only(event, features, signals, plan)
        return IntradayTDecision(
            decision=event.decision,  # type: ignore[arg-type]
            status="INTENT_CREATED",
            reason=event.reason,
            intent=intent,
            evidence=_evidence(features, signals=signals, plan=plan, position_event=event),
        )

    if event and event.side == "BUY":
        parent_block = _parent_first_leg_block(features, "BUY", parent, signals, plan)
        if parent_block:
            return parent_block
        if account.cash < position.last_price * config.default_t_qty:
            return _no_trade("insufficient_cash", features, signals=signals, plan=plan)
        intent = _intent(features, account, config, "BUY", event.condition_id, state if state.waiting_second_leg else None)
        if _first_leg_confirmation_enabled(config):
            return _pending_first_leg_candidate(event, features, signals, plan)
        if config.observe_only:
            return _candidate_only(event, features, signals, plan)
        return IntradayTDecision(
            decision=event.decision,  # type: ignore[arg-type]
            status="INTENT_CREATED",
            reason=event.reason,
            intent=intent,
            evidence=_evidence(features, signals=signals, plan=plan, position_event=event),
        )

    return _no_trade("no_baseline_t_trigger", features, signals=signals, plan=plan)


def _pending_first_leg_decision(
    features: IntradayTFeatures,
    account: PaperAccount,
    config: PaperRiskConfig,
    state: IntradayTState,
    signals: dict,
    plan,
) -> IntradayTDecision | None:
    if not _first_leg_confirmation_enabled(config) or not state.pending_first_leg_side:
        return None
    if state.pending_first_leg_bars > config.event_freshness_bars:
        return _no_trade("pending_first_leg_stale", features, signals=signals, plan=plan)
    if not _pending_first_leg_confirmed(features, state, config):
        return IntradayTDecision(
            decision="NO_TRADE",
            status="WATCHING",
            reason="pending_first_leg_confirmation",
            evidence={
                **_evidence(features, signals=signals, plan=plan),
                "pending_first_leg": _pending_evidence(features, state, config),
            },
        )
    side = state.pending_first_leg_side
    position = account.positions.get(features.symbol)
    if side == "SELL" and position and position.available_t_qty < config.default_t_qty:
        return _no_trade("insufficient_sellable_t_quantity", features, signals=signals, plan=plan)
    if side == "BUY" and position and account.cash < position.last_price * config.default_t_qty:
        return _no_trade("insufficient_cash", features, signals=signals, plan=plan)
    intent = _intent(features, account, config, side, state.pending_first_leg_condition_id, None)
    return IntradayTDecision(
        decision="SELL_THEN_BUY_BACK" if side == "SELL" else "BUY_THEN_SELL_BACK",
        status="INTENT_CREATED",
        reason="confirmed_first_leg",
        intent=intent,
        evidence={
            **_evidence(features, signals=signals, plan=plan),
            "pending_first_leg": _pending_evidence(features, state, config),
        },
    )


def _pending_first_leg_candidate(event: IntradayTEvent, features: IntradayTFeatures, signals: dict, plan) -> IntradayTDecision:
    return IntradayTDecision(
        decision=event.decision,  # type: ignore[arg-type]
        status="CANDIDATE_ONLY",
        reason="pending_first_leg_confirmation",
        intent=None,
        evidence={
            **_evidence(features, signals=signals, plan=plan, position_event=event),
            "pending_first_leg": {
                "side": event.side,
                "condition_id": event.condition_id,
                "event_key": _event_key(features),
                "event_price": _event_price(features),
            },
        },
    )


def _first_leg_confirmation_enabled(config: PaperRiskConfig) -> bool:
    return int(getattr(config, "first_leg_confirmation_bars", 0) or 0) > 0


def _pending_first_leg_confirmed(features: IntradayTFeatures, state: IntradayTState, config: PaperRiskConfig) -> bool:
    min_bars = int(getattr(config, "first_leg_confirmation_bars", 0) or 0)
    if state.pending_first_leg_bars < min_bars:
        return False
    current = _feature_current_price(features)
    event_price = state.pending_first_leg_event_price
    if current <= 0 or event_price <= 0:
        return False
    if state.pending_first_leg_side == "SELL":
        return current < event_price
    if state.pending_first_leg_side == "BUY":
        return current > event_price
    return False


def _pending_evidence(features: IntradayTFeatures, state: IntradayTState, config: PaperRiskConfig) -> dict:
    current = _feature_current_price(features)
    return {
        "side": state.pending_first_leg_side,
        "condition_id": state.pending_first_leg_condition_id,
        "event_key": state.pending_first_leg_event_key,
        "event_price": state.pending_first_leg_event_price,
        "current_price": current,
        "bars": state.pending_first_leg_bars,
        "confirmation_bars": int(getattr(config, "first_leg_confirmation_bars", 0) or 0),
        "price_confirmed": _pending_first_leg_confirmed(features, state, config),
    }


def _event_price(features: IntradayTFeatures) -> float:
    try:
        return float(features.latest_event.get("price") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _feature_current_price(features: IntradayTFeatures) -> float:
    try:
        current_price = float(getattr(features, "current_price", 0.0) or 0.0)
    except (TypeError, ValueError):
        current_price = 0.0
    if current_price > 0:
        return current_price
    try:
        price = float(features.position_to_center.get("price") or 0.0)
    except (TypeError, ValueError):
        price = 0.0
    return price or _event_price(features)


def _candidate_only(
    event: IntradayTEvent,
    features: IntradayTFeatures,
    signals: dict,
    plan,
) -> IntradayTDecision:
    return IntradayTDecision(
        decision=event.decision,  # type: ignore[arg-type]
        status="CANDIDATE_ONLY",
        reason=f"observe_only_{event.reason}",
        intent=None,
        evidence={
            **_evidence(features, signals=signals, plan=plan, position_event=event),
            "observe_only": True,
        },
    )


def parent_context_from_features(
    features: IntradayTFeatures,
    *,
    max_cycles: int = 1,
    previous: IntradayTParentContext | None = None,
) -> IntradayTParentContext | None:
    raw = features.parent_context or {}
    allowed_first_side = str(raw.get("allowed_first_side") or "").upper()
    parent_leg_id = str(raw.get("parent_leg_id") or "")
    if not allowed_first_side or not parent_leg_id:
        return previous

    parent_level = str(raw.get("parent_level") or "")
    parent_task = str(raw.get("parent_task") or "")
    if previous is not None and previous.parent_leg_id == parent_leg_id:
        return IntradayTParentContext(
            parent_level=parent_level or previous.parent_level,
            parent_task=parent_task or previous.parent_task,
            parent_leg_id=parent_leg_id,
            allowed_first_side=allowed_first_side,
            max_cycles=max_cycles,
            used_cycles=previous.used_cycles,
            consumed_event_keys=previous.consumed_event_keys,
        )
    return IntradayTParentContext(
        parent_level=parent_level,
        parent_task=parent_task,
        parent_leg_id=parent_leg_id,
        allowed_first_side=allowed_first_side,
        max_cycles=max_cycles,
    )


def _evaluate_second_leg(
    features: IntradayTFeatures,
    account: PaperAccount,
    config: PaperRiskConfig,
    state: IntradayTState,
    plan,
    signals,
) -> IntradayTDecision:
    event = plan.select_event(signals)
    if event is not None and event.side in {"BUY", "SELL"}:
        intent = _intent(features, account, config, event.side, event.condition_id, state)
        return IntradayTDecision(
            decision=event.decision,  # type: ignore[arg-type]
            status="INTENT_CREATED",
            reason=event.reason,
            intent=intent,
            evidence={
                **_evidence(features, signals=signals, plan=plan, position_event=event),
                "bars_since_first_leg": state.bars_since_first_leg,
            },
        )

    if state.bars_since_first_leg >= config.buyback_timeout_bars:
        return IntradayTDecision(
            decision="BUYBACK_TIMEOUT",
            status="OPEN_RISK",
            reason="second_leg_timeout",
            evidence={**_evidence(features, signals=signals, plan=plan), "bars_since_first_leg": state.bars_since_first_leg},
        )

    return IntradayTDecision(
        decision="SECOND_LEG_WATCHING",
        status="WATCHING",
        reason="waiting_second_leg_trigger",
        evidence={**_evidence(features, signals=signals, plan=plan), "bars_since_first_leg": state.bars_since_first_leg},
    )


def _is_sell_first(features: IntradayTFeatures) -> bool:
    return (
        features.latest_event_side == "sell"
        and features.divergence_direction == "top"
        and features.divergence_strength >= 0.5
    )


def _is_buy_first(features: IntradayTFeatures) -> bool:
    return (
        features.latest_event_side == "buy"
        and features.divergence_direction == "bottom"
        and features.divergence_strength >= 0.5
    )


def _event_is_fresh(features: IntradayTFeatures, config: PaperRiskConfig) -> bool:
    return features.bars_since_event <= config.event_freshness_bars


def _parent_first_leg_block(
    features: IntradayTFeatures,
    first_side: str,
    parent: IntradayTParentContext | None,
    signals: dict | None = None,
    plan=None,
) -> IntradayTDecision | None:
    if parent is None:
        return None
    if parent.allowed_first_side and first_side != parent.allowed_first_side:
        return _no_trade("parent_direction_blocked", features, parent, signals=signals, plan=plan)
    if parent.max_cycles > 0 and parent.used_cycles >= parent.max_cycles:
        return _no_trade("parent_cycle_budget_used", features, parent, signals=signals, plan=plan)
    event_key = _event_key(features)
    if event_key and event_key in parent.consumed_event_keys:
        return _no_trade("event_already_consumed", features, parent, signals=signals, plan=plan)
    return None


def _event_key(features: IntradayTFeatures) -> str:
    event = features.latest_event or {}
    side = str(event.get("side") or "")
    code = str(event.get("code") or event.get("type") or "")
    time = str(event.get("time") or "")
    price = str(event.get("price") or "")
    if not (side or code or time):
        return ""
    return f"{features.symbol}:{side}:{code}:{time}:{price}"


def _intent(
    features: IntradayTFeatures,
    account: PaperAccount,
    config: PaperRiskConfig,
    side: str,
    condition_id: str,
    state: IntradayTState | None = None,
) -> PaperIntent:
    serial = account.trade_count + 1
    idempotency_key = (
        f"paper:{account.user_id}:{features.symbol}:intraday_t_base_position:"
        f"{features.as_of}:{condition_id}:{serial}"
    )
    return PaperIntent(
        intent_id=f"paper_intent_{serial}",
        idempotency_key=idempotency_key,
        user_id=account.user_id,
        paper_account_id=account.paper_account_id,
        symbol=features.symbol,
        side=side,  # type: ignore[arg-type]
        quantity=config.default_t_qty,
        created_at=features.as_of,
        price_policy={"source": "NEXT_BAR_OPEN", "slippage_bps": config.fees.slippage_bps},
        reason={"condition_id": condition_id, "evidence": _evidence(features)},
        linked_intent_id=state.first_leg_intent_id if state else "",
    )


def _no_trade(
    reason: str,
    features: IntradayTFeatures,
    parent: IntradayTParentContext | None = None,
    signals: dict | None = None,
    plan=None,
) -> IntradayTDecision:
    return IntradayTDecision(
        decision="NO_TRADE",
        status="BLOCKED" if reason in {"stale_structure", "no_protected_base_position"} else "WATCHING",
        reason=reason,
        evidence=_evidence(features, parent, signals=signals, plan=plan),
    )


def _evidence(
    features: IntradayTFeatures,
    parent: IntradayTParentContext | None = None,
    signals: dict | None = None,
    plan=None,
    position_event: IntradayTEvent | None = None,
) -> dict:
    evidence = {
        "symbol": features.symbol,
        "as_of": features.as_of,
        "paths": features.paths,
        "latest_event": features.latest_event,
        "divergence": features.divergence,
        "position_to_center": features.position_to_center,
        "pattern_tags": features.pattern_tags,
        "parent_context": features.parent_context,
    }
    if signals is not None:
        evidence["signals"] = signal_evidence(signals)
    if plan is not None:
        evidence["position_plan"] = {
            "name": plan.name,
            "timeout_bars": plan.timeout_bars,
            "stop_loss_bps": plan.stop_loss_bps,
            "interval_seconds": plan.interval_seconds,
            "max_cycles": plan.max_cycles,
            "exit_plan": plan.exit_plan,
        }
    if position_event is not None:
        evidence["position_event"] = {
            "name": position_event.name,
            "decision": position_event.decision,
            "reason": position_event.reason,
            "side": position_event.side,
            "signals_all": list(position_event.signals_all),
            "signals_any": list(position_event.signals_any),
            "signals_not": list(position_event.signals_not),
        }
    event_key = _event_key(features)
    if event_key:
        evidence["event_key"] = event_key
    if parent is not None:
        evidence["parent"] = {
            "parent_level": parent.parent_level,
            "parent_task": parent.parent_task,
            "parent_leg_id": parent.parent_leg_id,
            "allowed_first_side": parent.allowed_first_side,
            "max_cycles": parent.max_cycles,
            "used_cycles": parent.used_cycles,
        }
    return evidence
