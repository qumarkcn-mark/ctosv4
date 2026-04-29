"""Level-chain deduction rules for empty-position Radar.

本模块只消费 chan_adapter 输出的结构事实，把 day -> 30 -> 5 组织成
可展示的推演路径。这里不查库、不调用行情、不调用 AI、不发送提醒。
"""

from typing import Optional


DEDUCTION_VERSION = "level_chain_deduction.v1"
DISCLAIMER = "仅供参考，不构成投资建议"

BUY_BSP_TYPES = ("1", "1p", "2", "2s", "3a", "3b")
BSP_TYPE_META = {
    "1": {
        "family": "FIRST_BUY",
        "code": "B1",
        "name": "一买",
        "source": "CChan BSP_TYPE.T1",
    },
    "1p": {
        "family": "FIRST_BUY",
        "code": "B1P",
        "name": "类一买",
        "source": "CChan BSP_TYPE.T1P",
    },
    "2": {
        "family": "SECOND_BUY",
        "code": "B2",
        "name": "二买",
        "source": "CChan BSP_TYPE.T2",
    },
    "2s": {
        "family": "SECOND_BUY",
        "code": "B2S",
        "name": "类二买",
        "source": "CChan BSP_TYPE.T2S",
    },
    "3a": {
        "family": "THIRD_BUY",
        "code": "B3A",
        "name": "三买A",
        "source": "CChan BSP_TYPE.T3A",
    },
    "3b": {
        "family": "THIRD_BUY",
        "code": "B3B",
        "name": "三买B",
        "source": "CChan BSP_TYPE.T3B",
    },
}
BUY_WORDS = ("一买", "二买", "三买", "类一买", "类二买", "类三买", "底背驰")
SELL_WORDS = ("顶背驰", "1卖", "一卖", "二卖", "三卖", "三卖确认")
UP_STATES = ("UPWARD_LEAVING", "THIRD_BUY_CONFIRMED")


def build_level_chain_deduction(
    levels: dict,
    freshness: Optional[dict] = None,
    mode: str = "EMPTY",
    disclaimer: str = DISCLAIMER,
) -> Optional[dict]:
    """Build the first Radar deduction contract for empty-position workflows."""
    if mode != "EMPTY":
        return None

    freshness = freshness or {}
    day = _level(levels, "day")
    m30 = _level(levels, "30")
    m5 = _level(levels, "5")

    if _is_stale(freshness):
        return _result(
            "STALE",
            "STALE",
            "正式结构数据过期，当前只保留历史参考",
            day,
            m30,
            m5,
            disclaimer,
            current_step="等待数据更新",
            next_if=["等待 BaoStock 正式结构刷新"],
            invalid_if=[],
        )

    day_state = _day_role(day)
    m30_state = _m30_role(m30)
    m5_state = _m5_role(m5)
    invalid_boundary = _invalidation_boundary(m30, m5)
    current_price = _price(m5) or _price(m30) or _price(day)

    if day_state["state"] == "UNSAFE":
        return _result(
            "NO_SETUP",
            "LOW",
            "日线结构不支持本轮空仓推演",
            day,
            m30,
            m5,
            disclaimer,
            current_step="放弃本轮观察",
            next_if=["等待日线重新形成买方结构"],
            invalid_if=day_state["invalid_if"],
            day_state=day_state,
            m30_state=m30_state,
            m5_state=m5_state,
        )

    if day_state["state"] != "VALID":
        return _result(
            "NO_SETUP",
            "LOW",
            "日线尚未形成可观察结构",
            day,
            m30,
            m5,
            disclaimer,
            current_step="等待日线结构",
            next_if=["等待日线买点、底背驰或向上离开中枢"],
            invalid_if=day_state["invalid_if"],
            day_state=day_state,
            m30_state=m30_state,
            m5_state=m5_state,
        )

    if _breaks_boundary(current_price, invalid_boundary):
        return _result(
            "FAILED",
            "LOW",
            "价格跌破关键结构边界，当前推演失败",
            day,
            m30,
            m5,
            disclaimer,
            current_step="推演失效",
            next_if=["等待重新站回结构边界后再观察"],
            invalid_if=[_boundary_text(invalid_boundary)],
            day_state=day_state,
            m30_state={**m30_state, "state": "CONFLICTING"},
            m5_state=m5_state,
        )

    if m30_state["state"] == "CONFLICTING":
        return _result(
            "FAILED",
            "LOW",
            "30分结构与日线机会冲突，当前推演失败",
            day,
            m30,
            m5,
            disclaimer,
            current_step="等待30分重新确认",
            next_if=["等待30分重新形成买方结构"],
            invalid_if=m30_state["invalid_if"],
            day_state=day_state,
            m30_state=m30_state,
            m5_state=m5_state,
        )

    if m30_state["state"] != "SUPPORTIVE":
        return _result(
            "WAITING_CONFIRMATION",
            "PREVIEW",
            "日线机会存在，等待30分结构确认",
            day,
            m30,
            m5,
            disclaimer,
            current_step="等待30分确认",
            next_if=["等待30分买点、底背驰或回踩不破关键中枢"],
            invalid_if=day_state["invalid_if"] + m30_state["invalid_if"],
            day_state=day_state,
            m30_state=m30_state,
            m5_state=m5_state,
        )

    current_bsp = _current_buy_bsp(m5)
    if current_bsp:
        candidate = _buy_point_candidate(
            current_bsp,
            "CONFIRMED",
            m5,
            m30,
            invalid_boundary,
        )
        return _result(
            "TRIGGER_CONFIRMED",
            "CONFIRMED",
            "日线和30分支持观察，5分买点已确认",
            day,
            m30,
            m5,
            disclaimer,
            current_step=f"{candidate['label']}，来源 {candidate['bsp']['display']}",
            next_if=["执行前复核止损距离、盈亏比和账户风险"],
            invalid_if=candidate["invalid_if"],
            day_state=day_state,
            m30_state=m30_state,
            m5_state={**m5_state, "state": "CONFIRMED"},
            buy_point_candidates=[candidate],
        )

    if _is_5m_forming(m5, invalid_boundary):
        candidate = _forming_candidate(m5, m30, invalid_boundary)
        active_boundary = candidate.get("active_boundary") or invalid_boundary
        return _result(
            "TRIGGER_FORMING",
            "PREVIEW",
            "日线和30分支持观察，5分买点正在形成",
            day,
            m30,
            m5,
            disclaimer,
            current_step="等待5分买点确认",
            next_if=candidate["trigger_if"],
            invalid_if=candidate["invalid_if"],
            day_state=day_state,
            m30_state=m30_state,
            m5_state={**m5_state, "state": "FORMING"},
            buy_point_candidates=[candidate],
            boundary_override=active_boundary,
        )

    return _result(
        "WAITING_TRIGGER",
        "PREVIEW",
        "日线机会有效，30分回踩验证，等待5分买点形成",
        day,
        m30,
        m5,
        disclaimer,
        current_step="5分买点未确认",
        next_if=["等待5分一买、二买或三买结构进入最新窗口"],
        invalid_if=day_state["invalid_if"] + m30_state["invalid_if"],
        day_state=day_state,
        m30_state=m30_state,
        m5_state=m5_state,
    )


