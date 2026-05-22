from server.engines.ai_native.unified_reasoning_service import (
    normalize_monitor_conditions,
    normalize_watchboard_payload,
    summarize_unified_reasoning,
)


def test_normalize_monitor_conditions_keeps_four_price_triggers():
    payload = {
        "triggers": [
            {"type": "price_below", "level": "4.37", "message_on_trigger": "到承接位了，观察5分钟", "action_on_trigger": "关注"},
            {"type": "price_below", "level": 4.13, "message_on_trigger": "三买失败，止损走人", "action_on_trigger": "止损"},
            {"type": "price_above", "level": 4.5, "message_on_trigger": "突破前高，趋势加速", "action_on_trigger": "加仓"},
            {"type": "price_above", "level": 4.8, "message_on_trigger": "强势延伸", "action_on_trigger": "减仓"},
            {"type": "price_above", "level": 5.2, "message_on_trigger": "第五条不要", "action_on_trigger": "关注"},
        ]
    }

    result = normalize_monitor_conditions(payload)

    assert len(result["triggers"]) == 4
    assert result["triggers"][0] == {
        "id": "t1",
        "type": "price_below",
        "level": 4.37,
        "message_on_trigger": "到承接位了，观察5分钟"[:15],
        "action_on_trigger": "关注",
    }
    assert result["triggers"][1]["action_on_trigger"] == "考虑止损"
    assert result["triggers"][2]["action_on_trigger"] == "考虑加仓"
    assert result["triggers"][3]["action_on_trigger"] == "考虑减仓"


def test_normalize_monitor_conditions_drops_invalid_levels_and_actions():
    payload = {
        "triggers": [
            {"type": "price_cross", "level": 4.37, "message_on_trigger": "无效", "action_on_trigger": "关注"},
            {"type": "price_below", "level": 0, "message_on_trigger": "无效", "action_on_trigger": "关注"},
            {"type": "price_above", "level": 4.5, "message_on_trigger": "", "action_on_trigger": "买爆"},
        ]
    }

    result = normalize_monitor_conditions(payload)

    assert result["triggers"] == [
        {
            "id": "t1",
            "type": "price_above",
            "level": 4.5,
            "message_on_trigger": "触发关键位",
            "action_on_trigger": "关注",
        }
    ]


def test_normalize_monitor_conditions_drops_wrong_way_confirmation_triggers():
    payload = {
        "triggers": [
            {"type": "price_below", "level": 253.49, "message_on_trigger": "回踩253.49不破，三买确认", "action_on_trigger": "加仓"},
            {"type": "price_below", "level": 32.71, "message_on_trigger": "跌破32.71，突破失败", "action_on_trigger": "观望"},
            {"type": "price_above", "level": 35.2, "message_on_trigger": "跌破35.2，结构失守", "action_on_trigger": "止损"},
        ]
    }

    result = normalize_monitor_conditions(payload)

    assert result["triggers"] == [
        {
            "id": "t1",
            "type": "price_below",
            "level": 32.71,
            "message_on_trigger": "跌破32.71，突破失败",
            "action_on_trigger": "观望",
        }
    ]


def test_summarize_unified_reasoning_skips_opening_chatter():
    text = """
    收到数据，开始为你拆解当下的缠论结构。
    当前走势处于日线回拉，正在考验中枢上沿。
    跌破中枢上沿后，先观察是否回到中枢震荡。
    """

    assert summarize_unified_reasoning(text) == "当前走势处于日线回拉，正在考验中枢上沿"


def test_summarize_unified_reasoning_skips_section_headings():
    text = """
    好的，我们现在切换到搭档模式。
    ### **📈 当前走势在做什么？**
    当前走势的核心是：在大级别中枢震荡格局内，小级别发起向上的冲击波。
    """

    assert summarize_unified_reasoning(text) == "当前走势的核心是：在大级别中枢震荡格局内，小级别发起向上的冲击波"


