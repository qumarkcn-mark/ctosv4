import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.decision.risk_sizing import calculate_position_size, check_stop_atr
from server.engines.decision.target_planner import (
    calculate_targets,
    check_reward_ratio,
    find_structural_high_s1,
    plan_holding_targets,
)


def test_calculate_targets_uses_recent_high_and_upper_center():
    targets = calculate_targets(
        current_price=20.0,
        bis=[
            {"is_up": True, "is_sure": True, "y1": 23.0},
            {"is_up": False, "is_sure": True, "y1": 19.0},
            {"is_up": True, "is_sure": True, "y1": 22.0},
        ],
        zhongshus=[{"zg": 25.0, "zd": 21.0}],
        stop_price=18.0,
    )

    assert targets[0]["label"] == "短期目标（前高）"
    assert targets[0]["price"] == 22.0
    assert targets[0]["rr_ratio"] == 1.0
    assert targets[1]["label"] == "中期目标（上方中枢上沿）"
    assert targets[1]["price"] == 25.0


def test_check_reward_ratio_blocks_insufficient_reward():
    result = check_reward_ratio(
        entry_price=20.0,
        stop_price=18.0,
        target_price=21.0,
        min_ratio=2.0,
    )

    assert result["ratio"] == 0.5
    assert result["ok"] is False


def test_check_stop_atr_marks_reasonable_range():
    result = check_stop_atr(current_price=20.0, stop_price=18.0, atr=1.0)

    assert result["valid"] is True
    assert result["atr_multiple"] == 2.0
    assert result["verdict"] == "合理"


def test_calculate_position_size_rounds_down_to_lots():
    result = calculate_position_size(
        account_value=100000.0,
        current_price=20.0,
        stop_price=18.0,
        risk_pct=0.01,
    )

    assert result["max_loss_amount"] == 1000.0
    assert result["suggested_shares"] == 500
    assert result["suggested_amount"] == 10000.0


def test_plan_holding_targets_uses_strategy_one_structural_high():
    result = plan_holding_targets(
        {
            "price": 20.0,
            "zg": 18.0,
            "bis": [
                {"is_up": True, "is_sure": True, "start_price": 10.0},
                {"is_up": False, "is_sure": True, "start_price": 25.0},
            ],
        },
        current_price=20.0,
        strategy_type="战法一",
    )

    assert result["target_price_1"] == 25.0
    assert result["target_price_2"] == 26.25
    assert result["target_is_placeholder"] is False
    assert result["target_open"] is False


def test_plan_holding_targets_keeps_strategy_two_open_target():
    result = plan_holding_targets(
        {"price": 20.0, "zg": 18.0, "bis": []},
        current_price=20.0,
        strategy_type="战法二",
    )

    assert result["target_price_1"] == 0.0
    assert result["target_is_placeholder"] is True
    assert result["target_open"] is True
    assert "无固定目标价" in result["target_label"]


def test_find_structural_high_s1_ignores_unconfirmed_or_lower_highs():
    result = find_structural_high_s1(
        [
            {"is_up": False, "is_sure": False, "start_price": 30.0},
            {"is_up": False, "is_sure": True, "start_price": 19.0},
            {"is_up": False, "is_sure": True, "start_price": 24.0},
        ],
        current_price=20.0,
    )

    assert result == 24.0