def _level(levels: dict, public_level: str) -> dict:
    if public_level == "day":
        return levels.get("day") or {}
    return levels.get(public_level) or levels.get(f"m{public_level}") or {}


def _is_stale(freshness: dict) -> bool:
    if freshness.get("is_stale"):
        return True
    level_freshness = freshness.get("levels") or {}
    for key in ("day", "30", "5", "m30", "m5"):
        item = level_freshness.get(key)
        if item and item.get("is_stale"):
            return True
    return False


def _day_role(day: dict) -> dict:
    invalid_if = ["日线回到原中枢内部", "日线出现卖点风险"]
    if _is_extreme_main_wave(day) and not _top_divergence(day) and _state(day) != "THIRD_SELL_CONFIRMED":
        return _role(
            "setup",
            "VALID",
            "日线主升浪延伸，高位卖点仅作风险提示",
            invalid_if=invalid_if,
        )
    if _has_current_sell_risk(day):
        return _role("setup", "UNSAFE", "日线出现卖点或顶背驰风险", invalid_if=invalid_if)
    if _has_buy_fact(day) or _state(day) in UP_STATES or _bottom_divergence(day):
        return _role("setup", "VALID", "日线有可观察结构", invalid_if=invalid_if)
    return _role("setup", "NEUTRAL", "日线结构尚未确认", invalid_if=invalid_if)


def _m30_role(m30: dict) -> dict:
    invalid_if = ["30分跌破关键ZD"]
    if _top_divergence(m30) or _state(m30) == "THIRD_SELL_CONFIRMED":
        return _role("confirmation", "CONFLICTING", "30分出现顶背驰或三卖风险", invalid_if=invalid_if)
    if _state(m30) in UP_STATES and _price_above_support(m30):
        if _has_current_sell_bsp_risk(m30):
            return _role(
                "confirmation",
                "SUPPORTIVE",
                "30分三买后回落，等待5分止跌",
                invalid_if=invalid_if,
                next_if=["等待5分回落止住并重新转强"],
            )
        return _role("confirmation", "SUPPORTIVE", "30分结构支持观察", invalid_if=invalid_if)
    if _has_current_sell_bsp_risk(m30):
        return _role(
            "confirmation",
            "NEUTRAL",
            "30分卖点后等待重新确认",
            invalid_if=invalid_if,
            next_if=["等待30分重新形成买点或回落不破后转强"],
        )
    if not m30.get("bsps") and _has_sell_risk(_pattern_text(m30)):
        return _role(
            "confirmation",
            "NEUTRAL",
            "30分卖点风险待确认",
            invalid_if=invalid_if,
            next_if=["等待30分结构重新转强"],
        )
    if _has_buy_fact(m30) or _price_above_support(m30):
        return _role("confirmation", "SUPPORTIVE", "30分结构支持观察", invalid_if=invalid_if)
    return _role("confirmation", "NEUTRAL", "30分尚未确认", invalid_if=invalid_if)


def _m5_role(m5: dict) -> dict:
    if _current_buy_bsp(m5):
        return _role("trigger", "CONFIRMED", "5分买点确认")
    if m5:
        return _role("trigger", "WAITING", "5分买点未确认", next_if=["出现5分一买、二买或三买结构"])
    return _role("trigger", "STALE", "5分结构缺失")


def _role(role: str, state: str, summary: str, invalid_if=None, next_if=None) -> dict:
    return {
        "role": role,
        "state": state,
        "summary": summary,
        "invalid_if": invalid_if or [],
        "next_if": next_if or [],
    }


def _result(
    status: str,
    confidence: str,
    summary: str,
    day: dict,
    m30: dict,
    m5: dict,
    disclaimer: str,
    current_step: str,
    next_if: list,
    invalid_if: list,
    day_state: Optional[dict] = None,
    m30_state: Optional[dict] = None,
    m5_state: Optional[dict] = None,
    buy_point_candidates: Optional[list] = None,
    boundary_override: Optional[dict] = None,
) -> dict:
    buy_point_candidates = buy_point_candidates or []
    roles = {
        "day": day_state or _day_role(day),
        "30": m30_state or _m30_role(m30),
        "5": m5_state or _m5_role(m5),
    }
    boundary = boundary_override or _invalidation_boundary(m30, m5)
    current_price = _price(m5) or _price(m30) or _price(day)
    div_context = _divergence_context(day, m30, m5, status)
    invalid_if = _dedupe(invalid_if)
    return {
        "version": DEDUCTION_VERSION,
        "mode": "EMPTY",
        "chain": ["day", "30", "5"],
        "status": status,
        "confidence": confidence,
        "summary": summary,
        "main_path": {
            "id": "day_30_wait_5_buy",
            "label": "day -> 30 -> 5 空仓推演",
            "current_step": current_step,
            "next_if": next_if,
            "invalid_if": invalid_if,
        },
        "path_thesis": _path_thesis(status, summary, current_step, roles, boundary, div_context, current_price),
        "coach_deduction": _coach_deduction(
            day,
            m30,
            m5,
            status,
            roles,
            boundary,
            buy_point_candidates,
            div_context,
        ),
        "complete_classification": _complete_classification(
            status,
            next_if,
            invalid_if,
            buy_point_candidates,
            roles,
            boundary,
            div_context,
        ),
        "buy_point_candidates": buy_point_candidates,
        "level_roles": roles,
        "evidence": {
            "day": _evidence(day),
            "30": _evidence(m30),
            "5": _evidence(m5),
        },
        "next_if": next_if,
        "invalid_if": invalid_if,
        "divergence_context": div_context,
        "disclaimer": disclaimer,
    }


