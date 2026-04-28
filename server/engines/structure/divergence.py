"""Divergence facts derived from chan.py serialized bi data."""

from typing import Optional


def detect_recent_divergence(bis: list) -> Optional[dict]:
    """检测最近一笔对应方向的背驰，只消费 chan.py 已序列化的笔数据。"""
    if not bis:
        return None
    last_bi = bis[-1]
    is_up = bool(last_bi.get("is_up"))
    return get_divergence(bis, is_up)


def detect_structural_divergence(
    bis: list,
    is_up: bool,
    lookback: int = 8,
    min_combined_score: float = 0.20,
) -> Optional[dict]:
    """检测最近若干同向笔里的结构背驰。

    `get_divergence` 只比较最近两段同向笔，适合实时末端确认；但高位主升
    常见的是“前主升笔力度最大，后续创新高笔力度衰减”。这里在最近若干同向
    笔中寻找最新创新高/新低笔，并与此前最强同向笔比较。
    """
    confirmed_bis = [bi for bi in bis if bi.get("is_sure", True)]
    same_dir = [bi for bi in confirmed_bis if bool(bi.get("is_up")) == is_up]
    if len(same_dir) < 2:
        return None

    window = same_dir[-max(2, lookback):]
    curr_bi = _latest_extreme_bi(window, is_up)
    if curr_bi is None:
        return None

    curr_idx = window.index(curr_bi)
    previous = window[:curr_idx]
    if not previous:
        return None

    # 前置笔必须没有被价格突破，否则不是“创新高/新低后的力度比较”。
    curr_y1 = _safe_float(curr_bi.get("y1"))
    if is_up:
        previous = [bi for bi in previous if curr_y1 > _safe_float(bi.get("y1"))]
    else:
        previous = [bi for bi in previous if curr_y1 < _safe_float(bi.get("y1"))]
    if not previous:
        return None

    prev_bi = max(previous, key=_momentum_score)
    div_info = _divergence_from_pair(prev_bi, curr_bi, is_up, min_combined_score)
    if not div_info:
        return None
    div_info.update({
        "source": "chan_adapter.bis.structural_momentum",
        "previous_bi": _bi_ref(prev_bi),
        "current_bi": _bi_ref(curr_bi),
    })
    return div_info


def build_momentum_compare(
    bis: list,
    is_up: Optional[bool] = None,
    lookback: int = 8,
) -> dict:
    """Build raw momentum comparison facts for the latest same-direction leg.

    返回结构事实，不直接判定买卖。`detect_structural_divergence` 负责把这些事实
    进一步归类成顶/底背驰。
    """
    confirmed_bis = [bi for bi in bis if bi.get("is_sure", True)]
    if is_up is None:
        if not confirmed_bis:
            return _empty_momentum_compare()
        is_up = bool(confirmed_bis[-1].get("is_up"))

    same_dir = [bi for bi in confirmed_bis if bool(bi.get("is_up")) == bool(is_up)]
    if len(same_dir) < 2:
        return _empty_momentum_compare(direction="up" if is_up else "down")

    window = same_dir[-max(2, lookback):]
    curr_bi = window[-1]
    prev_bi = max(window[:-1], key=_momentum_score)
    prev_metrics = _momentum_metrics(prev_bi)
    curr_metrics = _momentum_metrics(curr_bi)
    prev_area = prev_metrics["area"]
    curr_area = curr_metrics["area"]
    prev_dif = prev_metrics["dif_extreme"]
    curr_dif = curr_metrics["dif_extreme"]

    area_ratio = curr_area / prev_area if prev_area > 0 else 0.0
    dif_ratio = curr_dif / prev_dif if prev_dif > 0 else area_ratio
    area_score = max(0.0, 1.0 - area_ratio) if prev_area > 0 else 0.0
    dif_score = max(0.0, 1.0 - dif_ratio) if prev_dif > 0 else area_score
    combined_score = area_score * 0.4 + dif_score * 0.6
    curr_y1 = _safe_float(curr_bi.get("y1"))
    prev_y1 = _safe_float(prev_bi.get("y1"))
    price_makes_extreme = curr_y1 >= prev_y1 if is_up else curr_y1 <= prev_y1

    return {
        "direction": "up" if is_up else "down",
        "price_makes_extreme": bool(price_makes_extreme),
        "is_weaker": bool(area_ratio < 1 or dif_ratio < 1),
        "area_ratio": round(area_ratio, 3),
        "dif_ratio": round(dif_ratio, 3),
        "combined_score": round(combined_score, 3),
        "previous": {
            **_bi_ref(prev_bi),
            "momentum_metrics": prev_metrics,
        },
        "current": {
            **_bi_ref(curr_bi),
            "momentum_metrics": curr_metrics,
        },
    }


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

    div_info = _divergence_from_pair(prev_bi, curr_bi, is_up, 0.20)
    if not div_info:
        return None
    div_info["source"] = "chan_adapter.bis.momentum"
    return div_info


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


def _empty_momentum_compare(direction: str = "") -> dict:
    return {
        "direction": direction,
        "price_makes_extreme": False,
        "is_weaker": False,
        "area_ratio": 0.0,
        "dif_ratio": 0.0,
        "combined_score": 0.0,
        "previous": {},
        "current": {},
    }


def _latest_extreme_bi(bis: list, is_up: bool) -> Optional[dict]:
    if not bis:
        return None
    if is_up:
        extreme = max(_safe_float(bi.get("y1")) for bi in bis)
        for bi in reversed(bis):
            if _safe_float(bi.get("y1")) == extreme:
                return bi
    else:
        extreme = min(_safe_float(bi.get("y1")) for bi in bis)
        for bi in reversed(bis):
            if _safe_float(bi.get("y1")) == extreme:
                return bi
    return None


def _divergence_from_pair(
    prev_bi: dict,
    curr_bi: dict,
    is_up: bool,
    min_combined_score: float,
) -> Optional[dict]:
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
    if combined < min_combined_score:
        return None

    severity = "高危" if combined >= 0.55 else "中等" if combined >= 0.35 else "轻微"
    return {
        "type": "顶背驰" if is_up else "底背驰",
        "ratio": round(area_ratio, 3),
        "dif_ratio": round(dif_ratio, 3),
        "combined_score": round(combined, 3),
        "severity": severity,
    }


def _momentum_score(bi: dict) -> float:
    momentum = bi.get("momentum") or {}
    return abs(_safe_float(momentum.get("area"))) + abs(_safe_float(momentum.get("dif_extreme")))


def _momentum_metrics(bi: dict) -> dict:
    momentum = bi.get("momentum") or {}
    return {
        "area": round(abs(_safe_float(momentum.get("area"))), 4),
        "dif_extreme": round(abs(_safe_float(momentum.get("dif_extreme"))), 4),
    }


def _bi_ref(bi: dict) -> dict:
    return {
        "x0": bi.get("x0") or "",
        "x1": bi.get("x1") or "",
        "y0": _safe_float(bi.get("y0")),
        "y1": _safe_float(bi.get("y1")),
        "momentum": bi.get("momentum") or {},
    }
