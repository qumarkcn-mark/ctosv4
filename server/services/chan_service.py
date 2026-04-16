"""CT-OS V4.5 — 忠于缠论原文的多级别矩阵分析服务

基于 chan_detail_service（官方 chan.py 引擎）的中枢和笔数据，
推导出每个级别的走势类型、完全分类和买卖点，供 TRadar 雷达和 AI 推演使用。

★ V4.5 核心变更（忠于缠论原文）：
  1. 走势类型只有三种：盘整、上涨趋势、下跌趋势
  2. 完全分类基于走势生命周期（延伸/完成），不基于价格位置
  3. 止损按买卖点类型区分（1买/2买/3买各不同）
  4. 区间套：递进缩小范围定位转折点
  5. 删除自创概念：FAKE_BREAK / CONFIRMED_BREAK / LIMBO
"""

import asyncio
import logging
from typing import Tuple, Optional

from server.services.chan_detail_service import get_chan_detail, _compute_macd

logger = logging.getLogger(__name__)

# ─── FSM 状态枚举（保持向后兼容） ───

class ChanState:
    UNKNOWN              = "UNKNOWN"
    IN_CENTER_OSC        = "IN_CENTER_OSC"        # 中枢内震荡
    UPWARD_LEAVING       = "UPWARD_LEAVING"        # 向上离开中枢
    DOWNWARD_LEAVING     = "DOWNWARD_LEAVING"      # 向下离开中枢
    THIRD_BUY_CONFIRMED  = "THIRD_BUY_CONFIRMED"   # 三买确认
    THIRD_SELL_CONFIRMED = "THIRD_SELL_CONFIRMED"   # 三卖确认
    TREND_EXTENDING      = "TREND_EXTENDING"        # 有笔但无中枢
    # 以下状态保留定义（向后兼容前端 STATE_CONFIG），但不再由引擎产出
    WAITING_FOR_PULLBACK = "WAITING_FOR_PULLBACK"
    LIMBO                = "LIMBO"
    FAKE_BREAK           = "FAKE_BREAK"
    SMALL_TO_BIG         = "SMALL_TO_BIG"
    CONFIRMED_BREAK      = "CONFIRMED_BREAK"


# ═══════════════════════════════════════════════════════════════
# 一、状态推导（简化版，只判断最基本的位置关系）
# ═══════════════════════════════════════════════════════════════

def _deduce_state_from_structures(bis: list, zhongshus: list) -> Tuple[str, dict, dict]:
    """从笔列表和中枢列表推导走势状态。

    简化逻辑（忠于原文）：
      - 无笔 → UNKNOWN
      - 有笔无中枢 → TREND_EXTENDING
      - 价格 > ZG + 向上笔 → UPWARD_LEAVING
      - 价格 > ZG + 向下笔（回踩不破ZG）→ THIRD_BUY_CONFIRMED
      - 价格 < ZD + 向下笔 → DOWNWARD_LEAVING
      - 价格 < ZD + 向上笔（反弹不破ZD）→ THIRD_SELL_CONFIRMED
      - 价格在中枢内 → IN_CENTER_OSC
    """
    if not bis:
        return ChanState.UNKNOWN, {}, {}

    # 算一下近期极值
    last_bi = bis[-1]
    recent_ex = {
        "current_price": last_bi["y1"],
        "support": min(last_bi["y0"], last_bi["y1"]),
        "pressure": max(last_bi["y0"], last_bi["y1"])
    }
    if len(bis) >= 2:
        prev = bis[-2]
        recent_ex["support"] = min(recent_ex["support"], prev["y0"], prev["y1"])
        recent_ex["pressure"] = max(recent_ex["pressure"], prev["y0"], prev["y1"])

    if not zhongshus:
        return ChanState.TREND_EXTENDING, {}, recent_ex

    last_zs = zhongshus[-1]
    zg = last_zs["zg"]
    zd = last_zs["zd"]

    bi_is_up = last_bi["is_up"]
    bi_end_price = last_bi["y1"]

    # 判断当前价格相对中枢的位置
    if bi_end_price > zg:
        if bi_is_up:
            state = ChanState.UPWARD_LEAVING
        else:
            # 向下笔但终点仍在ZG之上 → 三买确认（回踩不破ZG）
            state = ChanState.THIRD_BUY_CONFIRMED
    elif bi_end_price < zd:
        if bi_is_up:
            # 向上笔但终点仍在ZD之下 → 三卖确认（反弹不破ZD）
            state = ChanState.THIRD_SELL_CONFIRMED
        else:
            state = ChanState.DOWNWARD_LEAVING
    else:
        state = ChanState.IN_CENTER_OSC

    return state, last_zs, recent_ex


