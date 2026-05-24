"""Local intraday event markers for watchboard cards.

These markers never call AI. They translate the latest price and saved watch
plan triggers into compact facts the UI can highlight during the trading day.
"""

from __future__ import annotations

from typing import Any


NEAR_THRESHOLD_PCT = 0.8


def build_intraday_event_state(item: dict[str, Any]) -> dict[str, Any]:
    price = _num(item.get("price"))
    triggers = ((item.get("monitor_conditions") or {}).get("triggers") or [])
    events: list[dict[str, Any]] = []
    if price <= 0 or not triggers:
        return {"events": [], "primary": None}

    for trigger in triggers:
        event = _event_from_trigger(trigger, price)
        if event:
            events.append(event)

    events = sorted(events, key=lambda event: (event["priority"], event["distance_pct"]))
    return {
        "events": events[:4],
        "primary": events[0] if events else None,
    }


def _event_from_trigger(trigger: dict[str, Any], price: float) -> dict[str, Any] | None:
    level = _num(trigger.get("level"))
    trigger_type = str(trigger.get("type") or "")
    if level <= 0 or trigger_type not in {"price_above", "price_below"}:
        return None
    distance_pct = abs(price - level) / price * 100 if price > 0 else 0
    message = str(trigger.get("message_on_trigger") or "").strip()
    action = str(trigger.get("action_on_trigger") or "").strip()

    if trigger_type == "price_above":
        if price >= level:
            event_type = "break_pressure_pending_confirm"
            text = message or f"站上{_fmt(level)}，待确认"
            priority = 0
        elif distance_pct <= NEAR_THRESHOLD_PCT:
            event_type = "near_pressure"
            text = f"接近{_fmt(level)}压力"
            priority = 2
        else:
            return None
    else:
        if price <= level:
            event_type = "lose_support_pending_confirm"
            text = message or f"跌破{_fmt(level)}，看能否收回"
            priority = 0
        elif distance_pct <= NEAR_THRESHOLD_PCT:
            event_type = "near_support"
            text = f"接近{_fmt(level)}支撑"
            priority = 2
        else:
            return None

    if action and action not in text:
        text = f"{text}，{action}"
    return {
        "type": event_type,
        "level": round(level, 4),
        "price": round(price, 4),
        "distance_pct": round(distance_pct, 3),
        "message": text[:42],
        "source": "watch_plan_trigger",
        "priority": priority,
        "trigger": {
            "type": trigger_type,
            "level": round(level, 4),
            "id": trigger.get("id") or "",
        },
    }


def _fmt(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
