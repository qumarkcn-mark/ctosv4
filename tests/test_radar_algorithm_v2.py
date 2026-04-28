import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.decision.radar_algorithm_v2 import (
    build_level_atom,
    build_radar_algorithm_v2,
)


def freshness(stale=False):
    return {
        "is_stale": stale,
        "levels": {
            "day": {"is_stale": False},
            "30": {"is_stale": False},
            "5": {"is_stale": False},
        },
    }


def level(
    name,
    price=20.0,
    zd=18.0,
    zg=19.5,
    state="UNKNOWN",
    last_bi_dir="up",
    patterns=None,
    bsps=None,
    active_begin="2026-04-24 09:30:00",
    previous=None,
    centers=None,
    div_info=None,
    bis=None,
):
    if centers is None:
        centers = []
        if previous:
            centers.append(previous)
        centers.append({"begin_date": active_begin, "zg": zg, "zd": zd, "gg": max(price, zg), "dd": min(price, zd)})
    return {
        "level": name,
        "price": price,
        "state": state,
        "zg": zg,
        "zd": zd,
        "zs_operative_zg": zg,
        "zs_operative_zd": zd,
        "active_zhongshu": centers[-1],
        "bi_zhongshus": centers,
        "last_bi_dir": last_bi_dir,
        "patterns": patterns or [],
        "bsps": bsps or [],
        "bis": bis or [
            {"x0": "2026-04-24 09:30:00", "x1": "2026-04-24 10:00:00", "is_up": True},
            {"x0": "2026-04-24 10:00:00", "x1": "2026-04-24 10:30:00", "is_up": False},
            {"x0": "2026-04-24 10:30:00", "x1": "2026-04-24 11:00:00", "is_up": True},
        ],
        "div_info": div_info,
    }


def levels(day=None, m30=None, m5=None):
    return {
        "day": day if day is not None else level("day", price=21.0, state="UPWARD_LEAVING"),
        "m30": m30 if m30 is not None else level("m30", price=21.0, state="UPWARD_LEAVING"),
        "m5": m5 if m5 is not None else level("m5", price=21.0, state="UPWARD_LEAVING"),
    }


def test_level_atom_classifies_price_position_and_center_relation():
    atom = build_level_atom(
        level(
            "m30",
            price=22.0,
            zd=18.0,
            zg=20.0,
            last_bi_dir="down",
            previous={"zd": 12.0, "zg": 16.0},
        ),
        "30",
    )

    assert atom.public_level == "30"
    assert atom.level == "m30"
    assert atom.position_state == "UP_RETEST"
    assert atom.center_relation == "UP_NEWBORN"


def test_level_atom_exposes_historical_centers():
    atom = build_level_atom(
        level(
            "m30",
            price=22.0,
            centers=[
                {"begin_date": "2026-04-20 09:30:00", "zd": 10.0, "zg": 12.0},
                {"begin_date": "2026-04-21 09:30:00", "zd": 14.0, "zg": 16.0},
                {"begin_date": "2026-04-22 09:30:00", "zd": 18.0, "zg": 20.0},
            ],
        ),
        "30",
    )

    assert [center.zd for center in atom.historical_centers] == [10.0, 14.0, 18.0]
    assert atom.previous_center.zg == 16.0
    assert atom.center.zg == 20.0


def test_level_atom_normalizes_buy_and_sell_bsp_codes():
    atom = build_level_atom(
        level(
            "m5",
            bsps=[
                {"type": "3a", "is_buy": True, "time": "2026-04-24 10:30:00", "price": 20.1},
                {"type": "2", "is_buy": False, "time": "2026-04-24 11:00:00", "price": 21.1},
            ],
        ),
        "5",
    )

    assert atom.buy_events[0].code == "B3A"
    assert atom.buy_events[0].display == "B3A 三买A"
    assert atom.buy_events[0].is_current is True
    assert atom.sell_events[0].code == "S2"
    assert atom.sell_events[0].display == "S2 二卖"
    assert "BUY_SIGNAL" in atom.tags
    assert "SELL_SIGNAL" in atom.tags


def test_level_atom_binds_bsp_events_to_current_and_previous_centers():
    atom = build_level_atom(
        level(
            "m30",
            price=22.0,
            zd=18.0,
            zg=20.0,
            previous={"zd": 12.0, "zg": 16.0},
            bsps=[
                {"type": "3a", "is_buy": True, "time": "2026-04-24 10:30:00", "price": 20.1},
                {"type": "2", "is_buy": False, "time": "2026-04-24 11:00:00", "price": 15.5},
            ],
        ),
        "30",
    )

    buy_binding = atom.buy_events[0].center_binding
    sell_binding = atom.sell_events[0].center_binding

    assert buy_binding["current"]["status"] == "above_zg"
    assert buy_binding["current"]["distance_to_zg"] == 0.1
    assert buy_binding["previous"]["status"] == "above_zg"
    assert sell_binding["current"]["status"] == "below_zd"
    assert sell_binding["previous"]["status"] == "inside"
    assert atom.center_binding["B3A@2026-04-24 10:30:00"]["current"]["status"] == "above_zg"
    assert atom.event_sequence[0]["center_binding"]["current"]["status"] == "above_zg"


def test_level_atom_marks_up_leave_returned_to_center():
    atom = build_level_atom(
        level(
            "m30",
            price=19.0,
            zd=18.0,
            zg=20.0,
            state="IN_CENTER_OSC",
            last_bi_dir="down",
            bsps=[
                {"type": "1", "is_buy": False, "time": "2026-04-24 10:30:00", "price": 22.5},
            ],
        ),
        "30",
    )

    assert atom.position_state == "CENTER_INSIDE"
    assert atom.leave_return_status["status"] == "UP_RETURNED_TO_CENTER"
    assert atom.leave_return_status["direction"] == "up"
    assert atom.leave_return_status["has_returned"] is True
    assert atom.leave_return_status["leave_extreme"] == 22.5


