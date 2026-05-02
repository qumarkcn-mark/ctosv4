"""Position-aware Radar coaching rule tests."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.decision.position_coach import (
    build_coach_action,
    build_position_context,
    infer_radar_state,
)


def algorithm(path="UPWARD_MAJOR_WAVE", patterns=None, price=20.0):
    return {
        "path": path,
        "summary": "主推演测试",
        "patterns": patterns or [],
        "atoms": {
            "L2": {"price": price},
            "L1": {"price": price},
            "L0": {"price": price},
        },
        "boundaries": {
            "confirm": [{"level": "5", "field": "ZG", "value": 19.5, "meaning": "确认"}],
            "maintain": [{"level": "5", "field": "ZD", "value": 18.0, "meaning": "防守"}],
            "invalidate": [{"level": "30", "field": "ZD", "value": 16.0, "meaning": "失效"}],
        },
        "trigger_playbook": [
            {"path": "A", "condition": "突破5ZG 19.5", "then": "转强"},
        ],
    }


def test_empty_third_buy_enters_pre_check():
    algo = algorithm(patterns=[{"code": "THIRD_BUY_RETEST_UP"}])
    context = build_position_context(None, algo)
    action = build_coach_action(context, algo)

    assert infer_radar_state(algo) == "THIRD_BUY_CONFIRMED"
    assert context["state"] == "EMPTY"
    assert action["action"] == "PRE_CHECK"
    assert action["tone"] == "confirm"
    assert action["boundaries"][0]["label"] == "确认线"


def test_profit_heavy_main_wave_uses_trailing_stop():
    algo = algorithm(price=23.0)
    holding = {"qty": 1000, "cost": 18.0}
    context = build_position_context(holding, algo, account_value=100000)
    action = build_coach_action(context, algo)

    assert context["state"] == "PROFIT_HEAVY"
    assert context["pnl_pct"] == 27.78
    assert action["action"] == "TRAIL_STOP"
    assert "利润保护" in action["focus"]


def test_position_context_prefers_quote_price_for_pnl():
    algo = algorithm(price=20.0)
    holding = {"qty": 1000, "cost": 18.0}
    context = build_position_context(
        holding,
        algo,
        quote={"price": 21.6, "time": "2026-04-28 11:30:00"},
    )

    assert context["current_price"] == 21.6
    assert context["structure_price"] == 20.0
    assert context["quote_price"] == 21.6
    assert context["price_source"] == "tencent_quote"
    assert context["pnl_pct"] == 20.0


def test_realtime_quote_desync_is_explicit_and_does_not_silent_mix_with_structure():
    algo = algorithm(path="CENTER_REBOUND", price=67.5)
    context = build_position_context(None, algo, quote={"price": 81.0})
    action = build_coach_action(context, algo)

    assert context["current_price"] == 81.0
    assert context["structure_price"] == 67.5
    assert context["is_realtime_desynced"] is True
    assert context["realtime_gap_pct"] == 20.0
    assert "实时价与正式结构价" in action["reason"]


def test_coach_action_merges_stop_and_structure_risk_lines():
    algo = algorithm(price=20.0)
    holding = {
        "qty": 1000,
        "cost": 18.0,
        "stop_loss_price": 16.5,
        "trailing_stop_price": 19.2,
        "m5_entry_zg": 18.8,
    }
    context = build_position_context(holding, algo, quote={"price": 20.0})
    action = build_coach_action(context, algo)

    labels = [item["label"] for item in action["risk_lines"]]
    assert "成本线" in labels
    assert "原始止损" in labels
    assert "移动止盈" in labels
    assert "结构防守 5ZD" in labels
    assert action["nearest_risk_line"]["label"] == "移动止盈"
    assert action["nearest_risk_line"]["distance_pct"] == 4.0


def test_loss_position_downward_prioritizes_stop_loss():
    algo = algorithm(path="DOWNWARD_DEFENSE", price=17.0)
    holding = {"qty": 1000, "cost": 20.0}
    context = build_position_context(holding, algo, account_value=100000)
    action = build_coach_action(context, algo)

    assert infer_radar_state(algo) == "DOWNWARD_LEAVING"
    assert context["state"] == "LOSS_HOLDING"
    assert "STRUCTURE_AGAINST_POSITION" in context["risk_flags"]
    assert action["action"] == "STOP_LOSS_PRIORITY"
    assert action["priority"] == "HIGH"
