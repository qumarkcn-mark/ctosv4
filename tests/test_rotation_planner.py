"""Rotation planner contract tests."""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services.rotation_planner import RISK_DISCLAIMER, build_rotation_item


def test_rotation_item_outputs_three_scenario_plans_for_holding():
    item = build_rotation_item(
        {
            "symbol": "sh600519",
            "name": "贵州茅台",
            "category": "持仓",
            "sort_score": 72,
            "state_label": "中枢震荡",
            "lifecycle_node": "持仓观察",
            "zoushi_type": "盘整",
            "price": 1500.0,
            "stop_loss": 1420.0,
            "distance_pct": 5.33,
            "main_action": "防线未破前观察结构演化。",
        },
        is_holding=True,
    )

    assert item["mode"] == "HOLDING"
    assert item["structure_summary"]["state_label"] == "中枢震荡"
    assert [plan["name"] for plan in item["plans"]] == ["甲", "乙", "丙"]
    assert all("disclaimer" in plan for plan in item["plans"])
    assert all("仅供参考" in plan["disclaimer"] for plan in item["plans"])
    assert "orders" not in item


def test_rotation_item_candidate_does_not_emit_execution_order():
    item = build_rotation_item(
        {
            "symbol": "sz000001",
            "category": "候选",
            "sort_score": 80,
            "state_label": "三买确认",
            "lifecycle_node": "候选观察",
            "zoushi_type": "上涨趋势",
        },
        is_holding=False,
    )

    assert item["mode"] == "CANDIDATE"
    assert item["risk_disclaimer"] == RISK_DISCLAIMER
    assert item["plans"][0]["position_action"].startswith("加入观察池")
    assert all("place_order" not in str(plan) for plan in item["plans"])