def _coach_deduction(
    day: dict,
    m30: dict,
    m5: dict,
    status: str,
    roles: dict,
    boundary: dict,
    buy_point_candidates: list,
    div_context: dict,
) -> dict:
    current_price = _price(m5) or _price(m30) or _price(day)
    day_zs = _zs_range_text(day)
    m30_zs = _zs_range_text(m30)
    m5_zs = _zs_range_text(m5)
    active_boundary = _boundary_label(boundary)
    hard_boundary = _fallback_hard_boundary(m30, boundary)
    strong_extension = (
        status in ("TRIGGER_FORMING", "TRIGGER_CONFIRMED", "WAITING_TRIGGER")
        and roles.get("day", {}).get("state") == "VALID"
        and roles.get("30", {}).get("state") == "SUPPORTIVE"
        and _state(day) == "UPWARD_LEAVING"
        and _price_above_resistance(day)
        and _price_above_resistance(m30)
        and current_price > 0
    )
    high_volatility = strong_extension and _is_high_volatility_micro(m5)
    downward_pressure = _is_downward_pressure(day, m30, m5)
    forming_type = ""
    if buy_point_candidates:
        forming_type = buy_point_candidates[0].get("type") or ""

    if downward_pressure:
        macro_title = "向下离开防守"
        diagnosis = (
            "当前宏观、中观、微观多个级别处于向下离开或跌破中枢状态。"
            "推演原则是先规避左侧接飞刀，等待5分底背驰、一买/二买或重新站回压力区后再讨论修复。"
        )
        macro_text = "日线级别深度回调 / 结构破位"
        mid_text = "30分钟向下延伸，空头势能释放中"
        micro_text = "5分钟向下离开段，尚未见明确底背驰确认"
        boundary_text = _downward_pressure_text(m30, m5, boundary)
    elif high_volatility:
        macro_title = "高位剧烈震荡"
        diagnosis = (
            "当前大级别仍处于主升浪延伸，但短级别已经出现冲高回落和卖点压力。"
            "推演原则不是猜顶，而是把走势归入震荡、续涨、防守三条路径，用5分边界机械管理。"
        )
        macro_text = "超长期主升浪延伸，乖离率极大"
        mid_text = "30分钟向上离开段，面临高位剧震"
        micro_text = "5分钟冲高回落，正在构建高位新中枢或确认顶背驰"
        boundary_text = f"第一防线 {active_boundary}；极限防线 {hard_boundary}。"
    elif strong_extension:
        macro_title = "强势上涨主升浪"
        diagnosis = (
            "当前结构处于向上离开段延伸，属于脱离中枢后的趋势推进。"
            "推演原则是跟随趋势，不主观预测顶部；防守线随短级别结构上移。"
        )
        macro_text = macro_title
        mid_text = roles.get("30", {}).get("summary", "等待30分确认")
        micro_text = "5分向上延伸后的回落验证"
        if forming_type == "THIRD_BUY":
            micro_text = "5分三买回试形成中，观察是否守住上一中枢上沿"
        boundary_text = f"第一防线 {active_boundary}；极限防线 {hard_boundary}。"
    else:
        macro_title = "级别递进观察"
        diagnosis = "当前按日线机会、30分确认、5分触发的递进关系管理。"
        macro_text = macro_title
        mid_text = roles.get("30", {}).get("summary", "等待30分确认")
        micro_text = roles.get("5", {}).get("summary", "等待5分结构")
        boundary_text = f"第一防线 {active_boundary}；极限防线 {hard_boundary}。"

    return {
        "version": "coach_deduction.v1",
        "title": macro_title,
        "diagnosis": diagnosis,
        "data_slice": [
            {
                "label": "最新收盘价",
                "value": f"{current_price:.2f}" if current_price > 0 else "—",
                "meaning": _price_position_text(current_price, day, m30, m5),
            },
            {
                "label": "日线级别",
                "value": day_zs,
                "meaning": _level_slice_meaning("day", day),
            },
            {
                "label": "30分钟级别",
                "value": m30_zs,
                "meaning": _level_slice_meaning("30", m30),
            },
            {
                "label": "5分钟级别",
                "value": m5_zs,
                "meaning": _level_slice_meaning("5", m5),
            },
        ],
        "windows": [
            {"code": "D", "label": "宏观定性", "text": macro_text},
            {"code": "C", "label": "中观确认", "text": mid_text},
            {"code": "A", "label": "微观触媒", "text": micro_text},
            {"code": "B", "label": "防守底线", "text": boundary_text},
        ],
        "scenario_plans": _coach_scenarios(
            strong_extension,
            current_price,
            boundary,
            hard_boundary,
            div_context,
            high_volatility=high_volatility,
            downward_pressure=downward_pressure,
            day=day,
            m30=m30,
            m5=m5,
        ),
    }


def _coach_scenarios(
    strong_extension: bool,
    current_price: float,
    boundary: dict,
    hard_boundary: str,
    div_context: dict,
    high_volatility: bool = False,
    downward_pressure: bool = False,
    day: Optional[dict] = None,
    m30: Optional[dict] = None,
    m5: Optional[dict] = None,
) -> list[dict]:
    if downward_pressure:
        return _downward_scenarios(current_price, day or {}, m30 or {}, m5 or {})

    active_boundary = _boundary_label(boundary)
    active_text = _boundary_price_text(boundary)
    upper = current_price * 1.10 if current_price > 0 else 0
    lower = current_price * 0.88 if current_price > 0 else 0
    scenario_prefix = "趋势延伸" if strong_extension else "路径确认"
    optimistic = {
            "id": "right_side_major_wave",
            "label": "乐观场景",
            "weight_pct": 30 if high_volatility else 60,
            "title": f"{scenario_prefix}不背驰",
            "shape": "短级别离开段继续延伸，未出现明确顶背驰或结构破坏。",
            "coach_tip": "续涨观察；不因主观猜顶提前否定趋势，跟随移动防守线管理。",
            "target": f"{current_price:.2f} — {upper:.2f}" if upper > 0 else "等待结构给出目标区",
            "period": "3 ~ 5 个交易日",
        }
    oscillation = {
            "id": "zhongshu_oscillation",
            "label": "震荡场景",
            "weight_pct": 50 if high_volatility else 30,
            "title": "高位构建新中枢",
            "shape": f"短期情绪释放后回落，但不跌破 {active_text}，在高位横向震荡并尝试构建新中枢。",
            "coach_tip": "观察三买或回踩不破；若放量滞涨，先降低动作冲动，等待结构确认。",
            "target": f"{max(_num(boundary.get('value')), lower):.2f} — {current_price:.2f}" if current_price > 0 else "等待区间形成",
            "period": "10 ~ 20 根30分钟K线",
        }
    defensive = {
            "id": "structural_breakdown",
            "label": "防守场景",
            "weight_pct": 20 if high_volatility else 10,
            "title": "顶背驰转折 / 结构破坏",
            "shape": f"出现顶背驰、二卖/三卖，或直接跌破 {active_text}，推演切换为 C 失效。",
            "coach_tip": "执行止损看门狗；跌破第一防线先降风险，跌回极限防线则放弃本轮推演。",
            "target": f"跌破 {active_text} -> 跌破 {hard_boundary}",
            "period": "1 ~ 2 个交易日内分出胜负",
        }
    if high_volatility:
        return [oscillation, optimistic, defensive]
    return [optimistic, oscillation, defensive]


