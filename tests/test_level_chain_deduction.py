import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.decision.level_chain_deduction import build_level_chain_deduction


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
    patterns=None,
    state="UPWARD_LEAVING",
    last_bi_dir="up",
    bsps=None,
    bis=None,
    zg=19.5,
    zd=18.0,
    div_info=None,
):
    bis = bis if bis is not None else [
        {"x0": "2026-04-24 09:30:00", "x1": "2026-04-24 10:00:00", "is_up": True},
        {"x0": "2026-04-24 10:00:00", "x1": "2026-04-24 10:30:00", "is_up": False},
        {"x0": "2026-04-24 10:30:00", "x1": "2026-04-24 11:00:00", "is_up": True},
    ]
    return {
        "level": name,
        "price": price,
        "state": state,
        "patterns": patterns or [],
        "zg": zg,
        "zd": zd,
        "zs_operative_zg": zg,
        "zs_operative_zd": zd,
        "active_zhongshu": {
            "begin_date": "2026-04-24 09:30:00",
            "zg": zg,
            "zd": zd,
        },
        "last_bi_dir": last_bi_dir,
        "bsps": bsps or [],
        "bis": bis,
        "detail_bis": bis,
        "div_info": div_info,
    }


def levels(day=None, m30=None, m5=None):
    return {
        "day": day if day is not None else level("day", patterns=["二买"]),
        "m30": m30 if m30 is not None else level("m30", patterns=["底背驰"]),
        "m5": m5 if m5 is not None else level("m5", patterns=[]),
    }


def test_stale_freshness_blocks_any_trigger():
    m5 = level(
        "m5",
        patterns=["三买"],
        bsps=[{"type": "3a", "is_buy": True, "time": "2026-04-24 10:30:00", "price": 20.0}],
    )

    result = build_level_chain_deduction(levels(m5=m5), freshness(stale=True))

    assert result["status"] == "STALE"
    assert result["confidence"] == "STALE"
    assert result["buy_point_candidates"] == []


def test_day_unsafe_returns_no_setup_even_when_lower_levels_have_buy_points():
    day = level("day", patterns=["顶背驰"])
    m5 = level(
        "m5",
        patterns=["三买"],
        bsps=[{"type": "3a", "is_buy": True, "time": "2026-04-24 10:30:00", "price": 20.0}],
    )

    result = build_level_chain_deduction(levels(day=day, m5=m5), freshness())

    assert result["status"] == "NO_SETUP"
    assert result["level_roles"]["day"]["state"] == "UNSAFE"


def test_historical_day_sell_pattern_does_not_block_later_buy_structure():
    day = level(
        "day",
        patterns=["1卖", "二买"],
        bsps=[
            {"type": "1", "is_buy": False, "time": "2026-04-20 15:00:00", "price": 22.0},
            {"type": "2", "is_buy": True, "time": "2026-04-24 15:00:00", "price": 20.0},
        ],
    )

    result = build_level_chain_deduction(levels(day=day), freshness())

    assert result["level_roles"]["day"]["state"] == "VALID"
    assert result["status"] != "NO_SETUP"


def test_latest_day_sell_bsp_still_blocks_empty_setup():
    day = level(
        "day",
        patterns=["二买", "1卖"],
        bsps=[
            {"type": "2", "is_buy": True, "time": "2026-04-20 15:00:00", "price": 20.0},
            {"type": "1", "is_buy": False, "time": "2026-04-24 15:00:00", "price": 22.0},
        ],
    )

    result = build_level_chain_deduction(levels(day=day), freshness())

    assert result["status"] == "NO_SETUP"
    assert result["level_roles"]["day"]["state"] == "UNSAFE"


def test_day_valid_but_m30_neutral_waits_for_confirmation():
    m30 = level("m30", patterns=[], state="UNKNOWN", price=17.5, zg=19.5, zd=18.0)

    result = build_level_chain_deduction(levels(m30=m30), freshness())

    assert result["status"] == "WAITING_CONFIRMATION"
    assert result["level_roles"]["30"]["state"] == "NEUTRAL"