# ═══════════════════════════════════════════════════════════════
# 二、走势类型分类（缠论原文：只有盘整、上涨趋势、下跌趋势）
# ═══════════════════════════════════════════════════════════════

def _classify_zoushi(zhongshus: list, bis: list) -> dict:
    """走势类型完全分类（严格按缠论原文）。

    - 无中枢 → 构建中（走势尚未形成中枢，无法判定类型）
    - 1个中枢 → 盘整
    - ≥2个中枢且全部上移不重叠 → 上涨趋势
    - ≥2个中枢且全部下移不重叠 → 下跌趋势
    - 中枢间有重叠 → 视为大级别盘整
    """
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
            "completion": f"突破ZG({zs['zg']:.2f})或跌破ZD({zs['zd']:.2f})后盘整结束",
        }

    # ≥2个中枢，逐对检查是否同向不重叠
    up_count, down_count = 0, 0
    for i in range(1, n_zs):
        prev_zs, curr_zs = zhongshus[i - 1], zhongshus[i]
        if curr_zs["zd"] > prev_zs["zg"]:    # 后ZD > 前ZG = 上移不重叠
            up_count += 1
        elif curr_zs["zg"] < prev_zs["zd"]:  # 后ZG < 前ZD = 下移不重叠
            down_count += 1

    if up_count == n_zs - 1:
        return {
            "type": "上涨趋势",
            "zs_count": n_zs,
            "completion": "最后一段向上离开中枢出现顶背驰 → 趋势完成",
        }
    elif down_count == n_zs - 1:
        return {
            "type": "下跌趋势",
            "zs_count": n_zs,
            "completion": "最后一段向下离开中枢出现底背驰 → 趋势完成",
        }
    else:
        # 中枢间有重叠 → 视为大级别盘整
        last_zs = zhongshus[-1]
        return {
            "type": "盘整",
            "zs_count": n_zs,
            "completion": f"中枢间存在重叠，视为大级别盘整。突破ZG({last_zs['zg']:.2f})或跌破ZD({last_zs['zd']:.2f})后结束",
        }


# ═══════════════════════════════════════════════════════════════
# 三、完全分类引擎（基于走势生命周期）
# ═══════════════════════════════════════════════════════════════

def _stop_for_1buy(bis: list) -> Optional[float]:
    """1买止损 = 背驰点最低价（跌破则背驰判断失败）"""
    if not bis:
        return None
    down_bis = [b for b in bis[-6:] if not b["is_up"]]
    if down_bis:
        return min(b["y1"] for b in down_bis)
    return None


def _stop_for_2buy(bis: list) -> Optional[float]:
    """2买止损 = 2买那笔的最低点（跌破则创新低，2买失败）"""
    if not bis:
        return None
    last_bi = bis[-1]
    return min(last_bi["y0"], last_bi["y1"])


def _stop_for_3buy(bis: list, zg: float) -> Optional[float]:
    """3买止损 = 回踩段最低点（跌破则三买失败，回踩进入中枢）"""
    if not bis:
        return zg
    # 找最近的回踩笔（向下笔且终点在ZG上方）
    for b in reversed(bis[-4:]):
        if not b["is_up"] and b["y1"] > zg:
            return min(b["y0"], b["y1"])
    return zg  # fallback


