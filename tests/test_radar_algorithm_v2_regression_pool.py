import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from server.engines.decision.radar_algorithm_v2 import build_radar_algorithm_v2


def freshness():
    return {
        "is_stale": False,
        "source": "baostock",
        "adjustflag": "2",
        "levels": {
            "day": {"is_stale": False},
            "30": {"is_stale": False},
            "5": {"is_stale": False},
        },
    }


def level(
    name,
    price,
    zd,
    zg,
    state="UNKNOWN",
    last_bi_dir="up",
    bsps=None,
    active_begin="2026-04-22 09:30:00",
    patterns=None,
):
    return {
        "level": name,
        "price": price,
        "state": state,
        "zd": zd,
        "zg": zg,
        "zs_operative_zd": zd,
        "zs_operative_zg": zg,
        "active_zhongshu": {"begin_date": active_begin, "zd": zd, "zg": zg},
        "bi_zhongshus": [{"begin_date": active_begin, "zd": zd, "zg": zg}],
        "last_bi_dir": last_bi_dir,
        "bsps": bsps or [],
        "patterns": patterns or [],
    }


def levels(day, m30, m5):
    return {"day": day, "m30": m30, "m5": m5}


def sell(raw_type, price, time="2026-04-23 14:00:00"):
    return {"type": raw_type, "is_buy": False, "price": price, "time": time}


def buy(raw_type, price, time="2026-04-23 13:00:00"):
    return {"type": raw_type, "is_buy": True, "price": price, "time": time}