def test_day_and_m30_supportive_wait_for_5m_trigger():
    m5 = level("m5", patterns=[], last_bi_dir="up")

    result = build_level_chain_deduction(levels(m5=m5), freshness())

    assert result["status"] == "WAITING_TRIGGER"
    assert result["level_roles"]["5"]["state"] == "WAITING"
    assert result["buy_point_candidates"] == []
    assert result["complete_classification"][1]["id"] == "B_EXTEND"
    assert result["complete_classification"][1]["state"] == "CURRENT"


def test_latest_5m_buy_bsp_confirms_trigger():
    m5 = level(
        "m5",
        patterns=["三买"],
        bsps=[{"type": "3a", "is_buy": True, "time": "2026-04-24 10:30:00", "price": 20.0}],
    )

    result = build_level_chain_deduction(levels(m5=m5), freshness())

    assert result["status"] == "TRIGGER_CONFIRMED"
    assert result["level_roles"]["5"]["state"] == "CONFIRMED"
    assert result["buy_point_candidates"][0]["type"] == "THIRD_BUY"
    assert result["buy_point_candidates"][0]["bsp"]["display"] == "B3A 三买A"
    assert result["buy_point_candidates"][0]["bsp"]["source"] == "CChan BSP_TYPE.T3A"
    assert result["path_thesis"]["phase"] == "结构触发"
    assert result["complete_classification"][0]["id"] == "A_CONFIRM"
    assert result["complete_classification"][0]["state"] == "CONFIRMED"


def test_5m_class_first_buy_bsp_keeps_cchan_label():
    m5 = level(
        "m5",
        patterns=["类一买"],
        bsps=[{"type": "1p", "is_buy": True, "time": "2026-04-24 10:30:00", "price": 20.0}],
    )

    result = build_level_chain_deduction(levels(m5=m5), freshness())

    candidate = result["buy_point_candidates"][0]
    assert result["status"] == "TRIGGER_CONFIRMED"
    assert candidate["type"] == "FIRST_BUY"
    assert candidate["bsp"]["raw_type"] == "1p"
    assert candidate["bsp"]["display"] == "B1P 类一买"
    assert "B1P 类一买" in result["main_path"]["current_step"]


def test_historical_5m_buy_bsp_does_not_confirm_current_trigger():
    m5 = level(
        "m5",
        patterns=["三买"],
        bsps=[{"type": "3a", "is_buy": True, "time": "2026-04-23 14:30:00", "price": 20.0}],
    )

    result = build_level_chain_deduction(levels(m5=m5), freshness())

    assert result["status"] == "WAITING_TRIGGER"
    assert result["buy_point_candidates"] == []


def test_5m_buy_bsp_without_timestamp_does_not_confirm_current_trigger():
    m5 = level(
        "m5",
        patterns=["三买"],
        bsps=[{"type": "3a", "is_buy": True, "price": 20.0}],
    )

    result = build_level_chain_deduction(levels(m5=m5), freshness())

    assert result["status"] == "WAITING_TRIGGER"
    assert result["buy_point_candidates"] == []


def test_5m_pullback_above_boundary_is_forming_not_confirmed():
    m5 = level("m5", patterns=[], last_bi_dir="down", price=19.0)

    result = build_level_chain_deduction(levels(m5=m5), freshness())

    assert result["status"] == "TRIGGER_FORMING"
    assert result["level_roles"]["5"]["state"] == "FORMING"
    assert result["buy_point_candidates"][0]["status"] == "FORMING"
    assert result["buy_point_candidates"][0]["type"] == "FIRST_BUY"
    assert result["buy_point_candidates"][0]["expected_bsp"]["display"] == "B1/B1P 一买/类一买"
    assert result["path_thesis"]["phase"] == "回落验证"
    assert result["complete_classification"][0]["state"] == "FORMING"
    assert "支持观察 A 确认" in result["divergence_context"]["summary"]
    assert "30分底背驰" in result["complete_classification"][0]["evidence"]