def _exhaustive_classification(zoushi: dict, last_zs: dict,
                                div_info: Optional[dict], bis: list) -> list:
    """完全分类：基于当前走势类型的生命周期，穷举所有可能后续。

    缠论原文：
      盘整 → 向上突破 / 向下突破 / 继续盘整
      上涨趋势 → 趋势延伸(无背驰) / 趋势完成(顶背驰)
      下跌趋势 → 趋势延伸(无背驰) / 趋势完成(底背驰)
    """
    zoushi_type = zoushi["type"]
    classifications = []

    if zoushi_type == "盘整":
        if not last_zs:
            return classifications
        zg, zd = last_zs["zg"], last_zs["zd"]
        sl_3buy = _stop_for_3buy(bis, zg)
        classifications = [
            {
                "id": "A", "name": "向上突破",
                "condition": f"次级别走势向上离开中枢，回踩不破ZG({zg:.2f})",
                "action": "三买确认后入场",
                "stopLoss": round(sl_3buy, 2) if sl_3buy else None,
                "stopReason": "回踩段最低点（破则三买失败）",
            },
            {
                "id": "B", "name": "继续盘整",
                "condition": f"价格在ZD({zd:.2f})-ZG({zg:.2f})之间运行",
                "action": "观望，等方向选择",
                "stopLoss": round(zd, 2),
                "stopReason": "中枢下沿",
            },
            {
                "id": "C", "name": "向下突破",
                "condition": f"次级别走势向下离开中枢，反弹不破ZD({zd:.2f})",
                "action": "三卖确认后离场",
                "stopLoss": None,
                "stopReason": "已离场",
            },
        ]

    elif zoushi_type == "上涨趋势":
        if not last_zs:
            return classifications
        zg, zd = last_zs["zg"], last_zs["zd"]
        classifications = [
            {
                "id": "A", "name": "趋势延伸",
                "condition": f"向上离开中枢且无顶背驰迹象",
                "action": "持有",
                "stopLoss": round(zd, 2),
                "stopReason": "当前中枢下沿（破则趋势结构被破坏）",
            },
            {
                "id": "B", "name": "趋势完成",
                "condition": "最后一段向上离开中枢出现顶背驰"
                             + (f"（当前MACD面积比:{div_info['ratio']:.0%}）" if div_info else ""),
                "action": "减仓/离场",
                "stopLoss": None,
                "stopReason": "背驰确认即离场",
            },
        ]
        # 如果有背驰迹象，增加转折后的分类
        if div_info:
            classifications.append({
                "id": "C", "name": "转折后等2买",
                "condition": "顶背驰确认 → 第一次回踩不创新低 = 2买",
                "action": "等2买确认后重新入场",
                "stopLoss": None,
                "stopReason": "2买确认后以2买低点为止损",
            })

    elif zoushi_type == "下跌趋势":
        if not last_zs:
            return classifications
        zg, zd = last_zs["zg"], last_zs["zd"]
        sl_1buy = _stop_for_1buy(bis)
        classifications = [
            {
                "id": "A", "name": "趋势延伸",
                "condition": "向下离开中枢且无底背驰迹象",
                "action": "空仓等待",
                "stopLoss": None,
                "stopReason": None,
            },
            {
                "id": "B", "name": "趋势完成(1买)",
                "condition": "最后一段向下离开中枢出现底背驰"
                             + (f"（当前MACD面积比:{div_info['ratio']:.0%}）" if div_info else ""),
                "action": "关注1买，轻仓试探",
                "stopLoss": round(sl_1buy, 2) if sl_1buy else None,
                "stopReason": "背驰点最低价（破则背驰判断失败）",
            },
        ]
        if div_info:
            sl_2buy = _stop_for_2buy(bis)
            classifications.append({
                "id": "C", "name": "1买后等2买",
                "condition": "底背驰确认后第一次回踩不创新低 = 2买",
                "action": "2买确认后入场",
                "stopLoss": round(sl_2buy, 2) if sl_2buy else None,
                "stopReason": "2买那笔最低点（破则创新低，2买失败）",
            })

    elif zoushi_type == "构建中":
        classifications = [
            {
                "id": "A", "name": "形成中枢",
                "condition": "三笔重叠形成中枢 → 可判定走势类型",
                "action": "等待中枢形成后再分析",
                "stopLoss": None,
                "stopReason": None,
            },
        ]

    return classifications


