"""CT-OS V4.0 — 调仓罗盘 打分引擎 (Rotation Scorer)

把 analyze_matrix_state 返回的缠论结构数据，通过纯规则引擎打成 0-100 的综合分，
以便在"持仓 + 候选股"同一张表里做横向对比。

评分构成 (满分 100)：
  1. 走势类型       30 分 — 上涨趋势/盘整上沿/盘整中枢内/盘整下沿/下跌趋势
  2. 买卖点信号     ±20 分 — 跨级别 pattern 扫描（三买最高 +20，三卖最低 -20）
  3. 区间套深度     ±15 分 — 底背驰共振加分，顶背驰共振扣分
  4. 背驰状态       ±15 分 — 按严重度计权
  5. 防线空间       10 分 — 当前价距离甲情形止损的百分比
  6. 动能位置(代理) 10 分 — 用日线 state 代理 MACD 0 轴位置

动作建议对照：
  80-100 = 加仓/开仓 ★★★★★
  65-79  = 持仓/关注 ★★★★
  45-64  = 观望      ★★★
  30-44  = 减仓      ★★
  0-29   = 清仓/回避 ★
"""

from typing import Optional
from server.services.chan_service import (
    PAT_TREND_BOT_DIV, PAT_RANGE_BOT_DIV, PAT_SECOND_BUY, PAT_THIRD_BUY,
    PAT_TREND_TOP_DIV, PAT_RANGE_TOP_DIV, PAT_SECOND_SELL, PAT_THIRD_SELL,
)


# ═══════════════════════════════════════════════════════════════
# 打分主函数
# ═══════════════════════════════════════════════════════════════

