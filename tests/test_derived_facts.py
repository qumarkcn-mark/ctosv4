import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.structure.derived_facts import check_interval_nesting, enrich_level


def test_enrich_level_adds_center_boundaries_and_state():
    level = enrich_level(
        {
            "klines": [{"time": "2026-01-01", "close": 21.0}],
            "bis": [{"y0": 18.0, "y1": 21.0, "is_up": True}],
            "bi_zhongshus": [{"zg": 20.0, "zd": 18.0, "gg": 21.0, "dd": 17.5}],
            "bsps": [],
            "stats": {},
        }
    )

    assert level["price"] == 21.0
    assert level["zg"] == 20.0
    assert level["zd"] == 18.0
    assert level["state"] == "UPWARD_LEAVING"
    assert level["zoushi_type"]["type"] == "盘整"
    assert level["last_bi_dir"] == "up"


def test_enrich_level_derives_buy_sell_patterns_from_bsps_and_state():
    level = enrich_level(
        {
            "klines": [{"time": "2026-01-01", "close": 20.5}],
            "bis": [{"y0": 22.0, "y1": 20.5, "is_up": False}],
            "bi_zhongshus": [{"zg": 20.0, "zd": 18.0}],
            "bsps": [
                {"type": "2", "is_buy": True},
                {"type": "3a", "is_buy": True},
                {"type": "2", "is_buy": False},
            ],
            "stats": {},
        }
    )

    assert "二买" in level["patterns"]
    assert "三买" in level["patterns"]
    assert "二卖" in level["patterns"]
    assert "三买确认" in level["patterns"]
    assert level["state"] == "THIRD_BUY_CONFIRMED"


def test_enrich_level_classifies_trending_centers():
    level = enrich_level(
        {
            "klines": [{"time": "2026-01-01", "close": 30.0}],
            "bis": [{"y0": 25.0, "y1": 30.0, "is_up": True}],
            "bi_zhongshus": [
                {"zg": 12.0, "zd": 10.0},
                {"zg": 20.0, "zd": 15.0},
            ],
            "bsps": [],
            "stats": {},
        }
    )

    assert level["zoushi_type"]["type"] == "上涨趋势"
    assert level["classifications"][0]["name"] == "趋势延伸"


def test_enrich_level_detects_top_divergence_from_momentum():
    level = enrich_level(
        {
            "klines": [{"time": "2026-01-01", "close": 20.0}],
            "bis": [
                {"y0": 16.0, "y1": 19.0, "is_up": True, "momentum": {"area": 100, "dif_extreme": 1.0}},
                {"y0": 19.0, "y1": 17.0, "is_up": False, "momentum": {"area": 80, "dif_extreme": 0.7}},
                {"y0": 17.0, "y1": 20.0, "is_up": True, "momentum": {"area": 45, "dif_extreme": 0.35}},
            ],
            "bi_zhongshus": [{"zg": 18.5, "zd": 17.0}],
            "bsps": [],
            "stats": {},
        }
    )

    assert level["div_info"]["type"] == "顶背驰"
    assert level["div_info"]["severity"] == "高危"
    assert level["latest_top_beichi_type"] == "疑似转折"
    assert "趋势顶背驰" in level["patterns"]


def test_enrich_level_detects_bottom_divergence_reversal():
    level = enrich_level(
        {
            "klines": [{"time": "2026-01-01", "close": 18.0}],
            "bis": [
                {"y0": 20.0, "y1": 16.0, "is_up": False, "momentum": {"area": 90, "dif_extreme": 1.0}},
                {"y0": 16.0, "y1": 18.0, "is_up": True, "momentum": {"area": 60, "dif_extreme": 0.8}},
                {"y0": 18.0, "y1": 15.5, "is_up": False, "momentum": {"area": 35, "dif_extreme": 0.4}},
            ],
            "bi_zhongshus": [{"zg": 18.5, "zd": 16.5}],
            "bsps": [],
            "stats": {},
        }
    )

    assert level["div_info"]["type"] == "底背驰"
    assert level["latest_bottom_beichi_type"] == "转折"
    assert "趋势底背驰" in level["patterns"]


def test_check_interval_nesting_requires_same_direction_divergence():
    nesting = check_interval_nesting(
        [
            {"patterns": ["趋势底背驰"]},
            {"div_info": {"type": "底背驰"}},
            {"patterns": ["趋势顶背驰"]},
        ],
        level_names=["day", "30", "5"],
    )

    assert nesting["depth"] == 2
    assert nesting["direction"] == "bottom"
    assert nesting["confidence_gate"] == "MEDIUM"
    assert nesting["levels"] == [
        {"level": "day", "type": "底背驰"},
        {"level": "30", "type": "底背驰"},
    ]