# ═══════════════════════════════════════════════════════════════
# 四、区间套（递进缩小范围定位转折点）
# ═══════════════════════════════════════════════════════════════

def _check_interval_nesting(levels_data: dict) -> Optional[dict]:
    """区间套检测：大级别背驰 → 次级别确认 → 小级别精确定位。

    步骤（缠论原文）：
      1. 在大级别（日线）的 patterns 中找到背驰信号
      2. 检查次级别（30m）在同方向上是否也有背驰
      3. 检查小级别（5m）是否也在走最后一段

    各级别的背驰必须嵌套在同一走势方向内。
    """
    nesting = []
    nesting_direction = None  # "top" 或 "bottom"

    for level_key in ["day", "m30", "m5"]:
        data = levels_data.get(level_key)
        if not data:
            break

        patterns_str = " ".join(data.get("patterns", []))

        has_top_div = "顶背驰" in patterns_str
        has_bottom_div = "底背驰" in patterns_str

        if not nesting:
            # 第一级：确定背驰方向
            if has_top_div:
                nesting_direction = "top"
                nesting.append({"level": level_key, "type": "顶背驰"})
            elif has_bottom_div:
                nesting_direction = "bottom"
                nesting.append({"level": level_key, "type": "底背驰"})
            else:
                break  # 大级别无背驰，不构成区间套
        else:
            # 后续级别：必须与大级别同方向
            if nesting_direction == "top" and has_top_div:
                nesting.append({"level": level_key, "type": "顶背驰"})
            elif nesting_direction == "bottom" and has_bottom_div:
                nesting.append({"level": level_key, "type": "底背驰"})
            else:
                break  # 方向不一致或无背驰，链条断了

    if len(nesting) >= 3:
        return {"depth": 3, "label": "三级区间套确认", "direction": nesting_direction, "levels": nesting}
    elif len(nesting) >= 2:
        return {"depth": 2, "label": "两级区间套", "direction": nesting_direction, "levels": nesting}
    elif len(nesting) >= 1:
        return {"depth": 1, "label": "单级别背驰", "direction": nesting_direction, "levels": nesting}
    return None


# ═══════════════════════════════════════════════════════════════
# 五、背驰检测
# ═══════════════════════════════════════════════════════════════

def _get_divergence(bis: list, is_up: bool) -> Optional[dict]:
    """检测最近同向笔的背驰关系。
    返回 {type, ratio, severity} 或 None。
    """
    same_dir = [b for b in bis if b["is_up"] == is_up]
    if len(same_dir) < 2:
        return None

    prev_bi = same_dir[-2]
    curr_bi = same_dir[-1]

    # 创新高/新低 是背驰的物理前提
    if is_up and curr_bi["y1"] < prev_bi["y1"]:
        return None
    if not is_up and curr_bi["y1"] > prev_bi["y1"]:
        return None

    prev_area = prev_bi.get("momentum", {}).get("area", 0)
    curr_area = curr_bi.get("momentum", {}).get("area", 0)

    if prev_area <= 0 or curr_area <= 0:
        return None

    ratio = curr_area / prev_area
    if ratio >= 0.7:
        return None  # 面积未缩减 30% 以上，不构成背驰

    severity = "高危" if ratio < 0.4 else "中等" if ratio < 0.55 else "轻微"
    return {"type": "顶背驰" if is_up else "底背驰", "ratio": ratio, "severity": severity}


# ═══════════════════════════════════════════════════════════════
# 六、形态提取（patterns — 向后兼容保留）
# ═══════════════════════════════════════════════════════════════