def test_level_atom_marks_up_leave_return_broken_after_center_break():
    atom = build_level_atom(
        level(
            "m30",
            price=17.5,
            zd=18.0,
            zg=20.0,
            state="DOWNWARD_LEAVING",
            last_bi_dir="down",
            bsps=[
                {"type": "1", "is_buy": False, "time": "2026-04-24 10:30:00", "price": 22.5},
            ],
        ),
        "30",
    )

    assert atom.position_state == "DOWN_LEAVING"
    assert atom.leave_return_status["status"] == "UP_RETURN_BROKEN"
    assert atom.leave_return_status["is_broken"] is True


def test_level_atom_exposes_momentum_compare_for_latest_same_direction_bi():
    atom = build_level_atom(
        level(
            "m30",
            price=24.6,
            last_bi_dir="up",
            bis=[
                {
                    "x0": "2026-02-02 09:30:00",
                    "x1": "2026-02-02 15:00:00",
                    "y0": 20.88,
                    "y1": 25.98,
                    "is_up": True,
                    "momentum": {"area": 4.1426, "dif_extreme": 0.5},
                },
                {
                    "x0": "2026-02-05 09:30:00",
                    "x1": "2026-02-09 11:00:00",
                    "y0": 22.0,
                    "y1": 24.6,
                    "is_up": True,
                    "momentum": {"area": 0.0635, "dif_extreme": 0.05},
                },
            ],
        ),
        "30",
    )

    compare = atom.momentum_compare
    assert compare["direction"] == "up"
    assert compare["price_makes_extreme"] is False
    assert compare["is_weaker"] is True
    assert compare["area_ratio"] == 0.015
    assert compare["combined_score"] > 0.9
    assert compare["previous"]["momentum_metrics"]["area"] == 4.1426
    assert compare["current"]["momentum_metrics"]["area"] == 0.0635


def test_level_atom_exposes_ordered_event_sequence():
    atom = build_level_atom(
        level(
            "m30",
            bsps=[
                {"type": "2", "is_buy": False, "time": "2026-02-09 11:00:00", "price": 24.6},
                {"type": "3a", "is_buy": True, "time": "2026-02-02 15:00:00", "price": 20.88},
                {"type": "1", "is_buy": False, "time": "2026-02-04 13:30:00", "price": 25.98},
            ],
        ),
        "30",
    )

    assert [item["code"] for item in atom.event_sequence] == ["B3A", "S1", "S2"]
    assert atom.event_sequence[0]["side"] == "buy"
    assert atom.event_sequence[1]["side"] == "sell"
    assert atom.event_sequence[2]["price"] == 24.6


def test_classifier_maps_strong_breakout_like_002176_to_upward_major_wave():
    result = build_radar_algorithm_v2(levels(), freshness())

    assert result["path"] == "UPWARD_MAJOR_WAVE"
    assert result["relation"] == "ALIGN_UP"
    assert result["confidence"] == "HIGH"


def test_classifier_keeps_limit_up_extension_when_sell_point_has_been_recovered():
    m5 = level(
        "m5",
        price=13.62,
        zd=11.62,
        zg=11.9,
        state="THIRD_BUY_CONFIRMED",
        last_bi_dir="down",
        bsps=[
            {"type": "1p", "is_buy": False, "time": "2026-04-23 10:30:00", "price": 12.7},
            {"type": "2", "is_buy": False, "time": "2026-04-23 14:10:00", "price": 12.46},
        ],
    )

    result = build_radar_algorithm_v2(levels(m5=m5), freshness())

    assert result["path"] == "UPWARD_MAJOR_WAVE"


def test_classifier_maps_lanqi_style_high_volatility_oscillation():
    m5 = level(
        "m5",
        price=163.5,
        zd=157.68,
        zg=172.0,
        state="IN_CENTER_OSC",
        last_bi_dir="down",
        bsps=[{"type": "1", "is_buy": False, "time": "2026-04-24 14:30:00", "price": 172.0}],
    )

    result = build_radar_algorithm_v2(levels(m5=m5), freshness())

    assert result["path"] == "HIGH_VOLATILITY_OSCILLATION"
    assert result["relation"] == "HIGH_UP_HIGH_VOLATILE"


def test_classifier_maps_zhaoyi_and_rockchip_style_pullback_in_uptrend():
    m5 = level(
        "m5",
        price=297.7,
        zd=302.57,
        zg=307.62,
        state="IN_CENTER_OSC",
        last_bi_dir="down",
        bsps=[
            {"type": "1", "is_buy": False, "time": "2026-04-23 09:35:00", "price": 326.01},
            {"type": "2", "is_buy": False, "time": "2026-04-23 14:05:00", "price": 309.75},
        ],
    )

    result = build_radar_algorithm_v2(levels(m5=m5), freshness())

    assert result["path"] == "PULLBACK_IN_UPTREND"
    assert result["relation"] == "HIGH_UP_LOW_WEAK"


