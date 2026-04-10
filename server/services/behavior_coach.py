"""投资行为教练 — 三层进化规则引擎

层 1: 纵向自我对比 (与上月/上周对比，检测进步或退化)
层 2: 行为模式挖掘 (连胜后必亏、冲动交易时间分布等)
层 3: 自适应阈值 (根据交易频率和风格动态调整评判标准)
"""

from typing import Optional
from server.services.behavior_engine import BehaviorReport


def generate_diagnosis(
    current: BehaviorReport,
    previous: Optional[BehaviorReport] = None
) -> list[dict]:
    """
    根据当前指标和历史对比，生成结构化诊断建议列表。

    返回:
        [
            {"level": "critical|warning|info|success", "title": "...", "detail": "..."},
            ...
        ]
    """
    items = []

    # ═══════════════════════════════════════════
    # 层 1: 核心指标诊断 + 纵向对比
    # ═══════════════════════════════════════════

    # ── 胜率 ──
    if current.win_rate < 30:
        items.append({
            "level": "critical",
            "title": "选股命中率极低",
            "detail": f"胜率仅 {current.win_rate}%，超过 7 成交易在亏损。建议严格按照缠论三买信号入场，拒绝一切无逻辑的交易。"
        })
    elif current.win_rate < 45:
        items.append({
            "level": "warning",
            "title": "选股精度待提升",
            "detail": f"胜率 {current.win_rate}%，低于散户生存线。优先复盘亏损交易，寻找共性原因。"
        })
    else:
        items.append({
            "level": "success",
            "title": "选股能力合格",
            "detail": f"胜率 {current.win_rate}%，保持当前纪律。"
        })

    # 纵向对比
    if previous and previous.total_pairs > 0:
        delta = current.win_rate - previous.win_rate
        if abs(delta) >= 5:
            trend = "↑ 进步" if delta > 0 else "↓ 退化"
            items.append({
                "level": "info" if delta > 0 else "warning",
                "title": f"胜率趋势 {trend}",
                "detail": f"上期 {previous.win_rate}% → 本期 {current.win_rate}%，变化 {delta:+.1f}%"
            })

    # ── 盈亏比 ──
    if current.profit_loss_ratio < 0.8:
        items.append({
            "level": "critical",
            "title": "赚小亏大，致命缺陷",
            "detail": f"盈亏比仅 {current.profit_loss_ratio}，意味着你赚的钱不够填亏损的坑。必须学会让利润奔跑，截断亏损。"
        })
    elif current.profit_loss_ratio < 1.5:
        items.append({
            "level": "warning",
            "title": "盈亏比偏低",
            "detail": f"盈亏比 {current.profit_loss_ratio}，尚可但有提升空间。考虑活用移动止盈。"
        })
    else:
        items.append({
            "level": "success",
            "title": "盈亏结构健康",
            "detail": f"盈亏比 {current.profit_loss_ratio}，赚多亏少，继续保持。"
        })

    # ── 止损纪律 ──
    if current.stop_loss_execution_rate < 40:
        items.append({
            "level": "critical",
            "title": "止损形同虚设",
            "detail": f"止损执行率仅 {current.stop_loss_execution_rate}%。设了止损却不执行，等于在裸泳。纪律是生存的底线。"
        })
    elif current.stop_loss_execution_rate < 70:
        items.append({
            "level": "warning",
            "title": "止损执行不够坚决",
            "detail": f"止损执行率 {current.stop_loss_execution_rate}%，还有一些犹豫。记住：止损不是认输，是保住本金。"
        })

    # ── 逆势交易 ──
    if current.counter_trend_rate > 40:
        items.append({
            "level": "critical",
            "title": "频繁逆势操作",
            "detail": f"逆势交易占比高达 {current.counter_trend_rate}%。在下跌趋势中抄底是散户最大的亏损来源。"
        })
    elif current.counter_trend_rate > 20:
        items.append({
            "level": "warning",
            "title": "注意逆势冲动",
            "detail": f"逆势交易占 {current.counter_trend_rate}%，偶尔可以，但不能成为习惯。"
        })

    # ── 冲动交易 ──
    if current.impulse_trade_rate > 40:
        items.append({
            "level": "critical",
            "title": "情绪化交易严重",
            "detail": f"凭感觉做的交易占 {current.impulse_trade_rate}%。每笔交易必须有明确的买入逻辑，否则不要下单。"
        })
    elif current.impulse_trade_rate > 15:
        items.append({
            "level": "warning",
            "title": "偶有冲动交易",
            "detail": f"感性交易占 {current.impulse_trade_rate}%，建议在下单前强制写下买入理由。"
        })

    # ═══════════════════════════════════════════
    # 层 2: 行为模式挖掘
    # ═══════════════════════════════════════════

    if current.early_exit_count > 0 and current.total_pairs > 3:
        exit_rate = current.early_exit_count / current.total_pairs * 100
        if exit_rate > 30:
            items.append({
                "level": "warning",
                "title": "频繁过早止盈",
                "detail": f"有 {current.early_exit_count} 笔盈利交易在 3 天内就匆匆离场，占比 {exit_rate:.0f}%。你可能正在截断利润。"
            })

    # 连败检测
    if current.pairs:
        max_streak = 0
        streak = 0
        for p in sorted(current.pairs, key=lambda x: x.sell_date):
            if p.pnl <= 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        if max_streak >= 4:
            items.append({
                "level": "warning",
                "title": f"曾连续亏损 {max_streak} 笔",
                "detail": "连败期间容易引发报复性交易。建议连亏 3 笔后主动停止交易 1-2 天冷静。"
            })

    # ═══════════════════════════════════════════
    # 层 3: 自适应阈值 — 根据交易频率调档
    # ═══════════════════════════════════════════

    if current.total_pairs > 20 and current.avg_hold_days < 3:
        # 高频交易者
        if current.win_rate >= 45 and current.profit_loss_ratio >= 1.5:
            items.append({
                "level": "success",
                "title": "短线战士表现优秀",
                "detail": f"作为高频交易者（平均持仓 {current.avg_hold_days} 天），你的胜率和盈亏比都处于健康区间。"
            })
    elif current.total_pairs <= 10 and current.avg_hold_days > 15:
        # 低频波段者
        if current.win_rate < 55:
            items.append({
                "level": "warning",
                "title": "波段选手胜率偏低",
                "detail": f"作为低频重仓型（平均持仓 {current.avg_hold_days} 天），每一笔的试错成本极高，胜率需至少 55% 以上。"
            })

    return items