def _downward_scenarios(current_price: float, day: dict, m30: dict, m5: dict) -> list[dict]:
    m5_zd = _num(m5.get("zd") or m5.get("zs_operative_zd"))
    m5_zg = _num(m5.get("zg") or m5.get("zs_operative_zg"))
    m30_zd = _num(m30.get("zd") or m30.get("zs_operative_zd"))
    lower_probe = current_price * 0.88 if current_price > 0 else 0
    first_pressure = _price_or_text(m5_zd, "5分上一中枢下沿")
    repair_confirm = _price_or_text(m5_zg, "5分上一中枢上沿")
    strong_pressure = _price_or_text(m30_zd, "30分上一中枢下沿")
    bottom_zone = (
        f"{max(current_price * 0.98, 0):.2f} — {m5_zd:.2f}"
        if current_price > 0 and m5_zd > 0
        else "等待5分底部中枢成形"
    )
    return [
        {
            "id": "structural_breakdown",
            "label": "防守场景",
            "weight_pct": 50,
            "title": "单边下跌延伸",
            "shape": (
                "多级别仍处于向下离开或跌破中枢后的延伸，尚未出现明确底背驰、放量止跌或5分买点确认。"
            ),
            "coach_tip": "空仓观望；严禁把下跌幅度当作买入理由，反弹不过压力区时按下跌中继处理。",
            "target": f"继续寻找下方支撑，先看 {lower_probe:.2f} 附近" if lower_probe > 0 else "等待下方支撑显形",
            "period": "1 ~ 3 个交易日",
        },
        {
            "id": "zhongshu_oscillation",
            "label": "震荡场景",
            "weight_pct": 30,
            "title": "底背驰转折 / 构建底部中枢",
            "shape": f"5分钟下跌段力度衰竭，出现底背驰或一买，随后在 {bottom_zone} 区间尝试构建底部中枢。",
            "coach_tip": "只允许极小仓试探；若反弹无法进入5分压力区，试探仓必须按失败处理。",
            "target": f"{bottom_zone}，上方先看 {first_pressure}",
            "period": "10 ~ 20 根5分钟K线",
        },
        {
            "id": "right_side_major_wave",
            "label": "乐观场景",
            "weight_pct": 20,
            "title": "V型反转 / 小转大",
            "shape": f"价格快速收复 {repair_confirm}，再向 {strong_pressure} 推进，短级别由下跌离开转为修复。",
            "coach_tip": "放弃第一波左侧利润，等待回踩不破后的5分二买或重新站回压力区再右侧确认。",
            "target": f"站回 {repair_confirm} -> 挑战 {strong_pressure}",
            "period": "3 ~ 5 个交易日",
        },
    ]


def _zs_range_text(level: dict) -> str:
    zd = _num(level.get("zd") or level.get("zs_operative_zd"))
    zg = _num(level.get("zg") or level.get("zs_operative_zg"))
    if zd > 0 and zg > 0:
        return f"[{zd:g} - {zg:g}]"
    return "暂无可靠中枢区间"


def _level_slice_meaning(level_key: str, level: dict) -> str:
    state = _state(level)
    if state == "UPWARD_LEAVING":
        return "价格已向上脱离当前中枢，处于离开段延伸。"
    if state == "THIRD_BUY_CONFIRMED":
        return "三买结构已确认，当前重点观察回试是否守住。"
    if state == "DOWNWARD_LEAVING":
        return "短级别向下离开，优先观察是否止跌或跌破防线。"
    if _price_above_resistance(level):
        return "价格运行在中枢上沿之上，结构偏强。"
    return "结构仍需等待新的方向确认。"


def _price_position_text(current_price: float, day: dict, m30: dict, m5: dict) -> str:
    if current_price <= 0:
        return "等待价格数据"
    if _is_downward_pressure(day, m30, m5):
        return "价格处于多级别中枢下方或向下离开段，优先规避左侧接飞刀。"
    if _price_above_resistance(day) and _price_above_resistance(m30):
        return "价格处于多级别中枢上方，偏强势离开段。"
    if _price_above_resistance(m5):
        return "价格在短级别中枢上方，观察能否延续。"
    return "价格仍在结构边界附近，优先看防线。"


def _price_above_resistance(level: dict) -> bool:
    price = _price(level)
    zg = _num(level.get("zg") or level.get("zs_operative_zg"))
    return price > 0 and zg > 0 and price > zg


def _is_extreme_main_wave(level: dict) -> bool:
    price = _price(level)
    zg = _num(level.get("zg") or level.get("zs_operative_zg"))
    return (
        _state(level) == "UPWARD_LEAVING"
        and price > 0
        and zg > 0
        and price >= zg * 1.5
        and _has_buy_fact(level)
    )


def _is_high_volatility_micro(level: dict) -> bool:
    patterns = _pattern_text(level)
    return (
        _state(level) == "IN_CENTER_OSC"
        and _price_above_resistance(level)
        and str(level.get("last_bi_dir") or "").lower() in ("down", "向下", "向下（未确认）")
        and _has_sell_risk(patterns)
    )


def _is_downward_pressure(day: dict, m30: dict, m5: dict) -> bool:
    levels = (day, m30, m5)
    downward_states = sum(1 for level in levels if _state(level) == "DOWNWARD_LEAVING")
    below_centers = sum(1 for level in levels if _price_below_support(level))
    return downward_states >= 2 or (downward_states >= 1 and below_centers >= 2)


def _price_below_support(level: dict) -> bool:
    price = _price(level)
    zd = _num(level.get("zd") or level.get("zs_operative_zd"))
    return price > 0 and zd > 0 and price < zd


def _downward_pressure_text(m30: dict, m5: dict, boundary: dict) -> str:
    m5_zd = _num(m5.get("zd") or m5.get("zs_operative_zd"))
    m30_zd = _num(m30.get("zd") or m30.get("zs_operative_zd"))
    first = _price_or_text(m5_zd, _boundary_label(boundary))
    strong = _price_or_text(m30_zd, "30分上一中枢下沿")
    return f"上方第一压力位 {first}；强压区 {strong}。"