def test_classifier_maps_rockchip_style_micro_conversion():
    day = level("day", price=178.65, zd=177.1979, zg=180.37, state="UPWARD_LEAVING", last_bi_dir="up")
    m30 = level("m30", price=178.65, zd=168.2, zg=171.2, state="THIRD_BUY_CONFIRMED", last_bi_dir="down")
    m5 = level(
        "m5",
        price=178.65,
        zd=178.29,
        zg=178.66,
        state="DOWNWARD_LEAVING",
        last_bi_dir="down",
        active_begin="2026-04-22 11:15:00",
        bsps=[
            {"type": "1", "is_buy": True, "time": "2026-04-23 13:10:00", "price": 175.08},
            {"type": "2", "is_buy": True, "time": "2026-04-23 14:35:00", "price": 175.9},
            {"type": "2s", "is_buy": False, "time": "2026-04-22 14:35:00", "price": 178.66},
            {"type": "3a", "is_buy": False, "time": "2026-04-23 14:05:00", "price": 177.3},
        ],
    )

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())

    assert result["path"] == "PULLBACK_IN_UPTREND"
    assert result["phase"] == "MICRO_CONVERSION"
    assert result["relation"] == "HIGH_UP_LOW_CONTEST"
    assert result["patterns"][0]["code"] == "MICRO_CONVERSION_BREAKOUT"


def test_patterns_detect_big_center_small_center_up_break():
    day = level(
        "day",
        price=537.27,
        zd=372.54,
        zg=433.33,
        state="THIRD_BUY_CONFIRMED",
        last_bi_dir="down",
        bsps=[{"type": "3a", "is_buy": True, "time": "2026-03-31", "price": 434.7}],
        active_begin="2025-10-29",
    )
    m30 = level("m30", price=537.27, zd=442.86, zg=462.87, state="UPWARD_LEAVING", last_bi_dir="up")
    m5 = level("m5", price=537.27, zd=490.02, zg=502.0, state="UPWARD_LEAVING", last_bi_dir="up")

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())

    assert result["path"] == "UPWARD_MAJOR_WAVE"
    assert [item["code"] for item in result["patterns"]] == ["BIG_CENTER_SMALL_CENTER_UP_BREAK"]
    assert result["patterns"][0]["path_hint"] == "UPWARD_MAJOR_WAVE"
    assert result["transition"]["status"] == "MAINTAINED"
    assert result["transition"]["pattern_code"] == "BIG_CENTER_SMALL_CENTER_UP_BREAK"
    assert any(item["level_role"] == "L0" and item["field"] == "ZG" for item in result["boundaries"]["maintain"])
    assert any("大中枢" in item["meaning"] for item in result["boundaries"]["invalidate"])


def test_big_center_small_center_up_break_requires_no_unresolved_sell_pressure():
    day = level(
        "day",
        price=537.27,
        zd=372.54,
        zg=433.33,
        state="THIRD_BUY_CONFIRMED",
        last_bi_dir="down",
        bsps=[{"type": "3a", "is_buy": True, "time": "2026-03-31", "price": 434.7}],
        active_begin="2025-10-29",
    )
    m30 = level(
        "m30",
        price=537.27,
        zd=442.86,
        zg=462.87,
        state="UPWARD_LEAVING",
        last_bi_dir="up",
        bsps=[{"type": "1", "is_buy": False, "time": "2026-04-23 14:00:00", "price": 627.8}],
    )
    m5 = level("m5", price=537.27, zd=490.02, zg=502.0, state="UPWARD_LEAVING", last_bi_dir="up")

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())

    assert "BIG_CENTER_SMALL_CENTER_UP_BREAK" not in [item["code"] for item in result["patterns"]]


def test_center_nesting_exposes_big_center_small_center_relation():
    day = level("day", price=537.27, zd=372.54, zg=433.33, state="THIRD_BUY_CONFIRMED", last_bi_dir="down")
    m30 = level("m30", price=537.27, zd=442.86, zg=462.87, state="UPWARD_LEAVING", last_bi_dir="up")
    m5 = level("m5", price=537.27, zd=445.0, zg=455.0, state="IN_CENTER_OSC", last_bi_dir="up")

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())

    assert result["center_nesting"]["L0_L1"]["relation"] == "CHILD_ABOVE_PARENT"
    assert result["center_nesting"]["L0_L1"]["gap_to_parent_zg"] == 9.53
    assert result["center_nesting"]["L1_L2"]["relation"] == "CHILD_INSIDE_PARENT"


def test_center_nesting_keeps_negative_gap_when_child_below_parent():
    day = level("day", price=64.75, zd=69.98, zg=75.48, state="DOWNWARD_LEAVING", last_bi_dir="down")
    m30 = level("m30", price=64.75, zd=63.0, zg=65.0, state="DOWNWARD_LEAVING", last_bi_dir="down")

    result = build_radar_algorithm_v2(levels(day=day, m30=m30), freshness())

    assert result["center_nesting"]["L0_L1"]["relation"] == "CHILD_BELOW_PARENT"
    assert result["center_nesting"]["L0_L1"]["gap_to_parent_zg"] == -12.48


def test_patterns_detect_third_buy_fast_sell_risk_without_mislabeling_up_break():
    m30 = level(
        "m30",
        price=22.87,
        zd=20.88,
        zg=22.28,
        state="IN_CENTER_OSC",
        last_bi_dir="down",
        bsps=[
            {"type": "3a", "is_buy": True, "time": "2026-02-02 15:00:00", "price": 20.88},
            {"type": "1", "is_buy": False, "time": "2026-02-04 13:30:00", "price": 25.98},
            {"type": "2", "is_buy": False, "time": "2026-02-09 11:00:00", "price": 24.6},
        ],
        active_begin="2026-01-26 10:00:00",
    )

    result = build_radar_algorithm_v2(levels(m30=m30), freshness())

    assert "THIRD_BUY_FAST_SELL_RISK" in [item["code"] for item in result["patterns"]]
    assert "BIG_CENTER_SMALL_CENTER_UP_BREAK" not in [item["code"] for item in result["patterns"]]
    assert result["transition"]["status"] == "RISK"
    assert result["transition"]["to"] == "HIGH_VOLATILITY_OSCILLATION"
    assert any(item["field"] == "S2" and item["trigger"] == "risk_event" for item in result["boundaries"]["invalidate"])
    assert any("二卖" in item["meaning"] for item in result["boundaries"]["pressure"])


