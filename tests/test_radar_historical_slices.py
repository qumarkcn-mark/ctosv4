import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.decision.radar_algorithm_v2 import build_radar_algorithm_v2
from server.engines.decision.radar_historical_slice import evaluate_future_result
from server.engines.structure.divergence import detect_structural_divergence


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


def test_rockchip_micro_conversion_slice_triggers_a_after_breakout():
    result = build_radar_algorithm_v2(
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
        freshness(),
    )

    future = evaluate_future_result(result, {"last_price": 185.49, "high": 187.99, "low": 178.09})

    assert (result["path"], result["phase"], result["action_bias"]) == (
        "PULLBACK_IN_UPTREND",
        "MICRO_CONVERSION",
        "WAIT_BREAKOUT",
    )
    assert [item["code"] for item in result["patterns"]] == ["MICRO_CONVERSION_BREAKOUT"]
    assert future["result"] == "A_TRIGGERED"
    assert {item["field"] for item in future["matched"]} == {"ZG", "S3A"}


def test_zhaoyi_pullback_slice_triggers_partial_a_not_full_major_wave():
    result = build_radar_algorithm_v2(
        levels(
            level("day", 297.7, 186.41, 234.63, "UPWARD_LEAVING"),
            level("m30", 297.7, 287.13, 287.48, "THIRD_BUY_CONFIRMED", "down", [buy("3a", 268.59)]),
            level("m5", 297.7, 302.57, 307.62, "IN_CENTER_OSC", "down", [sell("2", 309.75)]),
        ),
        freshness(),
    )

    future = evaluate_future_result(result, {"last_price": 303.42, "high": 309.58, "low": 297.75})

    assert (result["path"], result["phase"], result["action_bias"]) == (
        "PULLBACK_IN_UPTREND",
        "STANDARD",
        "WAIT_RECLAIM",
    )
    assert future["result"] == "A_PARTIAL_TRIGGERED"
    assert [item["field"] for item in future["matched"]] == ["ZD"]
    assert [item["field"] for item in future["unmatched"]] == ["ZG"]


def test_lanqi_top_divergence_and_30m_third_sell_slice_triggers_c():
    result = build_radar_algorithm_v2(
        levels(
            level("day", 177.93, 77.33, 80.15, "UPWARD_LEAVING"),
            level("m30", 177.93, 165.0, 171.0, "UPWARD_LEAVING", "down", [sell("1", 188.38)]),
            level("m5", 177.93, 176.41, 180.31, "IN_CENTER_OSC", "down", [sell("1", 188.38)]),
        ),
        freshness(),
    )
    bis = [
        _bi("2026-01-13 15:00:00", "2026-01-22 10:00:00", 128.5, 181.43, True, 44.4819, 6.1160),
        _bi("2026-01-22 10:00:00", "2026-01-23 14:00:00", 181.43, 158.03, False, 12.7877, 3.1479),
        _bi("2026-01-23 14:00:00", "2026-01-26 10:30:00", 158.03, 169.3, True, 0.0, 3.1479),
        _bi("2026-01-26 10:30:00", "2026-01-27 10:30:00", 169.3, 158.91, False, 10.273, 1.7423),
        _bi("2026-01-27 10:30:00", "2026-01-28 10:00:00", 158.91, 176.18, True, 1.6897, 2.9838),
        _bi("2026-01-28 10:00:00", "2026-01-30 10:00:00", 176.18, 160.2, False, 16.4129, 0.1708),
        _bi("2026-01-30 10:00:00", "2026-02-02 10:00:00", 160.2, 188.38, True, 13.6219, 3.8543),
    ]

    divergence = detect_structural_divergence(bis, is_up=True)
    future = evaluate_future_result(
        result,
        {"last_price": 162.27, "high": 165.61, "low": 161.58},
        [{"type": "third_sell_below_boundary", "level": "30", "price": 156.6, "boundary_value": 165.0}],
    )

    assert result["path"] == "HIGH_VOLATILITY_OSCILLATION"
    assert divergence is not None
    assert divergence["type"] == "顶背驰"
    assert divergence["severity"] == "中等"
    assert divergence["combined_score"] >= 0.45
    assert divergence["previous_bi"]["y1"] == 181.43
    assert divergence["current_bi"]["y1"] == 188.38
    assert future["result"] == "C_TRIGGERED"


