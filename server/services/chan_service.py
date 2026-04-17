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

    # ── 最近笔的端点数据（供前瞻推演使用）──
    detail_bis = []
    for b in bis[-6:]:
        detail_bis.append({
            "y0": b["y0"],
            "y1": b["y1"],
            "is_up": b["is_up"],
            "start_date": b.get("x0", ""),
            "end_date":   b.get("x1", ""),
        })

    # ── 最近K线片段（供走势叙述使用）──
    recent_klines = [
        {"date": k["time"], "close": k["close"], "high": k["high"], "low": k["low"]}
        for k in klines[-5:]
    ] if klines else []

    return {
        "level": level,
        "state": state,
        "zd": zd,
        "zg": zg,
        "patterns": patterns,
        "zoushi_type": zoushi,
        "classifications": classifications,
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
        "detail_bis": detail_bis,
        "recent_klines": recent_klines,
        # 向后兼容字段
        "in_limbo": False,
        "zs_distance_pct": 0.0,
    }


# ═══════════════════════════════════════════════════════════════
# 八、前瞻推演（三层叙述：最近走势 + 当下定位 + 今日完全分类）
# ═══════════════════════════════════════════════════════════════

_FORWARD_LEVEL_NAMES = {
    "day": "日线", "m30": "30分", "m5": "5分",
    "m60": "60分", "m15": "15分", "week": "周线",
}


def _describe_recent_action(l2: dict) -> str:
    """描述次级别最近两笔发生了什么。"""
    bis = l2.get("detail_bis", [])
    klines = l2.get("recent_klines", [])
    name = _FORWARD_LEVEL_NAMES.get(l2.get("level", ""), "次级别")
    price = l2.get("price", 0)

    if len(bis) < 1:
        return "数据不足，无法描述近期走势"

    parts = []
    last = bis[-1]
    d = "↑" if last["is_up"] else "↓"
    sd = str(last.get("start_date", ""))[:10]
    ed = str(last.get("end_date", ""))[:10]
    parts.append(f"{name}最后一笔（{d}）{last['y0']:.2f}→{last['y1']:.2f}（{sd}→{ed}）")

    # 判断当前价格是否已超出最后笔的终点，说明新笔正在形成
    if last["is_up"] and price > last["y1"]:
        parts.append(
            f"此后价格继续上行，新的上行笔正在形成（当前 {price:.2f}，"
            f"前笔高点 {last['y1']:.2f} 已被超越）"
        )
    elif not last["is_up"] and price < last["y1"]:
        parts.append(
            f"此后继续下行，新的下行笔正在形成（当前 {price:.2f}，"
            f"前笔低点 {last['y1']:.2f} 已被突破）"
        )
    elif not last["is_up"] and price > last["y1"]:
        # 下行笔结束后反弹——新上行笔可能已开始
        up_delta = price - last["y1"]
        parts.append(
            f"触底 {last['y1']:.2f} 后反弹，当前 {price:.2f}"
            f"（已反弹 {up_delta:.2f} 元），新上行笔进行中"
        )
    elif last["is_up"] and price < last["y1"]:
        # 上行笔结束后回落——新下行笔可能已开始
        dn_delta = last["y1"] - price
        parts.append(
            f"见顶 {last['y1']:.2f} 后回落，当前 {price:.2f}"
            f"（已回落 {dn_delta:.2f} 元），新下行笔进行中"
        )

    return "；".join(parts)