def test_patterns_require_ordered_sell_sequence_after_third_buy():
    m30 = level(
        "m30",
        price=22.87,
        zd=20.88,
        zg=22.28,
        state="IN_CENTER_OSC",
        last_bi_dir="down",
        bsps=[
            {"type": "1", "is_buy": False, "time": "2026-02-04 13:30:00", "price": 25.98},
            {"type": "2", "is_buy": False, "time": "2026-02-09 11:00:00", "price": 24.6},
            {"type": "3a", "is_buy": True, "time": "2026-02-10 15:00:00", "price": 20.88},
        ],
        active_begin="2026-01-26 10:00:00",
    )

    result = build_radar_algorithm_v2(levels(m30=m30), freshness())

    assert "THIRD_BUY_FAST_SELL_RISK" not in [item["code"] for item in result["patterns"]]


def test_patterns_use_sell_sequence_after_third_buy_for_evidence_prices():
    m30 = level(
        "m30",
        price=228.71,
        zd=235.3,
        zg=239.5,
        state="IN_CENTER_OSC",
        last_bi_dir="down",
        bsps=[
            {"type": "1", "is_buy": False, "time": "2025-11-27 11:00:00", "price": 164.98},
            {"type": "2", "is_buy": False, "time": "2025-12-04 15:00:00", "price": 143.42},
            {"type": "3a", "is_buy": True, "time": "2026-04-07 15:00:00", "price": 230.23},
            {"type": "1", "is_buy": False, "time": "2026-04-15 11:00:00", "price": 258.6},
            {"type": "2", "is_buy": False, "time": "2026-04-21 10:00:00", "price": 254.04},
        ],
        active_begin="2026-03-27 10:00:00",
    )

    result = build_radar_algorithm_v2(levels(m30=m30), freshness())
    evidence = result["patterns"][0]["evidence"]

    assert result["patterns"][0]["code"] == "THIRD_BUY_FAST_SELL_RISK"
    assert [item["value"] for item in evidence[:3]] == [230.23, 258.6, 254.04]


def test_stale_third_buy_sell_risk_is_suppressed_after_repair_buy_chain():
    m30 = level(
        "m30",
        price=62.43,
        zd=55.24,
        zg=57.05,
        state="IN_CENTER_OSC",
        last_bi_dir="down",
        bsps=[
            {"type": "3b", "is_buy": True, "time": "2025-12-04 10:30:00", "price": 34.89},
            {"type": "1", "is_buy": False, "time": "2025-12-23 10:30:00", "price": 44.11},
            {"type": "2", "is_buy": False, "time": "2025-12-24 15:00:00", "price": 41.05},
            {"type": "3a", "is_buy": True, "time": "2026-02-26 15:00:00", "price": 63.75},
            {"type": "1p", "is_buy": False, "time": "2026-03-05 10:00:00", "price": 81.38},
            {"type": "2", "is_buy": False, "time": "2026-03-11 10:00:00", "price": 74.28},
            {"type": "1", "is_buy": True, "time": "2026-03-24 10:30:00", "price": 51.14},
            {"type": "2", "is_buy": True, "time": "2026-03-31 11:30:00", "price": 55.24},
            {"type": "2s", "is_buy": True, "time": "2026-04-02 14:30:00", "price": 52.77},
        ],
        active_begin="2026-03-31 11:30:00",
    )

    result = build_radar_algorithm_v2(levels(m30=m30), freshness())
    codes = [item["code"] for item in result["patterns"]]

    assert "THIRD_BUY_FAST_SELL_RISK" not in codes
    assert "SMALL_TURN_BIG_FAST_B2_B3" not in codes
    assert result["transition"]["status"] == "UNCHANGED"


def test_pattern_boundaries_for_pullback_repair_put_reclaim_before_sell_pressure():
    day = level(
        "day",
        price=84.99,
        zd=29.7779,
        zg=31.5699,
        state="THIRD_BUY_CONFIRMED",
        last_bi_dir="down",
        bsps=[{"type": "1p", "is_buy": False, "time": "2026-04-14", "price": 88.48}],
    )
    m30 = level(
        "m30",
        price=84.99,
        zd=77.51,
        zg=81.44,
        state="UPWARD_LEAVING",
        last_bi_dir="up",
        bsps=[
            {"type": "1", "is_buy": False, "time": "2026-04-14 15:00:00", "price": 88.39},
            {"type": "2", "is_buy": False, "time": "2026-04-24 15:00:00", "price": 86.25},
        ],
        active_begin="2026-03-30 10:00:00",
    )
    m5 = level(
        "m5",
        price=84.99,
        zd=79.78,
        zg=80.49,
        state="THIRD_BUY_CONFIRMED",
        last_bi_dir="down",
        bsps=[
            {"type": "1", "is_buy": True, "time": "2026-04-23 09:55:00", "price": 79.0},
            {"type": "2", "is_buy": True, "time": "2026-04-23 13:10:00", "price": 79.5},
            {"type": "2s", "is_buy": True, "time": "2026-04-23 14:35:00", "price": 79.78},
            {"type": "3a", "is_buy": True, "time": "2026-04-24 11:10:00", "price": 82.0},
            {"type": "1p", "is_buy": False, "time": "2026-04-24 14:40:00", "price": 86.25},
        ],
        active_begin="2026-04-23 10:30:00",
    )

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())
    confirm = result["boundaries"]["confirm"]

    assert [item["code"] for item in result["patterns"]] == ["BIG_CENTER_SMALL_CENTER_PULLBACK_REPAIR"]
    assert result["transition"]["status"] == "PARTIAL"
    assert result["transition"]["to"] == "A_PARTIAL_TRIGGERED"
    assert confirm[0]["field"] == "ZG"
    assert confirm[0]["value"] == 80.49
    assert confirm[1]["field"] == "S1P"
    assert any(item["field"] == "ZG" and item["value"] == 81.44 for item in result["boundaries"]["maintain"])