def test_5m_pullback_above_zg_forms_third_buy_path():
    m5 = level("m5", patterns=[], last_bi_dir="down", price=19.8, zg=19.5, zd=18.0)

    result = build_level_chain_deduction(levels(m5=m5), freshness())

    candidate = result["buy_point_candidates"][0]
    assert result["status"] == "TRIGGER_FORMING"
    assert candidate["type"] == "THIRD_BUY"
    assert candidate["expected_bsp"]["display"] == "B3A/B3B 三买A/三买B"
    assert "CChan 确认 B3A/B3B 三买" in candidate["trigger_if"]


def test_bottom_divergence_is_attached_to_confirm_path():
    m5 = level(
        "m5",
        patterns=["趋势底背驰"],
        last_bi_dir="down",
        price=19.0,
        div_info={"type": "底背驰", "severity": "高危"},
    )

    result = build_level_chain_deduction(levels(m5=m5), freshness())

    scenario = result["complete_classification"][0]
    assert result["status"] == "TRIGGER_FORMING"
    assert "支持观察 A 确认" in result["divergence_context"]["summary"]
    assert result["divergence_context"]["review_items"][2]["state"] == "SUPPORT_A"
    assert result["divergence_context"]["review_items"][2]["title"] == "底背驰"
    assert "5分底背驰（高危）" in scenario["evidence"]
    assert "5分底背驰（高危）" in scenario["trigger_if"]


def test_top_divergence_is_attached_to_invalid_path():
    m30 = level(
        "m30",
        patterns=["趋势顶背驰"],
        state="UPWARD_LEAVING",
        div_info={"type": "顶背驰", "severity": "高危"},
    )

    result = build_level_chain_deduction(levels(m30=m30), freshness())

    scenario = result["complete_classification"][2]
    assert result["status"] == "FAILED"
    assert "优先防 C 失效" in result["divergence_context"]["summary"]
    assert result["divergence_context"]["review_items"][1]["state"] == "RISK_C"
    assert result["divergence_context"]["review_items"][1]["title"] == "顶背驰"
    assert "30分顶背驰（高危）" in scenario["evidence"]
    assert "30分顶背驰（高危）" in scenario["trigger_if"]


def test_warning_top_divergence_text_does_not_enter_divergence_review():
    m5 = level(
        "m5",
        patterns=["三买后顶背驰警示，谨防3买转1卖"],
        last_bi_dir="up",
    )

    result = build_level_chain_deduction(levels(m5=m5), freshness())

    m5_review = result["divergence_context"]["review_items"][2]
    assert m5_review["state"] == "NEUTRAL"
    assert m5_review["title"] == "无明确背驰"


def test_5m_pullback_after_historical_first_buy_forms_second_buy_path():
    m5 = level(
        "m5",
        patterns=[],
        last_bi_dir="down",
        price=18.8,
        bsps=[{"type": "1", "is_buy": True, "time": "2026-04-23 14:30:00", "price": 18.2}],
    )

    result = build_level_chain_deduction(levels(m5=m5), freshness())

    candidate = result["buy_point_candidates"][0]
    assert result["status"] == "TRIGGER_FORMING"
    assert candidate["type"] == "SECOND_BUY"
    assert candidate["expected_bsp"]["display"] == "B2/B2S 二买/类二买"


def test_price_breaking_30m_zd_fails_deduction():
    m5 = level("m5", patterns=[], last_bi_dir="down", price=17.5)

    result = build_level_chain_deduction(levels(m5=m5), freshness())

    assert result["status"] == "FAILED"
    assert result["level_roles"]["30"]["state"] == "CONFLICTING"
    assert result["path_thesis"]["phase"] == "推演失效"
    assert result["path_thesis"]["boundaries"][0]["meaning"] == "已跌破，主推演进入失效路径。"
    assert result["complete_classification"][2]["state"] == "TRIGGERED"