def _describe_current_position(l1: dict, l2: dict) -> dict:
    """精确描述当前价格在多级别结构中的位置。"""
    price  = l2.get("price", 0)
    l2_zg  = l2.get("zg", 0)
    l2_zd  = l2.get("zd", 0)
    l1_zg  = l1.get("zg", 0)
    l1_zd  = l1.get("zd", 0)
    l1_sup = l1.get("ex_support", 0)  # 日线近期笔的低点（更真实的止损锚）
    l2_dir = l2.get("last_bi_dir", "unknown")
    l2_name = _FORWARD_LEVEL_NAMES.get(l2.get("level", ""), "次级别")
    l1_name = _FORWARD_LEVEL_NAMES.get(l1.get("level", ""), "日线")

    # ── 日线有效止损：主升浪底线 = l1_zd（日线结构致命位置），不是 l1_zg（已穿越的台阶）──
    if price > l1_zg and l1_zg > 0:
        effective_day_stop = l1_zd if l1_zd > 0 else l1_sup
        day_stop_src = f"日线ZD={effective_day_stop:.2f}（主升结构破坏红线）"
    elif l1_sup > 0 and l1_sup > l1_zd:
        effective_day_stop = l1_sup
        day_stop_src = f"日线近期笔低点 {effective_day_stop:.2f}"
    elif l1_zd > 0:
        effective_day_stop = l1_zd
        day_stop_src = f"日线ZD {effective_day_stop:.2f}"
    else:
        effective_day_stop = l1_sup
        day_stop_src = f"日线近期支撑 {effective_day_stop:.2f}"

    dir_cn = "↑上行" if l2_dir == "up" else ("↓下行" if l2_dir == "down" else "")

    if l2_zg > 0 and price > l2_zg:
        pct = (price - l2_zg) / l2_zg * 100
        rel = f"在{l2_name}中枢（ZD={l2_zd:.2f} / ZG={l2_zg:.2f}）上方 +{pct:.1f}%"
    elif l2_zd > 0 and price < l2_zd:
        pct = (l2_zd - price) / l2_zd * 100
        rel = f"在{l2_name}中枢（ZD={l2_zd:.2f} / ZG={l2_zg:.2f}）下方 -{pct:.1f}%"
    elif l2_zg > 0 and l2_zd > 0:
        pct = (price - l2_zd) / max(l2_zg - l2_zd, 0.01) * 100
        rel = f"在{l2_name}中枢内部（ZD={l2_zd:.2f} / ZG={l2_zg:.2f}，位置 {pct:.0f}%）"
    else:
        rel = f"{l2_name}中枢数据不足"

    # 日线背景描述（用有效ZD/ZG，若ZD很旧则只描述笔支撑）
    if l1_zg > 0 and l1_zd > 0 and (price - l1_zd) / price <= 0.40:
        if l1_zd < price < l1_zg:
            day_rel = f"{l1_name}中枢内（ZD={l1_zd:.2f} / ZG={l1_zg:.2f}）"
        elif price >= l1_zg:
            day_rel = f"在{l1_name}中枢上方（ZG={l1_zg:.2f}），趋势延伸区"
        else:
            day_rel = f"在{l1_name}中枢下方（ZD={l1_zd:.2f}），结构偏弱"
    else:
        # ZD偏旧，改为描述笔支撑
        day_rel = f"{l1_name}近期笔支撑 {effective_day_stop:.2f}（缠论止损锚）"

    return {
        "summary": f"当前价 {price:.2f}，{l2_name}笔{dir_cn}进行中，{rel}",
        "day_context": day_rel,
        "stop_loss": round(effective_day_stop, 2) if effective_day_stop > 0 else 0,
    }


