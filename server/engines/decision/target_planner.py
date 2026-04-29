"""Target and reward/risk planning rules."""


def calculate_targets(current_price: float, bis: list, zhongshus: list, stop_price: float) -> list:
    """根据结构前高和上方中枢计算空仓入场后的参考目标。"""
    targets = []
    confirmed = [bi for bi in bis if bi.get("is_sure", True)]

    up_bis = [bi for bi in confirmed if bi.get("is_up")]
    if up_bis:
        recent_high = _safe_float(up_bis[-1].get("y1"))
        if recent_high > current_price > 0:
            targets.append(
                {
                    "label": "短期目标（前高）",
                    "price": round(recent_high, 2),
                    "distance_pct": round((recent_high - current_price) / current_price, 4),
                }
            )

    upper_zs = [zs for zs in zhongshus if _safe_float(zs.get("zg")) > current_price > 0]
    if upper_zs:
        nearest_zg = min(upper_zs, key=lambda zs: _safe_float(zs.get("zg"))).get("zg")
        nearest_zg = _safe_float(nearest_zg)
        targets.append(
            {
                "label": "中期目标（上方中枢上沿）",
                "price": round(nearest_zg, 2),
                "distance_pct": round((nearest_zg - current_price) / current_price, 4),
            }
        )
    elif len(up_bis) >= 2 and current_price > 0:
        hist_high = max(_safe_float(bi.get("y1")) for bi in up_bis)
        if hist_high > current_price:
            targets.append(
                {
                    "label": "中期目标（历史前高）",
                    "price": round(hist_high, 2),
                    "distance_pct": round((hist_high - current_price) / current_price, 4),
                }
            )

    if targets and stop_price > 0:
        risk = current_price - stop_price
        for target in targets:
            reward = target["price"] - current_price
            target["rr_ratio"] = round(reward / risk, 2) if risk > 0 else None

    return targets


def check_reward_ratio(
    entry_price: float,
    stop_price: float,
    target_price: float,
    min_ratio: float,
    is_open_target: bool = False,
) -> dict:
    """赔率门控，只输出检查结果，不执行交易动作。"""
    stop_dist = entry_price - stop_price if entry_price > stop_price > 0 else 0.0
    if stop_dist <= 0:
        return {
            "ratio": 0.0,
            "ok": False,
            "verdict": "止损价格异常，无法计算赔率",
            "is_open": is_open_target,
        }

    if is_open_target or target_price <= 0:
        return {
            "ratio": None,
            "ok": True,
            "verdict": "战法二目标开放（1:3+ 预期），赔率以结构为准",
            "is_open": True,
        }

    reward = target_price - entry_price
    ratio = round(reward / stop_dist, 2) if stop_dist > 0 else 0.0
    ok = ratio >= min_ratio
    if ok:
        verdict = f"赔率 1:{ratio:.1f} >= {min_ratio:.0f}:1"
    else:
        verdict = f"赔率 1:{ratio:.1f} 不足（目标要求 >= {min_ratio:.0f}:1），建议重新评估入场。仅供参考"
    return {"ratio": ratio, "ok": ok, "verdict": verdict, "is_open": False}


def plan_holding_targets(
    day: dict,
    current_price: float,
    strategy_type: str = "战法一",
) -> dict:
    """计算持仓模式的目标字段，保持战法一/战法二口径统一。"""
    is_strategy_two = strategy_type == "战法二"
    if is_strategy_two:
        return {
            "target_price_1": 0.0,
            "target_price_2": 0.0,
            "target_is_placeholder": True,
            "target_open": True,
            "target_label": "趋势进行中，无固定目标价——以日线顶背驰为出场信号",
            "target_1_reached": False,
            "target_2_reached": False,
        }

    day_zg = _safe_float(day.get("zg"))
    day_bis = day.get("bi_list", []) or day.get("bis", [])
    target_1 = find_structural_high_s1(day_bis, current_price, day_zg)
    if target_1 > 0:
        target_2 = round(target_1 * 1.05, 2)
        target_label = f"结构前高 {target_1:.2f}"
    else:
        target_1 = round(day_zg * 1.10, 2) if day_zg > 0 else 0.0
        target_2 = round(day_zg * 1.20, 2) if day_zg > 0 else 0.0
        target_label = "前高未检测到，使用估算"

    return {
        "target_price_1": target_1,
        "target_price_2": target_2,
        "target_is_placeholder": not bool(target_1 > 0),
        "target_open": False,
        "target_label": target_label,
        "target_1_reached": bool(current_price > 0 and target_1 > 0 and current_price >= target_1),
        "target_2_reached": bool(current_price > 0 and target_2 > 0 and current_price >= target_2),
    }


def find_structural_high_s1(day_bis: list, current_price: float, day_zg: float = 0.0) -> float:
    """查找战法一持仓目标：最近确认向下笔的起点前高。"""
    if not day_bis:
        return 0.0
    for bi in reversed(day_bis):
        is_up = bool(bi.get("is_up") or bi.get("isUp"))
        is_sure = bool(bi.get("is_sure") or bi.get("isSure"))
        if not is_up and is_sure:
            peak = bi.get("start_price") or bi.get("high") or bi.get("fx_high") or 0.0
            peak = _safe_float(peak)
            if peak > current_price > 0:
                return round(peak, 2)
    return 0.0


def _safe_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
