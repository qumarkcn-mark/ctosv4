"""Holding management rules for Radar."""

from server.engines.decision.target_planner import (
    find_structural_high_s1,
    plan_holding_targets,
)


def compute_holding_status(
    day: dict,
    m30: dict,
    holding,
    forward_a: dict,
    m30_bis: list = None,
    strategy_type: str = "战法一",
) -> dict:
    """Compute the holding stage and risk references.

    这是持仓管理状态机，只输出教练计划字段，不执行交易。
    """
    import datetime as _dt

    if m30_bis is None:
        m30_bis = m30.get("detail_bis", [])

    day_patterns = " ".join(day.get("patterns", []))
    m30_patterns = " ".join(m30.get("patterns", []))
    current_price = day.get("price", 0.0)
    day_zg = day.get("zg", 0.0)
    m30_zg = m30.get("zg", 0.0)

    if not holding or not (holding.get("cost", 0) > 0 and holding.get("qty", 0) > 0):
        return _empty_status(strategy_type)

    cost = holding["cost"]
    persisted_stop = holding.get("trailing_stop_price") or 0.0

    computed_stop = 0.0
    if forward_a:
        for fc in forward_a.get("forward_classes") or []:
            sl = fc.get("stop_loss") or fc.get("stopLoss")
            if sl and float(sl) > 0:
                computed_stop = float(sl)
                break
    if computed_stop == 0.0:
        computed_stop = round(m30_zg, 2) if m30_zg > 0 else (round(day_zg, 2) if day_zg > 0 else 0.0)
    stair_stop = round(max(computed_stop, persisted_stop), 2)

    top_diverge_30min = any(kw in m30_patterns for kw in ("顶背驰", "1卖"))
    top_diverge_day = any(kw in day_patterns for kw in ("顶背驰", "1卖"))
    second_sell_day = "二卖" in day_patterns
    third_sell_day = "三卖" in day_patterns
    broken_stop = current_price > 0 and stair_stop > 0 and current_price <= stair_stop

    m30_beichi_type = m30.get("latest_top_beichi_type", "")
    if not m30_beichi_type and top_diverge_30min:
        m30_beichi_type = "中继" if "中继" in m30_patterns else "转折"

    stop_distance = (cost - stair_stop) if stair_stop > 0 and stair_stop < cost else (cost * 0.03)
    profit_amount = (current_price - cost) if current_price > 0 else 0.0
    profit_multiple = (profit_amount / stop_distance) if stop_distance > 0 else 0.0
    locked_profit_pct = round(profit_amount / cost * 100, 2) if cost > 0 else 0.0

    m30_bi_direction = "未形成"
    m30_bi_complete = False
    if m30_bis:
        last_bi = m30_bis[-1]
        if last_bi.get("is_sure") or last_bi.get("isSure"):
            m30_bi_direction = "向上" if (last_bi.get("is_up") or last_bi.get("isUp")) else "向下"
            m30_bi_complete = True
        else:
            m30_bi_direction = "向上（未确认）" if (last_bi.get("is_up") or last_bi.get("isUp")) else "向下"

    bars_since_entry = 0
    entry_date_str = holding.get("entry_date")
    if entry_date_str:
        try:
            bars_since_entry = max(0, (_dt.date.today() - _dt.date.fromisoformat(entry_date_str)).days) * 8
        except ValueError:
            pass
    validation_bars = 10
    bars_remaining = max(0, validation_bars - bars_since_entry)

    if m30_bi_complete and m30_bi_direction == "向上":
        val_status = "验证通过"
    elif m30_bi_complete and m30_bi_direction == "向下":
        val_status = "预案失效"
    elif bars_since_entry >= validation_bars:
        val_status = "时间失效"
    else:
        val_status = "验证中"

    validation = {
        "m30_bi_direction": m30_bi_direction,
        "m30_bi_complete": m30_bi_complete,
        "bars_since_entry": bars_since_entry,
        "bars_remaining": bars_remaining,
        "status": val_status,
    }

    is_s2 = strategy_type == "战法二"
    if third_sell_day or broken_stop or top_diverge_day:
        stage = 5
    elif is_s2:
        if top_diverge_30min and m30_beichi_type == "转折":
            stage = 4
        elif second_sell_day:
            stage = 4
        elif profit_multiple >= 3.0:
            stage = 3
        elif profit_multiple >= 1.0:
            stage = 2
        elif m30_bi_complete and m30_bi_direction == "向上":
            stage = 1
        else:
            stage = 0
    else:
        if top_diverge_30min or second_sell_day:
            stage = 4
        elif profit_multiple >= 2.0:
            stage = 3
        elif profit_multiple >= 1.0:
            stage = 2
        elif m30_bi_complete and m30_bi_direction == "向上":
            stage = 1
        else:
            stage = 0

    label, action = _stage_meta(is_s2).get(stage, (str(stage), ""))

    m30_relay_note = ""
    if is_s2 and top_diverge_30min and m30_beichi_type == "中继" and stage < 4:
        m30_relay_note = "30分出现中继背驰，次级别震荡，结构仍有效，继续持有"

    targets = plan_holding_targets(day, current_price, strategy_type)

    return {
        "stage": stage,
        "label": label,
        "strategy_type": strategy_type,
        "stair_stop_price": stair_stop,
        "locked_profit_pct": locked_profit_pct,
        "top_diverge_30min": top_diverge_30min,
        "top_diverge_30min_type": m30_beichi_type,
        "top_diverge_day": top_diverge_day,
        "m30_relay_note": m30_relay_note,
        "action": action,
        **targets,
        "validation": validation,
    }


