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
from typing import Tuple, Optional, Union

from server.services.chan_detail_service import get_chan_detail, _compute_macd

# 区间套引擎：用于计算每个级别的 free_bis 和完整中枢序列
try:
    from chan_engine.models import KLine as ChanKLine
    from chan_engine.parser import ChanParser
    from chan_engine.fsm import ChanFSM
    _CHAN_ENGINE_AVAILABLE = True
except ImportError:
    _CHAN_ENGINE_AVAILABLE = False

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
# H3 修复：Pattern 常量——两处（chan_service + rotation_scorer）统一引用
# 之前两处用脆弱子串匹配，任意一侧改模板则另一侧静默失效
# ═══════════════════════════════════════════════════════════════

# 买点信号（出现即加分）
PAT_TREND_BOT_DIV    = "🟢 趋势底背驰 → 1买机会"      # 趋势底部背驰，一买信号
PAT_RANGE_BOT_DIV    = "🟡 盘整底背驰 → 2买可能"      # 中枢内底背驰
PAT_SECOND_BUY       = "🟢 二买确认(底部抬高,不创新低)"  # 二买形态确认
PAT_THIRD_BUY        = "🟢 三买确认(回踩不破ZG)"       # 三买形态确认

# 卖点信号（出现即减分）
PAT_TREND_TOP_DIV    = "🔴 趋势顶背驰 → 1卖风险"      # 趋势顶部背驰，一卖信号
PAT_RANGE_TOP_DIV    = "⚠️ 盘整顶背驰 → 注意"         # 中枢内顶背驰
PAT_SECOND_SELL      = "🔴 二卖确认(顶部不创新高)"     # 二卖形态确认
PAT_THIRD_SELL       = "🔴 三卖确认(回踩不守ZD)"       # 三卖形态确认


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
    """一买止据 = 背驰确认笔（最后一段向下笔）的最低点。

    缺论原文：1买止据 = 该笔的最低价，不是历史最低点。
    跳破止据 = 当前下跌笔失败，背驰判断无效。
    """
    if not bis:
        return None
    # 找最后一段向下笔（背驰确认笔）
    for b in reversed(bis):
        if not b["is_up"]:
            return min(b["y0"], b["y1"])
    return None