def _price_or_text(value: float, fallback: str) -> str:
    return f"{value:g}" if value > 0 else fallback


def _fallback_hard_boundary(m30: dict, active_boundary: dict) -> str:
    m30_zg = _num(m30.get("zg") or m30.get("zs_operative_zg"))
    if m30_zg > 0:
        return f"30分ZG {m30_zg:g}"
    return _boundary_label(active_boundary)


def _boundary_price_text(boundary: dict) -> str:
    value = _num(boundary.get("value"))
    return f"{value:g}" if value > 0 else "结构边界"


def _path_thesis(
    status: str,
    summary: str,
    current_step: str,
    roles: dict,
    boundary: dict,
    div_context: dict,
    current_price: float,
) -> dict:
    phase = {
        "STALE": "历史参考",
        "NO_SETUP": "无主推演",
        "WAITING_CONFIRMATION": "等待确认",
        "WAITING_TRIGGER": "等待触发",
        "TRIGGER_FORMING": "回落验证",
        "TRIGGER_CONFIRMED": "结构触发",
        "FAILED": "推演失效",
    }.get(status, "推演中")
    return {
        "title": summary,
        "phase": phase,
        "narrative": _path_narrative(status, current_step, roles, boundary, div_context, current_price),
        "boundaries": _path_boundaries(status, boundary, current_price),
        "divergence_summary": div_context.get("summary", ""),
        "level_meanings": [
            {"level": "day", "label": "日线", "meaning": roles.get("day", {}).get("summary", "")},
            {"level": "30", "label": "30分", "meaning": roles.get("30", {}).get("summary", "")},
            {"level": "5", "label": "5分", "meaning": roles.get("5", {}).get("summary", "")},
        ],
    }


def _path_narrative(
    status: str,
    current_step: str,
    roles: dict,
    boundary: dict,
    div_context: dict,
    current_price: float,
) -> str:
    if status == "TRIGGER_CONFIRMED":
        return f"{current_step}，走势已进入结构触发后的执行前复核阶段。"
    if status == "TRIGGER_FORMING":
        return "大级别仍支持观察，短级别正在回落验证，等待结构事件把路径归入 A 确认。"
    if status == "WAITING_TRIGGER":
        return "大级别路径未破坏，但5分还没有给出结构触发，当前按推演延长处理。"
    if status == "WAITING_CONFIRMATION":
        return "日线有可观察机会，但30分还没有完成支持，先等待次级别确认。"
    if status == "FAILED":
        if _breaks_boundary(current_price, boundary):
            return "关键结构边界已经被破坏，本轮主推演转入失效路径。"
        if roles.get("30", {}).get("state") == "CONFLICTING":
            return "30分出现反向风险，虽然防守边界尚未跌破，本轮主推演先转入失效路径。"
        return "结构条件冲突，本轮主推演转入失效路径。"
    if status == "STALE":
        return "正式结构数据过期，当前不能确认新的5分钟触发。"
    return current_step


def _path_boundaries(status: str, boundary: dict, current_price: float = 0) -> list[dict]:
    value = _num(boundary.get("value"))
    if value <= 0:
        return [{
            "role": "边界",
            "label": "结构边界缺失",
            "meaning": "缺少可靠边界时，不确认新的结构触发。",
            "price": None,
        }]
    boundary_label = _boundary_label(boundary)
    if status == "FAILED":
        if not _breaks_boundary(current_price, boundary):
            return [{
                "role": "防守线",
                "label": boundary_label,
                "price": value,
                "meaning": "尚未跌破；本次失效来自级别冲突或反向风险。",
            }]
        return [{
            "role": "失效",
            "label": boundary_label,
            "price": value,
            "meaning": "已跌破，主推演进入失效路径。",
        }]
    return [
        {
            "role": "维持",
            "label": boundary_label,
            "price": value,
            "meaning": "未跌破前，主推演仍按当前路径管理。",
        },
        {
            "role": "失效",
            "label": f"跌破 {boundary_label}",
            "price": value,
            "meaning": "跌破后，当前推演切换到 C 失效。",
        },
    ]


def _complete_classification(
    status: str,
    next_if: list,
    invalid_if: list,
    buy_point_candidates: list,
    roles: dict,
    boundary: dict,
    div_context: dict,
) -> list[dict]:
    candidate = buy_point_candidates[0] if buy_point_candidates else {}
    expected = candidate.get("bsp") or candidate.get("expected_bsp") or {}
    confirm_events = []
    if expected.get("display"):
        confirm_events.append(expected["display"])
    if candidate.get("trigger_if"):
        confirm_events.extend(candidate["trigger_if"])
    elif next_if:
        confirm_events.extend(next_if)
    confirm_events.extend(div_context.get("bullish_evidence") or [])

    return [
        {
            "id": "A_CONFIRM",
            "code": "A",
            "label": "确认",
            "state": _scenario_state(status, "A"),
            "title": "回落止住，路径转强",
            "summary": "短级别出现确认事件，主推演进入结构触发或执行前复核。",
            "trigger_if": _dedupe(confirm_events or ["5分出现新的买点确认事件"]),
            "evidence": div_context.get("bullish_evidence") or [],
            "level_meaning": {
                "day": roles.get("day", {}).get("summary", ""),
                "30": "30分支持继续观察",
                "5": "5分由回落验证转为结构触发",
            },
        },
        {
            "id": "B_EXTEND",
            "code": "B",
            "label": "延长",
            "state": _scenario_state(status, "B"),
            "title": "没有确认，也没有破坏",
            "summary": "走势继续震荡或等待，雷达保留当前推演但不进入执行前复核。",
            "trigger_if": [
                "5分没有新的确认买点",
                f"价格仍守住 {_boundary_label(boundary)}" if _num(boundary.get("value")) > 0 else "结构边界未被破坏",
                "30分没有新增卖点风险",
            ] + (div_context.get("neutral_evidence") or []),
            "evidence": div_context.get("neutral_evidence") or [],
            "level_meaning": {
                "day": roles.get("day", {}).get("summary", ""),
                "30": roles.get("30", {}).get("summary", ""),
                "5": "5分继续等待或震荡",
            },
        },
        {
            "id": "C_INVALID",
            "code": "C",
            "label": "失效",
            "state": _scenario_state(status, "C"),
            "title": "关键边界被破坏",
            "summary": "跌破结构边界或出现反向风险，本轮主推演作废。",
            "trigger_if": _dedupe((invalid_if or [_boundary_text(boundary)]) + (div_context.get("bearish_evidence") or [])),
            "evidence": div_context.get("bearish_evidence") or [],
            "level_meaning": {
                "day": "日线机会降级或重新等待",
                "30": "30分支持失效或转为冲突",
                "5": "5分下破，当前触发路径取消",
            },
        },
    ]


