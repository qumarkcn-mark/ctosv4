"""Translate semantic signal codes into expert and plain Chinese labels."""

from __future__ import annotations

from server.engines.signal.models import SignalCode


LEVEL_TRANSLATIONS = {
    "w": ("周线", "周线级别"),
    "d1": ("日线", "日线级别"),
    "m60": ("60分", "60分钟级别"),
    "m30": ("30分", "30分钟级别"),
    "m15": ("15分", "15分钟级别"),
    "m5": ("5分", "5分钟级别"),
}

POSITION_TRANSLATIONS = {
    "zs_above": ("中枢上", "中枢上方"),
    "zs_inside": ("中枢内", "中枢内部"),
    "zs_below": ("中枢下", "中枢下方"),
    "unknown": ("位置未知", "位置待确认"),
}

PATTERN_TRANSLATIONS = {
    "bs1": ("一买", "底部反转信号", "买入"),
    "bs2": ("二买", "回踩确认买点", "买入"),
    "bs3": ("三买", "回踩支撑位不破", "买入"),
    "ss1": ("一卖", "顶部反转信号", "减仓"),
    "ss2": ("二卖", "反弹确认卖点", "减仓"),
    "ss3": ("三卖", "反弹压力位不破", "减仓"),
    "top_div": ("顶背驰", "上涨动力衰竭", "降风险"),
    "bot_div": ("底背驰", "下跌动力衰竭", "观察买点"),
    "range_osc": ("震荡", "区间内震荡", "观望"),
    "trend_up": ("上升趋势", "上升趋势延续", "持有"),
    "trend_down": ("下降趋势", "下降趋势延续", "防守"),
    "breakout": ("突破", "突破关键位", "关注确认"),
    "pullback": ("回踩", "回踩关键位", "等待确认"),
    "unknown": ("形态未知", "结构形态待确认", "观望"),
}

STRENGTH_TRANSLATIONS = {
    "strong": ("强", "信号较强"),
    "medium": ("中", "信号中等"),
    "weak": ("弱", "信号较弱"),
}


def translate_signal(parts: SignalCode) -> dict:
    """Return expert/plain labels for a semantic signal."""
    level_expert, level_plain = LEVEL_TRANSLATIONS.get(parts.level, (parts.level, parts.level))
    position_expert, _position_plain = POSITION_TRANSLATIONS.get(
        parts.position,
        (_position_expert(parts.position), _position_expert(parts.position)),
    )
    pattern_expert, pattern_plain, action_bias = PATTERN_TRANSLATIONS.get(
        parts.pattern,
        (parts.pattern, parts.pattern, "观望"),
    )
    strength_expert, strength_plain = STRENGTH_TRANSLATIONS.get(parts.strength, (parts.strength, parts.strength))

    return {
        "label_expert": f"{level_expert} · {position_expert} · {pattern_expert} · {strength_expert}",
        "label_plain": f"{level_plain}{pattern_plain}，{strength_plain}",
        "action_bias": action_bias,
    }


def _position_expert(position: str) -> str:
    if position.startswith("bi"):
        return f"{position[2:]}笔"
    if position.startswith("seg"):
        return f"{position[3:]}段"
    return position or "位置未知"