def test_classifier_maps_wuxi_style_center_upper_contest():
    day = level(
        "day",
        price=100.08,
        zd=92.43,
        zg=106.58,
        state="UPWARD_LEAVING",
        last_bi_dir="up",
        bsps=[
            {"type": "2", "is_buy": True, "time": "2026-02-03", "price": 92.43},
            {"type": "1", "is_buy": False, "time": "2026-04-15", "price": 108.75},
        ],
        active_begin="2026-01-14",
    )
    m30 = level(
        "m30",
        price=100.08,
        zd=98.9,
        zg=100.19,
        state="UPWARD_LEAVING",
        last_bi_dir="up",
        bsps=[
            {"type": "3a", "is_buy": True, "time": "2026-04-21 11:00:00", "price": 97.63},
            {"type": "2", "is_buy": False, "time": "2026-04-23 10:00:00", "price": 101.24},
        ],
        active_begin="2026-03-31 10:00:00",
    )
    m5 = level(
        "m5",
        price=100.08,
        zd=98.83,
        zg=99.54,
        state="THIRD_BUY_CONFIRMED",
        last_bi_dir="down",
        bsps=[
            {"type": "2s", "is_buy": True, "time": "2026-04-23 13:05:00", "price": 98.97},
            {"type": "1", "is_buy": False, "time": "2026-04-23 10:00:00", "price": 101.24},
            {"type": "2", "is_buy": False, "time": "2026-04-23 14:05:00", "price": 100.58},
        ],
        active_begin="2026-04-22 09:40:00",
    )

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())

    assert result["path"] == "HIGH_VOLATILITY_OSCILLATION"
    assert result["phase"] == "CENTER_UPPER_CONTEST"
    assert result["relation"] == "CENTER_UPPER_SELL_PRESSURE"


def test_boundaries_for_micro_conversion_use_5m_edge_and_30m_support():
    day = level("day", price=178.65, zd=177.1979, zg=180.37, state="UPWARD_LEAVING", last_bi_dir="up")
    m30 = level("m30", price=178.65, zd=168.2, zg=171.2, state="THIRD_BUY_CONFIRMED", last_bi_dir="down")
    m5 = level(
        "m5",
        price=178.65,
        zd=178.29,
        zg=178.66,
        state="DOWNWARD_LEAVING",
        last_bi_dir="down",
        active_begin="2026-04-22 11:15:00",
        bsps=[
            {"type": "2", "is_buy": True, "time": "2026-04-23 14:35:00", "price": 175.9},
            {"type": "3a", "is_buy": False, "time": "2026-04-23 14:05:00", "price": 177.3},
        ],
    )

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())
    boundaries = result["boundaries"]

    assert boundaries["confirm"][0]["value"] == 178.66
    assert boundaries["confirm"][0]["trigger"] == "break_above"
    assert boundaries["maintain"][0]["value"] == 178.29
    assert boundaries["invalidate"][1]["value"] == 168.2


def test_boundaries_for_center_upper_contest_use_30m_zg_zd_and_sell_pressure():
    day = level("day", price=100.08, zd=92.43, zg=106.58, state="UPWARD_LEAVING", last_bi_dir="up")
    m30 = level(
        "m30",
        price=100.08,
        zd=98.9,
        zg=100.19,
        state="UPWARD_LEAVING",
        last_bi_dir="up",
        active_begin="2026-03-31 10:00:00",
        bsps=[
            {"type": "2", "is_buy": False, "time": "2026-04-23 10:00:00", "price": 101.24},
        ],
    )
    m5 = level(
        "m5",
        price=100.08,
        zd=98.83,
        zg=99.54,
        state="THIRD_BUY_CONFIRMED",
        last_bi_dir="down",
        active_begin="2026-04-22 09:40:00",
        bsps=[
            {"type": "2s", "is_buy": True, "time": "2026-04-23 13:05:00", "price": 98.97},
            {"type": "2", "is_buy": False, "time": "2026-04-23 14:05:00", "price": 100.58},
        ],
    )

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())
    boundaries = result["boundaries"]

    assert boundaries["confirm"][0]["value"] == 100.19
    assert boundaries["confirm"][1]["value"] == 100.58
    assert boundaries["maintain"][0]["value"] == 98.9
    assert boundaries["pressure"][1]["value"] == 101.24


def test_boundaries_for_pullback_in_uptrend_use_l2_reclaim_and_l1_failure():
    m30 = level("m30", price=297.7, zd=287.13, zg=287.48, state="THIRD_BUY_CONFIRMED", last_bi_dir="down")
    m5 = level("m5", price=297.7, zd=302.57, zg=307.62, state="IN_CENTER_OSC", last_bi_dir="down")

    result = build_radar_algorithm_v2(levels(m30=m30, m5=m5), freshness())
    boundaries = result["boundaries"]

    assert result["path"] == "PULLBACK_IN_UPTREND"
    assert boundaries["confirm"][0]["value"] == 302.57
    assert boundaries["confirm"][1]["value"] == 307.62
    assert boundaries["invalidate"][0]["value"] == 287.13


def test_confirmation_marks_pullback_reclaim_as_partial_a():
    day = level("day", price=303.42, zd=186.41, zg=234.63, state="UPWARD_LEAVING")
    m30 = level("m30", price=303.42, zd=287.13, zg=287.48, state="THIRD_BUY_CONFIRMED", last_bi_dir="down")
    m5 = level("m5", price=303.42, zd=302.57, zg=307.62, state="IN_CENTER_OSC", last_bi_dir="down")

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())

    assert result["path"] == "PULLBACK_IN_UPTREND"
    assert result["a_state"] == "A_PARTIAL_TRIGGERED"
    assert result["confirmation"]["progress"] == 0.5
    assert [item["field"] for item in result["confirmation"]["matched"]] == ["ZD"]
    assert [item["field"] for item in result["confirmation"]["unmatched"]] == ["ZG"]