def _scenario_state(status: str, scenario: str) -> str:
    if scenario == "A" and status == "TRIGGER_CONFIRMED":
        return "CONFIRMED"
    if scenario == "A" and status == "TRIGGER_FORMING":
        return "FORMING"
    if scenario == "B" and status in ("WAITING_CONFIRMATION", "WAITING_TRIGGER", "NO_SETUP", "STALE"):
        return "CURRENT"
    if scenario == "C" and status == "FAILED":
        return "TRIGGERED"
    return "WATCH"


def _evidence(level: dict) -> dict:
    return {
        "level": level.get("level"),
        "state": level.get("state"),
        "price": level.get("price"),
        "zg": level.get("zg") or level.get("zs_operative_zg"),
        "zd": level.get("zd") or level.get("zs_operative_zd"),
        "last_bi_dir": level.get("last_bi_dir"),
        "patterns": level.get("patterns") or [],
        "div_info": level.get("div_info"),
    }


def _divergence_context(day: dict, m30: dict, m5: dict, status: str = "") -> dict:
    levels = (("day", "日线", day), ("30", "30分", m30), ("5", "5分", m5))
    bullish = []
    bearish = []
    neutral = []
    facts = []
    review_items = []
    for level_key, label, level in levels:
        fact = _divergence_fact(level_key, label, level)
        if not fact:
            neutral.append(f"{label}暂无明确背驰确认")
            review_items.append(_divergence_review_item(level_key, label, None, status))
            continue
        facts.append(fact)
        review_items.append(_divergence_review_item(level_key, label, fact))
        if fact["direction"] == "bottom":
            bullish.append(fact["text"])
        elif fact["direction"] == "top":
            bearish.append(fact["text"])

    if bearish:
        summary = "背驰状态：出现顶背驰/卖点风险，优先防 C 失效。"
    elif bullish:
        summary = "背驰状态：出现底背驰/力度衰减，支持观察 A 确认。"
    elif status == "FAILED":
        summary = "背驰状态：尚无明确背驰确认，本次失效不是由背驰触发。"
    else:
        summary = "背驰状态：尚无明确背驰确认，背驰暂不改变主推演。"

    return {
        "summary": summary,
        "facts": facts,
        "review_items": review_items,
        "bullish_evidence": bullish,
        "bearish_evidence": bearish,
        "neutral_evidence": neutral[:2],
    }


def _divergence_fact(level_key: str, label: str, level: dict) -> Optional[dict]:
    patterns = _pattern_text(level)
    div_info = level.get("div_info") or {}
    div_type = str(div_info.get("type") or "")
    if not div_type:
        if "趋势底背驰" in patterns or "底背驰" in patterns:
            div_type = "底背驰"
        elif "趋势顶背驰" in patterns:
            div_type = "顶背驰"
    if div_type not in ("底背驰", "顶背驰"):
        return None
    severity = div_info.get("severity") or div_info.get("classification") or ""
    direction = "bottom" if div_type == "底背驰" else "top"
    suffix = f"（{severity}）" if severity else ""
    return {
        "level": level_key,
        "label": label,
        "type": div_type,
        "direction": direction,
        "severity": severity,
        "text": f"{label}{div_type}{suffix}",
    }


def _divergence_review_item(level_key: str, label: str, fact: Optional[dict], status: str = "") -> dict:
    if not fact:
        return {
            "level": level_key,
            "label": label,
            "state": "NEUTRAL",
            "tone": "neutral",
            "title": "无明确背驰",
            "meaning": "本次失效不是由背驰触发。" if status == "FAILED" else "暂不改变主推演，以当前 A/B/C 状态为准。",
            "evidence": "",
        }
    if fact["direction"] == "bottom":
        return {
            "level": level_key,
            "label": label,
            "state": "SUPPORT_A",
            "tone": "bullish",
            "title": fact["type"],
            "meaning": "力度衰减支持 A 确认，但仍要等待结构触发。",
            "evidence": fact["text"],
        }
    return {
        "level": level_key,
        "label": label,
        "state": "RISK_C",
        "tone": "bearish",
        "title": fact["type"],
        "meaning": "反向力度衰减提示 C 失效风险，优先检查关键边界。",
        "evidence": fact["text"],
    }


def _pattern_text(level: dict) -> str:
    return " ".join(str(item) for item in (level.get("patterns") or []))


def _has_buy_fact(level: dict) -> bool:
    patterns = _pattern_text(level)
    if any(word in patterns for word in BUY_WORDS):
        return True
    return bool(_current_buy_bsp(level))


def _has_sell_risk(patterns: str) -> bool:
    return any(word in patterns for word in SELL_WORDS)


def _has_current_sell_risk(level: dict) -> bool:
    patterns = _pattern_text(level)
    if _top_divergence(level):
        return True
    if _state(level) == "THIRD_SELL_CONFIRMED":
        return True
    if _has_current_sell_bsp_risk(level):
        return True

    # 兼容单元测试和降级数据：没有结构化 BSP 时才回退到文字风险。
    if not level.get("bsps") and _has_sell_risk(patterns):
        return True
    return False


def _has_current_sell_bsp_risk(level: dict) -> bool:
    latest_sell = _latest_sell_bsp(level)
    latest_buy = _latest_buy_bsp(level)
    if latest_sell:
        sell_time = _bsp_time(latest_sell)
        buy_time = _bsp_time(latest_buy) if latest_buy else ""
        sell_is_later = not buy_time or (sell_time and str(sell_time) >= str(buy_time))
        if sell_is_later and _is_bsp_in_latest_window(level, latest_sell):
            return True
    return False


def _bottom_divergence(level: dict) -> bool:
    div_info = level.get("div_info") or {}
    return div_info.get("type") == "底背驰"


def _top_divergence(level: dict) -> bool:
    div_info = level.get("div_info") or {}
    if div_info.get("type") == "顶背驰":
        return True
    patterns = _pattern_text(level)
    return "趋势顶背驰" in patterns


def _state(level: dict) -> str:
    return str(level.get("state") or "")


def _price(level: dict) -> float:
    try:
        return float(level.get("price") or 0)
    except (TypeError, ValueError):
        return 0.0