def test_m30_third_buy_pullback_waits_for_5m_stop_falling():
    m30 = level(
        "m30",
        price=15.08,
        state="THIRD_BUY_CONFIRMED",
        patterns=["三买确认", "1卖"],
        zg=13.72,
        zd=12.9,
        bsps=[
            {"type": "3b", "is_buy": True, "time": "2026-04-21 14:30:00", "price": 14.6},
            {"type": "1", "is_buy": False, "time": "2026-04-22 13:30:00", "price": 15.99},
        ],
    )
    m30["active_zhongshu"]["begin_date"] = "2026-03-04 10:00:00"
    m5 = level("m5", price=15.08, last_bi_dir="down", zg=15.8, zd=15.64)

    result = build_level_chain_deduction(levels(m30=m30, m5=m5), freshness())

    assert result["status"] == "TRIGGER_FORMING"
    assert result["level_roles"]["30"]["state"] == "SUPPORTIVE"
    assert result["level_roles"]["30"]["summary"] == "30分三买后回落，等待5分止跌"
    assert result["level_roles"]["5"]["state"] == "FORMING"
    assert result["path_thesis"]["boundaries"][0]["role"] == "维持"
    assert result["path_thesis"]["boundaries"][0]["price"] == 14.6
    assert result["path_thesis"]["boundaries"][0]["label"] == "30分三买低点 14.6"
    assert "尚无明确背驰确认" in result["divergence_context"]["summary"]


def test_5m_third_buy_forming_uses_5m_zg_as_active_boundary():
    m30 = level(
        "m30",
        price=13.62,
        state="UPWARD_LEAVING",
        patterns=["一买"],
        zg=11.06,
        zd=10.68,
    )
    m5 = level(
        "m5",
        price=13.62,
        state="THIRD_BUY_CONFIRMED",
        patterns=["三买", "三买确认"],
        last_bi_dir="down",
        zg=11.9,
        zd=11.62,
    )

    result = build_level_chain_deduction(levels(m30=m30, m5=m5), freshness())

    assert result["status"] == "TRIGGER_FORMING"
    assert result["buy_point_candidates"][0]["type"] == "THIRD_BUY"
    assert result["path_thesis"]["boundaries"][0]["label"] == "5分ZG 11.9"
    assert result["path_thesis"]["boundaries"][0]["price"] == 11.9
    assert "价格仍守住 5分ZG 11.9" in result["complete_classification"][1]["trigger_if"]
    assert "背驰状态" not in result["path_thesis"]["narrative"]


def test_strong_extension_builds_coach_deduction_sandbox():
    day = level("day", price=13.62, state="UPWARD_LEAVING", patterns=["三买"], zg=11.02, zd=9.01)
    m30 = level("m30", price=13.62, state="UPWARD_LEAVING", patterns=["一买"], zg=11.06, zd=10.68)
    m5 = level(
        "m5",
        price=13.62,
        state="THIRD_BUY_CONFIRMED",
        patterns=["三买", "三买确认"],
        last_bi_dir="down",
        zg=11.9,
        zd=11.62,
    )

    result = build_level_chain_deduction(levels(day=day, m30=m30, m5=m5), freshness())

    coach = result["coach_deduction"]
    assert coach["title"] == "强势上涨主升浪"
    assert "脱离中枢" in coach["diagnosis"]
    assert coach["windows"][0]["code"] == "D"
    assert "第一防线 5分ZG 11.9" in coach["windows"][3]["text"]
    assert [item["id"] for item in coach["scenario_plans"]] == [
        "right_side_major_wave",
        "zhongshu_oscillation",
        "structural_breakdown",
    ]
    assert coach["scenario_plans"][2]["weight_pct"] == 10