def _build_forward_classes(l1: dict, l2: dict, l3: Optional[dict] = None) -> list:
    """生成今日完全分类：穷举当前笔可能的后续，每条含条件/结构意义/操作/止损。

    l1 = 日线, l2 = 次级别（30分/60分）, l3 = 小级别（5分/15分，可选）
    主升浪场景下：甲情形止损优先使用 l3_zd（小级别中枢ZD），远比 l2_zd 更有效。
    """
    price    = l2.get("price", 0)
    l2_zg    = l2.get("zg", 0)
    l2_zd    = l2.get("zd", 0)
    l2_press = l2.get("ex_pressure", 0)  # 近期阻力（前笔高点）
    l2_supp  = l2.get("ex_support", 0)   # 近期支撑（前笔低点）
    l2_dir   = l2.get("last_bi_dir", "unknown")
    l1_zd    = l1.get("zd", 0)
    l1_zg    = l1.get("zg", 0)
    l1_sup   = l1.get("ex_support", 0)   # 日线近期笔低点
    l2_name  = _FORWARD_LEVEL_NAMES.get(l2.get("level", ""), "次级别")

    # ── 5分钟小级别（l3）──
    l3       = l3 or {}
    l3_zg    = l3.get("zg", 0)
    l3_zd    = l3.get("zd", 0)
    l3_name  = _FORWARD_LEVEL_NAMES.get(l3.get("level", ""), "5分")

    # ── 日线有效止损：主升浪底线 = l1_zd（日线结构致命位置），而非 l1_zg（已穿越的台阶）──
    if price > l1_zg and l1_zg > 0:
        # 主升窗口：日线最宽止损 = l1_zd（不是 l1_zg，l1_zg 是躨过的起跳台）
        day_stop = l1_zd if l1_zd > 0 else l1_sup
        day_stop_label = f"日线ZD={day_stop:.2f}，主升结构破坏红线"
    elif l1_sup > 0 and l1_sup > l1_zd:
        day_stop = l1_sup
        day_stop_label = f"日线近期笔低点 {day_stop:.2f}"
    elif l1_zd > 0:
        day_stop = l1_zd
        day_stop_label = f"日线ZD={day_stop:.2f}"
    else:
        day_stop = l1_sup
        day_stop_label = f"日线近期支撑 {day_stop:.2f}"

    # ── 次级别有效止损：主升区保守使用 ZG，或近期低点 ──
    if price > l2_zg and l2_zg > 0:
        effective_stop = max(l2_supp, l2_zg)
        stop_src = (
            f"前笔低点 {effective_stop:.2f}" if l2_supp > l2_zg else f"{l2_name}ZG={effective_stop:.2f}"
        )
    else:
        effective_stop = l2_supp if l2_supp > 0 and l2_supp > l2_zd else l2_zd
        stop_src = (
            f"前笔低点（三买回踩底）{effective_stop:.2f}"
            if l2_supp > 0 and l2_supp > l2_zd
            else f"{l2_name}ZD={effective_stop:.2f}"
        )

    # ── 主升浪甲情形：优先用 l3（5分钟）ZD 作为锁利止损，远比 l2 更贴近当前结构 ──
    if price > l2_zg and l3_zd > 0 and l3_zd > l2_zg:
        tight_stop = l3_zd
        tight_stop_label = f"{l3_name}中枢ZD={tight_stop:.2f}（锁利止损）"
    else:
        tight_stop = effective_stop
        tight_stop_label = stop_src

    classes = []

    # ── 当前笔向上，价格在ZG上方（已脱离中枢，上行笔进行中）──
    if l2_dir == "up" and l2_zg > 0 and price > l2_zg:
        prev_high = l2_press if l2_press > price else price  # 前笔/近期高点

        classes = [
            {
                "id": "甲",
                "condition": f"开盘延续，突破前高 {prev_high:.2f}",
                "meaning": (
                    f"{l2_name}主升趋势延续；"
                    f"小级别支撑防线抬升至 {tight_stop:.2f}"
                ),
                "action": (
                    f"持仓。缩量回踩守住 {tight_stop:.2f}（{tight_stop_label}）→ 可加仓；"
                    f"跌破 {tight_stop:.2f} 先减半锁利"
                ),
                "stop_loss": round(tight_stop, 2),
                "stop_reason": tight_stop_label + "，跌破=短线止损",

                "next_watch": (
                    f"{l3_name} ZD={l3_zd:.2f} 跌破先减半仓；{l2_name} ZG={l2_zg:.2f} 跌破清仓"
                    if l3_zd > l2_zg else (
                        f"沿着多头通道跟随，不测顶部"
                        if price > l1_zg else (
                            f"回踩守住 {tight_stop:.2f} → 继续持；日线目标 ZG={l1_zg:.2f}"
                            if l1_zg > 0 else f"回踩守住 {tight_stop:.2f} → 继续持"
                        )
                    )
                ),
            },
            {
                "id": "乙",
                "condition": f"冲高 {prev_high-4:.0f}~{prev_high:.2f} 后出现顶分型",
                "meaning": (
                    f"当前笔与前笔形成双顶，{l2_name}开始在 "
                    f"{l2_zg:.2f}~{prev_high:.2f} 构建新中枢"
                ),
                "action": (
                    f"减轻仓位，等新中枢 ZG 形成后回踩确认 → 再入"
                ),
                "stop_loss": round(l2_zd, 2),
                "stop_reason": f"现中枢ZD={l2_zd:.2f}，跌破结构破坏",
                "next_watch": "新中枢区间确定后，等下一次三买位入场",
            },
            {
                "id": "丙",
                "condition": f"低开或快速回落跌破 {l2_zg:.2f}（ZG）",
                "meaning": (
                    f"当前笔提前结束，{l2_name}可能重回中枢内震荡"
                ),
                "action": (
                    f"止盈离场；若守住 {l2_zd:.2f}（ZD）观察，跌破必止损"
                ),
                "stop_loss": round(l2_zd, 2),
                "stop_reason": f"中枢ZD={l2_zd:.2f}，跌破=中枢结构完全破坏",
                "next_watch": f"观察 {l2_zd:.2f} 能否守住；破则等更低位一买",
            },
        ]

    # ── 当前笔向下，分三种价格位置 ──
    elif l2_dir == "down" and l2_zg > 0:

        # 【情况A】价格仍在ZG上方（上行后正在回踩，标准三买场景）
        if price >= l2_zg:
            prev_high = l2_press if l2_press > price else price

            classes = [
                {
                    "id": "甲",
                    "condition": f"回踩在 {l2_zg:.2f}~{l2_zg+(l2_zg-l2_zd)*0.3:.1f} 止跌，出现底分型",
                    "meaning": f"{l2_name}三买确认；ZG={l2_zg:.2f} 有效支撑",
                    "action": f"底分型确认 + 小级别向上笔启动 → 入场",
                    "stop_loss": round(l2_zd, 2),
                    "stop_reason": f"中枢ZD={l2_zd:.2f}，跌破=三买失败",
                    "next_watch": (
                        f"守住 {l2_zg:.2f} → 持仓；日线目标 ZG={l1_zg:.2f}"
                        if l1_zg > 0 else f"守住 {l2_zg:.2f} → 继续持"
                    ),
                },
                {
                    "id": "乙",
                    "condition": f"跌穿 {l2_zg:.2f}（ZG），回落至 {l2_zd:.2f}~{l2_zg:.2f} 中枢内",
                    "meaning": f"三买条件暂未确立，{l2_name}重回中枢内部震荡",
                    "action": "减仓观望，等再次突破ZG确认再入",
                    "stop_loss": round(l2_zd, 2),
                    "stop_reason": f"ZD={l2_zd:.2f}，跌破离场",
                    "next_watch": "观察能否重新站回ZG",
                },
                {
                    "id": "丙",
                    "condition": f"跌破 {l2_zd:.2f}（中枢ZD）",
                    "meaning": f"三买失败，{l2_name}结构受损",
                    "action": "清仓；等新低背驰或一买信号",
                    "stop_loss": round(day_stop, 2) if day_stop > 0 else round(l2_zd, 2),
                    "stop_reason": day_stop_label + "，结构红线",
                    "next_watch": "等日线级别底背驰出现，不提前抄底",
                },
            ]

        # 【情况B】价格在ZD~ZG之间（中枢内回落，方向未定）
        elif price >= l2_zd:
            classes = [
                {
                    "id": "甲",
                    "condition": f"守住 {l2_zd:.2f}（ZD）并反弹，突破 {l2_zg:.2f}（ZG）",
                    "meaning": f"中枢内形成底部，向上脱离中枢 → 三买机会",
                    "action": f"突破ZG={l2_zg:.2f} 后回踩确认入场；止损ZD",
                    "stop_loss": round(l2_zd, 2),
                    "stop_reason": f"中枢ZD={l2_zd:.2f}，跌破离场",
                    "next_watch": (
                        f"能否有效突破 {l2_zg:.2f}，突破后不测顶部"
                        if price > l1_zg else (
                            f"能否有效突破 {l2_zg:.2f}，突破后看日线ZG={l1_zg:.2f}" if l1_zg > 0 else f"能否突破 {l2_zg:.2f}"
                        )
                    ),
                },
                {
                    "id": "乙",
                    "condition": f"在 {l2_zd:.2f}~{l2_zg:.2f} 持续震荡",
                    "meaning": "中枢延伸，方向未定",
                    "action": "观望，等突破后再介入",
                    "stop_loss": round(l2_zd, 2),
                    "stop_reason": f"ZD={l2_zd:.2f}，跌破离场",
                    "next_watch": "等突破方向明确",
                },
                {
                    "id": "丙",
                    "condition": f"跌破 {l2_zd:.2f}（ZD），向下离开中枢",
                    "meaning": f"中枢向下破坏，{l2_name}结构偏弱，空仓观望",
                    "action": "已离场空仓；等下方出现底背驰后再重新规划入场",
                    "stop_loss": None,
                    "stop_reason": "空仓无止损；等底背驰入场信号再定止损",
                    "next_watch": f"观察 {l2_zd:.2f} 能否重新站回；或等更低位底背驰出现",
                },
            ]

        # 【情况C】价格已跌破ZD，在中枢下方（最弱情形，需要先修复再观察）
        else:
            classes = [
                {
                    "id": "甲",
                    "condition": f"反弹站回 {l2_zd:.2f}（ZD）并守稳，进而突破 {l2_zg:.2f}（ZG）",
                    "meaning": f"结构逐步修复：ZD→ZG→三买，分步确认",
                    "action": f"第一步：等站回ZD={l2_zd:.2f} + 5分底分型 → 轻仓介入；止损 {l2_supp:.2f}",
                    "stop_loss": round(l2_supp if l2_supp > 0 else day_stop, 2),
                    "stop_reason": f"近期笔低点 {l2_supp:.2f}，跌破=修复失败",
                    "next_watch": f"站稳ZD后看能否突破ZG={l2_zg:.2f}，进入三买区间",
                },
                {
                    "id": "乙",
                    "condition": f"在 {l2_supp:.2f}~{l2_zd:.2f} 之间横盘整理",
                    "meaning": f"ZD下方积蓄能量，等待修复信号",
                    "action": "观望，不在ZD下方盲目抄底",
                    "stop_loss": round(day_stop, 2) if day_stop > 0 else round(l2_supp, 2),
                    "stop_reason": day_stop_label,
                    "next_watch": f"观察是否出现5分底背驰或日线底分型",
                },
                {
                    "id": "丙",
                    "condition": f"继续下行，跌破近期低点 {l2_supp:.2f}",
                    "meaning": f"下行结构延续，暂无支撑",
                    "action": "严格空仓；等日线级别底背驰或一买信号，不提前抄底",
                    "stop_loss": None,
                    "stop_reason": "空仓无止损；等底背驰入场信号再定止损",
                    "next_watch": "等日线底背驰确认后再考虑介入",
                },
            ]

    # ── 中枢内震荡（方向未定）──
    elif l2_zg > 0 and l2_zd > 0:
        classes = [
            {
                "id": "甲",
                "condition": f"放量突破 {l2_zg:.2f}（ZG），不回落",
                "meaning": f"可能向上脱离中枢，形成三买机会",
                "action": f"突破后等回踩 {l2_zg:.2f} 确认 → 入场",
                "stop_loss": round(l2_zd, 2),
                "stop_reason": f"ZD={l2_zd:.2f}",
                "next_watch": f"回踩守ZG → 三买入场",
            },
            {
                "id": "乙",
                "condition": f"在 {l2_zd:.2f}~{l2_zg:.2f} 持续震荡",
                "meaning": "中枢延伸，等待方向选择",
                "action": "观望，不追",
                "stop_loss": round(l2_zd, 2),
                "stop_reason": f"ZD={l2_zd:.2f}",
                "next_watch": "等方向突破后再介入",
            },
            {
                "id": "丙",
                "condition": f"跌破 {l2_zd:.2f}（ZD）",
                "meaning": "向下离开中枢，空仓观望",
                "action": "空仓，等底背驰信号",
                "stop_loss": None,
                "stop_reason": "空仓无止损；底背驰入场后再定止损",
                "next_watch": "等日线底背驰出现",
            },
        ]

    return classes


