"""Divergence facts derived from chan.py serialized bi data."""

from typing import Optional


def detect_recent_divergence(bis: list) -> Optional[dict]:
    """检测最近一笔对应方向的背驰，只消费 chan.py 已序列化的笔数据。"""
    if not bis:
        return None
    last_bi = bis[-1]
    is_up = bool(last_bi.get("is_up"))
    return get_divergence(bis, is_up)


def get_divergence(bis: list, is_up: bool) -> Optional[dict]:
    """检测最近同向笔背驰，返回结构化事实而非交易建议。"""
    confirmed_bis = [bi for bi in bis if bi.get("is_sure", True)]
    same_dir = [bi for bi in confirmed_bis if bool(bi.get("is_up")) == is_up]
    if len(same_dir) < 2:
        return None

    prev_bi = same_dir[-2]
    curr_bi = same_dir[-1]
    prev_y1 = _safe_float(prev_bi.get("y1"))
    curr_y1 = _safe_float(curr_bi.get("y1"))

    # 背驰必须先有价格创新高/新低，否则只是力度变化，不标记买卖点风险。
    if is_up and curr_y1 < prev_y1:
        return None
    if not is_up and curr_y1 > prev_y1:
        return None

    prev_mom = prev_bi.get("momentum") or {}
    curr_mom = curr_bi.get("momentum") or {}
    prev_area = abs(_safe_float(prev_mom.get("area")))
    curr_area = abs(_safe_float(curr_mom.get("area")))
    prev_dif = abs(_safe_float(prev_mom.get("dif_extreme")))
    curr_dif = abs(_safe_float(curr_mom.get("dif_extreme")))

    if prev_area <= 0:
        return None

    area_ratio = curr_area / prev_area
    area_score = max(0.0, 1.0 - area_ratio)
    if prev_dif > 0:
        dif_ratio = curr_dif / prev_dif
        dif_score = max(0.0, 1.0 - dif_ratio)
    else:
        dif_ratio = area_ratio
        dif_score = area_score

    combined = area_score * 0.4 + dif_score * 0.6
    if combined < 0.20:
        return None

    severity = "高危" if combined >= 0.55 else "中等" if combined >= 0.35 else "轻微"
    return {
        "type": "顶背驰" if is_up else "底背驰",
        "ratio": round(area_ratio, 3),
        "dif_ratio": round(dif_ratio, 3),
        "combined_score": round(combined, 3),
        "severity": severity,
        "source": "chan_adapter.bis.momentum",
    }


def classify_divergence_type(
    div_info: Optional[dict],
    detail_bis: list,
    price: float,
) -> str:
    """基于背驰后的价格行为区分中继/转折，保持为事实字段。"""
    if not div_info:
        return ""

    div_type = div_info.get("type", "")
    is_top = div_type == "顶背驰"
    is_bottom = div_type == "底背驰"
    if not (is_top or is_bottom):
        return ""

    confirmed = [bi for bi in detail_bis if bi.get("is_sure", True)]
    if len(confirmed) < 2:
        return "疑似转折"

    div_extreme = _safe_float(confirmed[-1].get("y1"))
    if div_extreme <= 0 or price <= 0:
        return "疑似转折"

    if is_top:
        if price > div_extreme * 1.005:
            return "中继"
        if price < div_extreme * 0.980:
            return "转折"
        return "疑似转折"

    if price < div_extreme * 0.995:
        return "中继"
    if price > div_extreme * 1.020:
        return "转折"
    return "疑似转折"


def _safe_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
