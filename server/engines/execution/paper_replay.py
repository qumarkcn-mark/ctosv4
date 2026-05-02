"""Small replay harness for paper-only intraday T experiments."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from server.engines.decision.intraday_t_features import IntradayTFeatures
from server.engines.decision.intraday_t_strategy import (
    IntradayTDecision,
    IntradayTParentContext,
    IntradayTState,
    evaluate_intraday_t,
    parent_context_from_features,
)
from server.engines.execution.paper_adapter import simulate_next_bar_fill
from server.engines.execution.paper_models import PaperAccount, PaperFill, PaperKline, PaperRiskConfig


@dataclass(frozen=True)
class ReplayStep:
    features: IntradayTFeatures
    next_bar: PaperKline | dict[str, Any] | None = None


@dataclass(frozen=True)
class ReplayResult:
    account: PaperAccount
    decisions: list[IntradayTDecision] = field(default_factory=list)
    fills: list[PaperFill] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


def replay_intraday_t_steps(
    account: PaperAccount,
    steps: list[ReplayStep],
    config: PaperRiskConfig,
    parent_context: IntradayTParentContext | None = None,
    auto_parent_context: bool = False,
    parent_max_cycles: int = 1,
) -> ReplayResult:
    """Replay precomputed feature steps through the baseline strategy."""
    decisions: list[IntradayTDecision] = []
    fills: list[PaperFill] = []
    state = IntradayTState()
    parent = parent_context
    current_account = account

    for index, step in enumerate(steps):
        if auto_parent_context:
            parent = parent_context_from_features(step.features, max_cycles=parent_max_cycles, previous=parent)
        decision = evaluate_intraday_t(step.features, current_account, config, state, parent)
        decision = _apply_first_leg_window_guard(decision, state, config, remaining_bars=len(steps) - index - 1)
        decisions.append(decision)
        if decision.intent is None:
            pending_state = _pending_state_from_decision(decision)
            if pending_state is not None:
                state = pending_state
                continue
            if state.pending_first_leg_side:
                state = _advance_pending_first_leg(state)
                continue
            if state.waiting_second_leg:
                state = IntradayTState(
                    waiting_second_leg=True,
                    first_leg_side=state.first_leg_side,
                    first_leg_intent_id=state.first_leg_intent_id,
                    first_leg_filled_at=state.first_leg_filled_at,
                    bars_since_first_leg=state.bars_since_first_leg + 1,
                )
            continue

        if step.next_bar is None:
            continue

        current_account, fill = simulate_next_bar_fill(current_account, decision.intent, step.next_bar, config)
        fills.append(fill)

        if fill.status == "FILLED":
            parent = _advance_parent_context(parent, decision, state)
            if state.waiting_second_leg:
                state = IntradayTState()
            else:
                state = IntradayTState(
                    waiting_second_leg=True,
                    first_leg_side=decision.intent.side,
                    first_leg_intent_id=decision.intent.intent_id,
                    first_leg_filled_at=fill.filled_at,
                )

    return ReplayResult(
        account=current_account,
        decisions=decisions,
        fills=fills,
        metrics=build_replay_metrics(account, current_account, decisions, fills),
    )


def _pending_state_from_decision(decision: IntradayTDecision) -> IntradayTState | None:
    if decision.status != "CANDIDATE_ONLY" or decision.reason != "pending_first_leg_confirmation":
        return None
    pending = (decision.evidence or {}).get("pending_first_leg") or {}
    side = str(pending.get("side") or "")
    if side not in {"BUY", "SELL"}:
        return None
    return IntradayTState(
        pending_first_leg_side=side,
        pending_first_leg_condition_id=str(pending.get("condition_id") or ""),
        pending_first_leg_event_key=str(pending.get("event_key") or ""),
        pending_first_leg_event_price=float(pending.get("event_price") or 0.0),
        pending_first_leg_bars=1,
    )


def _advance_pending_first_leg(state: IntradayTState) -> IntradayTState:
    return IntradayTState(
        pending_first_leg_side=state.pending_first_leg_side,
        pending_first_leg_condition_id=state.pending_first_leg_condition_id,
        pending_first_leg_event_key=state.pending_first_leg_event_key,
        pending_first_leg_event_price=state.pending_first_leg_event_price,
        pending_first_leg_bars=state.pending_first_leg_bars + 1,
    )


def _apply_first_leg_window_guard(
    decision: IntradayTDecision,
    state: IntradayTState,
    config: PaperRiskConfig,
    *,
    remaining_bars: int,
) -> IntradayTDecision:
    min_remaining = config.min_bars_before_window_end_for_first_leg
    if min_remaining <= 0 or state.waiting_second_leg or decision.intent is None:
        return decision
    if remaining_bars >= min_remaining:
        return decision
    return IntradayTDecision(
        decision="NO_TRADE",
        status="WATCHING",
        reason="insufficient_window_for_first_leg",
        evidence={
            **(decision.evidence or {}),
            "window_guard": {
                "remaining_bars": remaining_bars,
                "min_bars_before_window_end_for_first_leg": min_remaining,
                "blocked_decision": decision.decision,
                "blocked_reason": decision.reason,
            },
        },
    )


def _advance_parent_context(
    parent: IntradayTParentContext | None,
    decision: IntradayTDecision,
    state: IntradayTState,
) -> IntradayTParentContext | None:
    if parent is None:
        return None
    event_key = str((decision.evidence or {}).get("event_key") or "")
    consumed = parent.consumed_event_keys | (frozenset([event_key]) if event_key else frozenset())
    used_cycles = parent.used_cycles + (1 if state.waiting_second_leg else 0)
    return IntradayTParentContext(
        parent_level=parent.parent_level,
        parent_task=parent.parent_task,
        parent_leg_id=parent.parent_leg_id,
        allowed_first_side=parent.allowed_first_side,
        max_cycles=parent.max_cycles,
        used_cycles=used_cycles,
        consumed_event_keys=consumed,
    )


def build_replay_metrics(
    start_account: PaperAccount,
    end_account: PaperAccount,
    decisions: list[IntradayTDecision],
    fills: list[PaperFill],
) -> dict[str, Any]:
    filled = [fill for fill in fills if fill.status == "FILLED"]
    first_leg_count = max(0, (len(filled) + 1) // 2)
    closed_t_count = len(filled) // 2
    open_t_count = max(0, first_leg_count - closed_t_count)
    no_fill_count = len([fill for fill in fills if fill.status == "NOT_FILLED"])
    timeout_count = len([d for d in decisions if d.decision == "BUYBACK_TIMEOUT"])
    normal_second_leg_count = len([d for d in decisions if d.reason in {"buyback_triggered", "sellback_triggered"}])
    forced_second_leg_count = len([d for d in decisions if d.reason == "second_leg_timeout_force_close"])
    second_leg_watch_count = len([d for d in decisions if d.decision == "SECOND_LEG_WATCHING"])
    open_risk_bars = _open_risk_bars(decisions)
    t_pnl = _closed_t_pnl(filled)
    closure_rate = closed_t_count / first_leg_count if first_leg_count else 0.0
    return {
        "filled_count": len(filled),
        "no_fill_count": no_fill_count,
        "first_leg_count": first_leg_count,
        "closed_t_count": closed_t_count,
        "open_t_count": open_t_count,
        "normal_second_leg_count": normal_second_leg_count,
        "forced_second_leg_count": forced_second_leg_count,
        "second_leg_watch_count": second_leg_watch_count,
        "max_open_risk_bars": max(open_risk_bars) if open_risk_bars else 0,
        "avg_open_risk_bars": round(sum(open_risk_bars) / len(open_risk_bars), 4) if open_risk_bars else 0.0,
        "last_open_risk_bars": open_risk_bars[-1] if open_risk_bars else 0,
        "t_closure_rate": round(closure_rate, 4),
        "timeout_count": timeout_count,
        **t_pnl,
        "realized_pnl": round(end_account.realized_pnl - start_account.realized_pnl, 4),
        "cash_delta": round(end_account.cash - start_account.cash, 4),
        "decision_counts": dict(Counter(decision.decision for decision in decisions)),
        "reason_counts": dict(Counter(decision.reason for decision in decisions)),
        "decision_status_counts": dict(Counter(decision.status for decision in decisions)),
    }


def _closed_t_pnl(filled: list[PaperFill]) -> dict[str, Any]:
    """Pair filled legs into closed T cycles and split spread, fees, and slippage."""
    cycles = []
    for index in range(0, len(filled) - 1, 2):
        first = filled[index]
        second = filled[index + 1]
        if first.symbol != second.symbol:
            continue
        quantity = min(first.quantity, second.quantity)
        if quantity <= 0:
            continue
        if first.side == "SELL" and second.side == "BUY":
            spread_pnl = (first.fill_price - second.fill_price) * quantity
        elif first.side == "BUY" and second.side == "SELL":
            spread_pnl = (second.fill_price - first.fill_price) * quantity
        else:
            continue
        fees = _allocated_cost(first, quantity) + _allocated_cost(second, quantity)
        slippage_cost = _allocated_slippage(first, quantity) + _allocated_slippage(second, quantity)
        gross_pnl = spread_pnl + slippage_cost
        cycles.append(
            {
                "spread_pnl": spread_pnl,
                "gross_pnl": gross_pnl,
                "fees": fees,
                "slippage_cost": slippage_cost,
                "net_pnl": spread_pnl - fees,
            }
        )

    spread_pnl = sum(item["spread_pnl"] for item in cycles)
    gross_pnl = sum(item["gross_pnl"] for item in cycles)
    fees = sum(item["fees"] for item in cycles)
    slippage_cost = sum(item["slippage_cost"] for item in cycles)
    net_pnl = sum(item["net_pnl"] for item in cycles)
    return {
        "closed_t_cycle_count": len(cycles),
        "gross_t_pnl": round(gross_pnl, 4),
        "spread_t_pnl": round(spread_pnl, 4),
        "total_fees": round(fees, 4),
        "slippage_cost": round(slippage_cost, 4),
        "net_t_pnl": round(net_pnl, 4),
        "avg_net_t_pnl": round(net_pnl / len(cycles), 4) if cycles else 0.0,
    }


def _allocated_cost(fill: PaperFill, quantity: int) -> float:
    if fill.quantity <= 0:
        return 0.0
    ratio = quantity / fill.quantity
    return fill.total_cost * ratio


def _allocated_slippage(fill: PaperFill, quantity: int) -> float:
    return abs(fill.slippage) * quantity


def _open_risk_bars(decisions: list[IntradayTDecision]) -> list[int]:
    values = []
    for decision in decisions:
        if decision.decision not in {"SECOND_LEG_WATCHING", "BUYBACK_TIMEOUT"}:
            continue
        try:
            values.append(int((decision.evidence or {}).get("bars_since_first_leg") or 0))
        except (TypeError, ValueError):
            values.append(0)
    return values
