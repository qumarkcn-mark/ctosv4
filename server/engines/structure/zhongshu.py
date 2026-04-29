"""Zhongshu and trend-state facts derived from serialized structures."""


def deduce_state_from_structures(bis: list, zhongshus: list) -> tuple[str, dict, dict]:
    if not bis:
        return "UNKNOWN", {}, {}

    last_bi = bis[-1]
    recent_ex = {
        "current_price": last_bi.get("y1", 0),
        "support": min(last_bi.get("y0", 0), last_bi.get("y1", 0)),
        "pressure": max(last_bi.get("y0", 0), last_bi.get("y1", 0)),
    }
    if len(bis) >= 2:
        prev = bis[-2]
        recent_ex["support"] = min(
            recent_ex["support"],
            prev.get("y0", 0),
            prev.get("y1", 0),
        )
        recent_ex["pressure"] = max(
            recent_ex["pressure"],
            prev.get("y0", 0),
            prev.get("y1", 0),
        )

    if not zhongshus:
        return "TREND_EXTENDING", {}, recent_ex

    last_zs = zhongshus[-1]
    zg = last_zs.get("zg", 0)
    zd = last_zs.get("zd", 0)
    bi_is_up = bool(last_bi.get("is_up"))
    bi_end_price = last_bi.get("y1", 0)

    if bi_end_price > zg:
        state = "UPWARD_LEAVING" if bi_is_up else "THIRD_BUY_CONFIRMED"
    elif bi_end_price < zd:
        state = "THIRD_SELL_CONFIRMED" if bi_is_up else "DOWNWARD_LEAVING"
    else:
        state = "IN_CENTER_OSC"

    return state, last_zs, recent_ex


def classify_zoushi(zhongshus: list) -> dict:
    n_zs = len(zhongshus)
    if n_zs == 0:
        return {
            "type": "构建中",
            "zs_count": 0,
            "completion": "形成第一个中枢后可判定走势类型",
        }
    if n_zs == 1:
        zs = zhongshus[0]
        return {
            "type": "盘整",
            "zs_count": 1,
            "completion": f"突破ZG({zs.get('zg', 0):.2f})或跌破ZD({zs.get('zd', 0):.2f})后盘整结束",
        }

    up_count = 0
    down_count = 0
    for idx in range(1, n_zs):
        prev_zs = zhongshus[idx - 1]
        curr_zs = zhongshus[idx]
        if curr_zs.get("zd", 0) > prev_zs.get("zg", 0):
            up_count += 1
        elif curr_zs.get("zg", 0) < prev_zs.get("zd", 0):
            down_count += 1

    if up_count == n_zs - 1:
        return {
            "type": "上涨趋势",
            "zs_count": n_zs,
            "completion": "最后一段向上离开中枢出现顶背驰 → 趋势完成",
        }
    if down_count == n_zs - 1:
        return {
            "type": "下跌趋势",
            "zs_count": n_zs,
            "completion": "最后一段向下离开中枢出现底背驰 → 趋势完成",
        }

    last_zs = zhongshus[-1]
    return {
        "type": "盘整",
        "zs_count": n_zs,
        "completion": f"中枢间存在重叠，视为大级别盘整。突破ZG({last_zs.get('zg', 0):.2f})或跌破ZD({last_zs.get('zd', 0):.2f})后结束",
    }