def test_huichuan_bottom_divergence_b1_does_not_turn_downtrend_bullish():
    result = build_radar_algorithm_v2(
        levels(
            level("day", 64.75, 69.98, 75.48, "DOWNWARD_LEAVING", "down", [buy("1", 64.55)]),
            level("m30", 64.75, 69.96, 71.4, "DOWNWARD_LEAVING", "down", [buy("1", 64.55)]),
            level("m5", 64.75, 66.01, 66.88, "DOWNWARD_LEAVING", "down", [buy("1", 63.65)]),
        ),
        freshness(),
    )
    bis = [
        _bi("2026-03-18 15:00:00", "2026-03-24 10:30:00", 70.05, 65.0, False, 5.948, 1.1792),
        _bi("2026-03-24 10:30:00", "2026-03-26 10:00:00", 65.0, 68.51, True, 5.6551, 0.0078),
        _bi("2026-03-26 10:00:00", "2026-03-27 10:00:00", 68.51, 66.66, False, 0.0, 0.0078),
        _bi("2026-03-27 10:00:00", "2026-03-31 10:00:00", 66.66, 68.86, True, 0.955, 0.2473),
        _bi("2026-03-31 10:00:00", "2026-03-31 15:00:00", 68.86, 66.98, False, 0.1959, 0.022),
        _bi("2026-03-31 15:00:00", "2026-04-01 13:30:00", 66.98, 68.63, True, 0.0971, 0.1224),
        _bi("2026-04-01 13:30:00", "2026-04-07 15:00:00", 68.63, 64.55, False, 5.15, 0.6379),
    ]

    divergence = detect_structural_divergence(bis, is_up=False)
    future = evaluate_future_result(result, {"last_price": 64.75, "high": 65.29, "low": 63.63})

    assert result["path"] == "DOWNWARD_DEFENSE"
    assert result["action_bias"] == "DEFENSIVE"
    assert divergence is not None
    assert divergence["type"] == "底背驰"
    assert future["result"] == "A_TRIGGERED"
    assert [item["trigger"] for item in future["matched"]] == ["fail_below"]


def test_shanxi_huada_b1_b2s_breakout_and_b3a_confirm_bottom_repair():
    result = build_radar_algorithm_v2(
        levels(
            level("day", 65.21, 52.45, 54.69, "IN_CENTER_OSC", "up", [buy("1", 51.57), buy("2s", 52.3)]),
            level("m30", 65.21, 52.45, 54.69, "IN_CENTER_OSC", "up", [buy("1", 51.57), buy("2s", 52.3)]),
            level("m5", 65.21, 64.0, 65.0, "THIRD_BUY_CONFIRMED", "down", [buy("3a", 65.21)]),
        ),
        freshness(),
    )
    future = evaluate_future_result(result, {"last_price": 65.21, "high": 68.62, "low": 65.21})

    assert result["path"] == "UPWARD_MAJOR_WAVE"
    assert result["action_bias"] == "HOLD_OR_TRAIL"
    assert any(item["field"] == "ZG" and item["value"] == 65.0 for item in result["boundaries"]["maintain"])
    assert future["result"] == "B_MAINTAINED"

    slice_contract = {
        "b1": 51.57,
        "b2s": 52.30,
        "recent_down_center_zg": 54.69,
        "b3a": 65.21,
        "breakout_high": 68.62,
    }
    assert slice_contract["b2s"] > slice_contract["b1"]
    assert slice_contract["breakout_high"] > slice_contract["recent_down_center_zg"]
    assert slice_contract["b3a"] > slice_contract["recent_down_center_zg"]


