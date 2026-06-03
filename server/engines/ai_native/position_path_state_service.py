"""Deterministic position path state derived from unified reasoning watch state."""
from __future__ import annotations

from typing import Any


def derive_position_path_state(
    *,
    summary: dict[str, Any] | None,
    current_price: float | int | None = None,
    position: dict[str, Any] | None = None,
    t0_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the v1 path state without calling an LLM.

    The AI still provides the compact watch_state_machine. This service only
    evaluates where the current price sits inside that machine and returns a
    stable UI payload.
    """
    machine = _extract_machine(summary or {})
    if not machine:
        return {
            "version": "position_path_state.v1",
            "data_status": "missing",
            "lifecycle_stage": _lifecycle_stage(position, t0_state),
            "major_task": "",
            "current_phase": "暂无路径状态",
            "success_path": "",
            "failure_path": "",
            "next_focus": "等待统一推演提取状态机",
            "draft_action": "WAIT",
            "range": None,
            "active_transition": None,
        }

    price = _num(current_price)
    current = machine.get("current_state") if isinstance(machine.get("current_state"), dict) else {}
    transitions = _normalized_transitions(machine)
    active = _active_transition(transitions, price)
    nearest = _nearest_transition(transitions, price)
    chosen = active or nearest or {}

    current_display = _clean(current.get("display") or current.get("name"))
    major_task = _clean(current.get("name") or current.get("display") or (summary or {}).get("one_liner"))
    current_phase = _clean((active or {}).get("next_state") or current_display or "等待路径确认")
    next_focus = _clean(
        chosen.get("observe")
        or chosen.get("next_watch")
        or (summary or {}).get("card_secondary")
        or "等待下一次结构触发"
    )
    success_path = _clean(chosen.get("success"))
    failure_path = _clean(chosen.get("failure"))

    return {
        "version": "position_path_state.v1",
        "data_status": "ready",
        "lifecycle_stage": _lifecycle_stage(position, t0_state),
        "major_task": major_task,
        "current_phase": current_phase,
        "success_path": success_path,
        "failure_path": failure_path,
        "next_focus": next_focus,
        "draft_action": _draft_action(active, t0_state),
        "range": _state_range(current),
        "active_transition": active,
        "nearest_transition": nearest,
    }


def _extract_machine(summary: dict[str, Any]) -> dict[str, Any]:
    direct = summary.get("watch_state_machine") if isinstance(summary.get("watch_state_machine"), dict) else {}
    plan = summary.get("watch_plan") if isinstance(summary.get("watch_plan"), dict) else {}
    nested = plan.get("watch_state_machine") if isinstance(plan.get("watch_state_machine"), dict) else {}
    machine = direct if direct.get("version") else nested
    transitions = machine.get("transitions") if isinstance(machine.get("transitions"), list) else []
    current = machine.get("current_state") if isinstance(machine.get("current_state"), dict) else {}
    if not transitions and not current:
        return {}
    return machine


def _normalized_transitions(machine: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for item in machine.get("transitions") or []:
        if not isinstance(item, dict):
            continue
        trigger = item.get("trigger") if isinstance(item.get("trigger"), dict) else {}
        trigger_type = str(trigger.get("type") or "")
        level = _num(trigger.get("level"))
        if trigger_type not in {"price_above", "price_below"} or level <= 0:
            continue
        result.append({**item, "trigger": {"type": trigger_type, "level": level}})
    return result


def _active_transition(transitions: list[dict[str, Any]], price: float) -> dict[str, Any] | None:
    if price <= 0:
        return None
    active = []
    for item in transitions:
        trigger = item.get("trigger") or {}
        level = _num(trigger.get("level"))
        if trigger.get("type") == "price_above" and price >= level:
            active.append(item)
        if trigger.get("type") == "price_below" and price <= level:
            active.append(item)
    return _nearest_transition(active, price)


def _nearest_transition(transitions: list[dict[str, Any]], price: float) -> dict[str, Any] | None:
    if not transitions:
        return None
    if price <= 0:
        return transitions[0]
    return sorted(
        transitions,
        key=lambda item: abs(price - _num((item.get("trigger") or {}).get("level"))),
    )[0]


def _state_range(current: dict[str, Any]) -> dict[str, float] | None:
    raw = current.get("range") if isinstance(current.get("range"), list) else []
    if len(raw) < 2:
        return None
    low = _num(raw[0])
    high = _num(raw[1])
    if low <= 0 or high <= 0 or low == high:
        return None
    return {"low": min(low, high), "high": max(low, high)}


def _lifecycle_stage(position: dict[str, Any] | None, t0_state: dict[str, Any] | None) -> str:
    if t0_state and str(t0_state.get("state") or "") == "LOCKDOWN":
        return "lockdown"
    shares = _num((position or {}).get("shares"))
    if shares <= 0:
        return "watching"
    if t0_state and str(t0_state.get("state") or "") in {"POSITION_LONG", "POSITION_SHORT"}:
        return "defense_t0"
    return "entry_validation"


def _draft_action(active_transition: dict[str, Any] | None, t0_state: dict[str, Any] | None) -> str:
    if t0_state and str(t0_state.get("state") or "") == "LOCKDOWN":
        return "LOCKDOWN"
    if t0_state and t0_state.get("signal"):
        return "T0_PAPER_ACTION"
    if active_transition:
        return "REVIEW_TRIGGER"
    return "WAIT"


def _num(value: Any) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0.0
    return num if num == num else 0.0


def _clean(value: Any) -> str:
    return str(value or "").strip()