def test_boundary_groups_translate_raw_codes_into_trading_roles():
    day = level("day", price=303.42, zd=186.41, zg=234.63, state="UPWARD_LEAVING")
    m30 = level("m30", price=303.42, zd=287.13, zg=287.48, state="THIRD_BUY_CONFIRMED", last_bi_dir="down")
    m5 = level("m5", price=303.42, zd=302.57, zg=307.62, state="IN_CENTER_OSC", last_bi_dir="down")

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())
    groups = {group["id"]: group for group in result["boundary_groups"]}

    assert groups["short_execution"]["label"] == "短线执行线"
    assert [item["value"] for item in groups["short_execution"]["items"][:2]] == [302.57, 307.62]
    assert groups["short_execution"]["items"][0]["display_label"] == "短线 5ZD"
    assert groups["mid_defense"]["items"][0]["source_label"] == "30分钟中级别结构"
    assert groups["invalidation"]["label"] == "失效线"


def test_trigger_playbook_expresses_if_then_path_events():
    day = level("day", price=303.42, zd=186.41, zg=234.63, state="UPWARD_LEAVING")
    m30 = level("m30", price=303.42, zd=287.13, zg=287.48, state="THIRD_BUY_CONFIRMED", last_bi_dir="down")
    m5 = level("m5", price=303.42, zd=302.57, zg=307.62, state="IN_CENTER_OSC", last_bi_dir="down")

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())
    playbook = result["trigger_playbook"]

    assert playbook[0]["path"] == "A"
    assert playbook[0]["condition"] == "突破5ZG 307.62"
    assert playbook[0]["then"] == "突破5分钟中枢上沿，回到上涨延续"
    assert any(item["path"] == "A" and item["condition"] == "守住30ZD 287.13" for item in playbook)
    assert any(item["path"] == "C" and item["condition"] == "跌破30ZD 287.13" for item in playbook)


def test_confirmation_marks_pullback_reclaim_as_full_a_after_zg_break():
    day = level("day", price=309.0, zd=186.41, zg=234.63, state="UPWARD_LEAVING")
    m30 = level("m30", price=309.0, zd=287.13, zg=287.48, state="THIRD_BUY_CONFIRMED", last_bi_dir="down")
    m5 = level("m5", price=309.0, zd=302.57, zg=307.62, state="IN_CENTER_OSC", last_bi_dir="down")

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())

    assert result["path"] == "PULLBACK_IN_UPTREND"
    assert result["a_state"] == "A_FULL_TRIGGERED"
    assert result["confirmation"]["progress"] == 1.0
    assert [item["field"] for item in result["confirmation"]["matched"]] == ["ZD", "ZG"]
    assert result["confirmation"]["unmatched"] == []
    assert all(item["condition"] not in {"突破5ZD 302.57", "突破5ZG 307.62"} for item in result["trigger_playbook"])
    assert "A 路径已确认" in result["summary"]


def test_high_volatility_summary_keeps_future_risk_conditional_after_a_confirmed():
    m5 = level(
        "m5",
        price=84.11,
        zd=81.53,
        zg=83.79,
        state="IN_CENTER_OSC",
        last_bi_dir="down",
        div_info={"type": "顶背驰", "severity": "warning"},
        bsps=[{"type": "2", "is_buy": False, "time": "2026-04-23 14:05:00", "price": 80.49}],
    )

    result = build_radar_algorithm_v2(levels(m5=m5), freshness())

    assert result["path"] == "HIGH_VOLATILITY_OSCILLATION"
    assert result["a_state"] == "A_FULL_TRIGGERED"
    assert result["summary"] == "高波动震荡后转强，A 路径已确认。"
    conditions = [item["condition"] for item in result["trigger_playbook"]]
    assert "突破5S2 80.49" not in conditions
    assert "突破5ZG 83.79" not in conditions
    assert "守住5ZD 81.53" in conditions
    assert "跌破5ZD 81.53" in conditions


def test_patterns_detect_third_buy_retest_up_as_template():
    day = level("day", price=178.09, zd=77.3385, zg=80.1509, state="UPWARD_LEAVING", last_bi_dir="up")
    m30 = level("m30", price=178.09, zd=144.46, zg=145.5, state="THIRD_BUY_CONFIRMED", last_bi_dir="down")
    m5 = level("m5", price=178.09, zd=157.68, zg=159.0, state="IN_CENTER_OSC", last_bi_dir="down")

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())
    codes = [item["code"] for item in result["patterns"]]

    assert result["path"] == "PULLBACK_IN_UPTREND"
    assert "THIRD_BUY_RETEST_UP" in codes
    assert result["transition"]["pattern_code"] == "THIRD_BUY_RETEST_UP"
    assert result["transition"]["to"] == "UPWARD_MAJOR_WAVE"
    assert result["summary"] == "三买回踩向上，A 路径已确认。"
    assert result["trigger_playbook"][0]["condition"] == "守住5ZG 159"
    assert result["trigger_playbook"][0]["then"] == "守住短级别旧中枢上沿，三买回踩向上有效"
    assert any(item["condition"] == "跌破5ZG 159" for item in result["trigger_playbook"])


