import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.decision.entry_planner import compute_entry_checklist


def test_compute_entry_checklist_passes_when_all_conditions_are_present():
    result = compute_entry_checklist(
        {"patterns": ["日线二买确认"]},
        {"patterns": ["30分底背驰"], "zoushi_type": {"type": "盘整"}},
        {"patterns": ["5分二买"]},
    )

    assert result == {
        "day_buy_node": True,
        "day_not_top_diverge": True,
        "thirty_min_structure": True,
        "thirty_min_buy_node": True,
        "five_min_entry_bar": True,
        "all_passed": True,
    }


def test_compute_entry_checklist_blocks_top_divergence_and_building_structure():
    result = compute_entry_checklist(
        {"patterns": ["日线二买确认", "顶背驰"]},
        {"patterns": ["30分底背驰"], "zoushi_type": {"type": "构建中"}},
        {"patterns": ["5分底背驰"]},
    )

    assert result["day_buy_node"] is True
    assert result["day_not_top_diverge"] is False
    assert result["thirty_min_structure"] is False
    assert result["all_passed"] is False


def test_compute_entry_checklist_defaults_missing_levels_to_false_or_safe():
    result = compute_entry_checklist({}, {}, {})

    assert result["day_buy_node"] is False
    assert result["day_not_top_diverge"] is True
    assert result["thirty_min_structure"] is False
    assert result["thirty_min_buy_node"] is False
    assert result["five_min_entry_bar"] is False
    assert result["all_passed"] is False