def _empty_status(strategy_type: str) -> dict:
    return {
        "stage": "empty",
        "label": "空仓",
        "strategy_type": strategy_type,
        "stair_stop_price": 0.0,
        "locked_profit_pct": 0.0,
        "top_diverge_30min": False,
        "top_diverge_30min_type": "",
        "top_diverge_day": False,
        "m30_relay_note": "",
        "action": "",
        "target_price_1": 0.0,
        "target_price_2": 0.0,
        "target_is_placeholder": True,
        "target_open": False,
        "target_label": "",
        "target_1_reached": False,
        "target_2_reached": False,
        "validation": {
            "m30_bi_direction": "未形成",
            "m30_bi_complete": False,
            "bars_since_entry": 0,
            "bars_remaining": 10,
            "status": "空仓",
        },
    }


def _stage_meta(is_strategy_two: bool) -> dict:
    if is_strategy_two:
        return {
            0: ("走势验证期", "持续观察30分走势，确认突破有效"),
            1: ("验证期", "30分上涨笔已确认，趋势启动中，持仓"),
            2: ("保本期", "浮盈≥1倍止损距，止损上移至成本附近"),
            3: ("利润保护期", "浮盈≥3倍止损距，台阶止损跟踪，等待日线信号"),
            4: ("次级减速", "⚠️ 30分转折型背驰，建议减仓50%，剩余等待日线顶背驰。仅供参考"),
            5: ("趋势终结", "🔴 日线顶背驰/三卖确认，建议清仓。仅供参考"),
        }
    return {
        0: ("走势验证期", "持续观察30分走势，尚未确认上涨笔"),
        1: ("验证期", "30分上涨笔已确认，持仓运行"),
        2: ("保本期", "浮盈≥1倍止损距，止损上移至成本附近"),
        3: ("利润保护期", "浮盈≥2倍止损距，台阶止损跟踪中枢ZG"),
        4: ("减速预警", "⚠️ 30分顶背驰/卖点，建议减仓50%观察。仅供参考"),
        5: ("趋势终结", "🔴 日线顶背驰/三卖确认或止损触穿，建议清仓。仅供参考"),
    }