def test_extreme_main_wave_with_micro_oscillation_uses_high_volatility_sandbox():
    day = level(
        "day",
        price=163.5,
        state="UPWARD_LEAVING",
        patterns=["二买", "1卖", "二卖", "三买"],
        zg=80.1509,
        zd=77.3385,
        bsps=[
            {"type": "2s", "is_buy": True, "time": "2025-04-07", "price": 61.601},
            {"type": "1p", "is_buy": False, "time": "2026-02-02", "price": 188.88},
        ],
    )
    m30 = level(
        "m30",
        price=163.5,
        state="THIRD_BUY_CONFIRMED",
        patterns=["二卖", "一买", "二买", "三买", "1卖", "三买确认"],
        last_bi_dir="down",
        zg=145.5,
        zd=144.46,
        bsps=[
            {"type": "3a", "is_buy": True, "time": "2026-04-13 10:00:00", "price": 142.4},
            {"type": "1p", "is_buy": False, "time": "2026-04-23 10:00:00", "price": 168.7},
        ],
    )
    m5 = level(
        "m5",
        price=163.5,
        state="IN_CENTER_OSC",
        patterns=["二卖", "一买", "二买", "1卖"],
        last_bi_dir="down",
        zg=159.0,
        zd=157.68,
        bsps=[
            {"type": "1p", "is_buy": True, "time": "2026-04-21 09:45:00", "price": 149.79},
            {"type": "2s", "is_buy": True, "time": "2026-04-23 13:10:00", "price": 158.16},
        ],
    )

    result = build_level_chain_deduction(levels(day=day, m30=m30, m5=m5), freshness())

    assert result["status"] in ("TRIGGER_FORMING", "TRIGGER_CONFIRMED")
    assert result["level_roles"]["day"]["state"] == "VALID"
    assert result["path_thesis"]["boundaries"][0]["label"] == "5分ZG 159"
    coach = result["coach_deduction"]
    assert coach["title"] == "高位剧烈震荡"
    assert coach["windows"][0]["text"] == "超长期主升浪延伸，乖离率极大"
    assert "第一防线 5分ZG 159" in coach["windows"][3]["text"]
    assert [item["id"] for item in coach["scenario_plans"]] == [
        "zhongshu_oscillation",
        "right_side_major_wave",
        "structural_breakdown",
    ]
    assert [item["weight_pct"] for item in coach["scenario_plans"]] == [50, 30, 20]


def test_downward_chain_uses_defensive_breakdown_sandbox():
    day = level(
        "day",
        price=64.75,
        state="DOWNWARD_LEAVING",
        patterns=["二卖", "三卖", "一买", "趋势底背驰"],
        last_bi_dir="down",
        zg=75.48,
        zd=69.98,
        bsps=[
            {"type": "3a", "is_buy": False, "time": "2026-04-15", "price": 69.9},
            {"type": "1p", "is_buy": True, "time": "2026-04-24", "price": 63.63},
        ],
    )
    m30 = level(
        "m30",
        price=64.75,
        state="DOWNWARD_LEAVING",
        patterns=["二买", "1卖", "二卖", "一买"],
        last_bi_dir="down",
        zg=71.4,
        zd=69.96,
        bsps=[{"type": "1", "is_buy": True, "time": "2026-04-07 15:00:00", "price": 64.55}],
    )
    m5 = level(
        "m5",
        price=64.75,
        state="DOWNWARD_LEAVING",
        patterns=["二买", "三买", "1卖", "二卖"],
        last_bi_dir="down",
        zg=66.88,
        zd=66.01,
    )

    result = build_level_chain_deduction(levels(day=day, m30=m30, m5=m5), freshness())

    coach = result["coach_deduction"]
    assert coach["title"] == "向下离开防守"
    assert coach["windows"][0]["text"] == "日线级别深度回调 / 结构破位"
    assert "上方第一压力位 66.01" in coach["windows"][3]["text"]
    assert [item["id"] for item in coach["scenario_plans"]] == [
        "structural_breakdown",
        "zhongshu_oscillation",
        "right_side_major_wave",
    ]
    assert [item["weight_pct"] for item in coach["scenario_plans"]] == [50, 30, 20]


def test_holding_mode_returns_none_in_v1():
    result = build_level_chain_deduction(levels(), freshness(), mode="HOLDING")

    assert result is None