def test_eoptolink_daily_third_buy_consolidates_then_extends_to_new_high():
    result = build_radar_algorithm_v2(
        levels(
            level(
                "day",
                537.27,
                372.54,
                433.33,
                "THIRD_BUY_CONFIRMED",
                "down",
                [buy("3a", 434.70, "2026-03-31")],
                active_begin="2025-10-29",
            ),
            level("m30", 537.27, 442.86, 462.87, "UPWARD_LEAVING", "up", active_begin="2026-04-01 14:00:00"),
            level("m5", 537.27, 490.02, 502.0, "UPWARD_LEAVING", "up", active_begin="2026-04-08 15:00:00"),
        ),
        freshness(),
    )
    future = evaluate_future_result(result, {"last_price": 537.27, "high": 627.80, "low": 441.19})

    assert (result["path"], result["phase"], result["action_bias"]) == (
        "UPWARD_MAJOR_WAVE",
        "STANDARD",
        "HOLD_OR_TRAIL",
    )
    assert [item["code"] for item in result["patterns"]] == ["BIG_CENTER_SMALL_CENTER_UP_BREAK"]
    assert future["result"] == "B_MAINTAINED"

    slice_contract = {
        "daily_center_zg": 433.33,
        "daily_b3a": 434.70,
        "narrow_30m_zd": 442.86,
        "narrow_30m_zg": 462.87,
        "future_high": 627.80,
    }
    assert slice_contract["daily_b3a"] > slice_contract["daily_center_zg"]
    assert slice_contract["future_high"] > slice_contract["narrow_30m_zg"]


def test_doti_micro_small_turn_big_b2s_repairs_then_b3a_confirms():
    preview = build_radar_algorithm_v2(
        levels(
            level("day", 160.0, 48.2667, 65.8454, "UPWARD_LEAVING", "up"),
            level(
                "m30",
                160.0,
                153.81,
                162.8,
                "IN_CENTER_OSC",
                "down",
                [
                    buy("1p", 134.0, "2026-03-23 15:00:00"),
                    buy("2", 153.81, "2026-03-25 11:30:00"),
                    buy("2s", 153.57, "2026-03-31 10:00:00"),
                ],
                active_begin="2026-03-24 14:30:00",
            ),
            level("m5", 160.0, 153.8, 162.8, "IN_CENTER_OSC", "up", [buy("2s", 153.57, "2026-03-31 10:00:00")]),
        ),
        freshness(),
    )
    preview_future = evaluate_future_result(preview, {"last_price": 185.11, "high": 217.75, "low": 179.43})

    confirmed = build_radar_algorithm_v2(
        levels(
            level("day", 182.97, 48.2667, 65.8454, "UPWARD_LEAVING", "up"),
            level(
                "m30",
                182.97,
                153.81,
                162.8,
                "THIRD_BUY_CONFIRMED",
                "down",
                [
                    buy("1p", 134.0, "2026-03-23 15:00:00"),
                    buy("2", 153.81, "2026-03-25 11:30:00"),
                    buy("2s", 153.57, "2026-03-31 10:00:00"),
                    buy("3a", 182.97, "2026-04-13 10:00:00"),
                ],
                active_begin="2026-03-24 14:30:00",
            ),
            level("m5", 182.97, 166.15, 172.02, "UPWARD_LEAVING", "up", [buy("3a", 182.97, "2026-04-13 09:35:00")]),
        ),
        freshness(),
    )
    confirmed_future = evaluate_future_result(confirmed, {"last_price": 185.11, "high": 217.75, "low": 179.43})

    assert (preview["path"], preview["action_bias"]) == ("CENTER_REBOUND", "WATCH_REBOUND")
    assert preview_future["result"] == "A_TRIGGERED"
    assert (confirmed["path"], confirmed["action_bias"]) == ("UPWARD_MAJOR_WAVE", "HOLD_OR_TRAIL")
    assert confirmed["transition"]["from"] == "CENTER_REBOUND"
    assert confirmed["transition"]["to"] == "UPWARD_MAJOR_WAVE"
    assert confirmed["transition"]["status"] == "CONFIRMED"
    assert confirmed_future["result"] == "B_MAINTAINED"

    slice_contract = {
        "b1p": 134.0,
        "b2": 153.81,
        "b2s": 153.57,
        "center_zg": 162.8,
        "b3a": 182.97,
        "future_high": 217.75,
    }
    assert slice_contract["b2"] > slice_contract["b1p"]
    assert slice_contract["b3a"] > slice_contract["center_zg"]
    assert slice_contract["future_high"] > slice_contract["b3a"]