def _extract_patterns(bis: list, zhongshus: list, state: str,
                      zoushi: dict) -> list[str]:
    """从笔和中枢结构中提取缠论形态信号。

    V4.5: 删除 in_limbo 相关逻辑，小转大从状态改为 pattern。
    """
    if not bis:
        return []

    patterns = []
    last_bi = bis[-1]
    is_up = last_bi["is_up"]
    price = last_bi["y1"]

    # ── 走势类型标签 ──
    zt = zoushi.get("type", "")
    if zt:
        patterns.append(f"走势:{zt}")

    # ── 基本走势方向 ──
    dir_cn = "向上" if is_up else "向下"
    patterns.append(f"{dir_cn}笔运行中")

    # ── 背驰检测 ──
    div = _get_divergence(bis, is_up)

    if not zhongshus:
        if div:
            patterns.append(f"⚠️ {div['type']}({div['severity']})")
        return patterns

    last_zs = zhongshus[-1]
    zg = last_zs["zg"]
    zd = last_zs["zd"]

    # ── 中枢位置关系 ──
    n_zs = len(zhongshus)

    # 多中枢趋势判定
    is_uptrend = False
    is_downtrend = False
    if n_zs >= 2:
        prev_zs = zhongshus[-2]
        if last_zs["zd"] > prev_zs["zg"]:
            is_uptrend = True
        elif last_zs["zg"] < prev_zs["zd"]:
            is_downtrend = True

    # ═══ 一、离开中枢后的情境分析 ═══

    if price > zg:
        if is_up:
            if div:
                patterns.append(f"⚠️ {div['type']}({div['severity']})")
                if is_uptrend:
                    patterns.append("🔴 趋势顶背驰 → 1卖风险")
                else:
                    patterns.append("🟡 盘整顶背驰 → 可能回中枢")
            else:
                patterns.append("向上离开中枢")
                if is_uptrend:
                    patterns.append(f"📈 上升趋势({n_zs}个中枢)")
        else:
            pullback_low = last_bi["y1"]
            if pullback_low > zg:
                patterns.append("🟢 三买确认(回踩不破ZG)")
                if div:
                    patterns.append(f"⚠️ 三买后{div['type']}→ 谨防3买转1卖")
            elif pullback_low > zd:
                patterns.append("三买失败(回落入中枢)")

    elif price < zd:
        if not is_up:
            if div:
                patterns.append(f"✅ {div['type']}({div['severity']})")
                if is_downtrend:
                    patterns.append("🟢 趋势底背驰 → 1买机会")
                else:
                    patterns.append("🟡 盘整底背驰 → 可能回中枢")
            else:
                patterns.append("向下离开中枢")
                if is_downtrend:
                    patterns.append(f"📉 下降趋势({n_zs}个中枢)")
        else:
            bounce_high = last_bi["y1"]
            if bounce_high < zd:
                patterns.append("🔴 三卖确认(反弹不破ZD)")
                if div:
                    patterns.append(f"✅ 三卖后{div['type']}→ 关注3卖转1买")
            elif bounce_high < zg:
                patterns.append("三卖失败(反弹入中枢)")

    else:
        # ═══ 二、中枢内部震荡 ═══
        if div:
            patterns.append(f"⚠️ 中枢内{div['type']}({div['severity']})")
        zs_range = zg - zd if zg > zd else 1
        if is_up and (price - zd) / zs_range > 0.75:
            patterns.append("接近中枢上沿(ZG)")
        elif not is_up and (zg - price) / zs_range > 0.75:
            patterns.append("接近中枢下沿(ZD)")

    # ═══ 三、二买/二卖检测 ═══

    if len(bis) >= 4 and n_zs >= 1:
        recent_4 = bis[-4:]
        highs = [max(b["y0"], b["y1"]) for b in recent_4]
        lows = [min(b["y0"], b["y1"]) for b in recent_4]

        if not is_up and len(lows) >= 4:
            prev_down_low = min(lows[0], lows[1])
            curr_low = price
            if prev_down_low < zd and curr_low > prev_down_low and curr_low <= zg:
                patterns.append("🟢 疑似二买(不创新低)")

        if is_up and len(highs) >= 4:
            prev_up_high = max(highs[0], highs[1])
            curr_high = price
            if prev_up_high > zg and curr_high < prev_up_high and curr_high >= zd:
                patterns.append("🔴 疑似二卖(不创新高)")

    # ═══ 四、小转大检测（缠论原文概念，放在 patterns 而非 state）═══

    if state in (ChanState.UPWARD_LEAVING, ChanState.DOWNWARD_LEAVING) and len(bis) >= 3:
        curr_bi = bis[-1]
        curr_area = curr_bi.get("momentum", {}).get("area", 0)
        prev_same_dir = None
        for b in reversed(bis[:-1]):
            if b["is_up"] == curr_bi["is_up"]:
                prev_same_dir = b
                break
        if prev_same_dir:
            prev_area = prev_same_dir.get("momentum", {}).get("area", 0)
            if prev_area > 0 and curr_area > prev_area * 2:
                patterns.append("🔮 小转大(次级别力度远超前段)")

    # ═══ 五、中枢演化 ═══

    if n_zs >= 2:
        prev_zs = zhongshus[-2]
        if last_zs["zg"] > prev_zs["zg"] and last_zs["zd"] < prev_zs["zd"]:
            patterns.append("中枢扩展(区间扩大)")
        elif is_uptrend:
            patterns.append(f"📈 上升趋势({n_zs}个中枢)")
        elif is_downtrend:
            patterns.append(f"📉 下降趋势({n_zs}个中枢)")

    return patterns