def _build_forward_analysis(matrix: list) -> dict:
    """组装完整的前瞻推演数据，供前端渲染叙述式面板。"""
    if not matrix or len(matrix) < 2:
        return {}
    l1 = matrix[0]
    l2 = matrix[1]
    l3 = matrix[2] if len(matrix) > 2 else None  # 5分钟（体系A）/ 15分钟（体系B）

    recent_action = _describe_recent_action(l2)
    position = _describe_current_position(l1, l2)
    forward_classes = _build_forward_classes(l1, l2, l3)

    return {
        "recent_action":    recent_action,
        "current_position": position["summary"],
        "day_context":      position["day_context"],
        "stop_loss":        position["stop_loss"],
        "forward_classes":  forward_classes,
    }


# ═══════════════════════════════════════════════════════════════
# 九、跨级别矩阵分析
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

    # ── 前瞻推演 ──
    forward_a = _build_forward_analysis(matrix_a)
    forward_b = _build_forward_analysis(matrix_b)

    return {
        "symbol": symbol,
        "matrix_a": matrix_a,
        "matrix_b": matrix_b,
        "week": week_data,
        "interval_nesting_a": nesting_a,
        "interval_nesting_b": nesting_b,
        "forward_analysis_a": forward_a,
        "forward_analysis_b": forward_b,
    }


# ─── 向后兼容：保留 analyze_stock_chan_state 供 price_monitor 等使用 ───

async def analyze_stock_chan_state(symbol: str):
    """单级别日线状态（供旧 API 兼容）。返回 (state_str, zs_dict)。"""
    result = await _analyze_single_level(symbol, "day")
    last_zs = {"ZD": result["zd"], "ZG": result["zg"]} if result["zd"] > 0 else None
    return result["state"], last_zs