def _price_above_support(level: dict) -> bool:
    price = _price(level)
    support = _num(level.get("zd") or level.get("zs_operative_zd"))
    return price > 0 and support > 0 and price >= support


def _invalidation_boundary(m30: dict, m5: dict) -> dict:
    m5_zg = _num(m5.get("zg") or m5.get("zs_operative_zg"))
    if _prefer_micro_zg_boundary(m5, m5_zg):
        return {"level": "5", "field": "zg", "value": m5_zg}
    m30_bsp = _latest_buy_bsp(m30)
    if m30_bsp and _buy_point_type(str(m30_bsp.get("type") or "")) == "THIRD_BUY":
        m30_bsp_price = _num(m30_bsp.get("price"))
        if m30_bsp_price > 0:
            return {"level": "30", "field": "third_buy_price", "value": m30_bsp_price}
    m30_zd = _num(m30.get("zd") or m30.get("zs_operative_zd"))
    if m30_zd > 0:
        return {"level": "30", "field": "zd", "value": m30_zd}
    m5_zd = _num(m5.get("zd") or m5.get("zs_operative_zd"))
    if m5_zd > 0:
        return {"level": "5", "field": "zd", "value": m5_zd}
    bsp = _latest_buy_bsp(m5)
    if bsp:
        return {"level": "5", "field": "buy_point_price", "value": _num(bsp.get("price"))}
    return {"level": "", "field": "", "value": 0}


def _prefer_micro_zg_boundary(m5: dict, m5_zg: float) -> bool:
    if m5_zg <= 0 or not _price_above_resistance(m5):
        return False
    if _state(m5) in ("IN_CENTER_OSC", "THIRD_BUY_CONFIRMED"):
        return True
    return str(m5.get("last_bi_dir") or "").lower() in ("down", "向下", "向下（未确认）")


def _breaks_boundary(price: float, boundary: dict) -> bool:
    value = _num(boundary.get("value"))
    return price > 0 and value > 0 and price < value


def _boundary_text(boundary: dict) -> str:
    value = _num(boundary.get("value"))
    if value <= 0:
        return "缺少可靠结构边界"
    field = str(boundary.get("field") or "")
    if field == "third_buy_price":
        return f"{boundary.get('level')}分跌破三买低点 {value:g}"
    if field == "buy_point_price":
        return f"{boundary.get('level')}分跌破买点低点 {value:g}"
    return f"{boundary.get('level')}分跌破关键{field.upper()} {value:g}"


def _boundary_label(boundary: dict) -> str:
    value = _num(boundary.get("value"))
    if value <= 0:
        return "结构边界"
    field = str(boundary.get("field") or "")
    if field == "third_buy_price":
        return f"{boundary.get('level')}分三买低点 {value:g}"
    if field == "buy_point_price":
        return f"{boundary.get('level')}分买点低点 {value:g}"
    return f"{boundary.get('level')}分{field.upper()} {value:g}"


def _current_buy_bsp(level: dict) -> Optional[dict]:
    latest = _latest_buy_bsp(level)
    if not latest:
        return None
    if _is_bsp_in_latest_window(level, latest):
        return latest
    return None


def _latest_buy_bsp(level: dict) -> Optional[dict]:
    for bsp in reversed(level.get("bsps") or []):
        if not bsp.get("is_buy"):
            continue
        raw_type = _normalize_bsp_type(bsp.get("type"))
        if raw_type in BSP_TYPE_META:
            return bsp
    return None


def _latest_sell_bsp(level: dict) -> Optional[dict]:
    for bsp in reversed(level.get("bsps") or []):
        if bsp.get("is_buy"):
            continue
        raw_type = _normalize_bsp_type(bsp.get("type"))
        if raw_type in BSP_TYPE_META:
            return bsp
    return None


def _is_bsp_in_latest_window(level: dict, bsp: dict) -> bool:
    bsp_time = _bsp_time(bsp)
    if not bsp_time:
        return False
    active_center = level.get("active_zhongshu") or {}
    center_begin = active_center.get("begin_date")
    if center_begin:
        return str(bsp_time) >= str(center_begin)

    bis = level.get("detail_bis") or level.get("recent_bis") or level.get("bis") or []
    if len(bis) <= 3:
        return True
    window = bis[-3:]
    starts = [item.get("x0") or item.get("start_date") for item in window if item.get("x0") or item.get("start_date")]
    if not starts or not bsp_time:
        return False
    return str(bsp_time) >= str(min(starts))


def _bsp_time(bsp: dict) -> str:
    return str(bsp.get("time") or bsp.get("date") or bsp.get("x") or "")


def _is_5m_forming(m5: dict, boundary: dict) -> bool:
    if not m5:
        return False
    if _current_buy_bsp(m5):
        return False
    price = _price(m5)
    if _breaks_boundary(price, boundary):
        return False
    return str(m5.get("last_bi_dir") or "").lower() in ("down", "向下", "向下（未确认）")


def _buy_point_candidate(
    bsp: dict,
    status: str,
    m5: dict,
    m30: dict,
    boundary: dict,
) -> dict:
    raw_type = _normalize_bsp_type(bsp.get("type"))
    meta = _bsp_type_meta(raw_type, is_buy=True)
    buy_type = meta["family"]
    label = {
        "FIRST_BUY": "5分一买确认",
        "SECOND_BUY": "5分二买确认",
        "THIRD_BUY": "5分三买确认",
    }.get(buy_type, "5分买点确认")
    return {
        "id": f"5m_{buy_type.lower()}",
        "type": buy_type,
        "level": "5",
        "status": status,
        "role": "trigger",
        "parent_level": "30",
        "parent_context": "30分结构确认",
        "label": label,
        "bsp": meta,
        "trigger_if": [],
        "invalid_if": [_boundary_text(boundary), "5分回试跌回中枢内部"],
        "evidence": {
            "time": _bsp_time(bsp),
            "price": bsp.get("price"),
            "zg": m5.get("zg") or m5.get("zs_operative_zg"),
            "zd": m5.get("zd") or m5.get("zs_operative_zd"),
            "parent_zd": m30.get("zd") or m30.get("zs_operative_zd"),
            "patterns": m5.get("patterns") or [],
        },
    }