# ─── 频率映射 ───
_LEVEL_TO_FREQ = {
    "day": "day",
    "m60": "60",
    "m30": "30",
    "m15": "15",
    "m5":  "5",
    "week": "week",
}


# ═══════════════════════════════════════════════════════════════
# 七、单级别分析
# ═══════════════════════════════════════════════════════════════

async def _analyze_single_level(symbol: str, level: str) -> dict:
    """单级别分析：调用 chan_detail_service 获取结构，再推导状态、走势类型、完全分类。"""

    freq = _LEVEL_TO_FREQ.get(level, level)
    count = 500  # 与前端 K 线请求条数一致，避免蝴蝶效应

    _MIN_KLINES_FOR_ANALYSIS = 120

    try:
        detail = await get_chan_detail(symbol, freq, count)
    except Exception as e:
        logger.warning("chan_detail 解析失败 %s/%s: %s", symbol, level, e)
        return {"level": level, "state": ChanState.UNKNOWN, "zd": 0, "zg": 0,
                "patterns": [], "zoushi_type": {"type": "数据不足", "zs_count": 0, "completion": ""},
                "classifications": [], "data_status": "missing", "kline_count": 0}

    if detail.get("error"):
        return {"level": level, "state": ChanState.UNKNOWN, "zd": 0, "zg": 0,
                "patterns": [], "zoushi_type": {"type": "数据不足", "zs_count": 0, "completion": ""},
                "classifications": [], "data_status": "missing", "kline_count": 0,
                "error_msg": detail["error"]}

    bis = detail.get("bis", [])
    zhongshus = detail.get("bi_zhongshus", [])
    klines = detail.get("klines", [])
    kline_count = len(klines)

    # 数据状态诊断
    if kline_count == 0:
        data_status = "missing"
    elif kline_count < _MIN_KLINES_FOR_ANALYSIS:
        data_status = "insufficient"
    else:
        data_status = "ok"

    # ── 基础状态推导 ──
    state, last_zs, recent_ex = _deduce_state_from_structures(bis, zhongshus)

    # 真实价格
    real_price = klines[-1]["close"] if klines else (recent_ex.get("current_price", 0) if recent_ex else 0)
    if recent_ex:
        recent_ex["current_price"] = real_price

    zd = last_zs.get("zd", 0) if last_zs else 0
    zg = last_zs.get("zg", 0) if last_zs else 0

    # ── 走势类型分类（新增）──
    zoushi = _classify_zoushi(zhongshus, bis)

    # ── 背驰检测 ──
    last_bi_is_up = bis[-1]["is_up"] if bis else True
    div_info = _get_divergence(bis, last_bi_is_up) if bis else None

    # ── 完全分类（新增）──
    classifications = _exhaustive_classification(zoushi, last_zs, div_info, bis)

    # ── 形态提取 ──
    patterns = _extract_patterns(bis, zhongshus, state, zoushi)

    # 最后一笔方向
    last_bi_dir = "up" if (bis and bis[-1].get("is_up")) else "down" if bis else "unknown"

    # ── 历史新高检测 ──
    all_time_high = max((k.get("high", 0) for k in klines), default=0) if klines else 0
    recent_high = max((k.get("high", 0) for k in klines[-20:]), default=0) if klines else 0
    is_near_historical_high = (recent_high >= all_time_high * 0.95) if all_time_high > 0 else False

    # ── 分型检测（所有级别统一逻辑，不再仅限周线）──
    has_bottom_fractal = False
    has_top_fractal = False
    if len(bis) >= 2 and len(klines) >= 3:
        last_bi = bis[-1]
        if not last_bi.get("is_up"):
            lows = [k["low"] for k in klines[-3:]]
            if lows[1] <= lows[0] and lows[1] <= lows[2]:
                has_bottom_fractal = True
        elif last_bi.get("is_up"):
            highs = [k["high"] for k in klines[-3:]]
            if highs[1] >= highs[0] and highs[1] >= highs[2]:
                has_top_fractal = True

    return {
        "level": level,
        "state": state,
        "zd": zd,
        "zg": zg,
        "patterns": patterns,
        "zoushi_type": zoushi,           # 新增：走势类型
        "classifications": classifications,  # 新增：完全分类
        "ex_support": recent_ex.get("support", 0) if recent_ex else 0,
        "ex_pressure": recent_ex.get("pressure", 0) if recent_ex else 0,
        "price": real_price,
        "bi_count": len(bis),
        "zs_count": len(zhongshus),
        "last_bi_dir": last_bi_dir,
        "is_near_historical_high": is_near_historical_high,
        "has_bottom_fractal": has_bottom_fractal,
        "has_top_fractal": has_top_fractal,
        "data_status": data_status,
        "kline_count": kline_count,
        # 向后兼容字段（保留但不再产出新值）
        "in_limbo": False,
        "zs_distance_pct": 0.0,
    }