def _stop_for_2buy(bis: list) -> Optional[float]:
    """二买止据 = 2买回调笔的最低点（跳破则创新低，2买失败）。

    缺论原文：当前笔向上时，2买回调笔就是上一笔（向下笔）的最低点。
    当前笔向下时，则取当前笔的最低点（尚未形成正式2买）。
    """
    if not bis:
        return None
    last_bi = bis[-1]
    if last_bi["is_up"] and len(bis) >= 2:
        # 当前向上笔已启动 = 2买形成中，回调笔是前一笔（向下）
        prev_bi = bis[-2]
        return min(prev_bi["y0"], prev_bi["y1"])
    else:
        # 当前向下笔已经是2买回调笔，取其当前最低
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
                "id": "甲", "name": "趋势延伸",
                "condition": "向下离开中枢且无底背驰迹象",
                "action": "空仓等待",
                "stopLoss": None,
                "stopReason": None,
            },
            {
                "id": "乙", "name": "趋势完成(一买)",
                "condition": "最后一段向下离开中枢出现底背驰"
                             + (f"（当前综合得分:{div_info['combined_score']:.0%}）" if div_info else ""),
                "action": "关注一买，轻仓试探",
                "stopLoss": round(sl_1buy, 2) if sl_1buy else None,
                "stopReason": "背驰确认笔最低价（破则背驰判断失败）",
            },
        ]
        if div_info:
            sl_2buy = _stop_for_2buy(bis)
            classifications.append({
                "id": "丙", "name": "一买后等二买",
                "condition": "底背驰确认后第一次回踩不创新低 = 二买",
                "action": "二买确认后入场",
                "stopLoss": round(sl_2buy, 2) if sl_2buy else None,
                "stopReason": "二买回调笔最低点（破则创新低，二买失败）",
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

def _check_interval_nesting(levels_data, level_names=None) -> Optional[dict]:
    """区间套检测：大级别背驰 → 次级别确认 → 小级别精确定位。

    C3 修复：接受有序列表而非 dict，避免体系B调用时 m60/m15 被误记为 m30/m5。

    Args:
        levels_data: 可以是：
            - list[dict]：有序列表 [大级别, 中级别, 小级别]（推荐，体系A/B都用这个）
            - dict：旧格式 {"day": ..., "m30": ..., "m5": ...}（向后兼容保留）
        level_names: list[str]，对应的级别名称，用于日志记录。
                     列表格式时默认 ["l1","l2","l3"]，dict格式时自动用key。

    步骤（缠论原文）：
      1. 在大级别（日线）的 patterns 中找到背驰信号
      2. 检查次级别（30m/60m）在同方向上是否也有背驰
      3. 检查小级别（5m/15m）是否也在走最后一段
    各级别的背驰必须嵌套在同一走势方向内。
    """
    # 兼容旧格式 dict 调用（体系A原有调用方式）
    if isinstance(levels_data, dict):
        ordered = [levels_data.get(k) for k in ["day", "m30", "m5"]]
        names = level_names or ["day", "m30", "m5"]
    else:
        ordered = levels_data
        names = level_names or [f"l{i+1}" for i in range(len(ordered))]

    nesting = []
    nesting_direction = None  # "top" 或 "bottom"

    for i, data in enumerate(ordered):
        level_key = names[i] if i < len(names) else f"l{i+1}"
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
        return {
            "depth": 3, "label": "三级区间套确认",
            "direction": nesting_direction, "levels": nesting,
            "confidence_gate": "HIGH",    # 允许全量操作信号
        }
    elif len(nesting) >= 2:
        return {
            "depth": 2, "label": "两级区间套",
            "direction": nesting_direction, "levels": nesting,
            "confidence_gate": "MEDIUM",  # 半仓信号
        }
    elif len(nesting) >= 1:
        return {
            "depth": 1, "label": "单级别背驰",
            "direction": nesting_direction, "levels": nesting,
            "confidence_gate": "LOW",     # 仅观察，不触发操作
        }
    return None


# ═══════════════════════════════════════════════════════════════
# 五、背驰检测
# ═══════════════════════════════════════════════════════════════

def _get_divergence(bis: list, is_up: bool) -> Optional[dict]:
    """检测最近同向笔的背驰关系，返回 {type, ratio, dif_ratio, combined_score, severity} 或 None。

    双维度背驰判断（更接近缠论原文）：
    1. MACD 柱状面积比（area）：能量是否在萎缩
    2. DIF 极值比（dif_extreme）：动能高点是否在背离（权重更高）
    两者加权合并，消除简单面积比的误报/漏报。

    注：仅使用已确认笔（is_sure=True）进行背驰判断，
    避免尾盘14:50未确认笔导致的信号不稳定。
    """
    confirmed_bis = [b for b in bis if b.get("is_sure", True)]
    same_dir = [b for b in confirmed_bis if b["is_up"] == is_up]
    if len(same_dir) < 2:
        return None

    prev_bi = same_dir[-2]
    curr_bi = same_dir[-1]

    # 创新高/新低 是背驰的物理前提
    if is_up and curr_bi["y1"] < prev_bi["y1"]:
        return None
    if not is_up and curr_bi["y1"] > prev_bi["y1"]:
        return None

    prev_mom  = prev_bi.get("momentum", {})
    curr_mom  = curr_bi.get("momentum", {})
    prev_area = prev_mom.get("area", 0)
    curr_area = curr_mom.get("area", 0)
    prev_dif  = prev_mom.get("dif_extreme", 0)
    curr_dif  = curr_mom.get("dif_extreme", 0)

    if prev_area <= 0:
        return None

    # 面积得分（萎缩越多得分越高）
    area_ratio = curr_area / prev_area
    area_score = max(0.0, 1.0 - area_ratio)

    # DIF 极值得分（权重更高，更接近缠论原文）
    if prev_dif > 0:
        dif_ratio = curr_dif / prev_dif
        dif_score = max(0.0, 1.0 - dif_ratio)
    else:
        dif_ratio = area_ratio
        dif_score = area_score

    # 加权合并：area 40%，DIF 60%
    combined = area_score * 0.4 + dif_score * 0.6

    if combined < 0.20:
        return None

    severity = "高危" if combined >= 0.55 else "中等" if combined >= 0.35 else "轻微"

    return {
        "type":           "顶背驰" if is_up else "底背驰",
        "ratio":          round(area_ratio, 3),
        "dif_ratio":      round(dif_ratio, 3),
        "combined_score": round(combined, 3),
        "severity":       severity,
    }


# ═══════════════════════════════════════════════════════════════
# 五A、Risk 1 修复：分型检测（防未来函数）
# ═══════════════════════════════════════════════════════════════

import datetime as _dt

def _is_market_open() -> bool:
    """判断当前是否在A股交易时间（上午9:30-11:30，下午13:00-15:00）。"""
    now = _dt.datetime.now()
    if now.weekday() >= 5:  # 周六日
        return False
    t = now.time()
    morning   = _dt.time(9, 30)  <= t <= _dt.time(11, 30)
    afternoon = _dt.time(13, 0) <= t <= _dt.time(15, 0)
    return morning or afternoon


def _detect_fractal_confirmed(klines: list, last_bi: dict,
                               is_realtime: bool = False) -> tuple[bool, bool]:
    """检测已确认的顶/底分型，防止未来函数污染。

    Risk 1 修复点：
      实时行情（盘中）时，klines[-1] 是当前未收盘K线。用它参与分型判断
      会导致回测/沙盘与实盘结果不一致（未来函数）。
      is_realtime=True 时自动排除最后一根未收盘K线，只用已收盘数据。

    Returns:
        (has_bottom_fractal, has_top_fractal)
    """
    # 实时模式：排除最后一根未收盘K线
    bars = klines[:-1] if (is_realtime and len(klines) >= 4) else klines
    if len(bars) < 3:
        return False, False

    has_bottom_fractal = False
    has_top_fractal    = False

    if not last_bi.get("is_up"):
        lows = [k["low"] for k in bars[-3:]]
        if lows[1] <= lows[0] and lows[1] <= lows[2]:
            has_bottom_fractal = True
    elif last_bi.get("is_up"):
        highs = [k["high"] for k in bars[-3:]]
        if highs[1] >= highs[0] and highs[1] >= highs[2]:
            has_top_fractal = True

    return has_bottom_fractal, has_top_fractal


# ═══════════════════════════════════════════════════════════════
# 五B、Risk 2 修复：中继背驰 vs 转折背驰区分
# ═══════════════════════════════════════════════════════════════

def _classify_divergence_type(
    div_info: Optional[dict],
    detail_bis: list,
    price: float,
) -> str:
    """区分中继背驰和转折背驰（基于背驰后的价格行为）。

    Risk 2 修复点：
      缠论原文：顶背驰出现后需要等待后续走势确认：
        - 随后创新高 → 中继背驰（力度补充，继续趋势，不减仓）
        - 随后无法创新高，开始回落 → 转折背驰（趋势终结，触发 Stage 4 减仓）
      此前所有顶背驰一律触发乙情形减仓建议，会导致主升浪中频繁假警报。

    Args:
        div_info:   _get_divergence() 的返回值
        detail_bis: l2.get("detail_bis", [])，最近6笔数据
        price:      当前实时价格

    Returns:
        "无背驰"    - 未检测到背驰
        "疑似转折"  - 背驰出现，尚未确认方向（等待中）
        "中继确认"  - 背驰后价格已创新高/新低，确认为中继，继续趋势
        "转折确认"  - 背驰后价格未能创新高/新低并开始反向，确认为转折
    """
    if not div_info:
        return "无背驰"

    div_type = div_info.get("type", "")
    is_top = (div_type == "顶背驰")
    is_bot = (div_type == "底背驰")

    if not (is_top or is_bot):
        return "无背驰"

    # 需要至少2笔已确认数据来判断背驰后走势
    confirmed = [b for b in detail_bis if b.get("is_sure", True)]
    if len(confirmed) < 2:
        return "疑似转折"  # 数据不足，保守处理

    # 背驰确认笔的极值（背驰那笔的终点）
    div_bi = confirmed[-1]
    div_extreme = div_bi.get("y1", 0)
    if div_extreme <= 0:
        return "疑似转折"

    if is_top:
        # 顶背驰后：当前价是否超越背驰笔高点（创新高）
        if price > div_extreme * 1.005:   # 容差 0.5%，避免微小震荡误判
            return "中继确认"
        elif price < div_extreme * 0.980: # 明显回落 2%，确认转折
            return "转折确认"
        else:
            return "疑似转折"             # 还在观望区间
    else:  # 底背驰
        # 底背驰后：当前价是否跌破背驰笔低点（创新低）
        if price < div_extreme * 0.995:
            return "中继确认"
        elif price > div_extreme * 1.020:
            return "转折确认"
        else:
            return "疑似转折"


def _analyze_bi_extremes(bis: list) -> dict:
    """提取笔的顶底序列，分析价格结构趋势。

    缠论核心：每笔的终点（顶分型/底分型）构成顶底序列，
    序列的趋势方向决定了当前处于哪种走势结构，
    也是判断二买/二卖和背驰可信度的基础。

    Returns:
        {
          "tops":           [顶部序列，向上笔终点价格列表],
          "bots":           [底部序列，向下笔终点价格列表],
          "structure":      "多头排列|空头排列|收敛三角|扩张三角|混合",
          "tops_rising":    True/False,    # 顶序列是否依次抬高
          "bots_rising":    True/False,    # 底序列是否依次抬高
          "is_higher_low":  True/False,    # 最近底 > 前一底（2买前提条件）
          "is_lower_high":  True/False,    # 最近顶 < 前一顶（2卖前提条件）
          "bot_count":      int,           # 底部序列长度
          "top_count":      int,           # 顶部序列长度
        }
    """
    if not bis:
        return {"tops": [], "bots": [], "structure": "数据不足",
                "tops_rising": False, "bots_rising": False,
                "is_higher_low": False, "is_lower_high": False,
                "bot_count": 0, "top_count": 0}

    # 只用已确认笔（is_sure=True），且限制最近 40 根笔（约 3-4 个月日线）
    # 避免拿数年前历史高/低点污染当前趋势的买卖点判断
    confirmed = [b for b in bis if b.get("is_sure", True)][-40:]

    # 顶序列：向上笔的终点 y1
    tops = [b["y1"] for b in confirmed if b.get("is_up")]
    # 底序列：向下笔的终点 y1
    bots = [b["y1"] for b in confirmed if not b.get("is_up")]

    # 序列趋势判断（至少需要2个点）
    tops_rising  = len(tops) >= 2 and all(tops[i] > tops[i-1] for i in range(1, len(tops)))
    tops_falling = len(tops) >= 2 and all(tops[i] < tops[i-1] for i in range(1, len(tops)))
    bots_rising  = len(bots) >= 2 and all(bots[i] > bots[i-1] for i in range(1, len(bots)))
    bots_falling = len(bots) >= 2 and all(bots[i] < bots[i-1] for i in range(1, len(bots)))

    # 结构判断
    if tops_rising and bots_rising:
        structure = "多头排列"           # 一买/二买/三买都具备条件
    elif tops_falling and bots_falling:
        structure = "空头排列"           # 一卖/二卖/三卖都具备条件
    elif bots_rising and tops_falling:
        structure = "收敛三角"           # 即将选方向，观望
    elif bots_falling and tops_rising:
        structure = "扩张三角"           # 震荡加剧，谨慎
    elif bots_rising and not tops_rising:
        structure = "底部抬高(顶未跟上)" # 初步多头信号，顶需确认
    elif tops_rising and not bots_rising:
        structure = "顶部抬高(底未跟上)" # 初步多头但底不稳
    else:
        structure = "混合"              # 序列无规律

    # 关键结构前提条件
    is_higher_low  = len(bots) >= 2 and bots[-1] > bots[-2]  # 2买前提：底不创新低
    is_lower_high  = len(tops) >= 2 and tops[-1] < tops[-2]  # 2卖前提：顶不创新高

    # 多次顶分型聚集在同一价格附近（阻力/支撑集中）
    top_cluster = None
    if len(tops) >= 3:
        recent_tops = tops[-5:]  # 最近5个顶
        top_range = max(recent_tops) - min(recent_tops)
        top_mid   = (max(recent_tops) + min(recent_tops)) / 2
        if top_range / top_mid < 0.05:  # 波动在5%以内 = 聚集
            top_cluster = {"price": round(top_mid, 3), "count": len(recent_tops)}

    bot_cluster = None
    if len(bots) >= 3:
        recent_bots = bots[-5:]
        bot_range = max(recent_bots) - min(recent_bots)
        bot_mid   = (max(recent_bots) + min(recent_bots)) / 2
        if bot_range / bot_mid < 0.05:
            bot_cluster = {"price": round(bot_mid, 3), "count": len(recent_bots)}

    return {
        "tops":          tops[-10:],   # 只返回最近10个，避免数据过大
        "bots":          bots[-10:],
        "structure":     structure,
        "tops_rising":   tops_rising,
        "bots_rising":   bots_rising,
        "is_higher_low": is_higher_low,
        "is_lower_high": is_lower_high,
        "bot_count":     len(bots),
        "top_count":     len(tops),
        "top_cluster":   top_cluster,  # 顶分型是否聚集（阻力墙）
        "bot_cluster":   bot_cluster,  # 底分型是否聚集（支撑墙）
    }


# ═══════════════════════════════════════════════════════════════
# 五C、战法工具函数（Task #1 #2 #6 #7 #3 #4 #12）
# ═══════════════════════════════════════════════════════════════

def _calc_beichi(bis: list, beichi_type: str = "笔") -> Optional[dict]:
    """背驰计算统一入口，区分笔/段/盘整三种类型。

    Task #1：重构背驰计算，让调用方明确声明背驰类型，避免混用。

    Args:
        bis:          笔列表（已含 momentum 字段）
        beichi_type:  "笔" | "段" | "盘整"
            笔背驰 —— 当前笔与上上笔的MACD能量对比，缺论最基础用法
            段背驰 —— 当前段（多笔组合）与上一段的能量对比，用于一买/二买判断
            盘整背驰 —— 中枢内振荡笔之间的能量对比，用于中枢内买卖点

    Returns:
        {type, ratio, dif_ratio, combined_score, severity, beichi_type} 或 None
    """
    if not bis:
        return None

    # 确认笔列表：只用 is_sure=True 的笔，防止尾盘未确认笔误报
    confirmed = [b for b in bis if b.get("is_sure", True)]
    if len(confirmed) < 2:
        return None

    if beichi_type == "笔":
        # 笔背驰：最近两根同向笔（标准缠论背驰定义）
        last_bi = confirmed[-1]
        is_up = last_bi["is_up"]
        result = _get_divergence(confirmed, is_up)
        if result:
            result["beichi_type"] = "笔"
        return result

    elif beichi_type == "段":
        # 段背驰：把每段（上涨段/下跌段）视为整体，对比两段的总MACD面积
        # 段 = 连续同向笔的集合（以方向切换为段边界）
        segments: list[list] = []
        cur_seg: list = []
        cur_dir: Optional[bool] = None
        for b in confirmed:
            if cur_dir is None:
                cur_dir = b["is_up"]
                cur_seg = [b]
            elif b["is_up"] == cur_dir:
                cur_seg.append(b)
            else:
                segments.append(cur_seg)
                cur_dir = b["is_up"]
                cur_seg = [b]
        if cur_seg:
            segments.append(cur_seg)

        if len(segments) < 2:
            return None

        last_seg = segments[-1]
        prev_seg = segments[-2]

        # 两段方向必须相同（背驰是同向比较）
        last_dir = last_seg[0]["is_up"]
        prev_dir = prev_seg[0]["is_up"]
        if last_dir != prev_dir:
            return None

        def _seg_area(seg: list) -> float:
            return sum(abs(b.get("momentum", {}).get("area", 0)) for b in seg)

        def _seg_dif(seg: list) -> float:
            extremes = [abs(b.get("momentum", {}).get("dif_extreme", 0)) for b in seg]
            return max(extremes) if extremes else 0

        prev_area = _seg_area(prev_seg)
        curr_area = _seg_area(last_seg)
        prev_dif  = _seg_dif(prev_seg)
        curr_dif  = _seg_dif(last_seg)

        if prev_area <= 0:
            return None

        # 价格端点：段背驰也要求创新高/新低
        prev_extreme = max(b["y1"] for b in prev_seg) if last_dir else min(b["y1"] for b in prev_seg)
        curr_extreme = max(b["y1"] for b in last_seg) if last_dir else min(b["y1"] for b in last_seg)
        if last_dir and curr_extreme < prev_extreme:
            return None  # 未创新高，不是背驰
        if not last_dir and curr_extreme > prev_extreme:
            return None  # 未创新低，不是背驰

        area_ratio = curr_area / prev_area
        area_score = max(0.0, 1.0 - area_ratio)
        dif_ratio  = curr_dif / prev_dif if prev_dif > 0 else area_ratio
        dif_score  = max(0.0, 1.0 - dif_ratio)
        combined   = area_score * 0.4 + dif_score * 0.6

        if combined < 0.20:
            return None

        severity = "高危" if combined >= 0.55 else "中等" if combined >= 0.35 else "轻微"
        return {
            "type":           "顶背驰" if last_dir else "底背驰",
            "ratio":          round(area_ratio, 3),
            "dif_ratio":      round(dif_ratio, 3),
            "combined_score": round(combined, 3),
            "severity":       severity,
            "beichi_type":    "段",
        }

    elif beichi_type == "盘整":
        # 盘整背驰：中枢内的振荡笔，用最近两根同向笔比较（与笔背驰相同算法）
        # 语义区别：盘整背驰意味着中枢内动能衰竭，下一步可能离开中枢
        last_bi = confirmed[-1]
        is_up = last_bi["is_up"]
        result = _get_divergence(confirmed, is_up)
        if result:
            result["beichi_type"] = "盘整"
            # 盘整背驰信号较弱，降低一级 severity
            sev_map = {"高危": "中等", "中等": "轻微", "轻微": "轻微"}
            result["severity"] = sev_map.get(result["severity"], "轻微")
        return result

    return None


def _check_third_buy_confirmed(bis: list, zg: float) -> bool:
    """修正后的三买确认判断：突破后必须等回踩完成（is_sure）且终点不破ZG。

    Task #2：原有逻辑在突破当天即触发三买，此函数要求：
      1. 存在已完成的向上突破笔（终点 > ZG，is_sure=True）
      2. 其后存在已完成的向下回踩笔（is_sure=True）
      3. 回踩笔终点（y1）> ZG（回踩不破中枢上沿）

    Returns:
        True = 三买结构确认，可以考虑入场
        False = 结构未成立（突破未回踩 / 回踩破ZG / 笔未确认）
    """
    if not bis or zg <= 0:
        return False

    confirmed = [b for b in bis if b.get("is_sure", True)]
    if len(confirmed) < 2:
        return False

    # 从最近笔往前找：最近一根向下笔（回踩笔）
    pullback_bi = None
    for b in reversed(confirmed):
        if not b["is_up"]:
            pullback_bi = b
            break

    if pullback_bi is None:
        return False  # 无回踩笔

    # 回踩笔终点必须在 ZG 上方（回踩不破）
    if pullback_bi["y1"] <= zg:
        return False

    # 回踩笔之前必须有向上突破笔（终点 > ZG）
    idx = confirmed.index(pullback_bi)
    if idx == 0:
        return False  # 没有前序笔

    breakout_bi = confirmed[idx - 1]
    if not breakout_bi["is_up"] or breakout_bi["y1"] <= zg:
        return False

    return True


def _check_stop_atr(current_price: float, stop_price: float, atr: float) -> dict:
    """止损ATR合理性校验。

    Task #6：入场前必须通过此校验，止损太紧或太宽都不入场。

    合理范围：1×ATR ≤ 止损距离 ≤ 2.5×ATR

    Returns:
        {
          stop_price: float,
          atr: float,
          stop_distance_pct: float,   # 止损距离占当前价的百分比
          atr_multiple: float,         # 止损距离 / ATR
          valid: bool,
          verdict: str,                # "合理" | "止损太紧" | "止损太宽" | "数据不足"
          note: str,                   # 可读解释
        }
    """
    if atr is None or atr <= 0 or current_price <= 0:
        return {
            "stop_price": stop_price,
            "atr": atr or 0,
            "stop_distance_pct": 0,
            "atr_multiple": 0,
            "valid": False,
            "verdict": "数据不足",
            "note": "ATR或价格数据缺失，无法校验止损合理性",
        }

    stop_distance = current_price - stop_price
    if stop_distance <= 0:
        return {
            "stop_price": stop_price,
            "atr": atr,
            "stop_distance_pct": 0,
            "atr_multiple": 0,
            "valid": False,
            "verdict": "止损价高于当前价",
            "note": f"止损价 {stop_price:.2f} 高于当前价 {current_price:.2f}，数据异常",
        }

    atr_multiple = stop_distance / atr
    stop_distance_pct = stop_distance / current_price

    if atr_multiple < 1.0:
        verdict = "止损太紧"
        valid = False
        note = (
            f"止损距离 {stop_distance:.2f}（{stop_distance_pct:.1%}）仅 {atr_multiple:.1f}×ATR，"
            f"正常波动即被扫出，建议等价格回调到更好的入场点。"
        )
    elif atr_multiple > 2.5:
        verdict = "止损太宽"
        valid = False
        note = (
            f"止损距离 {stop_distance:.2f}（{stop_distance_pct:.1%}）达 {atr_multiple:.1f}×ATR，"
            f"单笔亏损超标，建议等价格回调压缩止损距离后入场。"
        )
    else:
        verdict = "合理"
        valid = True
        note = (
            f"止损距离 {stop_distance:.2f}（{stop_distance_pct:.1%}），"
            f"{atr_multiple:.1f}×ATR，在合理范围（1~2.5×ATR）内。✓"
        )

    return {
        "stop_price":        round(stop_price, 2),
        "atr":               round(atr, 3),
        "stop_distance_pct": round(stop_distance_pct, 4),
        "atr_multiple":      round(atr_multiple, 2),
        "valid":             valid,
        "verdict":           verdict,
        "note":              note,
    }


def _calc_target_price(current_price: float, bis: list, zhongshus: list,
                       stop_price: float) -> list:
    """目标价计算。

    Task #7：根据缠论结构计算两个目标价，供赔率评估使用。

    优先级：
      目标1 = 最近确认向上笔的前高（短期目标，保守）
      目标2 = 上方中枢上沿（如果有）或前高×1.15（中期目标）

    Returns:
        [
          {"label": "短期目标（前高）", "price": float, "distance_pct": float},
          {"label": "中期目标（上方中枢）", "price": float, "distance_pct": float},
        ]
    """
    targets = []
    confirmed = [b for b in bis if b.get("is_sure", True)]

    # 目标1：最近的顶分型高点（向上笔终点）
    up_bis = [b for b in confirmed if b["is_up"]]
    if up_bis:
        recent_high = up_bis[-1]["y1"]
        if recent_high > current_price:
            dist = (recent_high - current_price) / current_price
            targets.append({
                "label":        "短期目标（前高）",
                "price":        round(recent_high, 2),
                "distance_pct": round(dist, 4),
            })

    # 目标2：上方中枢上沿（如果存在高于当前价的中枢）
    upper_zs = [z for z in zhongshus if z.get("zg", 0) > current_price]
    if upper_zs:
        nearest_zg = min(upper_zs, key=lambda z: z["zg"])["zg"]
        dist2 = (nearest_zg - current_price) / current_price
        targets.append({
            "label":        "中期目标（上方中枢上沿）",
            "price":        round(nearest_zg, 2),
            "distance_pct": round(dist2, 4),
        })
    elif up_bis and len(up_bis) >= 2:
        # 没有上方中枢，用历史最高顶×做参考
        hist_high = max(b["y1"] for b in up_bis)
        if hist_high > current_price:
            dist2 = (hist_high - current_price) / current_price
            targets.append({
                "label":        "中期目标（历史前高）",
                "price":        round(hist_high, 2),
                "distance_pct": round(dist2, 4),
            })

    # 注入赔率估算（只在两个目标都存在时计算）
    if len(targets) >= 1 and stop_price > 0:
        risk = current_price - stop_price
        for t in targets:
            reward = t["price"] - current_price
            t["rr_ratio"] = round(reward / risk, 2) if risk > 0 else None

    return targets


def _calc_position_size(account_value: float, current_price: float,
                        stop_price: float, risk_pct: float = 0.01) -> dict:
    """建议仓位计算。

    Task #7：基于固定风险比例模型，每笔最大亏损 = 账户 × risk_pct。

    Args:
        account_value:  账户总资金（元）
        current_price:  当前入场价
        stop_price:     止损价
        risk_pct:       单笔最大风险比例（默认1%）

    Returns:
        {
          risk_pct, max_loss_amount, stop_distance,
          suggested_shares, suggested_amount,
          position_pct,  # 建议仓位占账户比例
          note,
        }
    """
    if account_value <= 0 or current_price <= 0 or stop_price <= 0:
        return {"error": "参数无效", "suggested_shares": 0, "suggested_amount": 0}

    stop_distance = current_price - stop_price
    if stop_distance <= 0:
        return {"error": "止损价高于入场价", "suggested_shares": 0, "suggested_amount": 0}

    max_loss_amount  = account_value * risk_pct
    # 建议股数（向下取整到100股整手）
    raw_shares       = max_loss_amount / stop_distance
    suggested_shares = max(100, int(raw_shares / 100) * 100)
    suggested_amount = suggested_shares * current_price
    position_pct     = suggested_amount / account_value

    return {
        "risk_pct":        risk_pct,
        "max_loss_amount": round(max_loss_amount, 2),
        "stop_distance":   round(stop_distance, 2),
        "suggested_shares": suggested_shares,
        "suggested_amount": round(suggested_amount, 2),
        "position_pct":    round(position_pct, 4),
        "note": (
            f"账户{account_value:.0f}元，单笔最大风险{risk_pct:.0%}="
            f"{max_loss_amount:.0f}元，"
            f"建议{suggested_shares}股（约{suggested_amount:.0f}元，"
            f"占仓{position_pct:.0%}）"
        ),
    }


def _build_entry_checklist_strategy1(
    day: dict, m30: dict, m5: dict, week: Optional[dict] = None
) -> dict:
    """战法一入场五条件逐一检测。Task #3

    战法一：日线2买 + 30分钟2买 + 5分钟3买 介入（三级别共振买入）

    五个条件全部满足 → actionable=True，输出空仓入场信号。
    任何一个不满足 → actionable=False，输出观察中并标注缺失项。

    Args:
        day:  日线级别分析结果（来自 _analyze_single_level）
        m30:  30分钟级别分析结果
        m5:   5分钟级别分析结果
        week: 周线级别分析结果（可选，用于条件⑤）

    Returns:
        {
          strategy: "战法一",
          conditions: [逐条检测结果],
          score: int,          # 0-5，满足几个条件
          missing: [str],      # 未满足的条件描述
          actionable: bool,    # 五条件全满足才为 True
          entry_zone: (low, high) | None,
          stop_price: float | None,   # 5分中枢ZG（入场止损）
          trigger_note: str,          # 当前缺什么 / 可以进了
        }
    """
    conditions = []
    missing = []

    day_price    = day.get("price", 0)
    day_state    = day.get("state", "")
    day_patterns = " ".join(day.get("patterns", []))
    day_bis      = day.get("detail_bis", [])
    day_zg       = day.get("zg", 0)
    day_zd       = day.get("zd", 0)

    m30_price    = m30.get("price", 0)
    m30_patterns = " ".join(m30.get("patterns", []))
    m30_bis      = m30.get("detail_bis", [])
    m30_zg       = m30.get("zg", 0)

    m5_price     = m5.get("price", 0)
    m5_patterns  = " ".join(m5.get("patterns", []))
    m5_bis       = m5.get("detail_bis", [])
    m5_zg        = m5.get("zs_operative_zg", 0) or m5.get("zg", 0)
    m5_zd        = m5.get("zs_operative_zd", 0) or m5.get("zd", 0)

    # ① 日线出现二买或类二买结构
    day_2buy = (
        "二买" in day_patterns
        or "类二买" in day_patterns
        or day_state in ("THIRD_BUY_CONFIRMED",)
        or (day.get("is_higher_low") and day.get("zs_departure") in ("above", "inside"))
    )
    cond1 = {"id": 1, "label": "日线二买/类二买", "pass": day_2buy}
    if not day_2buy:
        missing.append("日线二买结构未确认")
    conditions.append(cond1)

    # ② 30分出现二买，与日线价格区域共振（价格偏差 < 3%）
    m30_2buy = "二买" in m30_patterns or "类二买" in m30_patterns
    price_resonance = (
        day_price > 0 and m30_price > 0
        and abs(day_price - m30_price) / day_price < 0.03
    )
    cond2_pass = m30_2buy and price_resonance
    cond2 = {"id": 2, "label": "30分二买+日线共振", "pass": cond2_pass,
             "detail": f"30分二买={'✓' if m30_2buy else '✗'}，价格共振={'✓' if price_resonance else '✗'}"}
    if not cond2_pass:
        missing.append("30分二买未形成" if not m30_2buy else "30分价格与日线偏差>3%")
    conditions.append(cond2)

    # ③ 5分出现底背驰（入场门控）
    m5_bottom_div = (
        "底背驰" in m5_patterns
        or "🟢 趋势底背驰" in m5_patterns
        or "🟡 盘整底背驰" in m5_patterns
    )
    # 补充：用 _calc_beichi 做实时计算（更准确）
    if not m5_bottom_div and m5_bis:
        beichi = _calc_beichi(m5_bis, "笔")
        m5_bottom_div = beichi is not None and beichi.get("type") == "底背驰"

    cond3 = {"id": 3, "label": "5分底背驰", "pass": m5_bottom_div}
    if not m5_bottom_div:
        missing.append("5分底背驰未出现")
    conditions.append(cond3)

    # ④ 入场窗口有效：5分执行点距底部 < 10%
    # 底部 = 5分最近向下笔终点（最低价）
    m5_confirmed = [b for b in m5_bis if b.get("is_sure", True)]
    m5_down_bis = [b for b in m5_confirmed if not b["is_up"]]
    window_valid = False
    distance_from_bottom = None
    if m5_down_bis and m5_price > 0:
        recent_low = m5_down_bis[-1]["y1"]
        if recent_low > 0:
            distance_from_bottom = (m5_price - recent_low) / recent_low
            window_valid = distance_from_bottom < 0.10
    cond4 = {"id": 4, "label": "入场窗口有效(<10%离底)", "pass": window_valid,
             "detail": f"距底{distance_from_bottom:.1%}" if distance_from_bottom is not None else "数据不足"}
    if not window_valid:
        missing.append(f"入场窗口过大({distance_from_bottom:.1%}离底，需<10%)" if distance_from_bottom else "5分数据不足")
    conditions.append(cond4)

    # ⑤ 周线无明显压制
    weekly_safe = True
    if week:
        week_patterns = " ".join(week.get("patterns", []))
        week_state = week.get("state", "")
        weekly_safe = not (
            "下跌趋势" in week_patterns
            or "顶背驰" in week_patterns
            or week_state == "DOWNWARD_LEAVING"
        )
    cond5 = {"id": 5, "label": "周线无压制", "pass": weekly_safe}
    if not weekly_safe:
        missing.append("周线处于下跌趋势，有明显压制")
    conditions.append(cond5)

    score = sum(1 for c in conditions if c["pass"])
    actionable = (score == 5)

    # 入场区间 & 止损
    stop_price = m5_zg if m5_zg > 0 else None  # 5分中枢ZG是止损线
    entry_zone = None
    if stop_price and m5_price > stop_price:
        entry_zone = (round(stop_price * 1.001, 2), round(m5_price, 2))

    if actionable:
        trigger_note = f"✅ 五条件全满足，可以介入。止损：{stop_price:.2f}（5分中枢ZG），执行点：{m5_price:.2f}"
    elif score >= 3:
        trigger_note = f"⏳ {score}/5条件满足，观察中。缺：{'、'.join(missing)}"
    else:
        trigger_note = f"❌ 结构未到位（{score}/5），不参与。"

    return {
        "strategy":     "战法一",
        "conditions":   conditions,
        "score":        score,
        "missing":      missing,
        "actionable":   actionable,
        "entry_zone":   entry_zone,
        "stop_price":   round(stop_price, 2) if stop_price else None,
        "trigger_note": trigger_note,
    }


def _build_entry_checklist_strategy2(
    day: dict, m30: dict, m5: dict, week: Optional[dict] = None
) -> dict:
    """战法二入场五条件逐一检测。Task #4

    战法二：日线中枢 + 小级别在中枢上沿附近震荡 + 5分中枢出现3买（中枢上沿突破）

    Args / Returns 结构同战法一。
    """
    conditions = []
    missing = []

    day_price    = day.get("price", 0)
    day_zhongshus = day.get("zhongshus", []) or []
    day_bis      = day.get("detail_bis", [])
    day_patterns = " ".join(day.get("patterns", []))

    m30_price    = m30.get("price", 0)
    m30_zg       = m30.get("zs_operative_zg", 0) or m30.get("zg", 0)
    m30_zd       = m30.get("zs_operative_zd", 0) or m30.get("zd", 0)
    m30_bis      = m30.get("detail_bis", [])

    m5_price     = m5.get("price", 0)
    m5_patterns  = " ".join(m5.get("patterns", []))
    m5_bis       = m5.get("detail_bis", [])
    m5_zg        = m5.get("zs_operative_zg", 0) or m5.get("zg", 0)

    # ① 日线存在明确中枢（震荡次数≥3，即至少3次进出中枢）
    day_has_zs = False
    day_zs_zg = 0.0
    day_zs_zd = 0.0
    if day_zhongshus:
        last_day_zs = day_zhongshus[-1]
        # 中枢内震荡次数通过笔进出中枢统计（简化：中枢笔数≥6代表至少3次进出）
        zs_bi_count = last_day_zs.get("bi_count", 0) or 0
        day_has_zs = zs_bi_count >= 6
        if day_has_zs:
            day_zs_zg = last_day_zs.get("zg", 0)
            day_zs_zd = last_day_zs.get("zd", 0)
    # 备用：从 detail_bis 估算
    if not day_has_zs and day_bis:
        day_has_zs = len(day_bis) >= 6
        if day_has_zs and day_zhongshus:
            last_day_zs = day_zhongshus[-1]
            day_zs_zg = last_day_zs.get("zg", 0)
            day_zs_zd = last_day_zs.get("zd", 0)

    cond1 = {"id": 1, "label": "日线存在明确中枢", "pass": day_has_zs,
             "detail": f"日线中枢ZG={day_zs_zg:.2f} ZD={day_zs_zd:.2f}" if day_has_zs else "无中枢或震荡不足"}
    if not day_has_zs:
        missing.append("日线中枢未形成或震荡次数不足")
    conditions.append(cond1)

    # ② 当前价在日线中枢上沿附近（偏差 < 5%）
    near_zg = False
    zg_deviation = None
    if day_zs_zg > 0 and day_price > 0:
        zg_deviation = (day_price - day_zs_zg) / day_zs_zg
        near_zg = abs(zg_deviation) < 0.05
    cond2 = {"id": 2, "label": "当前价在日线中枢上沿附近(<5%)", "pass": near_zg,
             "detail": f"偏差{zg_deviation:.1%}" if zg_deviation is not None else "数据不足"}
    if not near_zg:
        missing.append(f"价格距日线中枢上沿偏差过大({zg_deviation:.1%})" if zg_deviation is not None else "无中枢ZG数据")
    conditions.append(cond2)

    # ③ 30分/5分中枢在日线ZG附近震荡（30分中枢中心在日线ZG ± 3%）
    m30_near_day_zg = False
    if day_zs_zg > 0 and m30_zg > 0 and m30_zd > 0:
        m30_center = (m30_zg + m30_zd) / 2
        m30_near_day_zg = abs(m30_center - day_zs_zg) / day_zs_zg < 0.03
    cond3 = {"id": 3, "label": "30分中枢在日线ZG附近震荡", "pass": m30_near_day_zg,
             "detail": f"30分中枢中心={(m30_zg+m30_zd)/2:.2f}，日线ZG={day_zs_zg:.2f}" if day_zs_zg > 0 else "数据不足"}
    if not m30_near_day_zg:
        missing.append("30分中枢未在日线中枢上沿附近积蓄")
    conditions.append(cond3)

    # ④ 5分出现三买（_check_third_buy_confirmed）
    m5_3buy = False
    if m5_bis and m5_zg > 0:
        m5_3buy = _check_third_buy_confirmed(m5_bis, m5_zg)
    # 备用：从 patterns 判断
    if not m5_3buy:
        m5_3buy = "三买" in m5_patterns or "PAT_THIRD_BUY" in m5_patterns

    cond4 = {"id": 4, "label": "5分三买确认", "pass": m5_3buy}
    if not m5_3buy:
        missing.append("5分三买未确认（等待突破后回踩不破ZG）")
    conditions.append(cond4)

    # ⑤ 周线无明显压制（同战法一）
    weekly_safe = True
    if week:
        week_patterns = " ".join(week.get("patterns", []))
        week_state = week.get("state", "")
        weekly_safe = not (
            "下跌趋势" in week_patterns
            or "顶背驰" in week_patterns
            or week_state == "DOWNWARD_LEAVING"
        )
    cond5 = {"id": 5, "label": "周线无压制", "pass": weekly_safe}
    if not weekly_safe:
        missing.append("周线处于下跌趋势，有明显压制")
    conditions.append(cond5)

    # 震荡收敛度（可选增强信号）
    narrowing_bonus = False
    if m30_bis and len(m30_bis) >= 4:
        recent_highs = [b["y1"] for b in m30_bis[-4:] if b["is_up"]]
        recent_lows  = [b["y1"] for b in m30_bis[-4:] if not b["is_up"]]
        if len(recent_highs) >= 2 and len(recent_lows) >= 2:
            high_range = recent_highs[-1] - recent_highs[-2]
            low_range  = recent_lows[-2]  - recent_lows[-1]  # 底部抬高
            narrowing_bonus = high_range < 0 and low_range > 0  # 顶降底升 = 收敛

    score = sum(1 for c in conditions if c["pass"])
    actionable = (score == 5)

    stop_price = m5_zg if m5_zg > 0 else None
    entry_zone = None
    if stop_price and m5_price > stop_price:
        entry_zone = (round(stop_price * 1.001, 2), round(m5_price, 2))

    if actionable:
        bonus_note = "（震荡收敛，突破可信度更高）" if narrowing_bonus else ""
        trigger_note = f"✅ 五条件全满足{bonus_note}，可以介入。止损：{stop_price:.2f}（5分中枢ZG）"
    elif score >= 3:
        trigger_note = f"⏳ {score}/5条件满足，观察中。缺：{'、'.join(missing)}"
    else:
        trigger_note = f"❌ 结构未到位（{score}/5），不参与。"

    return {
        "strategy":        "战法二",
        "conditions":      conditions,
        "score":           score,
        "missing":         missing,
        "actionable":      actionable,
        "entry_zone":      entry_zone,
        "stop_price":      round(stop_price, 2) if stop_price else None,
        "trigger_note":    trigger_note,
        "narrowing_bonus": narrowing_bonus,  # 额外：震荡收敛加分
    }


def _detect_top_divergence(l2: dict, price: float) -> dict:
    """顶背驰检测并区分中继与转折。Task #12

    已有 _get_divergence() 做基础检测，_classify_divergence_type() 做分类。
    本函数整合两者，输出完整的顶背驰状态供持仓模式使用。

    Args:
        l2:    30分钟或日线级别分析结果
        price: 当前实时价格

    Returns:
        {
          detected: bool,           # 是否检测到顶背驰
          classification: str,      # "无背驰"|"疑似转折"|"中继确认"|"转折确认"
          severity: str | None,     # "高危"|"中等"|"轻微"
          combined_score: float,    # 0-1
          action_hint: str,         # 操作建议
        }
    """
    bis      = l2.get("detail_bis", [])
    div_info = _get_divergence(bis, is_up=True)  # 只检测向上背驰（顶背驰）

    if not div_info:
        return {
            "detected":       False,
            "classification": "无背驰",
            "severity":       None,
            "combined_score": 0,
            "action_hint":    "无顶背驰，结构健康，持仓。",
        }

    classification = _classify_divergence_type(div_info, bis, price)

    if classification == "中继确认":
        action_hint = "中继背驰（价格已创新高），趋势延续，上移台阶止损继续持仓。"
    elif classification == "转折确认":
        action_hint = "⚠️ 转折背驰确认，建议减仓50%锁利，剩余守台阶止损。"
    elif classification == "疑似转折":
        action_hint = "顶背驰出现，等待后续走势确认（创新高=中继，回落=转折），暂不操作。"
    else:
        action_hint = "无背驰。"

    return {
        "detected":       True,
        "classification": classification,
        "severity":       div_info.get("severity"),
        "combined_score": div_info.get("combined_score", 0),
        "action_hint":    action_hint,
    }


def _detect_bottom_divergence(l2: dict) -> dict:
    """底背驰检测（空仓模式辅助）。对称实现卖点背驰检测的买点版本。

    Returns:
        {detected, classification, severity, combined_score, action_hint}
    """
    bis      = l2.get("detail_bis", [])
    div_info = _get_divergence(bis, is_up=False)  # 向下背驰（底背驰）

    if not div_info:
        return {
            "detected":       False,
            "classification": "无背驰",
            "severity":       None,
            "combined_score": 0,
            "action_hint":    "无底背驰。",
        }

    return {
        "detected":       True,
        "classification": "底背驰",
        "severity":       div_info.get("severity"),
        "combined_score": div_info.get("combined_score", 0),
        "action_hint":    f"底背驰出现（{div_info.get('severity', '')}），关注一买/二买机会。",
    }


def _classify_strategy(
    day: dict, m30: dict, m5: dict, week: Optional[dict] = None
) -> dict:
    """战法分类器：判断当前股票符合哪套战法。Task #5 前置

    先跑战法一，再跑战法二，输出分类结果。
    同一股票可能同时符合两套战法（输出并列）。

    Returns:
        {
          strategy_type: "战法一" | "战法二" | "双战法" | "观察中",
          strategy1: dict | None,   # _build_entry_checklist_strategy1 结果
          strategy2: dict | None,   # _build_entry_checklist_strategy2 结果
          primary: dict | None,     # 主推的那套（score更高的）
          summary: str,
        }
    """
    s1 = _build_entry_checklist_strategy1(day, m30, m5, week)
    s2 = _build_entry_checklist_strategy2(day, m30, m5, week)

    both = s1["actionable"] and s2["actionable"]
    only1 = s1["actionable"] and not s2["actionable"]
    only2 = s2["actionable"] and not s1["actionable"]

    if both:
        strategy_type = "双战法"
        primary = s1 if s1["score"] >= s2["score"] else s2
        summary = f"✅ 双战法共振！战法一({s1['score']}/5) + 战法二({s2['score']}/5)，信号极强。"
    elif only1:
        strategy_type = "战法一"
        primary = s1
        summary = f"✅ 战法一（三级别共振）：{s1['trigger_note']}"
    elif only2:
        strategy_type = "战法二"
        primary = s2
        summary = f"✅ 战法二（中枢上沿突破）：{s2['trigger_note']}"
    else:
        strategy_type = "观察中"
        primary = s1 if s1["score"] >= s2["score"] else s2
        best_score = max(s1["score"], s2["score"])
        summary = f"⏳ 暂不符合任何战法（最高 {best_score}/5），继续观察。"

    return {
        "strategy_type": strategy_type,
        "strategy1":     s1,
        "strategy2":     s2,
        "primary":       primary,
        "summary":       summary,
    }


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

    # 走势类型直接从 zoushi 参数获取（与 _classify_zoushi 统一，避免重复计算造成矛盾）
    zoushi_type_str = zoushi.get("type", "")
    is_uptrend   = zoushi_type_str == "上涨趋势"
    is_downtrend = zoushi_type_str == "下跌趋势"

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

    # ═══ 三、二买/二卖检测（基于顶底序列，比随机4笔更准确）═══
    extremes = _analyze_bi_extremes(bis)

    if n_zs >= 1:
        bots = extremes.get("bots", [])
        tops = extremes.get("tops", [])

        # 二买：底部抬高 + 前底确实出过ZD（确保有过中枢外的下跌）
        # 且要求本轮 patterns 里已出现过底背驰（一买先行）
        has_recent_bot_div = any("底背驰" in p for p in patterns)
        if not is_up and extremes["is_higher_low"] and len(bots) >= 2 and has_recent_bot_div:
            if bots[-2] < zd:
                patterns.append("🟢 二买确认(底部抬高,不创新低)")

        # 二卖：顶部下移 + 前顶确实出过ZG
        # 缠论原文：二卖的前提是必须先出现顶背驰（一卖），反弹后不创新高才构成二卖
        # 没有前置顶背驰直接判二卖是跳级，会在修复段/新高前误触发
        elif is_up and extremes["is_lower_high"] and len(tops) >= 2:
            has_recent_top_div = any("顶背驰" in p for p in patterns)
            if tops[-2] > zg and has_recent_top_div:
                patterns.append("🔴 二卖确认(顶部下移,不创新高)")

        # 顶分型聚集（阻力墙）— 如厦门钨业60元多次顶分型
        top_cluster = extremes.get("top_cluster")
        if top_cluster and top_cluster["count"] >= 3:
            if is_up and price >= top_cluster["price"] * 0.97:
                patterns.append(
                    f"🧱 压力墙={top_cluster['price']:.2f}"
                    f"({top_cluster['count']}次顶分型聚集)"
                )
        bot_cluster = extremes.get("bot_cluster")
        if bot_cluster and bot_cluster["count"] >= 3:
            if not is_up and price <= bot_cluster["price"] * 1.03:
                patterns.append(
                    f"🛡️ 支撑墙={bot_cluster['price']:.2f}"
                    f"({bot_cluster['count']}次底分型聚集)"
                )

        # 结构标签增强背驰可信度
        struct = extremes.get("structure", "")
        if struct in ("多头排列", "底部抬高(顶未跟上)") and div and not is_up:
            patterns.append(f"📶 顶底结构={struct}，底背驰可信度↑")
        elif struct in ("空头排列",) and div and is_up:
            patterns.append(f"📉 顶底结构={struct}，顶背驰可信度↑")

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

def _build_level_context_label(result: dict) -> str:
    """根据 parent_level 和 parent_bi_is_up 生成通俗的级别归属描述。"""
    if not result.get("parent_level"):
        return ""
    
    p_name = _FORWARD_LEVEL_NAMES.get(result["parent_level"], result["parent_level"])
    p_dir = "向上" if result.get("parent_bi_is_up") else "向下"
    
    return f"【归属于 {p_name}第{result.get('bi_count', 0)}笔 {p_dir}推升/回落阶段】"


# ═══════════════════════════════════════════════════════════════
# 七、单级别分析
# ═══════════════════════════════════════════════════════════════

# 各周期的 K 线数量，必须与前端 KlineChart.jsx 的 INTERVALS.count 保持完全一致
# 原因：KlineChart 和 TRadar 共用同一个 get_chan_detail 引擎，
#       若两端数量不一致，会导致缠论引擎在不同数据量下工作，划分出不同的笔/中枢
_LEVEL_KLINE_COUNT = {
    "day":  500,    # 与 KlineChart day count=500 一致
    "week": 500,    # 周线独立，无前端 KlineChart 对应，用 500
    "m60":  800,    # 与 KlineChart m60 count=800 一致
    "m30":  1000,   # 与 KlineChart m30 count=1000 一致
    "m15":  1200,   # 与 KlineChart m15 count=1200 一致
    "m5":   1500,   # 与 KlineChart m5 count=1500 一致
}

def _detect_virtual_box(bis: list) -> dict:
    """提取最新的虚拟中枢防线（覆盖滞后的 CChan 严格中枢）

    缠论预备中枢条件：最后三笔方向必须交替（上-下-上 或 下-上-下），
    且三笔存在价格重叠区间。方向不交替时不构成预备中枢，返回 None。
    """
    if not bis or len(bis) < 3:
        return None

    last_3 = bis[-3:]
    b0, b1, b2 = last_3[0], last_3[1], last_3[2]

    # 验证三笔方向交替（上-下-上 或 下-上-下）
    dirs = [b0.get("is_up"), b1.get("is_up"), b2.get("is_up")]
    is_alternating = (
        (dirs[0] is True  and dirs[1] is False and dirs[2] is True) or
        (dirs[0] is False and dirs[1] is True  and dirs[2] is False)
    )
    if not is_alternating:
        return None

    highs = [max(b.get("y0", 0), b.get("y1", 0)) for b in last_3]
    lows  = [min(b.get("y0", 0), b.get("y1", 0)) for b in last_3]
    zg = min(highs)   # 三笔最高点中的最低值
    zd = max(lows)    # 三笔最低点中的最高值

    # 必须存在重叠区间（zg > zd 才有意义）
    if zg > zd:
        return {"virtual_zd": round(zd, 4), "virtual_zg": round(zg, 4)}
    return None


def _detect_dead_cat_bounce(bis: list, price: float) -> str:
    """单级别检测A字杀与潜在死猫跳反抽"""
    if not bis or len(bis) < 3:
        return ""
    b3 = bis[-3]
    b2 = bis[-2]
    b1 = bis[-1]  # 当前笔

    if b3.get("is_up") and not b2.get("is_up") and b1.get("is_up"):
        up1_high = max(b3.get("y0", 0), b3.get("y1", 0))
        up1_low = min(b3.get("y0", 0), b3.get("y1", 0))
        
        # 条件1：暴涨笔 (幅度 > 40%)
        if up1_low > 0 and (up1_high / up1_low) > 1.4:
            # 条件2：暴跌笔 (跌破前涨幅的 50%)
            down1_low = min(b2.get("y0", 0), b2.get("y1", 0))
            if down1_low < (up1_high + up1_low) / 2:
                # 条件3：当前向上反弹没过前高
                if price < up1_high:
                    return "🔴 A字杀反抽(潜在死猫跳)"
    return ""


def _build_zs_context(
    zhongshus: list,
    bis: Optional[Union[list, str]] = None,
    klines: Optional[list] = None,
    level: Optional[str] = None,
) -> dict:
    """从 chan_detail_service 已算好的中枢和笔数据中提取区间套信息。

    数据源改为 chan.py 的正确结构（接收方传入），不再由 chan_engine 滚动窗口重算中枢。
    操作中枢选取逻辑不变；free_bis 改从 chan.py 笔列表中按时间截取。

    Args:
        zhongshus: chan_detail_service 输出的中枢列表（dict，含 zg/zd/begin_date/end_date）
        bis:       chan_detail_service 输出的笔列表（dict，含 x0/x1/y0/y1/is_up）
        klines:    原始K线（仅用于取当前收盘价）
        level:     级别名称（用于日志）

    返回：
      zs_operative_zd/zg   操作中枢边界
      zs_free_bis_count     最后中枢之后的自由笔数量
      zs_free_bis_dir       自由笔整体方向（"up"/"down"/"none"）
      zs_free_high/low      自由笔段极值（用于止损锚定）
      zs_last_centers       最近3个中枢的 ZD/ZG 列表（从旧到新）
      zs_departure          价格相对操作中枢的状态（"above"/"below"/"inside"）
    """
    # 兼容旧测试/旧调用方：历史签名是 _build_zs_context(klines, level)。
    if level is None and isinstance(bis, str):
        level = bis
        klines = zhongshus
        zhongshus = []
        bis = []

    _empty = {
        "zs_operative_zd": 0, "zs_operative_zg": 0,
        "zs_free_bis_count": 0, "zs_free_bis_dir": "none",
        "zs_free_high": 0, "zs_free_low": 0,
        "zs_last_centers": [], "zs_departure": "unknown",
        "zs_data_ok": False,
    }

    if not zhongshus or not klines:
        return _empty

    try:
        price = float(klines[-1].get("close", 0))
        if price == 0:
            return _empty

        # ── 操作中枢选取（缠论区间套逻辑）──
        # 规则优先级：
        #   1. 价格在某中枢内部 → 直接用该中枢（departure=inside）
        #   2. 价格在所有中枢上方 → 用 ZG 最高的中枢（最近突破的支撑面）
        #   3. 价格在所有中枢下方 → 用 ZD 最低的中枢（最近压力位）

        # 步骤1：检查是否在某中枢内
        operative_zs = None
        for zs in zhongshus:
            if zs["zd"] <= price <= zs["zg"]:
                operative_zs = zs
                break

        if operative_zs is None:
            above_centers = [zs for zs in zhongshus if price > zs["zg"]]
            if above_centers:
                # 步骤2：价格在中枢上方 → ZG 最高 = 最近突破的止损锚
                operative_zs = max(above_centers, key=lambda z: z["zg"])
            else:
                # 步骤3：价格在所有中枢下方 → ZD 最低 = 最近压力位
                operative_zs = min(zhongshus, key=lambda z: z["zd"])

        op_zg = round(operative_zs["zg"], 2)
        op_zd = round(operative_zs["zd"], 2)

        if price > op_zg:
            departure = "above"
        elif price < op_zd:
            departure = "below"
        else:
            departure = "inside"

        # ── 自由笔：最后中枢结束后的笔 ──
        last_zs_end = zhongshus[-1].get("end_date", "")
        if last_zs_end and bis:
            # 取起点在最后中枢结束日期之后的笔（严格大于，排除离开段本身）
            free_bis_list = [b for b in bis if b.get("x0", "") > last_zs_end]
        else:
            free_bis_list = []

        free_count = len(free_bis_list)
        if free_count == 0:
            free_dir = "none"
            free_high = free_low = 0.0
        else:
            # 上涨笔：高点=y1，低点=y0；下跌笔：高点=y0，低点=y1
            free_high = max(
                (b["y1"] if b["is_up"] else b["y0"]) for b in free_bis_list
            )
            free_low = min(
                (b["y0"] if b["is_up"] else b["y1"]) for b in free_bis_list
            )
            # 整体方向：看第一笔离开时的方向
            free_dir = "up" if free_bis_list[0].get("is_up") else "down"

        # 最近3个中枢（从旧到新）
        last_centers = [
            {"zd": round(zs["zd"], 2), "zg": round(zs["zg"], 2)}
            for zs in zhongshus[-3:]
        ]

        return {
            "zs_operative_zd": op_zd,
            "zs_operative_zg": op_zg,
            "zs_free_bis_count": free_count,
            "zs_free_bis_dir": free_dir,
            "zs_free_high": round(free_high, 2),
            "zs_free_low": round(free_low, 2),
            "zs_last_centers": last_centers,
            "zs_departure": departure,
            "zs_data_ok": True,
        }
    except Exception as e:
        logger.warning("_build_zs_context failed for level=%s: %s", level, e)
        return _empty


async def _analyze_single_level(symbol: str, level: str,
                                prefetched_detail: Optional[dict] = None) -> dict:
    """单级别分析：调用 chan_detail_service 获取结构，再推导状态、走势类型、完全分类。

    Args:
        symbol:            股票代码
        level:             级别名称 (day/m30/m5/...)
        prefetched_detail: 预取的 detail 数据（来自 get_chan_multi_level）。
                           若为 None 则自行拉取（兼容旧调用方式）。
    """

    freq = _LEVEL_TO_FREQ.get(level, level)
    count = _LEVEL_KLINE_COUNT.get(level, 800)  # 按周期分级取数，复盘模式无前后端蝴蝶效应

    _MIN_KLINES_FOR_ANALYSIS = 120

    # 优先使用预取数据（来自多级别联动接口）
    if prefetched_detail is not None:
        detail = prefetched_detail
    else:
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
    
    # A 股定制化：大级别用线段防洗盘，小级别用笔抓逃跑
    if freq in ("day", "week", "60", "30"):
        zhongshus = detail.get("seg_zhongshus", [])
        # 容错：如果股票刚上市不久，线段中枢没跑出来，降级使用笔中枢
        if not zhongshus:
            zhongshus = detail.get("bi_zhongshus", [])
    else:
        # 15分钟，5分钟 等小级别：维持敏感的笔中枢
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

    # V5 新增: A字杀与潜在死猫跳检测
    dcb_pattern = _detect_dead_cat_bounce(bis, real_price)
    if dcb_pattern:
        patterns.insert(0, dcb_pattern)
        
    # V5 新增: 虚拟中枢防线
    virtual_box = _detect_virtual_box(bis)
    virtual_zd = virtual_box["virtual_zd"] if virtual_box else 0
    virtual_zg = virtual_box["virtual_zg"] if virtual_box else 0

    # 最后一笔方向
    last_bi_dir = "up" if (bis and bis[-1].get("is_up")) else "down" if bis else "unknown"

    # ── 历史新高检测 ──
    all_time_high = max((k.get("high", 0) for k in klines), default=0) if klines else 0
    recent_high = max((k.get("high", 0) for k in klines[-20:]), default=0) if klines else 0
    is_near_historical_high = (recent_high >= all_time_high * 0.95) if all_time_high > 0 else False

    # ── 分型检测（Risk 1 修复：实时模式排除未收盘K线，防止未来函数）──
    has_bottom_fractal = False
    has_top_fractal = False
    if len(bis) >= 2 and len(klines) >= 3:
        _is_rt = _is_market_open()  # 盘中=实时模式，排除最后一根未收盘K线
        has_bottom_fractal, has_top_fractal = _detect_fractal_confirmed(
            klines, bis[-1], is_realtime=_is_rt
        )

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

    # ── 区间套数据（使用 chan_detail_service 的正确中枢，不再由 chan_engine 重算）──
    zs_context = _build_zs_context(zhongshus, bis, klines, level)

    # 优先使用 chan_engine 操作中枢边界；若不可用则保留 chan_detail_service 旧值。
    # 这样所有下游（_describe_current_position / 狙击位检测 / 前端 readBoard）
    # 读取 l2.get("zg") 时自动拿到正确值，无需逐处 patch。
    effective_zg = zs_context.get("zs_operative_zg") or zg
    effective_zd = zs_context.get("zs_operative_zd") or zd

    return {
        "level": level,
        "state": state,
        "zd": effective_zd,
        "zg": effective_zg,
        "virtual_zd": virtual_zd,
        "virtual_zg": virtual_zg,
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
        # 级别归属字段（第一阶段增加）
        "bi_extreme_seq": _analyze_bi_extremes(bis),  # 顶底序列分析
        "div_info": div_info,  # 动能背驰分析
        # 区间套字段（chan_engine 计算）
        **zs_context,
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



# ═══════════════════════════════════════════════════════════════
# 八A-0、中枢序列身份评估（context-aware 中枢震荡专用）
# ═══════════════════════════════════════════════════════════════

def _assess_center_context(l1: dict, l2: dict, l3: Optional[dict] = None) -> dict:
    """分析当前中枢所处的买卖点序列身份与多空偏向。

    使用 chan_engine 输出的 zs_departure / zs_operative_zg/zd / zs_free_bis 字段，
    实现真正的区间套逻辑：从高级别到低级别逐层定位，止损锚定在最近操作级别的中枢边界，
    不再出现"日线止损价=28块，但条件是守住30分32块"的级别混用错误。

    Returns:
        {
          "bias":        "偏多" | "偏空" | "收敛待选",
          "identity":    中枢序列身份文字,
          "context_desc": 简短情境描述,
          "bull_scenario": 偏多情形（含止损锚），
          "bear_scenario": 偏空情形，
        }
    """
    l3 = l3 or {}

    price    = l2.get("price", 0)
    l1_name  = _FORWARD_LEVEL_NAMES.get(l1.get("level", ""), "日线")
    l2_name  = _FORWARD_LEVEL_NAMES.get(l2.get("level", ""), "30分")
    l3_name  = _FORWARD_LEVEL_NAMES.get(l3.get("level", ""), "5分")

    # ── chan_engine 输出的精确区间套数据 ──
    l1_dep    = l1.get("zs_departure", "unknown")  # "above" / "below" / "inside"
    l1_op_zg  = l1.get("zs_operative_zg", 0) or l1.get("zg", 0)
    l1_op_zd  = l1.get("zs_operative_zd", 0) or l1.get("zd", 0)

    l2_dep         = l2.get("zs_departure", "unknown")
    l2_op_zg       = l2.get("zs_operative_zg", 0) or l2.get("zg", 0)
    l2_op_zd       = l2.get("zs_operative_zd", 0) or l2.get("zd", 0)
    l2_free_count  = l2.get("zs_free_bis_count", 0)
    l2_free_dir    = l2.get("zs_free_bis_dir", "none")
    l2_last_ctrs   = l2.get("zs_last_centers", [])  # 最近3个中枢 [{zd,zg}, ...]

    l3_dep    = l3.get("zs_departure", "unknown")
    l3_op_zg  = l3.get("zs_operative_zg", 0) or l3.get("zg", 0)
    l3_op_zd  = l3.get("zs_operative_zd", 0) or l3.get("zd", 0)

    # ─────────────────────────────────────────────────────────────
    # 区间套分类：以日线（l1）位置为主轴，30分（l2）为操作级别，5分（l3）为入场级别
    # ─────────────────────────────────────────────────────────────

    # ── 日线在中枢上方 → 日线级别向上，三买/上涨趋势背景 ──
    if l1_dep == "above":
        if l2_dep == "above":
            # 30分也站上中枢 → 强势持续上行
            stop_desc = (f"，若跌破{l3_name}ZG={l3_op_zg:.2f}则止损" if l3_op_zg > 0 else "")
            return {
                "bias": "偏多",
                "identity": f"{l1_name}上方+{l2_name}上方：趋势持续",
                "context_desc": (
                    f"{l1_name}操作中枢（{l1_op_zd:.2f}–{l1_op_zg:.2f}）上方，"
                    f"{l2_name}价格{price:.2f}亦站上{l2_name}中枢ZG={l2_op_zg:.2f}，趋势偏多。"
                ),
                "bull_scenario": (
                    f"回踩守住{l2_name}ZG={l2_op_zg:.2f}，出现向上分型→持有/加仓"
                    + stop_desc
                ),
                "bear_scenario": (
                    f"跌回{l2_name}ZG={l2_op_zg:.2f}以下→止损观望，"
                    f"等{l2_name}中枢重新确认后再评估"
                ),
            }
        elif l2_dep == "below":
            # 日线上方 + 30分回调至中枢下方 → 三买回踩测支撑
            prev_zd = l2_last_ctrs[-2]["zd"] if len(l2_last_ctrs) >= 2 else 0
            stop_anchor = l3_op_zd if l3_op_zd > 0 else l2_op_zd
            return {
                "bias": "偏多",
                "identity": f"{l1_name}三买区间，{l2_name}回踩测中枢支撑",
                "context_desc": (
                    f"{l1_name}仍在操作中枢（{l1_op_zd:.2f}–{l1_op_zg:.2f}）上方，"
                    f"{l2_name}价格{price:.2f}回落至中枢（{l2_op_zd:.2f}–{l2_op_zg:.2f}）下方，"
                    f"等待止跌信号。"
                ),
                "bull_scenario": (
                    f"守住{l2_name}ZD={l2_op_zd:.2f}"
                    + (f"（前中枢底={prev_zd:.2f}为下一支撑）" if prev_zd > 0 else "")
                    + f"，{l3_name}出现底分型后轻仓介入；跌破{stop_anchor:.2f}则止损出场"
                ),
                "bear_scenario": (
                    f"有效跌破{l2_name}ZD={l2_op_zd:.2f}→三买回踩失败，"
                    f"等{l2_name}中枢重新建立后再评估"
                ),
            }
        else:  # l2_dep == "inside"
            # 日线上方 + 30分在中枢内震荡 → 三买酝酿，等30分突破
            stop_desc = (f"，若跌破{l3_name}ZG={l3_op_zg:.2f}则止损" if l3_op_zg > 0 else "")
            return {
                "bias": "偏多",
                "identity": f"{l1_name}三买酝酿，{l2_name}中枢等方向",
                "context_desc": (
                    f"{l1_name}站上操作中枢（ZG={l1_op_zg:.2f}），"
                    f"{l2_name}价格{price:.2f}在中枢（{l2_op_zd:.2f}–{l2_op_zg:.2f}）内震荡，"
                    f"等{l2_name}向上方向确认。"
                ),
                "bull_scenario": (
                    f"突破{l2_name}ZG={l2_op_zg:.2f}，脱离{l2_name}中枢向上"
                    + stop_desc
                ),
                "bear_scenario": (
                    f"跌破{l2_name}ZD={l2_op_zd:.2f}→{l2_name}向下选择，暂不入场"
                ),
            }

    # ── 日线在中枢内 → 日线级别震荡，操作依赖30分/5分 ──
    elif l1_dep == "inside":
        if l2_dep == "above":
            # 日线震荡 + 30分脱离中枢向上 → 向日线ZG方向推进
            stop_desc = (f"，若跌破{l3_name}ZG={l3_op_zg:.2f}则止损" if l3_op_zg > 0 else "")
            return {
                "bias": "偏多",
                "identity": f"{l1_name}中枢内偏多，{l2_name}脱离中枢向上",
                "context_desc": (
                    f"{l1_name}中枢（{l1_op_zd:.2f}–{l1_op_zg:.2f}）内震荡，"
                    f"{l2_name}价格{price:.2f}已站上{l2_name}ZG={l2_op_zg:.2f}，"
                    f"向{l1_name}ZG={l1_op_zg:.2f}方向推进。"
                ),
                "bull_scenario": (
                    f"守住{l2_name}ZG={l2_op_zg:.2f}回踩→轻仓持有"
                    + stop_desc
                    + f"，目标关注{l1_name}ZG={l1_op_zg:.2f}"
                ),
                "bear_scenario": (
                    f"跌回{l2_name}ZG={l2_op_zg:.2f}以下→上行动能减弱，清仓观望"
                ),
            }
        elif l2_dep == "below":
            # 日线震荡 + 30分在中枢下方 → 等止跌，5分定入场
            prev_zd = l2_last_ctrs[-2]["zd"] if len(l2_last_ctrs) >= 2 else 0
            stop_anchor = l3_op_zd if l3_op_zd > 0 else l2_op_zd
            return {
                "bias": "收敛待选",
                "identity": f"{l1_name}中枢内，{l2_name}回调等止跌",
                "context_desc": (
                    f"{l1_name}中枢（{l1_op_zd:.2f}–{l1_op_zg:.2f}）内，"
                    f"{l2_name}价格{price:.2f}在{l2_name}中枢（{l2_op_zd:.2f}–{l2_op_zg:.2f}）下方回调，"
                    f"等止跌信号后操作。"
                ),
                "bull_scenario": (
                    f"守住{l2_name}ZD={l2_op_zd:.2f}"
                    + (f"（前中枢底={prev_zd:.2f}为支撑参考）" if prev_zd > 0 else "")
                    + f"，{l3_name}出现底分型后轻仓介入；跌破{stop_anchor:.2f}则止损出场"
                ),
                "bear_scenario": (
                    f"有效跌破{l2_name}ZD={l2_op_zd:.2f}→"
                    f"向{l1_name}ZD={l1_op_zd:.2f}方向回落，空仓等底背驰"
                ),
            }
        else:  # l2_dep == "inside"
            # 双级别在中枢内 → 看5分定方向
            if l3_dep == "above":
                return {
                    "bias": "偏多",
                    "identity": f"双层震荡，{l3_name}率先向上",
                    "context_desc": (
                        f"{l1_name}（{l1_op_zd:.2f}–{l1_op_zg:.2f}）和"
                        f"{l2_name}（{l2_op_zd:.2f}–{l2_op_zg:.2f}）双层震荡，"
                        f"{l3_name}价格{price:.2f}站上{l3_name}ZG={l3_op_zg:.2f}，短线偏多。"
                    ),
                    "bull_scenario": (
                        f"守住{l3_name}ZG={l3_op_zg:.2f}→持有，"
                        f"等{l2_name}突破{l2_op_zg:.2f}扩大为{l2_name}级别机会"
                    ),
                    "bear_scenario": f"跌回{l3_name}ZG={l3_op_zg:.2f}以下→短线止损，等待重新方向确认",
                }
            elif l3_dep == "below":
                return {
                    "bias": "偏空",
                    "identity": f"双层震荡，{l3_name}率先向下",
                    "context_desc": (
                        f"{l1_name}和{l2_name}双层震荡，"
                        f"{l3_name}价格{price:.2f}跌破{l3_name}ZD={l3_op_zd:.2f}，短线偏空。"
                    ),
                    "bull_scenario": (
                        f"守住{l3_name}ZD={l3_op_zd:.2f}，出现{l3_name}底分型→等结构确认"
                    ),
                    "bear_scenario": (
                        f"继续下行，关注{l2_name}ZD={l2_op_zd:.2f}支撑，跌破则等底背驰"
                    ),
                }
            else:
                # 三级别全在中枢内 → 真正的方向待选
                l3_bull = f"{l3_name}先突破{l3_op_zg:.2f}→" if l3_op_zg > 0 else ""
                l3_bear = f"{l3_name}先跌破{l3_op_zd:.2f}→" if l3_op_zd > 0 else ""
                return {
                    "bias": "收敛待选",
                    "identity": "三级别中枢震荡，等最小级别方向选择",
                    "context_desc": (
                        f"日/30分/5分三个级别均在各自中枢内震荡，"
                        f"{l2_name}操作中枢={l2_op_zd:.2f}–{l2_op_zg:.2f}，"
                        f"等{l3_name}率先选择方向。"
                    ),
                    "bull_scenario": (
                        f"{l3_bull}突破{l2_name}ZG={l2_op_zg:.2f}→偏多方向；"
                        + (f"若突破后跌回{l3_name}ZG={l3_op_zg:.2f}以下则止损"
                           if l3_op_zg > 0 else "")
                        if l3_op_zg > 0 else f"突破{l2_name}ZG={l2_op_zg:.2f}→偏多方向"
                    ),
                    "bear_scenario": (
                        f"{l3_bear}跌破{l2_name}ZD={l2_op_zd:.2f}→偏空方向，空仓等底背驰"
                        if l3_op_zd > 0 else f"跌破{l2_name}ZD={l2_op_zd:.2f}→偏空方向"
                    ),
                }

    # ── 日线在中枢下方 → 日线级别偏空，下跌趋势或弱势 ──
    else:  # l1_dep == "below" or "unknown"
        if l2_dep == "above":
            # 日线弱势 + 30分反弹出中枢 → 高风险反弹中枢
            return {
                "bias": "偏空",
                "identity": f"{l1_name}弱势背景，{l2_name}反弹中枢",
                "context_desc": (
                    f"{l1_name}价格在操作中枢（{l1_op_zd:.2f}–{l1_op_zg:.2f}）下方，偏空背景，"
                    f"{l2_name}出现反弹并站上ZG={l2_op_zg:.2f}，属于弱势中的反弹，需高度谨慎。"
                ),
                "bull_scenario": (
                    f"守住{l2_name}ZG={l2_op_zg:.2f}，且出现{l1_name}底背驰信号"
                    f"→考虑轻仓一买；若跌破{l2_name}ZG={l2_op_zg:.2f}则止损出场"
                ),
                "bear_scenario": f"跌回{l2_name}ZG={l2_op_zg:.2f}以下→反弹结束，严格空仓",
            }
        elif l2_dep == "below":
            # 双级别在中枢下方 → 下跌趋势明确，等底背驰
            return {
                "bias": "偏空",
                "identity": f"双级别下跌，等底背驰一买",
                "context_desc": (
                    f"{l1_name}和{l2_name}价格均在各自中枢下方，"
                    f"下跌趋势明确（{l2_name}中枢={l2_op_zd:.2f}–{l2_op_zg:.2f}），"
                    f"严格空仓等待底背驰信号。"
                ),
                "bull_scenario": (
                    f"等{l2_name}出现底背驰，且{l3_name}形成底分型→考虑轻仓一买；"
                    + (f"跌破{l3_op_zd:.2f}则止损出场" if l3_op_zd > 0 else "")
                ),
                "bear_scenario": f"继续下跌→等更低位底背驰信号，不追空",
            }
        else:  # l2_dep == "inside"
            return {
                "bias": "偏空",
                "identity": f"{l1_name}弱势，{l2_name}震荡等方向",
                "context_desc": (
                    f"{l1_name}价格在操作中枢下方（偏空背景），"
                    f"{l2_name}价格{price:.2f}在中枢（{l2_op_zd:.2f}–{l2_op_zg:.2f}）内震荡，"
                    f"等{l2_name}方向选择。"
                ),
                "bull_scenario": (
                    f"突破{l2_name}ZG={l2_op_zg:.2f}，且{l1_name}出现底背驰→考虑一买机会"
                ),
                "bear_scenario": (
                    f"跌破{l2_name}ZD={l2_op_zd:.2f}→{l1_name}下跌趋势延续，严格空仓等底背驰"
                ),
            }


def _detect_coiling_near_pivot(l3: dict, target_price: float,
                                tolerance: float = 0.012) -> dict:
    """检测5分级别是否在目标价位（通常是大级别ZG/3买上沿）附近窄幅震荡。

    窄幅震荡三个必要条件：
      1. 当前价贴近目标位（偏差 < tolerance，默认1.2%）
      2. 自由笔振幅极小（< 2%）——说明多空在此胶着蓄力
      3. 自由笔数量 >= 2——说明已形成盘整而非刚刚到位

    A股假突破检测（主力洗盘特征）：
      - 某根K线的 low 跌破盘整低点，但随后立即收回
      - 假跌破深度 < 0.5%，持续不超过2根K线
      - 假跌破次数 1~2 次：信号强化（洗盘完成）
      - 假跌破次数 >= 3 次：降级，疑似真实下跌

    Returns:
      {
        "is_coiling": bool,
        "range_pct": float,          # 盘整振幅（free_bis极值/价格）
        "distance_pct": float,       # 当前价偏离目标价的距离
        "trigger_line": float,       # 突破触发线（盘整高点）
        "stop_line": float,          # 止损线（盘整低点，假跌破后更新）
        "fake_breakdown": bool,      # 是否出现假跌破
        "fake_breakdown_count": int,
        "signal_level": str,         # "无" / "备战" / "备战_强化"
      }
    """
    _empty = {
        "is_coiling": False, "range_pct": 0.0, "distance_pct": 0.0,
        "trigger_line": 0.0, "stop_line": 0.0,
        "fake_breakdown": False, "fake_breakdown_count": 0,
        "signal_level": "无",
    }
    if not l3 or target_price <= 0:
        return _empty

    price = l3.get("price", 0)
    if price <= 0:
        return _empty

    free_count = l3.get("zs_free_bis_count", 0)
    free_high  = l3.get("zs_free_high", 0)
    free_low   = l3.get("zs_free_low", 0)

    # 振幅
    range_pct = ((free_high - free_low) / free_high
                 if (free_count >= 2 and free_high > 0 and free_low > 0)
                 else 1.0)

    # 距离目标价
    distance_pct = abs(price - target_price) / target_price

    # 主条件
    is_coiling = (range_pct < 0.02 and distance_pct < tolerance and free_count >= 2)
    if not is_coiling:
        return _empty

    # 假跌破检测（recent_klines：最近5根5分K）
    recent       = l3.get("recent_klines", [])
    stop_line    = free_low
    fake_count   = 0

    for i, k in enumerate(recent):
        if k.get("low", stop_line) < stop_line:
            subsequent = recent[i + 1:]
            if subsequent and subsequent[0].get("close", 0) > stop_line:
                depth = (stop_line - k["low"]) / stop_line
                if depth < 0.005:  # 假跌破深度 < 0.5%
                    fake_count += 1
                    # 假跌破最低点作为更精确的止损锚
                    stop_line = min(stop_line, k["low"])

    # fake_count >= 3 说明压力真实，降级
    if fake_count >= 3:
        return _empty

    # ── 买点支撑检测（文章核心过滤：盘整低点必须有小级别买点）──
    # 只有盘整低点处形成了5分底背驰（1买）或底分型确认，突破才是"位置3"级别的信号。
    # 没有买点支撑的突破 = 文章中的"位置1/位置2"，进场后大概率被止损。
    l3_patterns_str = " ".join(l3.get("patterns", []) or [])
    l3_div          = l3.get("div_info") or {}
    has_buy_point_support = (
        l3_div.get("type") == "底背驰"
        or "底背驰" in l3_patterns_str
        or l3.get("has_bottom_fractal", False)
    )

    signal = "备战_强化" if fake_count >= 1 else "备战"
    return {
        "is_coiling": True,
        "range_pct": round(range_pct, 4),
        "distance_pct": round(distance_pct, 4),
        "trigger_line": round(free_high, 2),
        "stop_line": round(stop_line, 2),
        "fake_breakdown": fake_count >= 1,
        "fake_breakdown_count": fake_count,
        "signal_level": signal,
        "has_buy_point_support": has_buy_point_support,  # 盘整低点是否有买点结构
    }


# ═══════════════════════════════════════════════════════════════
# 八A、买卖点生命周期节点识别（空仓路径核心）
# ═══════════════════════════════════════════════════════════════

def _detect_lifecycle_node(l2: dict, l3: Optional[dict] = None) -> dict:
    """检测当前所处的买卖点生命周期节点（空仓路径使用）。

    Step 4 重写：以 zs_departure（价格相对操作中枢的位置）作为第一道门，
    彻底解决"价格已涨45%还给100块止损"的级别混用问题。

    三个主分支：
      above  → 中枢上方：类二买回踩 / 三买酝酿
      inside → 中枢内部：原有买卖点节点逻辑（用 op_zg/op_zd）
      below  → 中枢下方：二买 / 一买 / 主跌（用 op_zd 作为参考线）

    Returns dict with keys:
      node, confidence, stop_loss, stop_basis,
      entry_zone, next_watch, action, position_hint
    """
    l3 = l3 or {}
    price = l2.get("price", 0)
    l2_name = _FORWARD_LEVEL_NAMES.get(l2.get("level", ""), "30分")
    l3_name = _FORWARD_LEVEL_NAMES.get(l3.get("level", ""), "5分")

    # ── chan_engine 操作中枢数据（Step 1 填充）──
    zs_dep     = l2.get("zs_departure", "unknown")
    op_zg      = l2.get("zs_operative_zg", 0) or l2.get("zg", 0)
    op_zd      = l2.get("zs_operative_zd", 0) or l2.get("zd", 0)
    free_count = l2.get("zs_free_bis_count", 0)
    free_dir   = l2.get("zs_free_bis_dir", "none")
    free_high  = l2.get("zs_free_high", 0)
    free_low   = l2.get("zs_free_low", 0)

    # 5分级别精细止损参考
    l3_op_zg = l3.get("zs_operative_zg", 0) or l3.get("zg", 0)
    l3_op_zd = l3.get("zs_operative_zd", 0) or l3.get("zd", 0)

    # 辅助数据（保留，仅在 inside/below 分支中使用）
    state        = l2.get("state", "")
    div          = l2.get("div_info") or {}
    seq          = l2.get("bi_extreme_seq") or {}
    patterns_str = " ".join(l2.get("patterns", []) or [])
    bots = seq.get("bots", [])
    tops = seq.get("tops", [])
    is_higher_low = seq.get("is_higher_low", False)
    is_lower_high = seq.get("is_lower_high", False)
    has_bottom_div = (div.get("type") == "底背驰"
                      or "🟢 趋势底背驰 → 1买机会" in patterns_str
                      or "✅ 底背驰" in patterns_str)
    # H2 修复：收紧 has_top_div 匹配
    # 之前 "顶背驰" in patterns_str 会匹配 "三买后顶背驰→谨防3买转1卖" 这类警示字符串，
    # 导致 一卖_触发 判断被错误触发。现在只匹配明确的卖点确认信号。
    _TOP_DIV_CONFIRMED = (
        "🔴 趋势顶背驰 → 1卖风险",
        "⚠️ 顶背驰(高危)",
        "⚠️ 顶背驰(中等)",
        "⚠️ 中枢内顶背驰(高危)",
        "⚠️ 中枢内顶背驰(中等)",
    )
    has_top_div = (div.get("type") == "顶背驰"
                   or any(p in patterns_str for p in _TOP_DIV_CONFIRMED))

    # ────────────────────────────────────────────────────────────
    # 分支 A：价格在操作中枢上方 → 趋势状态，不再用历史旧低做止损
    # ────────────────────────────────────────────────────────────
    if zs_dep == "above" and op_zg > 0:

        # A1. 类二买：中枢上方已出现回踩，回踩低点守住 ZG 上方
        # 止损 = 回踩低点（free_bis 中的最低价），精确且当日可执行
        if free_count >= 1 and free_dir == "down" and free_low > op_zg:
            # 精细止损：若 5分中枢 ZD 在 free_low 附近，优先用 5分 ZD
            fine_stop = l3_op_zd if (l3_op_zd > 0 and l3_op_zd >= free_low * 0.995) else free_low
            stop_desc = (f"{l3_name}ZD={fine_stop:.2f}" if fine_stop == l3_op_zd
                         else f"回踩低点{fine_stop:.2f}")
            return {
                "node": "类二买",
                "confidence": "中",
                "stop_loss": round(fine_stop, 2),
                "stop_basis": (
                    f"{l2_name}操作中枢ZG={op_zg:.2f}上方回踩，"
                    f"守住{fine_stop:.2f}→结构完整，跌破→类二买失败"
                ),
                "entry_zone": (round(fine_stop, 2), round(free_low * 1.02, 2)),
                "next_watch": (
                    f"守住{fine_stop:.2f}，{l3_name}突破确认→入场；跌破→观望等结构重建"
                    if l3_op_zg > 0 else
                    f"守住{fine_stop:.2f}出现向上分型→入场；跌破→观望等结构重建"
                ),
                "action": (
                    f"类二买：回踩守住{stop_desc}，{l3_name}突破确认后入场"
                    if l3_op_zg > 0 else
                    f"类二买：回踩守住{stop_desc}，出现向上分型后入场"
                ),
                "position_hint": "30-50%",
            }

        # A2. 中枢上方无明显回踩 → 先检测5分级别是否在ZG附近窄幅盘整
        # 窄幅震荡说明多空在此胶着蓄力，一旦盘整上沿突破=精确入场点
        coiling = _detect_coiling_near_pivot(l3, op_zg)
        if coiling["is_coiling"]:
            fake_note = (
                f"，已出现{coiling['fake_breakdown_count']}次假跌破后收回，主力洗盘完成，阻力出清"
                if coiling["fake_breakdown"] else ""
            )
            has_bp = coiling.get("has_buy_point_support", False)

            if has_bp:
                # ── 有买点支撑：文章"位置3"，有效突破信号 ──
                conf = "高" if coiling["fake_breakdown"] else "中"
                hint = "50-70%" if coiling["fake_breakdown"] else "30-50%"
                return {
                    "node": "三买上沿_盘整",
                    "confidence": conf,
                    "stop_loss": coiling["stop_line"],
                    "stop_basis": (
                        f"盘整低点{coiling['stop_line']:.2f}（{l3_name}底背驰支撑，止损锚精确）{fake_note}；"
                        f"跌破说明盘整买点失效，不入场"
                    ),
                    "entry_zone": (coiling["stop_line"], coiling["trigger_line"]),
                    "next_watch": (
                        f"盯住{coiling['trigger_line']:.2f}突破→入场，"
                        f"止损={coiling['stop_line']:.2f}"
                    ),
                    "action": (
                        f"{'⚡ 洗盘完成，信号强化！' if coiling['fake_breakdown'] else ''}"
                        f"三买上沿盘整 + {l3_name}底背驰买点支撑，"
                        f"突破{coiling['trigger_line']:.2f}即入场，"
                        f"止损={coiling['stop_line']:.2f}（振幅{coiling['range_pct']*100:.1f}%）"
                    ),
                    "position_hint": hint,
                    "coiling_info": coiling,
                }
            else:
                # ── 无买点支撑：文章"位置1/2"，降级为观察 ──
                # 盘整存在但低点没有底背驰，突破后容易被止损，不操作
                return {
                    "node": "三买上沿_盘整",
                    "confidence": "低",
                    "stop_loss": coiling["stop_line"],
                    "stop_basis": (
                        f"盘整低点{coiling['stop_line']:.2f}，但{l3_name}底部无背驰/买点结构，"
                        f"属于位置1/2，突破后容易被止损"
                    ),
                    "entry_zone": None,
                    "next_watch": (
                        f"等待{l3_name}盘整低点出现底背驰后再关注突破，"
                        f"当前突破不操作"
                    ),
                    "action": (
                        f"⚠️ 三买上沿盘整，但{l3_name}低点无买点支撑（位置1/2），"
                        f"观察为主，等底背驰形成后再考虑入场"
                    ),
                    "position_hint": "观望",
                    "coiling_info": coiling,
                }

        # A2b. 无盘整 → 流畅运行 or 等待回踩
        stop_ref = l3_op_zg if l3_op_zg > op_zg else op_zg
        stop_label = (f"{l3_name}ZG={stop_ref:.2f}" if stop_ref == l3_op_zg
                      else f"{l2_name}ZG={op_zg:.2f}")

        # ── 流畅上涨段识别：中枢上方无自由笔（刚突破），趋势流畅不追入 ──
        # 文章："当走势步入流畅运行阶段，这样的回调位置就没有了"
        if free_count == 0:
            return {
                "node": "流畅上涨_不追入",
                "confidence": "中",
                "stop_loss": round(op_zg, 2),
                "stop_basis": f"{l2_name}ZG={op_zg:.2f}，跌回以下=突破失效",
                "entry_zone": None,
                "next_watch": (
                    f"趋势流畅运行中，无安全入场位。"
                    f"等回踩形成新中枢后，在新中枢ZG附近寻找{l3_name}买点"
                ),
                "action": (
                    f"🚫 流畅上涨段，不追入。"
                    f"等价格回踩{stop_label}形成回调结构，再找买点入场"
                ),
                "position_hint": "观望",
            }

        # 有自由笔但未形成盘整：等回踩稳定
        next_entry = f"等回踩稳定在{l2_name}ZG={op_zg:.2f}以上，{l3_name}出现突破确认→入场"
        return {
            "node": "三买_确认",
            "confidence": "中",
            "stop_loss": round(stop_ref, 2),
            "stop_basis": f"操作中枢ZG={op_zg:.2f}，跌回以下=上方趋势信号暂时失效",
            "entry_zone": (round(op_zg, 2), round(op_zg * 1.05, 2)),
            "next_watch": next_entry,
            "action": f"中枢上方有回踩迹象，{next_entry}",
            "position_hint": "观望（等回踩确认后再入）",
        }

    # ────────────────────────────────────────────────────────────
    # 分支 B：价格在操作中枢内部 → 震荡，节点逻辑用 op_zg/op_zd
    # ────────────────────────────────────────────────────────────
    elif zs_dep == "inside" and op_zg > 0 and op_zd > 0:

        # B1. 三买确认（FSM 状态）
        if state == "THIRD_BUY_CONFIRMED" or "三买确认" in patterns_str:
            pullback_low = bots[-1] if bots else op_zg
            return {
                "node": "三买_确认",
                "confidence": "高",
                "stop_loss": round(pullback_low, 2) if pullback_low > 0 else round(op_zg, 2),
                "stop_basis": f"ZG={op_zg:.2f}回踩低点，跌破=三买失败",
                "entry_zone": (round(pullback_low, 2), round(op_zg * 1.02, 2)) if pullback_low > 0 else None,
                "next_watch": f"守住ZG={op_zg:.2f}后顺势持仓",
                "action": "三买确认，入场，止损=ZG回踩低点",
                "position_hint": "30-50%",
            }

        # B2. 二卖_形成（禁止入场）
        if (is_lower_high and len(tops) >= 2 and tops[-2] > op_zg and price < op_zg):
            next_watch_price = bots[-1] if bots else 0
            return {
                "node": "二卖_形成",
                "confidence": "高",
                "stop_loss": None,
                "stop_basis": "卖点方向，空仓无止损",
                "entry_zone": None,
                "next_watch": (f"等继续下跌出现底背驰（低于{next_watch_price:.2f}）→一买机会"
                               if next_watch_price else "等底背驰出现→一买机会"),
                "action": "🔴 二卖！顶不创新高，禁止入场，这是卖点",
                "position_hint": "观望",
            }

        # B3. 一卖触发
        if has_top_div and price >= op_zg * 0.97:
            return {
                "node": "一卖_触发",
                "confidence": "高" if div.get("severity") == "高危" else "中",
                "stop_loss": None,
                "stop_basis": "一卖方向向下，空仓无止损",
                "entry_zone": None,
                "next_watch": "等顶背驰确认→下跌段启动→底背驰出现→一买",
                "action": "🔴 一卖区域！不追高，等下跌完成底背驰再入",
                "position_hint": "观望",
            }

        # B4. 中枢震荡（默认）
        return {
            "node": "中枢震荡",
            "confidence": "低",
            "stop_loss": round(op_zd, 2),
            "stop_basis": f"{l2_name}中枢ZD={op_zd:.2f}，跌破方向向下",
            "entry_zone": (round(op_zd, 2), round(op_zg, 2)),
            "next_watch": f"突破{l2_name}ZG={op_zg:.2f}→三买；跌破{l2_name}ZD={op_zd:.2f}→等底背驰",
            "action": "⚖️ 中枢震荡，等方向选择",
            "position_hint": "观望",
        }

    # ────────────────────────────────────────────────────────────
    # 分支 C：价格在操作中枢下方 → 寻底阶段，二买/一买/主跌
    # ────────────────────────────────────────────────────────────
    else:  # zs_dep == "below" or "unknown"

        # C0. 无中枢数据
        if op_zg == 0 and op_zd == 0:
            return {
                "node": "构建中",
                "confidence": "低",
                "stop_loss": None,
                "stop_basis": "尚未形成中枢，无结构参考位",
                "entry_zone": None,
                "next_watch": "等待三笔重叠形成中枢后再判断方向",
                "action": "🔵 走势构建中，等中枢形成后判断买卖点",
                "position_hint": "观望",
            }

        # C1. 二买_形成（最高性价比）
        # 条件：底部抬高（不创新低）+ 前底曾跌破操作中枢ZD（一买已发生）
        if (is_higher_low and len(bots) >= 2 and op_zd > 0 and bots[-2] < op_zd):
            prev_low = bots[-2]  # 一买低点 = 止损锚
            curr_low = bots[-1]  # 当前底 > 前底
            conf = "高" if has_bottom_div else "中"
            return {
                "node": "二买_形成",
                "confidence": conf,
                "stop_loss": round(prev_low, 2),
                "stop_basis": f"前低（一买低点）{prev_low:.2f}，跌破=一买失败结构破坏",
                "entry_zone": (round(curr_low, 2), round(curr_low * 1.03, 2)),
                "next_watch": (
                    f"守住{curr_low:.2f}，{l3_name}突破确认→入场；跌破{prev_low:.2f}→放弃"
                    if l3_op_zg > 0 else
                    f"守住{curr_low:.2f}出现向上分型→入场；跌破{prev_low:.2f}→放弃"
                ),
                "action": "⭐ 二买！缠论最高性价比。止损=前低，入场仓位参考区间套深度",
                "position_hint": "30-50%（有区间套则50-70%）",
            }

        # C2. 一买_机会
        if has_bottom_div and price <= op_zd * 1.03:
            stop_1buy = bots[-1] if bots else round(price * 0.95, 2)
            return {
                "node": "一买_机会",
                "confidence": "高" if div.get("severity") == "高危" else "中",
                "stop_loss": round(stop_1buy, 2),
                "stop_basis": f"背驰确认笔最低价{stop_1buy:.2f}，跌破=背驰判断失败",
                "entry_zone": (round(stop_1buy, 2), round(op_zd, 2)) if op_zd > 0 else None,
                "next_watch": f"⚠️ 更佳选择：等回调不创新低（>{stop_1buy:.2f}）形成二买再入",
                "action": "⚠️ 一买，轻仓观察。最优操作是等二买确认",
                "position_hint": "10-20%（轻仓试探）",
            }

        # C3. 主跌延续
        if state in ("DOWNWARD_LEAVING", "THIRD_SELL_CONFIRMED") and not has_bottom_div:
            return {
                "node": "主跌延续",
                "confidence": "中",
                "stop_loss": None,
                "stop_basis": "主跌阶段，空仓无止损",
                "entry_zone": None,
                "next_watch": f"等底背驰信号（{l2_name}ZD={op_zd:.2f}附近）→一买机会",
                "action": "🔴 主跌延续，严格空仓，等底背驰",
                "position_hint": "观望",
            }

        # C4. 中枢下方震荡等待
        return {
            "node": "中枢震荡",
            "confidence": "低",
            "stop_loss": None,
            "stop_basis": f"价格在{l2_name}中枢（{op_zd:.2f}–{op_zg:.2f}）下方，等止跌信号",
            "entry_zone": None,
            "next_watch": f"等{l2_name}ZD={op_zd:.2f}附近出现底背驰或底分型后考虑入场",
            "action": f"⚖️ {l2_name}中枢下方等待，不追跌",
            "position_hint": "观望",
        }



# ═══════════════════════════════════════════════════════════════
# 八B、空仓路径预案生成
# ═══════════════════════════════════════════════════════════════

def _build_empty_position_classes(l1: dict, l2: dict, l3: Optional[dict] = None) -> list:
    """空仓路径：基于买卖点生命周期节点生成预案。

    关键逻辑：先评估高级别上下文（l1），再运行 l2 级别节点识别。
    若高级别偏多而 l2 误判为"二卖_形成"或"主跌延续"，用上下文覆盖节点，
    避免"日线底背驰→二买→30分测三买"被错误标记为"禁止入场的二卖"。
    """
    # ── 先评估上下文 ──
    ctx  = _assess_center_context(l1, l2, l3)
    ctx_bias = ctx["bias"]

    node_info = _detect_lifecycle_node(l2, l3)
    node      = node_info["node"]

    # ── 上下文偏多 × l2误判卖点 → 覆盖为"中枢震荡"走多情形分支 ──
    # 场景：日线下跌笔内部底背驰已发生（底部抬高），30分上笔脱离日线中枢
    # 测试三买压力位时，30分顶底序列满足"顶不创新高"→ _detect_lifecycle_node 误判二卖。
    # 高级别偏多才是真实背景，强制覆盖。
    _BEARISH_NODES = {"二卖_形成", "一卖_触发", "主跌延续"}
    # C2 修复：高危顶背驰不被偏多上下文覆盖
    # 设计意图：防止"日线底背驰→二买→30分测三买"被误判为二卖。
    # 但若30分顶背驰已达高危程度，说明卖点是真实的，不应覆盖。
    _l2_div = l2.get("div_info") or {}
    _is_severe_top_div = (
        _l2_div.get("type") == "顶背驰"
        and _l2_div.get("severity") == "高危"
    )
    if ctx_bias == "偏多" and node in _BEARISH_NODES and not _is_severe_top_div:
        node = "中枢震荡"
        node_info = {**node_info, "node": "中枢震荡", "confidence": "中"}
    price     = l2.get("price", 0)
    # ── 优先使用 chan_engine 的操作中枢边界；若尚未填充则回退旧字段 ──
    l2_zg     = l2.get("zs_operative_zg", 0) or l2.get("zg", 0)
    l2_zd     = l2.get("zs_operative_zd", 0) or l2.get("zd", 0)
    l2_name   = _FORWARD_LEVEL_NAMES.get(l2.get("level", ""), "30分")
    l3        = l3 or {}
    l3_zg     = l3.get("zs_operative_zg", 0) or l3.get("zg", 0)
    l3_name   = _FORWARD_LEVEL_NAMES.get(l3.get("level", ""), "5分")
    l1_zg     = l1.get("zs_operative_zg", 0) or l1.get("zg", 0)
    l1_name   = _FORWARD_LEVEL_NAMES.get(l1.get("level", ""), "日线")
    seq       = l2.get("bi_extreme_seq") or {}
    bots      = seq.get("bots", [])
    stop      = node_info.get("stop_loss")
    entry_z   = node_info.get("entry_zone")
    hint      = node_info.get("position_hint", "观望")

    # ── 入场触发描述：优先用5分突破，无数据退回向上分型 ──
    def _entry_trigger(level_zg: float, level_name: str) -> str:
        if level_zg > 0:
            return f"{level_name}突破{level_zg:.2f}确认"
        return "出现向上分型"

    # ── 二买（最高性价比） ──
    if node == "二买_形成":
        prev_low = stop
        curr_low = bots[-1] if bots else price
        trigger  = _entry_trigger(l3_zg, l3_name)
        return [
            {
                "id": "甲",
                "condition": f"回调守住前低 {prev_low:.2f}，{trigger}",
                "meaning": (
                    f"二买确认！前低={prev_low:.2f}已验证有买盘，此次不创新低说明多头结构成立。"
                    f"缠论最高性价比：止损={prev_low:.2f}，空间指向{l2_zg:.2f}以上。"
                ),
                "action": f"{trigger}后入场，仓位 {hint}；止损 {prev_low:.2f}",
                "stop_loss": round(prev_low, 2),
                "stop_reason": f"前低（一买低点）{prev_low:.2f}，跌破=一买结构失败，立即止损",
                "lifecycle_node": node,
                "confidence": node_info["confidence"],
                "position_size": hint,
                "next_watch": node_info["next_watch"],
            },
            {
                "id": "乙",
                "condition": f"跌破前低 {prev_low:.2f}",
                "meaning": f"一买失败，上涨结构破坏，需等待更低位底背驰重新形成一买",
                "action": "放弃，等更低位底背驰 → 重新识别新的一买",
                "stop_loss": None,
                "stop_reason": "空仓无止损",
                "lifecycle_node": node,
                "confidence": "高",
                "position_size": "观望",
                "next_watch": "等新低出现后观察是否出现底背驰",
            },
        ]

    # ── 三买上沿盘整（新节点：比三买_确认更精确的入场时机）──
    if node == "三买上沿_盘整":
        coiling = node_info.get("coiling_info", {})
        trigger_line = coiling.get("trigger_line", l2_zg)
        stop_line    = coiling.get("stop_line", l2_zg)
        fake_note    = (
            f"⚡ 洗盘完成（{coiling.get('fake_breakdown_count', 0)}次假跌破已收回），阻力出清，"
            if coiling.get("fake_breakdown") else ""
        )
        trigger = _entry_trigger(l3_zg, l3_name) if l3_zg > 0 else f"突破盘整上沿{trigger_line:.2f}"
        return [
            {
                "id": "甲",
                "condition": f"盘整上沿{trigger_line:.2f}向上突破确认",
                "meaning": (
                    f"{fake_note}5分级别在{l2_name}ZG={l2_zg:.2f}附近窄幅盘整"
                    f"（振幅{coiling.get('range_pct', 0)*100:.1f}%），"
                    f"止损={stop_line:.2f}（比大级别ZG更精确），"
                    f"突破即确认{l2_name}三买成立。"
                ),
                "action": f"突破{trigger_line:.2f}后立即跟进，仓位 {hint}；止损={stop_line:.2f}",
                "stop_loss": round(stop_line, 2),
                "stop_reason": f"盘整低点{stop_line:.2f}跌破=盘整失败，三买结构未成立",
                "lifecycle_node": node,
                "confidence": node_info["confidence"],
                "position_size": hint,
                "next_watch": node_info["next_watch"],
            },
            {
                "id": "乙",
                "condition": f"跌破盘整低点{stop_line:.2f}",
                "meaning": f"盘整失败，{l2_name}三买结构暂不成立，等待更低位重新蓄力",
                "action": "不入场，等结构重建后重新判断",
                "stop_loss": None,
                "stop_reason": "空仓无止损",
                "lifecycle_node": node,
                "confidence": "高",
                "position_size": "观望",
                "next_watch": f"等{l2_name}ZG={l2_zg:.2f}附近再次形成底部结构",
            },
        ]

    # ── 三买确认 ──
    if node == "三买_确认":
        pullback_low = stop if stop else l2_zg
        trigger      = _entry_trigger(l3_zg, l3_name)
        return [
            {
                "id": "甲",
                "condition": f"守住ZG={l2_zg:.2f}回踩低点 {pullback_low:.2f}，{trigger}",
                "meaning": (
                    f"三买确认：{l2_name}向上脱离中枢后回踩守住ZG={l2_zg:.2f}，结构完整。"
                    f"{l1_name}趋势延伸空间指向ZG={l1_zg:.2f}以上。"
                ),
                "action": f"{trigger}后跟进，仓位 {hint}；止损={pullback_low:.2f}",
                "stop_loss": round(pullback_low, 2),
                "stop_reason": f"ZG={l2_zg:.2f}回踩低点{pullback_low:.2f}跌破=三买失败",
                "lifecycle_node": node,
                "confidence": node_info["confidence"],
                "position_size": hint,
                "next_watch": node_info["next_watch"],
            },
            {
                "id": "乙",
                "condition": f"跌破ZG={l2_zg:.2f}",
                "meaning": f"三买失败，重回{l2_name}中枢，等待再次确认",
                "action": "不入场，观望",
                "stop_loss": None,
                "stop_reason": "空仓无止损",
                "lifecycle_node": node,
                "confidence": "高",
                "position_size": "观望",
                "next_watch": f"关注{l2_name}中枢内是否形成底部再三买",
            },
        ]

    # ── 类二买 ──
    if node == "类二买":
        trigger = _entry_trigger(l3_zg, l3_name)
        return [
            {
                "id": "甲",
                "condition": f"回踩守住低点 {stop:.2f}，{trigger}",
                "meaning": f"类二买形成：三买后回踩守住，止损={stop:.2f}，性价比仅次于二买",
                "action": f"{trigger}后入场，仓位 {hint}；止损={stop:.2f}",
                "stop_loss": round(stop, 2),
                "stop_reason": f"回踩低点{stop:.2f}跌破=类二买失败",
                "lifecycle_node": node,
                "confidence": node_info["confidence"],
                "position_size": hint,
                "next_watch": node_info["next_watch"],
            },
            {
                "id": "乙",
                "condition": f"跌破 {stop:.2f}",
                "meaning": "三买结构破坏，等待中枢重新形成后再三买",
                "action": "不入场，观望",
                "stop_loss": None,
                "stop_reason": "空仓无止损",
                "lifecycle_node": node,
                "confidence": "高",
                "position_size": "观望",
                "next_watch": "等中枢形成后再三买",
            },
        ]

    # ── 一买（观察为主） ──
    if node == "一买_机会":
        stop_1b = stop if stop else round(price * 0.95, 2)
        return [
            {
                "id": "甲",
                "condition": "底背驰确认，此处轻仓试探",
                "meaning": (
                    f"一买信号出现，止损={stop_1b:.2f}（背驰确认笔最低价）。"
                    "缠论最优操作：轻仓观察，等回调不创新低形成二买再加仓。"
                ),
                "action": f"轻仓10-20%，止损={stop_1b:.2f}。重点等二买确认后再加仓",
                "stop_loss": round(stop_1b, 2),
                "stop_reason": f"背驰确认笔最低价{stop_1b:.2f}，跌破=背驰失败",
                "lifecycle_node": node,
                "confidence": node_info["confidence"],
                "position_size": hint,
                "next_watch": node_info["next_watch"],
            },
            {
                "id": "乙",
                "condition": f"跌破 {stop_1b:.2f}，背驰失效",
                "meaning": "一买判断失误，继续下跌，等待更低位底背驰",
                "action": "止损离场，等下一个底背驰信号",
                "stop_loss": None,
                "stop_reason": "空仓无止损",
                "lifecycle_node": node,
                "confidence": "高",
                "position_size": "观望",
                "next_watch": "等更低位新的底背驰",
            },
        ]

    # ── 中枢震荡 → 按上下文展开甲/乙(/丙)多情形 ──
    if node == "中枢震荡":
        # E2 修复：复用入口处已计算的 ctx，避免重复调用 _assess_center_context
        bias     = ctx["bias"]
        identity = ctx["identity"]
        ctx_desc = ctx["context_desc"]
        bull_sc  = ctx["bull_scenario"]
        bear_sc  = ctx["bear_scenario"]

        if bias == "偏多":
            # 甲：偏多条件成立，给出明确入场条件
            # 乙：守不住，降级处理
            # 丙：彻底破位，空仓等待
            # 止损锚：优先使用5分操作中枢ZG（今日可执行），否则用30分操作中枢ZG
            bull_stop = round(l3_zg, 2) if l3_zg > 0 else (round(l2_zg, 2) if l2_zg > 0 else None)
            stop_level = l3_name if l3_zg > 0 else l2_name
            stop_zg_val = l3_zg if l3_zg > 0 else l2_zg
            bull_stop_txt = (f"跌回{stop_level}ZG={stop_zg_val:.2f}以下"
                             if bull_stop else "回踩不创新低")
            return [
                {
                    "id": "甲",
                    "condition": bull_sc,
                    "meaning": ctx_desc + f" 偏多背景下，{bull_sc}是结构成立的关键确认。",
                    "action": (
                        f"条件成立后轻仓介入（{hint}），"
                        f"止损：{bull_stop_txt}→视为假突破，离场"
                    ),
                    "stop_loss": bull_stop,
                    "stop_reason": f"{bull_stop_txt}→偏多结构失效，离场防守",
                    "lifecycle_node": node,
                    "confidence": "中",
                    "position_size": hint,
                    "next_watch": bull_sc,
                },
                {
                    "id": "乙",
                    "condition": bear_sc.split("→")[0] if "→" in bear_sc else bear_sc,
                    "meaning": f"偏多条件未成立：{bear_sc}。降级观望，等待新的结构信号。",
                    "action": "不入场，观望，等待更清晰的结构确认",
                    "stop_loss": None,
                    "stop_reason": "空仓无止损",
                    "lifecycle_node": node,
                    "confidence": "高",
                    "position_size": "观望",
                    "next_watch": bear_sc,
                },
                {
                    "id": "丙",
                    "condition": f"跌破{l2_name}ZD={l2_zd:.2f}",
                    "meaning": f"{l2_name}中枢下沿破位，{identity}结构完全失效，偏多前提不再成立。",
                    "action": f"严格空仓，等{l2_name}ZD={l2_zd:.2f}下方出现底背驰后重新评估",
                    "stop_loss": None,
                    "stop_reason": "空仓无止损",
                    "lifecycle_node": node,
                    "confidence": "高",
                    "position_size": "观望",
                    "next_watch": f"等{l2_name}ZD={l2_zd:.2f}以下出现底背驰→重新识别一买",
                },
            ]

        elif bias == "偏空":
            return [
                {
                    "id": "甲",
                    "condition": f"等待底背驰信号出现（{l2_name}ZD={l2_zd:.2f}附近或更低）",
                    "meaning": ctx_desc + f" 偏空背景下不宜入场，等底部结构转折信号。",
                    "action": f"空仓观望，等底背驰确认→识别一买机会",
                    "stop_loss": None,
                    "stop_reason": "偏空背景，空仓无止损",
                    "lifecycle_node": node,
                    "confidence": "中",
                    "position_size": "观望",
                    "next_watch": bull_sc,
                },
                {
                    "id": "乙",
                    "condition": f"继续下跌突破{l2_name}ZD={l2_zd:.2f}",
                    "meaning": f"中枢下沿破位，下跌趋势延续。等更低位底背驰后重新识别一买。",
                    "action": f"继续空仓，等{l2_name}ZD={l2_zd:.2f}以下底背驰出现后再考虑",
                    "stop_loss": None,
                    "stop_reason": "空仓无止损",
                    "lifecycle_node": node,
                    "confidence": "高",
                    "position_size": "观望",
                    "next_watch": bear_sc,
                },
            ]

        else:  # 收敛待选
            # H4 修复：止损融入结构叙述，不再以"止损=数字"形式跳出
            _bull_stop_txt = (
                f"若突破后价格回落至{l2_name}ZG={l2_zg:.2f}以下，说明突破为假，结构未形成，应止损出场"
                if l2_zg > 0 else "若突破后回踩创新低，说明结构未形成，应止损"
            )
            return [
                {
                    "id": "甲",
                    "condition": bull_sc,
                    "meaning": ctx_desc + f" 方向向上选择条件：{bull_sc}。",
                    "action": f"向上突破{l2_name}ZG={l2_zg:.2f}并获得确认后轻仓跟进。{_bull_stop_txt}。",
                    "stop_loss": round(l2_zg, 2) if l2_zg > 0 else None,
                    "stop_reason": f"突破后跌回{l2_name}ZG={l2_zg:.2f}以下，假突破结构不成立",
                    "lifecycle_node": node,
                    "confidence": "低",
                    "position_size": "轻仓",
                    "next_watch": bull_sc,
                },
                {
                    "id": "乙",
                    "condition": bear_sc.split("→")[0] if "→" in bear_sc else bear_sc,
                    "meaning": ctx_desc + f" 方向向下选择条件：{bear_sc}。",
                    "action": f"严格空仓，等{l2_name}中枢下沿{l2_zd:.2f}以下出现底背驰信号后再评估入场机会",
                    "stop_loss": None,
                    "stop_reason": "空仓无止损",
                    "lifecycle_node": node,
                    "confidence": "中",
                    "position_size": "观望",
                    "next_watch": bear_sc,
                },
            ]

    # ── 流畅上涨段（不追入，等回踩） ──
    if node == "流畅上涨_不追入":
        return [
            {
                "id": "甲",
                "condition": f"价格回踩{l2_name}ZG={l2_zg:.2f}附近，形成回调结构",
                "meaning": (
                    f"趋势当前处于流畅上涨段，无安全入场位。"
                    f"此阶段强行追入是「大级别做突破」的典型误区——"
                    f"缺乏战略性止损锚，被止损概率极高。"
                    f"等回踩形成新中枢后，在新中枢ZG附近寻找{l3_name}买点。"
                ),
                "action": (
                    f"🚫 不追入。等回踩{l2_name}ZG={l2_zg:.2f}附近，"
                    f"{l3_name}出现底背驰后再入场"
                ),
                "stop_loss": None,
                "stop_reason": "空仓无止损",
                "lifecycle_node": node,
                "confidence": node_info["confidence"],
                "position_size": "观望",
                "next_watch": node_info.get("next_watch", ""),
            }
        ]

    # ── 三买上沿盘整（无买点支撑，降级观察） ──
    if node == "三买上沿_盘整" and node_info.get("confidence") == "低":
        coiling = node_info.get("coiling_info", {})
        trigger = coiling.get("trigger_line", l2_zg)
        stop    = coiling.get("stop_line", l2_zg)
        return [
            {
                "id": "甲",
                "condition": f"{l3_name}盘整低点出现底背驰信号",
                "meaning": (
                    f"盘整形态存在（振幅{coiling.get('range_pct',0)*100:.1f}%），"
                    f"但{l3_name}底部尚无底背驰/买点结构，属于文章描述的「位置1/2」。"
                    f"此类突破进场后大概率被反复止损，等买点出现再操作。"
                ),
                "action": (
                    f"观察等待。{l3_name}低点形成底背驰后，"
                    f"再盯住{trigger:.2f}突破入场，止损={stop:.2f}"
                ),
                "stop_loss": None,
                "stop_reason": "买点未形成，空仓无止损",
                "lifecycle_node": node,
                "confidence": "低",
                "position_size": "观望",
                "next_watch": f"等{l3_name}底部底背驰形成→突破{trigger:.2f}→入场",
            }
        ]

    # ── 卖点/主跌/构建中（空仓等待类） ──
    return [
        {
            "id": "甲",
            "condition": node_info.get("next_watch", "等待结构信号"),
            "meaning": f"当前处于【{node}】阶段，{node_info.get('action', '')}",
            "action": node_info.get("action", "观望"),
            "stop_loss": None,
            "stop_reason": node_info.get("stop_basis", "空仓无止损"),
            "lifecycle_node": node,
            "confidence": node_info["confidence"],
            "position_size": "观望",
            "next_watch": node_info.get("next_watch", ""),
        }
    ]


# ═══════════════════════════════════════════════════════════════
# 八B、止盈六阶段状态机（Task #9）
# ═══════════════════════════════════════════════════════════════

def _detect_holding_stage(
    holding: dict,
    l1: dict,   # 日线
    l2: dict,   # 30分钟
    l3: dict,   # 5分钟
) -> dict:
    """止盈六阶段状态机（Stage 0-5）。Task #9

    Stage 0 走势验证期  —— 入场后等30分走出上涨笔（由 _validate_entry_thesis 处理）
    Stage 1 验证期      —— 浮盈 < 1×止损距离，守原始止损
    Stage 2 保本期      —— 浮盈 ≥ 1×止损距离，止损上移至成本线±0.5%
    Stage 3 利润保护期  —— 浮盈 ≥ 2×止损距离，跟踪30分中枢ZG（台阶止损）
    Stage 4 减速预警    —— 30分顶背驰确认为转折，减仓50%
    Stage 5 趋势终结    —— 日线顶背驰 或 跌破台阶止损，清仓

    台阶止损：只上移不下移。每次30分中枢ZG升高，台阶上移。

    Returns:
        {
          stage: int,                  # 0-5
          label: str,
          trailing_stop: float,        # 当前有效止损价
          trailing_stop_basis: str,    # 止损依据说明
          locked_profit_pct: float,    # 即使触发止损锁定的最低利润%（负数=仍亏）
          m30_top_beichi: str,         # "无"|"疑似转折"|"中继确认"|"转折确认"
          day_top_beichi: str,         # "无"|"疑似转折"|"中继确认"|"转折确认"
          action: str,                 # 操作建议
          should_notify: bool,         # 是否需要推送通知
        }
    """
    cost   = holding.get("cost", 0)
    price  = l2.get("price", 0) or l1.get("price", 0)
    if not cost or not price:
        return {
            "stage": 0, "label": "数据不足",
            "trailing_stop": 0, "trailing_stop_basis": "数据缺失",
            "locked_profit_pct": 0,
            "m30_top_beichi": "无", "day_top_beichi": "无",
            "action": "数据不足，无法判断。", "should_notify": False,
        }

    pnl_pct = (price - cost) / cost

    # 原始止损 = holding 里记录的，或5分中枢ZG
    orig_stop = (
        holding.get("stop_loss_price", 0)
        or holding.get("m5_entry_zg", 0)
        or (l3.get("zs_operative_zg", 0) if l3 else 0)
    )
    stop_distance = (cost - orig_stop) if orig_stop > 0 else cost * 0.05  # 无止损默认5%

    # 台阶止损 = 之前记录的或从30分中枢动态计算
    m30_zg = l2.get("zs_operative_zg", 0) or l2.get("zg", 0)
    stored_trailing = holding.get("trailing_stop_price", 0) or 0
    # 台阶只上移：取 max(存储的台阶, 30分ZG)，但不能超过当前价
    trailing_stop = max(stored_trailing, m30_zg if m30_zg > 0 else 0)
    if trailing_stop >= price:
        trailing_stop = stored_trailing or orig_stop  # 防止台阶超过价格

    # 顶背驰检测
    m30_div = _detect_top_divergence(l2, price)
    day_div = _detect_top_divergence(l1, price)
    m30_beichi = m30_div.get("classification", "无背驰")
    day_beichi = day_div.get("classification", "无背驰")

    # ── 终结条件判断（最高优先级）──
    trailing_broken = trailing_stop > 0 and price <= trailing_stop
    day_reversal    = day_beichi == "转折确认"

    if trailing_broken or day_reversal:
        reason = "台阶止损触及" if trailing_broken else "日线顶背驰转折确认"
        return {
            "stage":               5,
            "label":               "趋势终结",
            "trailing_stop":       round(trailing_stop, 2),
            "trailing_stop_basis": f"{reason}，清仓信号",
            "locked_profit_pct":   round(pnl_pct, 4),
            "m30_top_beichi":      m30_beichi,
            "day_top_beichi":      day_beichi,
            "action":              f"🚨 {reason}，建议清仓出场。",
            "should_notify":       True,
        }

    # Stage 4：30分转折背驰
    if m30_beichi == "转折确认":
        locked = (trailing_stop - cost) / cost if trailing_stop > cost else pnl_pct
        return {
            "stage":               4,
            "label":               "减速预警",
            "trailing_stop":       round(trailing_stop, 2),
            "trailing_stop_basis": f"30分顶背驰转折确认，减仓后守台阶止损{trailing_stop:.2f}",
            "locked_profit_pct":   round(locked, 4),
            "m30_top_beichi":      m30_beichi,
            "day_top_beichi":      day_beichi,
            "action":              f"⚠️ 30分顶背驰转折，减仓50%锁利，剩余守台阶止损{trailing_stop:.2f}。",
            "should_notify":       True,
        }

    # Stage 3：浮盈 ≥ 2×止损距离 → 台阶追踪
    if pnl_pct >= 0 and (price - cost) >= 2 * stop_distance:
        trailing_basis = f"30分中枢ZG={m30_zg:.2f}" if m30_zg > 0 else "历史止损"
        locked = (trailing_stop - cost) / cost if trailing_stop > cost else 0
        return {
            "stage":               3,
            "label":               "利润保护期",
            "trailing_stop":       round(trailing_stop, 2),
            "trailing_stop_basis": trailing_basis,
            "locked_profit_pct":   round(locked, 4),
            "m30_top_beichi":      m30_beichi,
            "day_top_beichi":      day_beichi,
            "action":              (
                f"浮盈{pnl_pct:.1%}，进入台阶追踪。"
                f"台阶止损={trailing_stop:.2f}（{trailing_basis}）。"
                f"{'30分顶背驰疑似，等待确认。' if m30_beichi == '疑似转折' else '无背驰，继续持有。'}"
            ),
            "should_notify":       (m30_beichi == "疑似转折"),
        }

    # Stage 2：浮盈 ≥ 1×止损距离 → 止损上移到成本线
    cost_stop = cost * 0.995  # 成本线下0.5%（含手续费容差）
    if pnl_pct >= 0 and (price - cost) >= stop_distance:
        effective_stop = max(cost_stop, orig_stop)
        return {
            "stage":               2,
            "label":               "保本期",
            "trailing_stop":       round(effective_stop, 2),
            "trailing_stop_basis": f"止损上移至成本线({cost:.2f})，保住不亏",
            "locked_profit_pct":   round((effective_stop - cost) / cost, 4),
            "m30_top_beichi":      m30_beichi,
            "day_top_beichi":      day_beichi,
            "action":              f"浮盈{pnl_pct:.1%}，止损上移至{effective_stop:.2f}（成本线），保住不亏。继续持有。",
            "should_notify":       False,
        }

    # Stage 1：验证期（浮盈 < 1×止损距离，守原始止损）
    return {
        "stage":               1,
        "label":               "验证期",
        "trailing_stop":       round(orig_stop, 2) if orig_stop > 0 else round(cost * 0.95, 2),
        "trailing_stop_basis": f"原始止损={orig_stop:.2f}（5分入场中枢ZG）",
        "locked_profit_pct":   round(pnl_pct, 4),
        "m30_top_beichi":      m30_beichi,
        "day_top_beichi":      day_beichi,
        "action":              (
            f"浮盈{pnl_pct:.1%}，仍在验证期。"
            f"守住原始止损{orig_stop:.2f}，等待浮盈扩大到1×止损距离后进入保本期。"
        ),
        "should_notify":       False,
    }


# ═══════════════════════════════════════════════════════════════
# 八C、持仓路径预案生成
# ═══════════════════════════════════════════════════════════════

def _compute_cost_floor(price: float, cost: float) -> float:
    """按浮盈层级计算成本保护底线（不低于此价才考虑持仓）。"""
    if cost <= 0:
        return 0.0
    pnl = (price - cost) / cost
    if pnl >= 1.0:
        return cost * 1.5    # 浮盈100%+，保住150%成本
    elif pnl >= 0.5:
        return cost * 1.2    # 浮盈50%+，保住120%成本
    elif pnl >= 0.2:
        return cost * 1.0    # 浮盈20%+，保住成本
    else:
        return cost * 0.95   # 微亏/平，留5%容差


def _pnl_tone(pnl_pct: float) -> str:
    """按盈亏状态返回叙述语气词。"""
    if pnl_pct >= 1.0:
        return f"浮盈{pnl_pct*100:.0f}%，结构不利时持有缺乏依据"
    elif pnl_pct >= 0.3:
        return f"浮盈{pnl_pct*100:.0f}%，可以从容应对结构变化"
    elif pnl_pct >= 0.0:
        return f"小幅盈利{pnl_pct*100:.0f}%，结构偏弱时机会成本上升"
    elif pnl_pct >= -0.1:
        return f"微亏{abs(pnl_pct)*100:.0f}%，需要新的结构依据支撑持仓"
    else:
        return f"亏损{abs(pnl_pct)*100:.0f}%，继续持仓需明确结构依据"


# ═══════════════════════════════════════════════════════════════
# 八B-0、Risk 3 修复：Stage 0 走势验证期
# ═══════════════════════════════════════════════════════════════

def _validate_entry_thesis(holding: dict, l2: dict) -> dict:
    """Stage 0 走势验证期：入场后监控30分笔方向。

    Risk 3 修复点：
      雷达重设计方案 §2.4 定义：入场后至多10根30分K线内必须走出向上笔；
      若30分先走出向下笔=预案失效；超时=时间失效。
      此前入场后直接进入甲乙丙丁固定框架，没有验证期逻辑。

    所需 holding 字段：
      - entry_date:  str  入场日期 (YYYY-MM-DD)，来自 positions.entry_date
      - cost:        float 成本价
      - stop_loss_price: float 原始止损价

    所需 l2 字段（30分级别）：
      - detail_bis:    list  最近6笔数据，每笔含 start_date/end_date/is_up/is_sure
      - recent_klines: list  最近K线，每根含 date 字段

    Returns dict with keys:
      stage, label, status, action,
      m30_bi_direction, m30_bi_complete,
      bars_since_entry, bars_remaining,
      monitoring_detail, stop_loss (原始止损价)
    """
    MAX_BARS = 10   # 最多等10根30分K线（约2个交易日）

    entry_date = holding.get("entry_date", "") or ""
    orig_stop  = holding.get("stop_loss_price", 0)

    bis         = l2.get("detail_bis", [])
    recent_klines = l2.get("recent_klines", [])
    price       = l2.get("price", 0)

    # 计算入场后经过的30分K线数量（用 recent_klines 的 date 比对）
    bars_since_entry = 0
    if entry_date:
        for k in recent_klines:
            k_date = str(k.get("date", ""))[:10]
            if k_date > entry_date:
                bars_since_entry += 1

    bars_remaining = max(0, MAX_BARS - bars_since_entry)

    # 判断入场后30分的笔方向
    # 只看 start_date 在入场日期之后的笔
    post_entry_bis = [
        b for b in bis
        if str(b.get("start_date", ""))[:10] > entry_date
    ]

    m30_bi_direction = "未形成"
    m30_bi_complete  = False

    if post_entry_bis:
        first_bi = post_entry_bis[0]
        if first_bi["is_up"]:
            m30_bi_direction = "向上"
            # 向上笔已完成 = is_sure=True 且有后续笔（有顶分型确认）
            m30_bi_complete = (
                first_bi.get("is_sure", False) and
                len(post_entry_bis) >= 2
            )
        else:
            m30_bi_direction = "向下"  # 预案失效信号

    # 状态机
    if m30_bi_direction == "向下":
        status = "预案失效"
        action = (
            "🚨 建议出场（预案失效）：30分走出向下笔，5分三买为假突破，"
            "结构逻辑链第二环断裂。当前亏损通常小于止损亏损，主动出场。"
        )
    elif m30_bi_complete:
        status = "验证通过"
        action = "✅ 验证通过：30分已走出完整向上笔，进入 Stage 1 持仓管理。"
    elif bars_since_entry >= MAX_BARS:
        status = "时间失效"
        action = (
            f"⏰ 建议出场（时间失效）：入场后已经过 {bars_since_entry} 根30分K线，"
            "市场未按预期节奏运动，等待更清晰的信号。"
        )
    else:
        verb = "向上" if m30_bi_direction == "向上" else "尚未形成"
        status = "验证中"
        action = (
            f"⏳ 继续观察：30分笔{verb}，已过 {bars_since_entry} 根30分K线，"
            f"还剩 {bars_remaining} 根截止。若30分出现向下笔立即出场。"
        )

    monitoring_detail = (
        f"入场后30分走势：{m30_bi_direction}，"
        f"已过 {bars_since_entry} 根30分K线"
        + (f"，笔{'已' if m30_bi_complete else '未'}完成" if m30_bi_direction == "向上" else "")
        + f"。验证状态：{status}。"
    )

    # Task #8：5分中枢完整性判断（"价格在磨" vs "结构失效"）
    # 用 l2 里的 m5 数据（如果有），否则用 l2 本身（兼容直接传 m5 的场景）
    m5_data = l2.get("m5") or l2  # analyze_matrix_state 会把 m5 dict 挂到 l2 上
    m5_entry_zg   = holding.get("m5_entry_zg", 0)    # 入场时记录的5分中枢ZG
    m5_curr_price = m5_data.get("price", 0) or price  # 当前价
    m5_zhongshu_intact = True  # 默认结构完整
    if m5_entry_zg > 0 and m5_curr_price > 0:
        m5_zhongshu_intact = m5_curr_price > m5_entry_zg  # 价格在5分中枢上方 = 结构完整

    if m5_zhongshu_intact:
        structure_status  = "结构完整"
        holding_rationale = "价格在5分入场中枢上方，结构有效，等待走出。继续持有。"
    else:
        structure_status  = "结构失效"
        holding_rationale = f"价格已跌回5分入场中枢（ZG={m5_entry_zg:.2f}），入场假设失效，建议出场。"

    return {
        "stage":              0,
        "label":              "走势验证期",
        "status":             status,
        "action":             action,
        "m30_bi_direction":   m30_bi_direction,
        "m30_bi_complete":    m30_bi_complete,
        "bars_since_entry":   bars_since_entry,
        "bars_remaining":     bars_remaining,
        "monitoring_detail":  monitoring_detail,
        "stop_loss":          orig_stop,   # 验证期止损 = 原始止损不动
        # Task #8 新增：5分中枢完整性（持仓判断核心）
        "stage_0_extended": {
            "m5_zhongshu_intact": m5_zhongshu_intact,
            "m5_entry_zg":        m5_entry_zg,
            "structure_status":   structure_status,
            "holding_rationale":  holding_rationale,
        },
    }


def _stage0_to_class(stage0: dict) -> dict:
    """把 Stage 0 结果包装成与 甲/乙/丙/丁 相同的 class dict 格式，供前端统一渲染。"""
    status = stage0.get("status", "验证中")
    color_map = {
        "验证通过": "green",
        "预案失效": "red",
        "时间失效": "red",
        "验证中":   "yellow",
    }
    return {
        "id":           "Stage0",
        "condition":    f"走势验证期 — {status}",
        "meaning":      stage0.get("monitoring_detail", ""),
        "action":       stage0.get("action", ""),
        "stop_loss":    stage0.get("stop_loss", 0),
        "stop_reason":  "原始止损（验证期内止损不上移）",
        "trigger_level": "l2",
        "trigger_pct":  0,
        "stage0":       stage0,          # 完整数据，供前端展开
        "_color":       color_map.get(status, "yellow"),
    }


def _build_holding_classes(l1: dict, l2: dict, l3: Optional[dict] = None,
                            holding: Optional[dict] = None) -> list:
    """持仓路径：结构威胁 + 盈亏融合叙述。

    核心修复：
      丙情形（当日可触发）→ 用 l3（5分）ZG，不用 l2（30分）ZG
      丁情形（多日结构线）→ 用 l2（30分）ZG
    止损不单独跳出，融入结构叙述。

    Risk 3 修复：
      若 holding 中有 entry_date，优先执行 Stage 0 走势验证期检测。
      仅在验证通过（m30走出完整向上笔）后才继续甲乙丙丁持仓管理。
    """
    holding   = holding or {}

    # ── Risk 3：Stage 0 优先入口 ──
    # 有入场日期 + 验证尚未通过 → 进入走势验证期
    entry_date = holding.get("entry_date", "") or ""
    if entry_date:
        stage0 = _validate_entry_thesis(holding, l2)
        # 只有验证通过才继续走甲乙丙丁逻辑（Stage 1+）
        if stage0["status"] != "验证通过":
            return [_stage0_to_class(stage0)]
        # 验证通过，继续下方正常持仓管理
        logger.debug("Stage 0 验证通过 [%s]，进入 Stage 1+ 逻辑", holding.get("symbol", ""))

    cost      = holding.get("cost", 0)
    qty       = holding.get("qty", 0)
    price     = l2.get("price", 0)
    # ── 优先使用 chan_engine 操作中枢边界；旧字段作为兜底 ──
    l2_zg     = l2.get("zs_operative_zg", 0) or l2.get("zg", 0)
    l2_zd     = l2.get("zs_operative_zd", 0) or l2.get("zd", 0)
    l2_supp   = l2.get("ex_support", 0)
    l2_press  = l2.get("ex_pressure", 0)
    l2_dir    = l2.get("last_bi_dir", "unknown")
    l2_name   = _FORWARD_LEVEL_NAMES.get(l2.get("level", ""), "30分")
    l1_zg     = l1.get("zs_operative_zg", 0) or l1.get("zg", 0)
    l1_zd     = l1.get("zs_operative_zd", 0) or l1.get("zd", 0)
    l1_name   = _FORWARD_LEVEL_NAMES.get(l1.get("level", ""), "日线")
    l3        = l3 or {}
    l3_zg     = l3.get("zs_operative_zg", 0) or l3.get("zg", 0)
    l3_zd     = l3.get("zs_operative_zd", 0) or l3.get("zd", 0)
    l3_name   = _FORWARD_LEVEL_NAMES.get(l3.get("level", ""), "5分")
    div_info  = l2.get("div_info") or {}
    is_intraday = l2.get("level", "") in ["m1", "m5", "m15", "m30", "m60"]
    t1_warn   = "（T+1：日内买入次日才能卖出）"

    pnl_pct   = (price - cost) / cost if cost > 0 else 0
    cost_floor = _compute_cost_floor(price, cost)
    tone      = _pnl_tone(pnl_pct)

    # 技术止损 vs 成本保护取高（但不作为规则跳出，融入叙述）
    def tech_stop_vs_cost(tech: float) -> tuple[float, str]:
        eff = max(tech, cost_floor) if cost_floor > 0 else tech
        cost_str = (f"，成本保护底线={cost_floor:.2f}" if cost_floor > 0 and cost_floor > tech
                    else "")
        return round(eff, 2), cost_str

    # ── 甲：当日延续（5分ZG作为当日锚）──
    # 丙情形触发价用 l3_zg（5分），距当前价通常在当日涨跌幅内可达到
    l3_trigger = l3_zg if l3_zg > 0 else l2_zg
    l3_trigger_lbl = (f"{l3_name}ZG={l3_trigger:.2f}" if l3_zg > 0
                      else f"{l2_name}ZG={l3_trigger:.2f}（5分数据缺失，降级用30分）")

    eff_jia, cost_jia = tech_stop_vs_cost(l3_zg if l3_zg > 0 else l2_supp)
    eff_bing, cost_bing = tech_stop_vs_cost(l3_zg if l3_zg > 0 else l2_zg)
    eff_ding, cost_ding = tech_stop_vs_cost(l2_zg)

    # 甲情形：延续上行
    prev_high = l2_press if l2_press > price else price
    jia = {
        "id": "甲",
        "condition": f"当日延续上行，突破前高 {prev_high:.2f}",
        "meaning": (
            f"{l2_name}主升延续；{l3_trigger_lbl}是当日关键支撑。"
            f"持仓背景：{tone}。"
        ),
        "action": (
            f"持仓。缩量回踩守住{l3_trigger:.2f}可加仓；"
            f"跌破{l3_trigger:.2f}先减半仓锁利。"
        ) if pnl_pct > 0 else (
            f"持仓观察，若跌破{l3_trigger:.2f}考虑减仓控制风险。"
        ),
        "stop_loss": eff_jia,
        "stop_reason": (
            f"{l3_trigger_lbl}跌破，次级别推升结束。{tone}{cost_jia}"
        ),
        "trigger_level": "l3",
        "trigger_pct": round((price - l3_trigger) / price * 100, 1) if l3_trigger > 0 else 0,
    }

    # ── 乙情形：背驰出现（Risk 2 修复：区分中继背驰 vs 转折背驰）──
    # 此前：顶背驰出现 → 立即减仓建议（导致主升浪中频繁假警报）
    # 修复后：
    #   疑似转折 → 预警但不强制减仓，等待后续确认
    #   转折确认 → 确认减仓（原乙情形逻辑）
    #   中继确认 → 明确提示中继，建议持仓
    has_div = div_info.get("type") == "顶背驰"
    div_classification = _classify_divergence_type(
        div_info if has_div else None,
        l2.get("detail_bis", []),
        price,
    )
    is_reversal = (div_classification == "转折确认")
    is_relay    = (div_classification == "中继确认")
    is_pending  = (div_classification == "疑似转折")

    if is_relay:
        # 中继背驰：价格创新高，确认中继，持仓
        yi_action = (
            f"⚡ 中继背驰（价格已创新高），趋势延续。守住{l3_trigger:.2f}继续持仓，"
            f"上移止损至当前{l3_trigger:.2f}。"
        )
        yi_meaning = (
            f"顶背驰出现后价格已创新高，确认为中继背驰（力度补充，非趋势终结）。"
            f"{tone}。"
        )
        yi_cond = f"顶背驰出现后创新高 → 中继确认"
    elif is_reversal:
        # 转折背驰：价格明显回落，确认转折，减仓
        yi_action = (
            f"🔴 转折背驰确认，顶背驰后未能创新高已回落。"
            f"减仓{'1/3~1/2' if pnl_pct >= 0.5 else '部分'}锁利；剩余仓守{l3_trigger:.2f}。"
        )
        yi_meaning = (
            f"顶背驰出现后价格未能创新高并回落，确认为转折背驰（趋势终结信号）。"
            f"{tone}。"
        )
        yi_cond = f"顶背驰后价格回落 → 转折确认"
    elif is_pending:
        # 疑似转折：背驰出现但尚未确认，预警但不强制减仓
        yi_action = (
            f"⚠️ 顶背驰出现，等待后续确认。若价格创新高=中继，继续持仓；"
            f"若价格跌破{l3_trigger:.2f}=转折确认，减仓。"
        )
        yi_meaning = (
            f"顶背驰出现，方向尚未确认（需等价格创新高或明显回落）。{tone}。"
        )
        yi_cond = f"顶背驰出现，等待方向确认（疑似转折）"
    else:
        # 无背驰：顶分型信号
        yi_action = (
            f"顶分型出现 → 观察是否放量。守住{l3_trigger:.2f}持仓，跌破减仓。"
        )
        yi_meaning = f"{l3_name}顶分型出现，短线动能衰竭，无背驰信号。{tone}。"
        yi_cond = f"冲高后出现顶分型（{l3_name}级别）"

    yi = {
        "id": "乙",
        "condition": yi_cond,
        "meaning": yi_meaning,
        "action": yi_action,
        "stop_loss": eff_jia,
        "stop_reason": f"顶分型+{l3_trigger_lbl}跌破=短线结束，{tone}",
        "trigger_level": "l3",
        "trigger_pct": round((price - l3_trigger) / price * 100, 1) if l3_trigger > 0 else 0,
        "div_classification": div_classification,  # 透传供前端展示
    }

    # 丙情形：跌破5分ZG（当日可触发，关键修复！）
    bing = {
        "id": "丙",
        "condition": f"跌破{l3_trigger_lbl}（当日可触发）",
        "meaning": (
            f"{l3_trigger_lbl}被跌破，次级别推升结束"
            + (f"，{l2_name}顶背驰得到初步确认。" if has_div else "。")
            + f"{tone}{cost_bing}。"
        ),
        "action": (
            f"跌破{l3_trigger:.2f} → 清仓{'或减至1/3' if pnl_pct < 0.3 else ''}。"
            if qty > 0 else f"跌破{l3_trigger:.2f} → 清仓。"
        ),
        "stop_loss": eff_bing,
        "stop_reason": (
            f"{l3_trigger_lbl}跌破=次级别结构终结，{tone}"
        ),
        "trigger_level": "l3",
        "trigger_pct": round((price - l3_trigger) / price * 100, 1) if l3_trigger > 0 else 0,
    }

    # 丁情形：跌破30分ZG（多日才触发，结构红线）
    ding = {
        "id": "丁",
        "condition": f"跌破{l2_name}ZG={l2_zg:.2f}（多日结构红线）",
        "meaning": (
            f"{l2_name}ZG={l2_zg:.2f}被跌破，{l2_name}主升结构完全破坏。"
            f"{tone}{cost_ding}。"
        ),
        "action": f"必须清仓。{l2_name}结构破坏不可等待。",
        "stop_loss": eff_ding,
        "stop_reason": (
            f"{l2_name}ZG={l2_zg:.2f}跌破=主升结构终结，{tone}"
        ),
        "trigger_level": "l2",
        "trigger_pct": round((price - l2_zg) / price * 100, 1) if l2_zg > 0 else 0,
    }

    classes = [jia, yi, bing]
    _ding_needed = l2_zg > 0 and l3_zg > 0 and abs(l2_zg - l3_zg) / max(l2_zg, 0.01) > 0.05
    if _ding_needed:
        # 两个价位差异>5%才单独列丁，否则丙已经覆盖了
        classes.append(ding)
    else:
        # M2 修复：丁被跳过时，在丙的 meaning 里补充说明
        # 用户看到只有甲/乙/丙三条时，应明白丙已覆盖多日结构意义
        if l2_zg > 0 and l3_zg > 0:
            bing["meaning"] += (
                f"（注：{l2_name}ZG={l2_zg:.2f}与{l3_name}ZG接近，"
                f"跌破该位置即意味着次级别与多日结构同时破坏。）"
            )

    return classes


# ═══════════════════════════════════════════════════════════════
# 八D、主入口（持仓/空仓分路）
# ═══════════════════════════════════════════════════════════════

def _build_forward_classes(l1: dict, l2: dict, l3: Optional[dict] = None,
                            holding: Optional[dict] = None) -> list:
    """生成今日完全分类预案。

    holding = None          → 空仓路径（买卖点生命周期分析）
    holding = {"qty":2000, "cost":125.0}  → 持仓路径（结构威胁+盈亏叙述）

    持仓路径核心修复：
      丙情形改用 l3（5分）ZG 作为当日触发，而非 l2（30分）ZG（后者当日触不到）
    """
    if holding is None:
        return _build_empty_position_classes(l1, l2, l3)
    else:
        return _build_holding_classes(l1, l2, l3, holding)


def _build_forward_analysis(matrix: list, nesting: Optional[dict] = None,
                            holding: Optional[dict] = None) -> dict:
    """组装完整的前瞻推演数据，供前端渲染叙述式面板。

    Args:
        matrix:  [日线, 中级别, 小级别] 三级矩阵数据
        nesting: 区间套检测结果 {depth, label, direction}，来自 _check_interval_nesting
    """
    if not matrix or len(matrix) < 2:
        return {}
    l1 = matrix[0]
    l2 = matrix[1]
    l3 = matrix[2] if len(matrix) > 2 else None  # 5分钟（体系A）/ 15分钟（体系B）

    recent_action = _describe_recent_action(l2)
    position = _describe_current_position(l1, l2)
    forward_classes = _build_forward_classes(l1, l2, l3, holding=holding)

    # ── 区间套门控：depth 决定操作信号级别，不再仅是标签 ──
    # confidence_gate:
    #   HIGH   (depth=3) → 全量操作，仓位上限不压制
    #   MEDIUM (depth=2) → 半仓信号，position_size 最高压到 50%
    #   LOW    (depth=1) → 仅观察，不触发入场，甲情形降级为"观察"
    #   无区间套          → 保持原始建议不变
    if nesting and forward_classes:
        gate        = nesting.get("confidence_gate", "")
        nest_label  = nesting.get("label", "")
        nest_dir    = nesting.get("direction", "")
        first_class = forward_classes[0]

        if first_class.get("id") == "甲":
            action_text = first_class.get("action", "")
            dir_match_buy  = (nest_dir == "bottom" and
                              any(kw in action_text for kw in ("入场", "加仓", "持仓", "跟进", "突破")))
            dir_match_sell = (nest_dir == "top" and
                              any(kw in action_text for kw in ("离场", "减仓", "空仓", "清仓")))

            if gate == "HIGH" and (dir_match_buy or dir_match_sell):
                prefix = f"【{nest_label}，胜率显著提升】"
                forward_classes[0]["action"] = prefix + action_text
                forward_classes[0]["nest_boost"] = True

            elif gate == "MEDIUM" and dir_match_buy:
                # 半仓门控：position_size 中含 "70%" 或 "50-70%" 的压到 "30-50%"
                ps = first_class.get("position_size", "")
                if any(x in ps for x in ("70%", "50-70%", "50%~70%")):
                    forward_classes[0]["position_size"] = "30-50%（两级区间套，未达全量条件）"
                prefix = f"【{nest_label}，半仓跟进】"
                forward_classes[0]["action"] = prefix + action_text
                forward_classes[0]["nest_boost"] = True

            elif gate == "LOW":
                # 单级别背驰：不触发操作，把甲情形降级为观察
                forward_classes[0]["action"] = (
                    f"【仅单级别背驰，观察为主，不入场】" + action_text
                )
                forward_classes[0]["position_size"] = "观望"
                forward_classes[0]["nest_boost"] = False

    # M1 修复：注入笔数归属字段，供用户和前端验证笔数漂移（36笔 vs 39笔）
    bi_attribution = {
        "l1_bi_count":   l1.get("bi_count", 0),
        "l1_level":      l1.get("level", "day"),
        "l2_bi_count":   l2.get("bi_count", 0),
        "l2_level":      l2.get("level", "m30"),
        "l3_bi_count":   l3.get("bi_count", 0) if l3 else 0,
        "l3_level":      l3.get("level", "m5") if l3 else "—",
        "l1_zs_data_ok": l1.get("zs_data_ok", True),  # E1 降级标记透传
        "l2_zs_data_ok": l2.get("zs_data_ok", True),
    }

    return {
        "recent_action":    recent_action,
        "current_position": position["summary"],
        "day_context":      position["day_context"],
        "stop_loss":        position["stop_loss"],
        "forward_classes":  forward_classes,
        "nesting":          nesting,  # 透传给前端展示
        "bi_attribution":   bi_attribution,
    }


# ═══════════════════════════════════════════════════════════════
# 九、跨级别矩阵分析
# ═══════════════════════════════════════════════════════════════

async def analyze_matrix_state(symbol: str, holding: Optional[dict] = None) -> dict:
    """双轴跨级别融合计算 + 区间套检测（第一阶段：真实级别关系）。

    体系 A: 日线 + 30分钟 + 5分钟  (短线维度)
    体系 B: 日线 + 60分钟 + 15分钟  (波段维度)

    V5 升级：使用 get_chan_multi_level 一次性获取各级别的真实层级关系，
    替代原来的 5 次独立 asyncio.gather 并行调用。
    """
    from server.services.chan_detail_service import get_chan_multi_level

    # ── 体系 A 多级别联动 + 体系 B 多级别联动 + 周线（并行）──
    task_a    = get_chan_multi_level(symbol, ["day", "m30", "m5"])
    task_b    = get_chan_multi_level(symbol, ["day", "m60", "m15"])
    task_week = _analyze_single_level(symbol, "week")   # 周线独立（不参与联动）

    multi_a, multi_b, week_data = await asyncio.gather(task_a, task_b, task_week)

    # ── 从体系 A 多级别结果提取各级别 detail ──
    # get_chan_multi_level 返回 {"day": detail, "m30": detail, "m5": detail, "_level_relations": ...}
    detail_day_a  = multi_a.get("day", {})
    detail_m30    = multi_a.get("m30", {})
    detail_m5     = multi_a.get("m5", {})
    relations_a   = multi_a.get("_level_relations", {})

    detail_day_b  = multi_b.get("day", {})
    detail_m60    = multi_b.get("m60", {})
    detail_m15    = multi_b.get("m15", {})
    relations_b   = multi_b.get("_level_relations", {})

    # ── 各级别结构分析（使用预取数据，不再重复拉取）──
    day_result = await _analyze_single_level(symbol, "day",  prefetched_detail=detail_day_a)
    m30_result = await _analyze_single_level(symbol, "m30",  prefetched_detail=detail_m30)
    m5_result  = await _analyze_single_level(symbol, "m5",   prefetched_detail=detail_m5)
    m60_result = await _analyze_single_level(symbol, "m60",  prefetched_detail=detail_m60)
    m15_result = await _analyze_single_level(symbol, "m15",  prefetched_detail=detail_m15)

    # 注入级别关系信息（关键升级：买卖点现在知道自己属于哪根高级别笔）
    m30_result["level_relations"] = relations_a.get("m30_in_day", [])
    m5_result["level_relations"]  = relations_a.get("m5_in_m30", [])
    m60_result["level_relations"] = relations_b.get("m60_in_day", [])
    m15_result["level_relations"] = relations_b.get("m15_in_m60", [])

    # 计算当前最后一笔所在的高级别笔方向（买卖点级别归属的核心字段）
    def _enrich_level_origin(result: dict, relations: list, high_level_name: str) -> dict:
        """在低级别分析结果里注入'当前笔属于高级别第几笔'和'高级别笔方向'信息。"""
        bis = result.get("detail_bis", [])
        if not bis or not relations:
            return result
        last_bi_idx = result.get("bi_count", 0) - 1 if result.get("bi_count") else 0
        # 找当前最后一笔对应的高级别笔
        for rel in reversed(relations):
            low_key = [k for k in rel.keys() if "bi_idx" in k and high_level_name not in k]
            if low_key and rel.get(low_key[0]) == result.get("bi_count", 1) - 1:
                result["parent_bi_is_up"] = rel.get("parent_is_up")
                result["parent_level"]    = high_level_name
                break
        
        result["level_context_label"] = _build_level_context_label(result)
        return result

    m30_result = _enrich_level_origin(m30_result, relations_a.get("m30_in_day", []), "day")
    m5_result  = _enrich_level_origin(m5_result,  relations_a.get("m5_in_m30",  []), "m30")
    m60_result = _enrich_level_origin(m60_result, relations_b.get("m60_in_day", []), "day")
    m15_result = _enrich_level_origin(m15_result, relations_b.get("m15_in_m60", []), "m60")

    data_map = {
        "day": day_result,
        "m30": m30_result,
        "m5":  m5_result,
        "m60": m60_result,
        "m15": m15_result,
        "week": week_data,
    }

    # ── 区间套检测（新增）──
    # 体系A: day → m30 → m5（C3 修复：使用列表格式，level_names 正确记录到 levels 字段）
    nesting_a = _check_interval_nesting(
        [data_map.get("day", {}), data_map.get("m30", {}), data_map.get("m5", {})],
        level_names=["day", "m30", "m5"],
    )
    # 体系B: day → m60 → m15（C3 修复：之前 m60 被错误记录为 "m30"，m15 被记录为 "m5"）
    nesting_b = _check_interval_nesting(
        [data_map.get("day", {}), data_map.get("m60", {}), data_map.get("m15", {})],
        level_names=["day", "m60", "m15"],
    )

    # V5 新增: 狙击位共振探测
    day_patterns_str = " ".join(data_map.get("day", {}).get("patterns", []))
    m5_patterns_str = " ".join(data_map.get("m5", {}).get("patterns", []))
    
    day_state = data_map.get("day", {}).get("state")
    day_price = data_map.get("day", {}).get("price", 0)
    day_zg = data_map.get("day", {}).get("zg", 1)
    
    is_day_ready = False
    if day_state == "UPWARD_LEAVING" and day_zg > 0 and 1.0 <= (day_price / day_zg) < 1.05:
        is_day_ready = True
    elif "三买" in day_patterns_str or "二买" in day_patterns_str or "接近中枢上沿" in day_patterns_str or "接近中枢下沿" in day_patterns_str:
        is_day_ready = True
        
    is_m5_ready = "底背驰" in m5_patterns_str or "接近中枢下沿" in m5_patterns_str or "二买" in m5_patterns_str
    
    if is_day_ready and is_m5_ready:
        if "patterns" in data_map.get("day", {}):
            data_map["day"]["patterns"].insert(0, "🎯 极高胜率区间套狙击点")

    matrix_a = [data_map["day"], data_map["m30"], data_map["m5"]]
    matrix_b = [data_map["day"], data_map["m60"], data_map["m15"]]
    week_data = data_map.get("week")

    # ── 前瞻推演（接入区间套深度 + 持仓路径）──
    # holding = {"cost": float, "qty": int} 时走持仓路径（甲减仓/乙止损叙述）
    # holding = None 时走空仓路径（甲买点入场/乙观望叙述）
    forward_a = _build_forward_analysis(matrix_a, nesting=nesting_a, holding=holding)
    forward_b = _build_forward_analysis(matrix_b, nesting=nesting_b, holding=holding)

    # Task #5：战法分类（空仓模式下输出，持仓模式下跳过）
    strategy_classification = None
    if not holding:
        day_data = data_map.get("day", {})
        m30_data = data_map.get("m30", {})
        m5_data  = data_map.get("m5",  {})
        try:
            strategy_classification = _classify_strategy(
                day_data, m30_data, m5_data, week_data
            )
        except Exception as e:
            logger.warning(f"[STRATEGY_CLASSIFY] {symbol} 战法分类异常: {e}")
            strategy_classification = {
                "strategy_type": "观察中",
                "summary": f"分类异常: {e}",
                "strategy1": None, "strategy2": None, "primary": None,
            }

    return {
        "symbol": symbol,
        "matrix_a": matrix_a,
        "matrix_b": matrix_b,
        "week": week_data,
        "interval_nesting_a": nesting_a,
        "interval_nesting_b": nesting_b,
        "forward_analysis_a": forward_a,
        "forward_analysis_b": forward_b,
        # Task #5：战法分类结果（空仓=入场分析，持仓=None）
        "strategy_classification": strategy_classification,
    }


# ─── 向后兼容：保留 analyze_stock_chan_state 供 price_monitor 等使用 ───

async def analyze_stock_chan_state(symbol: str):
    """单级别日线状态（供旧 API 兼容）。返回 (state_str, zs_dict)。"""
    result = await _analyze_single_level(symbol, "day")
    last_zs = {"ZD": result["zd"], "ZG": result["zg"]} if result["zd"] > 0 else None
    return result["state"], last_zs