def _forming_candidate(m5: dict, m30: dict, boundary: dict) -> dict:
    forming_type = _forming_buy_type(m5)
    expected_bsp = _expected_bsp_meta(forming_type)
    label = {
        "FIRST_BUY": "等待5分一买确认",
        "SECOND_BUY": "等待5分二买确认",
        "THIRD_BUY": "等待5分三买确认",
    }.get(forming_type, "等待5分买点确认")
    trigger_if = _forming_trigger_if(forming_type, m5)
    invalid_if = _forming_invalid_if(forming_type, m5, boundary)
    active_boundary = _forming_active_boundary(forming_type, m5, boundary)
    return {
        "id": f"5m_{forming_type.lower()}_forming",
        "type": forming_type,
        "level": "5",
        "status": "FORMING",
        "role": "trigger",
        "parent_level": "30",
        "parent_context": "30分回踩验证",
        "label": label,
        "expected_bsp": expected_bsp,
        "trigger_if": trigger_if,
        "invalid_if": invalid_if,
        "active_boundary": active_boundary,
        "evidence": {
            "zg": m5.get("zg") or m5.get("zs_operative_zg"),
            "zd": m5.get("zd") or m5.get("zs_operative_zd"),
            "parent_zd": m30.get("zd") or m30.get("zs_operative_zd"),
            "price": m5.get("price"),
            "last_bi_dir": m5.get("last_bi_dir"),
            "patterns": m5.get("patterns") or [],
        },
    }


def _forming_active_boundary(forming_type: str, m5: dict, fallback: dict) -> dict:
    zg = _num(m5.get("zg") or m5.get("zs_operative_zg"))
    zd = _num(m5.get("zd") or m5.get("zs_operative_zd"))
    if forming_type == "THIRD_BUY" and zg > 0:
        return {"level": "5", "field": "zg", "value": zg}
    if forming_type == "FIRST_BUY" and zd > 0:
        return {"level": "5", "field": "zd", "value": zd}
    return fallback


def _forming_buy_type(m5: dict) -> str:
    price = _price(m5)
    zg = _num(m5.get("zg") or m5.get("zs_operative_zg"))
    zd = _num(m5.get("zd") or m5.get("zs_operative_zd"))
    patterns = _pattern_text(m5)
    latest_bsp = _latest_buy_bsp(m5)

    if zg > 0 and price >= zg:
        return "THIRD_BUY"
    if latest_bsp:
        latest_type = _buy_point_type(str(latest_bsp.get("type") or ""))
        if latest_type == "FIRST_BUY" and _num(latest_bsp.get("price")) > 0:
            return "SECOND_BUY"
    if "底背驰" in patterns or (zd > 0 and price > 0 and price <= zg and price >= zd):
        return "FIRST_BUY"
    return "UNKNOWN_BUY"


def _expected_bsp_meta(forming_type: str) -> dict:
    expected = {
        "FIRST_BUY": ("B1/B1P", "一买/类一买", "CChan BSP_TYPE.T1/T1P"),
        "SECOND_BUY": ("B2/B2S", "二买/类二买", "CChan BSP_TYPE.T2/T2S"),
        "THIRD_BUY": ("B3A/B3B", "三买A/三买B", "CChan BSP_TYPE.T3A/T3B"),
    }.get(forming_type)
    if not expected:
        return {
            "family": "UNKNOWN_BUY",
            "code": "",
            "name": "买点待确认",
            "display": "买点待确认",
            "source": "等待 CChan BSP 确认",
            "is_buy": True,
        }
    code, name, source = expected
    return {
        "family": forming_type,
        "code": code,
        "name": name,
        "display": f"{code} {name}",
        "source": f"等待 {source} 确认",
        "is_buy": True,
    }


def _forming_trigger_if(forming_type: str, m5: dict) -> list[str]:
    zg = _num(m5.get("zg") or m5.get("zs_operative_zg"))
    zd = _num(m5.get("zd") or m5.get("zs_operative_zd"))
    if forming_type == "THIRD_BUY":
        return [
            f"5分回试不跌破中枢ZG {zg:g}" if zg > 0 else "5分回试不跌回中枢上沿",
            "回落段力度衰减或出现向上转折",
            "CChan 确认 B3A/B3B 三买",
        ]
    if forming_type == "SECOND_BUY":
        return [
            "5分一买后的回试不破前低",
            "回试后出现向上笔或类二买结构",
            "CChan 确认 B2/B2S 二买",
        ]
    if forming_type == "FIRST_BUY":
        return [
            f"5分下跌段在ZD {zd:g} 附近止跌" if zd > 0 else "5分下跌段出现止跌迹象",
            "回落段力度衰减或底背驰继续成立",
            "CChan 确认 B1/B1P 一买",
        ]
    return [
        "5分回落段力度衰减",
        "5分出现一买、二买或三买结构",
        "回试不跌破关键结构边界",
    ]


def _forming_invalid_if(forming_type: str, m5: dict, boundary: dict) -> list[str]:
    zg = _num(m5.get("zg") or m5.get("zs_operative_zg"))
    if forming_type == "THIRD_BUY" and zg > 0:
        return [_boundary_text(boundary), f"5分回试跌回中枢ZG {zg:g} 下方"]
    return [_boundary_text(boundary), "5分回试跌回中枢内部"]


def _buy_point_type(raw_type: str) -> str:
    raw_type = _normalize_bsp_type(raw_type)
    if raw_type in BSP_TYPE_META:
        return BSP_TYPE_META[raw_type]["family"]
    if raw_type.startswith("3"):
        return "THIRD_BUY"
    if raw_type.startswith("2"):
        return "SECOND_BUY"
    if raw_type.startswith("1"):
        return "FIRST_BUY"
    return "UNKNOWN_BUY"


def _normalize_bsp_type(raw_type) -> str:
    if isinstance(raw_type, (list, tuple)):
        raw_type = raw_type[0] if raw_type else ""
    return str(raw_type or "").strip().lower()


def _bsp_type_meta(raw_type: str, is_buy: bool) -> dict:
    raw_type = _normalize_bsp_type(raw_type)
    meta = BSP_TYPE_META.get(raw_type)
    if not meta:
        code = f"{'B' if is_buy else 'S'}{raw_type.upper()}" if raw_type else ""
        return {
            "raw_type": raw_type,
            "family": _buy_point_type(raw_type) if is_buy else "UNKNOWN_SELL",
            "code": code,
            "name": "买点" if is_buy else "卖点",
            "display": code or "未知买卖点",
            "source": "CChan BSP_TYPE",
            "is_buy": is_buy,
        }
    code = meta["code"] if is_buy else meta["code"].replace("B", "S", 1)
    name = meta["name"] if is_buy else meta["name"].replace("买", "卖")
    return {
        "raw_type": raw_type,
        "family": meta["family"],
        "code": code,
        "name": name,
        "display": f"{code} {name}",
        "source": meta["source"],
        "is_buy": is_buy,
    }


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _dedupe(items: list[str]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