def test_risen_energy_third_buy_then_first_and_second_sell_turns_to_risk():
    result = build_radar_algorithm_v2(
        levels(
            level("day", 20.90, 10.31, 11.53, "THIRD_BUY_CONFIRMED", "down"),
            level(
                "m30",
                20.90,
                20.88,
                22.28,
                "THIRD_BUY_CONFIRMED",
                "down",
                [buy("3a", 20.88, "2026-02-02 15:00:00")],
                active_begin="2026-01-26 10:00:00",
            ),
            level(
                "m5",
                20.90,
                20.88,
                21.40,
                "THIRD_BUY_CONFIRMED",
                "down",
                [buy("3a", 20.88, "2026-02-02 15:00:00")],
                active_begin="2026-02-02 13:30:00",
            ),
        ),
        freshness(),
    )
    future = evaluate_future_result(
        result,
        {"last_price": 22.87, "high": 24.60, "low": 22.76},
        [{"type": "second_sell_below_prior_high", "level": "30", "price": 24.60, "boundary_value": 25.98}],
    )
    risk_pattern = build_radar_algorithm_v2(
        levels(
            level("day", 22.87, 10.31, 11.53, "THIRD_BUY_CONFIRMED", "down"),
            level(
                "m30",
                22.87,
                20.88,
                22.28,
                "IN_CENTER_OSC",
                "down",
                [
                    buy("3a", 20.88, "2026-02-02 15:00:00"),
                    sell("1", 25.98, "2026-02-04 13:30:00"),
                    sell("2", 24.60, "2026-02-09 11:00:00"),
                ],
                active_begin="2026-01-26 10:00:00",
            ),
            level("m5", 22.87, 22.0, 23.0, "IN_CENTER_OSC", "down"),
        ),
        freshness(),
    )

    assert (result["path"], result["phase"], result["action_bias"]) == (
        "PULLBACK_IN_UPTREND",
        "STANDARD",
        "WAIT_RECLAIM",
    )
    assert [item["code"] for item in risk_pattern["patterns"]] == ["THIRD_BUY_FAST_SELL_RISK"]
    assert risk_pattern["transition"]["from"] == "UPWARD_MAJOR_WAVE"
    assert risk_pattern["transition"]["to"] == "HIGH_VOLATILITY_OSCILLATION"
    assert risk_pattern["transition"]["status"] == "RISK"
    assert future["result"] == "C_TRIGGERED"
    assert future["matched"][0]["type"] == "second_sell_below_prior_high"

    slice_contract = {
        "third_buy_low": 20.88,
        "center_zd": 20.88,
        "first_sell": 25.98,
        "second_sell": 24.60,
        "first_up_area": 4.1426,
        "second_up_area": 0.0635,
    }
    assert slice_contract["third_buy_low"] == slice_contract["center_zd"]
    assert slice_contract["second_sell"] < slice_contract["first_sell"]
    assert slice_contract["second_up_area"] < slice_contract["first_up_area"] * 0.05