def score_symbol(matrix_result: dict, is_holding: bool = True) -> dict:
    """对单只股票的 analyze_matrix_state 输出打分。

    Args:
        matrix_result: analyze_matrix_state 的返回
        is_holding:    True=持仓，False=候选；影响"加仓 vs 开仓"文案

    Returns:
        {
          score:        综合分 0-100,
          breakdown:    {各维度分值},
          action:       动作文字（加仓/持仓/观望/减仓/清仓...）,
          stars:        1-5 星,
          state_emoji:  状态 emoji,
          state_label:  状态中文标签,
          distance_pct: 距防线百分比（可能为 None）,
          stop_loss:    防线价,
          price:        当前价,
          zd/zg:        日线中枢下沿/上沿,
          main_action:  forward_classes[甲].action 前 60 字（原汁原味的操作指导）,
        }
    """
    matrix_a = matrix_result.get("matrix_a") or []
    if len(matrix_a) < 3:
        return _empty_score("数据不足", is_holding=is_holding)

    day = matrix_a[0]
    m30 = matrix_a[1]
    m5 = matrix_a[2]

    if day.get("data_status") in ("missing", "insufficient"):
        return _empty_score(day.get("data_status") or "数据不足", is_holding=is_holding)

    nesting = matrix_result.get("interval_nesting_a") or {}
    forward = matrix_result.get("forward_analysis_a") or {}

    breakdown: dict = {}

    # ── 1. 走势类型 (30 分) ──
    zoushi = (day.get("zoushi_type") or {}).get("type", "")
    price = day.get("price", 0) or 0
    zd = day.get("zd", 0) or 0
    zg = day.get("zg", 0) or 0

    if zoushi == "上涨趋势":
        s_zoushi = 30
    elif zoushi == "盘整":
        if zg > 0 and price > zg:
            s_zoushi = 22    # 在中枢上方的盘整 = 偏强
        elif zd > 0 and price < zd:
            s_zoushi = 6     # 在中枢下方的盘整 = 偏弱
        else:
            s_zoushi = 15    # 中枢内盘整 = 中性
    elif zoushi == "下跌趋势":
        s_zoushi = 0
    else:
        s_zoushi = 10       # 构建中/数据不足
    breakdown["走势"] = s_zoushi

    # ── 2. 买卖点 (±20 分) 跨级别扫描 patterns ──
    all_patterns = " ".join(
        (day.get("patterns") or [])
        + (m30.get("patterns") or [])
        + (m5.get("patterns") or [])
    )

    buy_score, sell_score = 0, 0
    # H3 修复：改为精确常量匹配，消除脆弱子串匹配（任意一个模板改动则另一侧静默失效）
    if PAT_THIRD_BUY in all_patterns:
        buy_score = max(buy_score, 20)
    elif PAT_SECOND_BUY in all_patterns:
        buy_score = max(buy_score, 18)
    elif PAT_TREND_BOT_DIV in all_patterns:
        buy_score = max(buy_score, 15)
    elif PAT_RANGE_BOT_DIV in all_patterns:
        buy_score = max(buy_score, 8)

    if PAT_THIRD_SELL in all_patterns:
        sell_score = max(sell_score, 20)
    elif PAT_SECOND_SELL in all_patterns:
        sell_score = max(sell_score, 18)
    elif PAT_TREND_TOP_DIV in all_patterns:
        sell_score = max(sell_score, 15)
    elif PAT_RANGE_TOP_DIV in all_patterns:
        sell_score = max(sell_score, 8)

    s_bs = buy_score - sell_score
    breakdown["买卖点"] = s_bs

    # ── 3. 区间套共振 (±15 分) ──
    depth = nesting.get("depth", 0)
    direction = nesting.get("direction", "")
    nest_table = {3: 15, 2: 10, 1: 5}
    if direction == "bottom":
        s_nest = nest_table.get(depth, 0)
    elif direction == "top":
        s_nest = -nest_table.get(depth, 0)
    else:
        s_nest = 0
    breakdown["区间套"] = s_nest

    # ── 4. 背驰状态 (±15 分) — 优先取 m30，回退日线 ──
    div = m30.get("div_info") or day.get("div_info") or {}
    severity_map = {"高危": 15, "中等": 10, "轻微": 5}
    s_div = 0
    if div:
        sev_score = severity_map.get(div.get("severity", ""), 0)
        if div.get("type") == "底背驰":
            s_div = sev_score
        elif div.get("type") == "顶背驰":
            s_div = -sev_score
    breakdown["背驰"] = s_div

    # ── 5. 防线空间 (10 分) ──
    fclasses = forward.get("forward_classes") or []
    stop = 0
    if fclasses and fclasses[0].get("stop_loss"):
        stop = fclasses[0]["stop_loss"]
    elif zd > 0:
        stop = zd

    distance_pct: Optional[float] = None
    if stop > 0 and price > 0:
        distance_pct = (price - stop) / price * 100
        if distance_pct <= 0:
            s_buffer = 0  # 已破位
        elif distance_pct < 2:
            s_buffer = 2
        elif distance_pct < 5:
            s_buffer = 5
        elif distance_pct < 10:
            s_buffer = 8
        else:
            s_buffer = 10
    else:
        s_buffer = 5
    breakdown["防线空间"] = s_buffer

    # ── 6. 动能位置代理 (10 分) ──
    day_state = day.get("state", "")
    if day_state in ("UPWARD_LEAVING", "THIRD_BUY_CONFIRMED"):
        s_macd = 10
    elif day_state in ("DOWNWARD_LEAVING", "THIRD_SELL_CONFIRMED"):
        s_macd = 0
    elif day_state == "IN_CENTER_OSC":
        if zd > 0 and zg > 0 and price > (zd + zg) / 2:
            s_macd = 7
        else:
            s_macd = 4
    elif day_state == "TREND_EXTENDING":
        s_macd = 6
    else:
        s_macd = 5
    breakdown["动能"] = s_macd

    # ── 总分 ──
    total = s_zoushi + s_bs + s_nest + s_div + s_buffer + s_macd
    total = max(0, min(100, int(round(total))))

    # C1 修复：去掉替用户做决策的 action/stars 字段
    # sort_score 内部使用（排序 + 颜色深度），不再对外展示为"综合分"

    # 状态展示
    emoji = _state_to_emoji(day_state, all_patterns)
    label = _state_to_label(day_state, all_patterns)

    # lifecycle_node：从甲情形取结构节点标签，替换"加仓/清仓"为客观描述
    lifecycle_node = ""
    if fclasses and fclasses[0].get("lifecycle_node"):
        lifecycle_node = fclasses[0]["lifecycle_node"]

    # 甲情形原始动作文案（≤60 字）
    main_action = ""
    if fclasses and fclasses[0].get("action"):
        raw = fclasses[0]["action"]
        # 去掉多余 T+1 提示，保持一行
        for cut_tok in ("注：基于 T+1", "【"):
            idx = raw.find(cut_tok)
            if idx > 0:
                raw = raw[:idx].strip()
                break
        # H1 修复：按句号截断，保证首句完整性，而非硬截60字破坏语义
        # 取第一个完整句子（≤100字），若无句号则取前80字
        first_end = raw.find("。")
        if 0 < first_end <= 100:
            main_action = raw[:first_end + 1]
        else:
            main_action = raw[:80]

    return {
        "sort_score":     total,       # 内部排序+颜色深度，不对外展示数字
        "state_emoji":    emoji,
        "state_label":    label,
        "lifecycle_node": lifecycle_node,  # 客观结构节点（替代 action 文字建议）
        "distance_pct":   round(distance_pct, 2) if distance_pct is not None else None,
        "stop_loss":      round(stop, 2) if stop > 0 else None,
        "price":          round(price, 2) if price else None,
        "zd":             round(zd, 2) if zd else None,
        "zg":             round(zg, 2) if zg else None,
        "main_action":    main_action,
        "zoushi_type":    zoushi,
        "breakdown":      breakdown,
    }


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _action_from_score(score: int, is_holding: bool) -> tuple[str, int]:
    """综合分 → 动作建议 + 星级"""
    if score >= 80:
        return ("加仓" if is_holding else "开仓", 5)
    if score >= 65:
        return ("持仓" if is_holding else "关注", 4)
    if score >= 45:
        return ("观望", 3)
    if score >= 30:
        return ("减仓" if is_holding else "回避", 2)
    return ("全仓清仓" if is_holding else "回避", 1)