def test_near_historical_high_adds_pressure_observation_without_path_trigger():
    day = level("day", price=178.09, zd=77.3385, zg=80.1509, state="UPWARD_LEAVING", last_bi_dir="up")
    day["historical_high"] = {"price": 188.88, "time": "2026-02-02"}
    m30 = level("m30", price=178.09, zd=144.46, zg=145.5, state="THIRD_BUY_CONFIRMED", last_bi_dir="down")
    m5 = level("m5", price=178.09, zd=157.68, zg=159.0, state="IN_CENTER_OSC", last_bi_dir="down")

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())

    assert result["atoms"]["L0"]["historical_high"]["time"] == "2026-02-02"
    assert result["atoms"]["L0"]["historical_high"]["is_near"] is True
    codes = [item["code"] for item in result["patterns"]]
    assert "HISTORICAL_HIGH_PRESSURE" in codes
    assert "THIRD_BUY_RETEST_UP" in codes
    assert result["transition"]["pattern_code"] == "THIRD_BUY_RETEST_UP"
    assert any(
        item["field"] == "ATH" and item["value"] == 188.88
        for item in result["boundaries"]["pressure"]
    )
    ath = next(item for item in result["boundaries"]["pressure"] if item["field"] == "ATH")
    assert ath["distance_pct"] == 0.0606
    assert all(item["condition"] != "观察历史前高 188.88" for item in result["trigger_playbook"])
    assert all(item["condition"] != "观察5ZD 157.68" for item in result["trigger_playbook"])


def test_boundaries_for_standard_high_volatility_are_not_empty():
    m5 = level(
        "m5",
        price=163.5,
        zd=157.68,
        zg=159.0,
        state="IN_CENTER_OSC",
        last_bi_dir="down",
        bsps=[{"type": "1", "is_buy": False, "time": "2026-04-24 14:30:00", "price": 168.7}],
    )

    result = build_radar_algorithm_v2(levels(m5=m5), freshness())
    boundaries = result["boundaries"]

    assert result["path"] == "HIGH_VOLATILITY_OSCILLATION"
    assert boundaries["confirm"]
    assert boundaries["invalidate"]
    assert result["next_watch"]


def test_scenarios_for_micro_conversion_are_abc_complete_classification():
    day = level("day", price=178.65, zd=177.1979, zg=180.37, state="UPWARD_LEAVING", last_bi_dir="up")
    m30 = level("m30", price=178.65, zd=168.2, zg=171.2, state="THIRD_BUY_CONFIRMED", last_bi_dir="down")
    m5 = level(
        "m5",
        price=178.65,
        zd=178.29,
        zg=178.66,
        state="DOWNWARD_LEAVING",
        last_bi_dir="down",
        active_begin="2026-04-22 11:15:00",
        bsps=[
            {"type": "2", "is_buy": True, "time": "2026-04-23 14:35:00", "price": 175.9},
            {"type": "3a", "is_buy": False, "time": "2026-04-23 14:05:00", "price": 177.3},
        ],
    )

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())
    scenarios = result["scenarios"]

    assert [item["id"] for item in scenarios] == ["A", "B", "C"]
    assert scenarios[0]["name"] == "转换点向上确认"
    assert scenarios[1]["state"] == "CURRENT"
    assert scenarios[2]["role"] == "invalidate"
    assert "178.66" in scenarios[0]["trigger_if"][0]


def test_scenarios_for_center_upper_contest_use_boundary_sources():
    day = level("day", price=100.08, zd=92.43, zg=106.58, state="UPWARD_LEAVING", last_bi_dir="up")
    m30 = level(
        "m30",
        price=100.08,
        zd=98.9,
        zg=100.19,
        state="UPWARD_LEAVING",
        last_bi_dir="up",
        active_begin="2026-03-31 10:00:00",
        bsps=[{"type": "2", "is_buy": False, "time": "2026-04-23 10:00:00", "price": 101.24}],
    )
    m5 = level(
        "m5",
        price=100.08,
        zd=98.83,
        zg=99.54,
        state="THIRD_BUY_CONFIRMED",
        last_bi_dir="down",
        active_begin="2026-04-22 09:40:00",
        bsps=[
            {"type": "2s", "is_buy": True, "time": "2026-04-23 13:05:00", "price": 98.97},
            {"type": "2", "is_buy": False, "time": "2026-04-23 14:05:00", "price": 100.58},
        ],
    )

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())
    scenarios = result["scenarios"]

    assert scenarios[0]["name"] == "上沿争夺转强"
    assert scenarios[0]["source_boundaries"][0]["value"] == 100.19
    assert scenarios[1]["name"] == "中枢上沿震荡"
    assert scenarios[2]["name"] == "上沿尝试失败"


def test_scenarios_for_no_edge_stay_defensive_and_complete():
    result = build_radar_algorithm_v2({"day": level("day")}, freshness())
    scenarios = result["scenarios"]

    assert result["path"] == "NO_EDGE"
    assert [item["id"] for item in scenarios] == ["A", "B", "C"]
    assert scenarios[1]["state"] == "CURRENT"
    assert scenarios[1]["name"] == "继续无优势"


def test_output_composer_for_micro_conversion_is_frontend_ready():
    day = level("day", price=178.65, zd=177.1979, zg=180.37, state="UPWARD_LEAVING", last_bi_dir="up")
    m30 = level("m30", price=178.65, zd=168.2, zg=171.2, state="THIRD_BUY_CONFIRMED", last_bi_dir="down")
    m5 = level(
        "m5",
        price=178.65,
        zd=178.29,
        zg=178.66,
        state="DOWNWARD_LEAVING",
        last_bi_dir="down",
        active_begin="2026-04-22 11:15:00",
        bsps=[
            {"type": "2", "is_buy": True, "time": "2026-04-23 14:35:00", "price": 175.9},
            {"type": "3a", "is_buy": False, "time": "2026-04-23 14:05:00", "price": 177.3},
        ],
    )

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())

    assert result["current_scenario_id"] == "B"
    assert result["action_bias"] == "WAIT_BREAKOUT"
    assert result["risk_level"] == "MEDIUM"
    assert "5分钟多空转换点" in result["summary"]
    assert "178.66" in result["next_watch"][0]


