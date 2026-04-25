import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.structure.divergence import classify_divergence_type, get_divergence
from server.engines.structure.lifecycle import classify_lifecycle, stop_for_1buy, stop_for_3buy
from server.engines.structure.nesting import check_interval_nesting
from server.engines.structure.strategy_detector import derive_patterns
from server.engines.structure.zhongshu import classify_zoushi, deduce_state_from_structures


def test_divergence_module_detects_top_divergence_and_classifies_reversal():
    bis = [
        {"y1": 19.0, "is_up": True, "momentum": {"area": 100, "dif_extreme": 1.0}},
        {"y1": 17.0, "is_up": False, "momentum": {"area": 80, "dif_extreme": 0.7}},
        {"y1": 20.0, "is_up": True, "momentum": {"area": 45, "dif_extreme": 0.35}},
    ]

    div = get_divergence(bis, is_up=True)

    assert div["type"] == "顶背驰"
    assert div["severity"] == "高危"
    assert classify_divergence_type(div, bis, price=19.5) == "转折"


def test_nesting_module_stops_when_direction_breaks():
    nesting = check_interval_nesting(
        [
            {"patterns": ["趋势顶背驰"]},
            {"div_info": {"type": "顶背驰"}},
            {"patterns": ["趋势底背驰"]},
        ],
        level_names=["day", "30", "5"],
    )

    assert nesting["depth"] == 2
    assert nesting["direction"] == "top"


def test_zhongshu_module_classifies_center_state_and_trend():
    state, last_zs, recent_ex = deduce_state_from_structures(
        [{"y0": 18.0, "y1": 21.0, "is_up": True}],
        [{"zg": 20.0, "zd": 18.0}],
    )

    assert state == "UPWARD_LEAVING"
    assert last_zs["zg"] == 20.0
    assert recent_ex["pressure"] == 21.0
    assert classify_zoushi([{"zg": 12.0, "zd": 10.0}, {"zg": 20.0, "zd": 15.0}])["type"] == "上涨趋势"


def test_lifecycle_module_builds_stops_from_bis():
    bis = [
        {"y0": 16.0, "y1": 20.0, "is_up": True},
        {"y0": 20.0, "y1": 18.5, "is_up": False},
    ]
    classifications = classify_lifecycle(
        {"type": "盘整"},
        {"zg": 18.0, "zd": 16.0},
        bis,
    )

    assert classifications[0]["stopLoss"] == 18.5
    assert stop_for_3buy(bis, 18.0) == 18.5
    assert stop_for_1buy(bis) == 18.5


def test_strategy_detector_module_derives_bsp_and_divergence_patterns():
    patterns = derive_patterns(
        [
            {"type": "2", "is_buy": True},
            {"type": "3a", "is_buy": True},
            {"type": "1", "is_buy": False},
        ],
        state="THIRD_BUY_CONFIRMED",
        div_info={"type": "底背驰"},
    )

    assert patterns == ["二买", "三买", "1卖", "三买确认", "趋势底背驰"]
