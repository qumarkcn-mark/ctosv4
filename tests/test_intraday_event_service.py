from server.engines.ai_native.intraday_event_service import build_intraday_event_state


def test_intraday_event_marks_near_support_without_ai():
    state = build_intraday_event_state({
        "price": 4.4,
        "monitor_conditions": {
            "triggers": [
                {"type": "price_below", "level": 4.37, "message_on_trigger": "到承接位", "action_on_trigger": "关注"},
                {"type": "price_above", "level": 4.8, "message_on_trigger": "突破压力", "action_on_trigger": "关注"},
            ]
        },
    })

    assert state["primary"]["type"] == "near_support"
    assert state["primary"]["message"] == "接近4.37支撑，关注"


def test_intraday_event_marks_break_and_loss_as_pending_confirmation():
    break_state = build_intraday_event_state({
        "price": 4.51,
        "monitor_conditions": {"triggers": [{"type": "price_above", "level": 4.5}]},
    })
    lose_state = build_intraday_event_state({
        "price": 4.12,
        "monitor_conditions": {"triggers": [{"type": "price_below", "level": 4.13}]},
    })

    assert break_state["primary"]["type"] == "break_pressure_pending_confirm"
    assert break_state["primary"]["message"] == "站上4.5，待确认"
    assert lose_state["primary"]["type"] == "lose_support_pending_confirm"
    assert lose_state["primary"]["message"] == "跌破4.13，看能否收回"


def test_intraday_event_ignores_far_levels():
    state = build_intraday_event_state({
        "price": 4.4,
        "monitor_conditions": {"triggers": [{"type": "price_above", "level": 4.8}]},
    })

    assert state == {"events": [], "primary": None}