def test_output_composer_for_center_upper_contest_marks_upper_break_watch():
    day = level("day", price=100.08, zd=92.43, zg=106.58, state="UPWARD_LEAVING", last_bi_dir="up")
    m30 = level(
        "m30",
        price=100.08,
        zd=98.9,
        zg=100.19,
        state="UPWARD_LEAVING",
        last_bi_dir="up",
        active_begin="2026-03-31 10:00:00",
        bsps=[{"type": "2", "is_buy": False, "time": "2026-04-23 10:00:00", "price": 101.24}],
    )
    m5 = level(
        "m5",
        price=100.08,
        zd=98.83,
        zg=99.54,
        state="THIRD_BUY_CONFIRMED",
        last_bi_dir="down",
        active_begin="2026-04-22 09:40:00",
        bsps=[
            {"type": "2s", "is_buy": True, "time": "2026-04-23 13:05:00", "price": 98.97},
            {"type": "2", "is_buy": False, "time": "2026-04-23 14:05:00", "price": 100.58},
        ],
    )

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())

    assert result["action_bias"] == "WAIT_UPPER_BREAK"
    assert result["risk_level"] == "MEDIUM_HIGH"
    assert "中枢上沿争夺" in result["summary"]
    assert result["next_watch"][0].startswith("30ZG 100.19")


def test_output_composer_for_no_edge_reports_data_notes():
    result = build_radar_algorithm_v2({"day": level("day")}, freshness())

    assert result["action_bias"] == "WAIT_STRUCTURE"
    assert result["risk_level"] == "HIGH"
    assert result["current_scenario_id"] == "B"
    assert result["data_notes"]["missing_or_weak_levels"] == ["L1", "L2"]


def test_output_summary_for_downward_defense_does_not_call_confirm_turn_strong():
    day = level("day", price=64.75, zd=69.98, zg=75.48, state="DOWNWARD_LEAVING", last_bi_dir="down")
    m30 = level("m30", price=64.75, zd=69.96, zg=71.4, state="DOWNWARD_LEAVING", last_bi_dir="down")
    m5 = level("m5", price=64.75, zd=66.01, zg=66.88, state="DOWNWARD_LEAVING", last_bi_dir="down")

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())

    assert result["path"] == "DOWNWARD_DEFENSE"
    assert "下跌延续" in result["summary"]
    assert "转强" not in result["summary"]


def test_bottom_repair_has_named_scenarios_and_boundaries():
    day = level("day", price=10.0, zd=12.0, zg=13.0, state="DOWNWARD_LEAVING", last_bi_dir="down")
    m30 = level("m30", price=10.0, zd=11.0, zg=11.5, state="DOWNWARD_LEAVING", last_bi_dir="down")
    m5 = level(
        "m5",
        price=10.0,
        zd=9.7,
        zg=10.2,
        state="IN_CENTER_OSC",
        last_bi_dir="up",
        patterns=["底背驰"],
        bsps=[{"type": "1", "is_buy": True, "time": "2026-04-24 10:30:00", "price": 9.6}],
    )

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())

    assert result["path"] == "BOTTOM_REPAIR"
    assert result["scenarios"][0]["name"] == "底部修复转强"
    assert result["boundaries"]["confirm"]
    assert result["next_watch"]


def test_classifier_maps_huichuan_and_jiejia_style_downward_defense():
    day = level("day", price=64.75, zd=69.98, zg=75.48, state="DOWNWARD_LEAVING", last_bi_dir="down")
    m30 = level("m30", price=64.75, zd=69.96, zg=71.4, state="DOWNWARD_LEAVING", last_bi_dir="down")
    m5 = level("m5", price=64.75, zd=66.01, zg=66.88, state="DOWNWARD_LEAVING", last_bi_dir="down")

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())

    assert result["path"] == "DOWNWARD_DEFENSE"
    assert result["relation"] == "ALIGN_DOWN"


def test_classifier_maps_changchun_gas_style_center_rebound():
    day = level("day", price=6.2, zd=5.8, zg=6.6, state="IN_CENTER_OSC", last_bi_dir="up")
    m30 = level("m30", price=6.2, zd=5.9, zg=6.4, state="IN_CENTER_OSC", last_bi_dir="up")
    m5 = level(
        "m5",
        price=6.2,
        zd=5.95,
        zg=6.3,
        state="IN_CENTER_OSC",
        last_bi_dir="up",
        patterns=["底背驰"],
    )

    result = build_radar_algorithm_v2(levels(day=day, m30=m30, m5=m5), freshness())

    assert result["path"] == "CENTER_REBOUND"
    assert result["relation"] == "CENTER_REPAIR"


def test_missing_trigger_level_returns_no_edge_for_wuxi_style_incomplete_data():
    result = build_radar_algorithm_v2(
        {
            "day": level("day", price=100.08, zd=92.43, zg=106.58, state="UPWARD_LEAVING"),
            "m30": level("m30", price=100.08, zd=97.63, zg=100.19, state="UPWARD_LEAVING"),
        },
        freshness(),
    )

    assert result["path"] == "NO_EDGE"
    assert result["requires_no_edge"] is True
    assert result["blocking_reasons"] == ["L2 缺少价格或中枢结构"]


def test_stale_freshness_blocks_path_even_when_levels_are_complete():
    result = build_radar_algorithm_v2(levels(), freshness(stale=True))

    assert result["path"] == "NO_EDGE"
    assert result["confidence"] == "STALE"
    assert result["reason_codes"] == ["FRESHNESS_STALE"]