# ═══════════════════════════════════════════════════════════════
# 八、跨级别矩阵分析
# ═══════════════════════════════════════════════════════════════

async def analyze_matrix_state(symbol: str) -> dict:
    """双轴跨级别融合计算 + 区间套检测。

    体系 A: 日线 + 30分钟 + 5分钟  (短线维度)
    体系 B: 日线 + 60分钟 + 15分钟  (波段维度)
    """
    levels = ["day", "m60", "m30", "m15", "m5", "week"]
    tasks = [_analyze_single_level(symbol, lvl) for lvl in levels]
    results = await asyncio.gather(*tasks)

    data_map = {r["level"]: r for r in results}

    # ── 区间套检测（新增）──
    # 体系A: day → m30 → m5
    nesting_a = _check_interval_nesting({
        "day": data_map.get("day", {}),
        "m30": data_map.get("m30", {}),
        "m5":  data_map.get("m5", {}),
    })
    # 体系B: day → m60 → m15
    nesting_b = _check_interval_nesting({
        "day": data_map.get("day", {}),
        "m30": data_map.get("m60", {}),  # reuse m30 key for nesting function
        "m5":  data_map.get("m15", {}),
    })

    matrix_a = [data_map["day"], data_map["m30"], data_map["m5"]]
    matrix_b = [data_map["day"], data_map["m60"], data_map["m15"]]
    week_data = data_map.get("week")

    return {
        "symbol": symbol,
        "matrix_a": matrix_a,
        "matrix_b": matrix_b,
        "week": week_data,
        "interval_nesting_a": nesting_a,  # 新增
        "interval_nesting_b": nesting_b,  # 新增
    }


# ─── 向后兼容：保留 analyze_stock_chan_state 供 price_monitor 等使用 ───

async def analyze_stock_chan_state(symbol: str):
    """单级别日线状态（供旧 API 兼容）。返回 (state_str, zs_dict)。"""
    result = await _analyze_single_level(symbol, "day")
    last_zs = {"ZD": result["zd"], "ZG": result["zg"]} if result["zd"] > 0 else None
    return result["state"], last_zs