def test_normalize_watchboard_payload_keeps_ai_card_fields():
    payload = {
        "card_summary": "小级别向上离开，冲击日线中枢上沿",
        "card_action": "继续持有",
        "triggers": [
            {"type": "price_above", "level": 40.77, "message_on_trigger": "突破日线中枢上沿", "action_on_trigger": "关注"},
            {"type": "price_below", "level": 38.12, "message_on_trigger": "跌破强弱线", "action_on_trigger": "卖出"},
        ],
    }

    result = normalize_watchboard_payload(payload, fallback_summary="fallback")

    assert result["card_summary"] == "小级别向上离开，冲击日线中枢上沿"
    assert result["card_action"] == "继续持有"
    assert result["monitor_conditions"]["triggers"][1]["action_on_trigger"] == "考虑减仓"


def test_normalize_watchboard_payload_compacts_card_action():
    result = normalize_watchboard_payload(
        {
            "card_summary": "冲击日线中枢上沿40.77，短线动能减弱",
            "card_action": "持仓观望，关注40.77",
            "triggers": [],
        },
        fallback_summary="fallback",
    )

    assert result["card_action"] == "持仓观望"


def test_normalize_watchboard_payload_keeps_watch_plan_and_derives_triggers():
    payload = {
        "watch_plan": {
            "main_task": "日线回拉后等待5分钟三买确认",
            "card": {
                "summary": "4.33压力，观察5分三买",
                "action": "持仓观望",
            },
            "key_levels": [
                {
                    "price": 4.33,
                    "side": "up",
                    "type": "pressure",
                    "shape_to_watch": "站上后回踩不破",
                    "meaning": "反弹压力位",
                    "trigger": "price_above",
                    "ai_review_when": "触及后5分钟动能增强或衰减",
                },
                {
                    "price": 4.18,
                    "side": "down",
                    "type": "support",
                    "shape_to_watch": "跌破后反抽回不去",
                    "meaning": "盘中承接位",
                    "trigger": "price_below",
                },
            ],
            "t_plan": {
                "enabled": True,
                "condition": "有底仓且冲到压力区，1/5分钟动能衰减",
                "watch_price": 4.33,
                "reentry_area": "4.18-4.22",
                "risk": "直接放量突破容易卖飞",
            },
            "recheck_policy": {
                "no_touch": "不重推",
                "near_key_level": "卡片轻量提示",
                "touched_with_momentum_change": "触发AI复核",
            },
        }
    }

    result = normalize_watchboard_payload(payload, fallback_summary="fallback")

    assert result["card_summary"] == "4.33压力，观察5分三买"
    assert result["card_action"] == "持仓观望"
    assert result["watch_plan"]["main_task"] == "日线回拉后等待5分钟三买确认"
    assert result["watch_plan"]["key_levels"][0]["price"] == 4.33
    assert result["watch_plan"]["key_levels"][0]["trigger"] == "price_above"
    assert result["watch_plan"]["t_plan"]["enabled"] is True
    assert result["monitor_conditions"]["triggers"] == [
        {
            "id": "t1",
            "type": "price_above",
            "level": 4.33,
            "message_on_trigger": "站上后回踩不破",
            "action_on_trigger": "关注",
        },
        {
            "id": "t2",
            "type": "price_below",
            "level": 4.18,
            "message_on_trigger": "跌破后反抽回不去",
            "action_on_trigger": "关注",
        },
    ]


def test_normalize_watchboard_payload_builds_watch_plan_from_legacy_triggers():
    result = normalize_watchboard_payload(
        {
            "card_summary": "196压力，冲高动能衰减",
            "card_action": "考虑做T",
            "triggers": [
                {"type": "price_above", "level": 196.0, "message_on_trigger": "测试196压力", "action_on_trigger": "关注"}
            ],
        },
        fallback_summary="fallback",
    )

    assert result["watch_plan"]["card"]["summary"] == "196压力，冲高动能衰减"
    assert result["watch_plan"]["card"]["action"] == "考虑做T"
    assert result["watch_plan"]["key_levels"][0]["price"] == 196.0
    assert result["watch_plan"]["key_levels"][0]["type"] == "pressure"