def _state_to_emoji(state: str, patterns: str) -> str:
    """按优先级把状态+形态映射成一个 emoji"""
    # 卖点/破位优先
    if "三卖确认" in patterns or state == "THIRD_SELL_CONFIRMED":
        return "🛑"
    if state == "DOWNWARD_LEAVING":
        return "🔴"
    if "二卖确认" in patterns or "趋势顶背驰" in patterns:
        return "⚠️"
    # 买点/机会
    if state == "THIRD_BUY_CONFIRMED" or "三买确认" in patterns:
        return "🟢"
    if state == "UPWARD_LEAVING":
        return "🚀"
    if "二买确认" in patterns or "趋势底背驰" in patterns:
        return "✅"
    # 中性
    if state == "IN_CENTER_OSC":
        return "⚖️"
    if state == "TREND_EXTENDING":
        return "📈"
    return "⚪"


def _state_to_label(state: str, patterns: str) -> str:
    """生成一行状态标签（取最关键的一条）"""
    # 按优先级短路返回
    if "🛑 三卖确认" in patterns or "三卖确认" in patterns:
        return "三卖确认"
    if "🟢 三买确认" in patterns or "三买确认" in patterns:
        return "三买确认"
    if "二买确认" in patterns:
        return "二买确认"
    if "二卖确认" in patterns:
        return "二卖确认"
    if "趋势顶背驰" in patterns:
        return "趋势顶背驰"
    if "趋势底背驰" in patterns:
        return "趋势底背驰"
    if "盘整顶背驰" in patterns:
        return "盘整顶背驰"
    if "盘整底背驰" in patterns:
        return "盘整底背驰"

    state_names = {
        "UPWARD_LEAVING": "向上离开中枢",
        "DOWNWARD_LEAVING": "向下破位",
        "THIRD_BUY_CONFIRMED": "三买确认",
        "THIRD_SELL_CONFIRMED": "三卖确认",
        "IN_CENTER_OSC": "中枢震荡",
        "TREND_EXTENDING": "趋势延伸",
        "UNKNOWN": "数据不足",
    }
    return state_names.get(state, "待定位")


def _empty_score(reason: str, is_holding: bool = True) -> dict:
    """数据不足时的降级返回 — sort_score=20 触发砍仓建议"""
    return {
        "sort_score":     20,
        "state_emoji":    "⚪",
        "state_label":    reason,
        "lifecycle_node": "",
        "distance_pct":   None,
        "stop_loss":      None,
        "price":          None,
        "zd":             None,
        "zg":             None,
        "main_action":    "",
        "zoushi_type":    "",
        "breakdown":      {},
    }