WATCHLIST_REGRESSION_CASES = [
    pytest.param(
        "sz002176 江特电机",
        levels(
            level("day", 13.62, 9.01, 11.02, "UPWARD_LEAVING"),
            level("m30", 13.62, 10.68, 11.06, "UPWARD_LEAVING"),
            level("m5", 13.62, 11.62, 11.90, "THIRD_BUY_CONFIRMED", "down", [sell("2", 12.46)]),
        ),
        ("UPWARD_MAJOR_WAVE", "STANDARD", "HOLD_OR_TRAIL"),
        id="jiangte-major-wave",
    ),
    pytest.param(
        "sh688008 澜起科技",
        levels(
            level("day", 163.5, 77.33, 80.15, "UPWARD_LEAVING"),
            level("m30", 163.5, 144.46, 145.5, "THIRD_BUY_CONFIRMED", "down"),
            level("m5", 163.5, 157.68, 159.0, "IN_CENTER_OSC", "down", [sell("1", 168.7)]),
        ),
        ("HIGH_VOLATILITY_OSCILLATION", "STANDARD", "REDUCE_CHASING"),
        id="lanqi-high-volatility",
    ),
    pytest.param(
        "sh603986 兆易创新",
        levels(
            level("day", 297.7, 186.41, 234.63, "UPWARD_LEAVING"),
            level("m30", 297.7, 287.13, 287.48, "THIRD_BUY_CONFIRMED", "down", [buy("3a", 268.59)]),
            level("m5", 297.7, 302.57, 307.62, "IN_CENTER_OSC", "down", [sell("2", 309.75)]),
        ),
        ("PULLBACK_IN_UPTREND", "STANDARD", "WAIT_RECLAIM"),
        id="zhaoyi-pullback",
    ),
    pytest.param(
        "sz300502 新易盛",
        levels(
            level("day", 537.27, 372.54, 433.33, "UPWARD_LEAVING"),
            level("m30", 537.27, 490.02, 502.0, "UPWARD_LEAVING"),
            level("m5", 537.27, 577.37, 588.0, "THIRD_BUY_CONFIRMED", "down", [sell("2", 620.12)]),
        ),
        ("PULLBACK_IN_UPTREND", "STANDARD", "WAIT_RECLAIM"),
        id="xinyisheng-pullback",
    ),
    pytest.param(
        "sz300124 汇川技术",
        levels(
            level("day", 64.75, 69.98, 75.48, "DOWNWARD_LEAVING", "down"),
            level("m30", 64.75, 69.96, 71.4, "DOWNWARD_LEAVING", "down"),
            level("m5", 64.75, 66.01, 66.88, "DOWNWARD_LEAVING", "down"),
        ),
        ("DOWNWARD_DEFENSE", "STANDARD", "DEFENSIVE"),
        id="huichuan-defense",
    ),
    pytest.param(
        "sz300724 捷佳伟创",
        levels(
            level("day", 89.8, 108.0, 113.87, "DOWNWARD_LEAVING", "down"),
            level("m30", 89.8, 118.27, 123.88, "DOWNWARD_LEAVING", "down"),
            level("m5", 89.8, 107.76, 108.79, "DOWNWARD_LEAVING", "down", [buy("1", 90.12)]),
        ),
        ("DOWNWARD_DEFENSE", "STANDARD", "DEFENSIVE"),
        id="jiejia-defense",
    ),
    pytest.param(
        "sh603893 瑞芯微",
        levels(
            level("day", 178.65, 177.1979, 180.37, "UPWARD_LEAVING"),
            level("m30", 178.65, 168.2, 171.2, "THIRD_BUY_CONFIRMED", "down"),
            level(
                "m5",
                178.65,
                178.29,
                178.66,
                "DOWNWARD_LEAVING",
                "down",
                [buy("2", 175.9), sell("3a", 177.3)],
                active_begin="2026-04-22 11:15:00",
            ),
        ),
        ("PULLBACK_IN_UPTREND", "MICRO_CONVERSION", "WAIT_BREAKOUT"),
        id="rockchip-micro-conversion",
    ),
    pytest.param(
        "sh603259 药明康德",
        levels(
            level("day", 100.08, 92.43, 106.58, "UPWARD_LEAVING"),
            level("m30", 100.08, 98.9, 100.19, "UPWARD_LEAVING", "up", [sell("2", 101.24)]),
            level(
                "m5",
                100.08,
                98.83,
                99.54,
                "THIRD_BUY_CONFIRMED",
                "down",
                [buy("2s", 98.97), sell("2", 100.58)],
            ),
        ),
        ("HIGH_VOLATILITY_OSCILLATION", "CENTER_UPPER_CONTEST", "WAIT_UPPER_BREAK"),
        id="wuxi-center-upper-contest",
    ),
    pytest.param(
        "sz300738 奥飞数据",
        levels(
            level("day", 24.0, 21.01, 24.97, "IN_CENTER_OSC", "down", patterns=["底背驰"]),
            level("m30", 24.0, 22.38, 23.08, "THIRD_BUY_CONFIRMED", "down"),
            level("m5", 24.0, 23.63, 23.75, "THIRD_BUY_CONFIRMED", "down"),
        ),
        ("CENTER_REBOUND", "STANDARD", "WATCH_REBOUND"),
        id="aofei-center-rebound",
    ),
    pytest.param(
        "sh600333 长春燃气",
        levels(
            level("day", 5.56, 5.47, 5.76, "IN_CENTER_OSC", "up"),
            level("m30", 5.56, 5.19, 5.32, "UPWARD_LEAVING"),
            level("m5", 5.56, 5.18, 5.21, "THIRD_BUY_CONFIRMED", "down"),
        ),
        ("CENTER_REBOUND", "STANDARD", "WATCH_REBOUND"),
        id="changchun-gas-center-rebound",
    ),
]


@pytest.mark.parametrize("case_name, case_levels, expected", WATCHLIST_REGRESSION_CASES)
def test_watchlist_regression_pool_path_phase_and_bias(case_name, case_levels, expected):
    result = build_radar_algorithm_v2(case_levels, freshness())

    assert (result["path"], result["phase"], result["action_bias"]) == expected, case_name
    assert [scenario["id"] for scenario in result["scenarios"]] == ["A", "B", "C"]
    assert result["summary"]
    assert result["next_watch"]