def test_ganfeng_lithium_sell_pressure_returns_to_origin_center_then_repairs():
    risk = build_radar_algorithm_v2(
        levels(
            level("day", 84.06, 29.7779, 31.5699, "THIRD_BUY_CONFIRMED", "down", [sell("1p", 88.48, "2026-04-14")]),
            level(
                "m30",
                84.06,
                77.51,
                81.44,
                "UPWARD_LEAVING",
                "down",
                [sell("1", 88.39, "2026-04-14 15:00:00")],
                active_begin="2026-03-30 10:00:00",
            ),
            level(
                "m5",
                84.06,
                81.53,
                83.79,
                "IN_CENTER_OSC",
                "down",
                [
                    sell("2", 83.79, "2026-04-15 13:10:00"),
                    sell("2s", 86.63, "2026-04-16 10:05:00"),
                ],
                active_begin="2026-04-15 11:15:00",
            ),
        ),
        freshness(),
    )
    risk_future = evaluate_future_result(risk, {"last_price": 79.88, "high": 82.50, "low": 79.00})

    repair = build_radar_algorithm_v2(
        levels(
            level("day", 84.99, 29.7779, 31.5699, "THIRD_BUY_CONFIRMED", "down", [sell("1p", 88.48, "2026-04-14")]),
            level(
                "m30",
                84.99,
                77.51,
                81.44,
                "UPWARD_LEAVING",
                "up",
                [sell("1", 88.39, "2026-04-14 15:00:00"), sell("2", 86.25, "2026-04-24 15:00:00")],
                active_begin="2026-03-30 10:00:00",
            ),
            level(
                "m5",
                84.99,
                79.78,
                80.49,
                "THIRD_BUY_CONFIRMED",
                "down",
                [
                    buy("1", 79.00, "2026-04-23 09:55:00"),
                    buy("2", 79.50, "2026-04-23 13:10:00"),
                    buy("2s", 79.78, "2026-04-23 14:35:00"),
                    buy("3a", 82.00, "2026-04-24 11:10:00"),
                    sell("1p", 86.25, "2026-04-24 14:40:00"),
                ],
                active_begin="2026-04-23 10:30:00",
            ),
        ),
        freshness(),
    )
    repair_future = evaluate_future_result(repair, {"last_price": 84.11, "high": 86.46, "low": 83.50})

    assert (risk["path"], risk["action_bias"]) == ("HIGH_VOLATILITY_OSCILLATION", "REDUCE_CHASING")
    assert risk_future["result"] == "C_TRIGGERED"
    assert {item["field"] for item in risk_future["matched"]} == {"ZD", "ZG"}
    assert (repair["path"], repair["action_bias"]) == ("HIGH_VOLATILITY_OSCILLATION", "REDUCE_CHASING")
    assert [item["code"] for item in repair["patterns"]] == ["BIG_CENTER_SMALL_CENTER_PULLBACK_REPAIR"]
    assert repair["transition"]["from"] == "C_TRIGGERED"
    assert repair["transition"]["to"] == "A_PARTIAL_TRIGGERED"
    assert repair["transition"]["status"] == "PARTIAL"
    assert repair_future["result"] == "A_PARTIAL_TRIGGERED"
    assert [item["field"] for item in repair_future["matched"]] == ["ZG"]

    slice_contract = {
        "first_sell": 88.39,
        "second_sell_pressure": 86.63,
        "origin_center_zg": 81.44,
        "fall_low": 79.00,
        "small_turn_big_b3a": 82.00,
        "unresolved_sell_pressure": 86.25,
    }
    assert slice_contract["fall_low"] < slice_contract["origin_center_zg"]
    assert slice_contract["small_turn_big_b3a"] > slice_contract["origin_center_zg"]
    assert slice_contract["unresolved_sell_pressure"] < slice_contract["first_sell"]


def _bi(x0, x1, y0, y1, is_up, area, dif_extreme):
    return {
        "x0": x0,
        "x1": x1,
        "y0": y0,
        "y1": y1,
        "is_up": is_up,
        "is_sure": True,
        "momentum": {"area": area, "dif_extreme": dif_extreme},
    }
