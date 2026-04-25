import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.decision.holding_manager import (
    compute_holding_status,
    find_structural_high_s1,
)


def test_compute_holding_status_returns_empty_status_without_position():
    result = compute_holding_status({}, {}, None, {})

    assert result["stage"] == "empty"
    assert result["label"] == "空仓"
    assert result["validation"]["status"] == "空仓"


def test_compute_holding_status_stage_five_on_day_top_divergence():
    result = compute_holding_status(
        {"price": 20.0, "zg": 19.0, "patterns": ["日线顶背驰"]},
        {"zg": 18.0, "patterns": []},
        {"cost": 15.0, "qty": 1000},
        {},
    )

    assert result["stage"] == 5
    assert result["top_diverge_day"] is True
    assert "清仓" in result["action"]


def test_compute_holding_status_strategy_two_relay_divergence_does_not_reduce():
    result = compute_holding_status(
        {"price": 20.0, "zg": 16.0, "patterns": []},
        {"zg": 18.0, "patterns": ["30分顶背驰 中继"], "latest_top_beichi_type": "中继"},
        {"cost": 19.5, "qty": 1000},
        {},
        strategy_type="战法二",
    )

    assert result["stage"] < 4
    assert result["top_diverge_30min_type"] == "中继"
    assert result["m30_relay_note"]


def test_find_structural_high_s1_uses_last_confirmed_down_bi_start():
    result = find_structural_high_s1(
        [
            {"is_up": True, "is_sure": True, "start_price": 10.0},
            {"is_up": False, "is_sure": True, "start_price": 25.0},
        ],
        current_price=20.0,
        day_zg=18.0,
    )

    assert result == 25.0
