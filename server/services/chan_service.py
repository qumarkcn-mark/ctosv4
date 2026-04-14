"""CT-OS V4.0 — 多级别矩阵状态分析服务

基于 chan_detail_service（官方 chan.py 引擎）的中枢和笔数据，
推导出每个级别的走势状态（FSM），供 TRadar 雷达和 AI 推演使用。

★ 关键设计决策：不再独立跑旧 chan_engine，而是复用 chan_detail_service
   的解析结果，保证矩阵中枢与 K 线图上绘制的中枢完全一致。
"""

import asyncio
import logging
from typing import Tuple, Optional

from server.services.chan_detail_service import get_chan_detail, _compute_macd

logger = logging.getLogger(__name__)

# ─── FSM 状态枚举（字符串化，不依赖旧 chan_engine） ───

class ChanState:
    UNKNOWN            = "UNKNOWN"
    IN_CENTER_OSC      = "IN_CENTER_OSC"
    UPWARD_LEAVING     = "UPWARD_LEAVING"
    DOWNWARD_LEAVING   = "DOWNWARD_LEAVING"
    WAITING_FOR_PULLBACK = "WAITING_FOR_PULLBACK"
    THIRD_BUY_CONFIRMED = "THIRD_BUY_CONFIRMED"
    TREND_EXTENDING    = "TREND_EXTENDING"  # 有笔但无中枢，趋势延伸中


def _deduce_state_from_structures(bis: list, zhongshus: list) -> Tuple[str, dict]:
    """从笔列表和中枢列表推导走势状态。
    
    纯逻辑推导，不依赖任何引擎对象。
    输入格式是 chan_detail_service 的序列化输出：
      bis: [{x0, y0, x1, y1, is_up, ...}, ...]
      zhongshus: [{begin_date, end_date, zg, zd, gg, dd}, ...]
    """
    if not bis:
        return ChanState.UNKNOWN, {}
    
    if not zhongshus:
        # 有笔但无中枢 → 趋势延伸（单边走势尚未形成中枢）
        return ChanState.TREND_EXTENDING, {}
    
    last_zs = zhongshus[-1]
    zg = last_zs["zg"]
    zd = last_zs["zd"]
    
    last_bi = bis[-1]
    bi_is_up = last_bi["is_up"]
    bi_end_price = last_bi["y1"]
    
    # 判断当前价格相对中枢的位置
    if bi_end_price > zg:
        # 价格在中枢上方
        if bi_is_up:
            state = ChanState.UPWARD_LEAVING
        else:
            state = ChanState.WAITING_FOR_PULLBACK
    elif bi_end_price < zd:
        # 价格在中枢下方
        if bi_is_up:
            # 从下方向上拉，可能是三买前站
            # 检查是否有前一个中枢 && 这个中枢的 zd > 前一个中枢的 zg
            if len(zhongshus) >= 2:
                prev_zs = zhongshus[-2]
                if zd > prev_zs["zg"]:
                    state = ChanState.THIRD_BUY_CONFIRMED
                else:
                    state = ChanState.WAITING_FOR_PULLBACK
            else:
                state = ChanState.WAITING_FOR_PULLBACK
        else:
            state = ChanState.DOWNWARD_LEAVING
    else:
        # 价格在中枢内
        state = ChanState.IN_CENTER_OSC
    
    return state, last_zs


# ─── 频率映射：chan_service 用 "m30" 格式, chan_detail 用 "30" 格式 ───
_LEVEL_TO_FREQ = {
    "day": "day",
    "m60": "60",
    "m30": "30",
    "m15": "15",
    "m5":  "5",
}


async def _analyze_single_level(symbol: str, level: str) -> dict:
    """单级别分析：调用 chan_detail_service 获取结构，再推导状态。"""
    
    freq = _LEVEL_TO_FREQ.get(level, level)
    count = 250 if level == "day" else 320
    
    try:
        detail = await get_chan_detail(symbol, freq, count)
    except Exception as e:
        logger.warning("chan_detail 解析失败 %s/%s: %s", symbol, level, e)
        return {"level": level, "state": ChanState.UNKNOWN, "zd": 0, "zg": 0, "patterns": []}
    
    if detail.get("error"):
        return {"level": level, "state": ChanState.UNKNOWN, "zd": 0, "zg": 0, "patterns": []}
    
    bis = detail.get("bis", [])
    zhongshus = detail.get("bi_zhongshus", [])
    
    state, last_zs = _deduce_state_from_structures(bis, zhongshus)
    
    zd = last_zs.get("zd", 0) if last_zs else 0
    zg = last_zs.get("zg", 0) if last_zs else 0
    
    # ─── 形态提取 (Patterns) ───
    patterns = []
    if bis:
        last_bi = bis[-1]
        is_up = last_bi["is_up"]
        dir_cn = "向上" if is_up else "向下"
        patterns.append(f"{dir_cn}笔延伸中")
        
        # 背驰检测：比较最近两根同向笔的 MACD 动能
        same_dir_bis = [b for b in bis if b["is_up"] == is_up]
        if len(same_dir_bis) >= 2:
            prev_bi = same_dir_bis[-2]
            curr_bi = same_dir_bis[-1]
            
            # 创新高/新低 是背驰的物理前提
            is_new_extreme = False
            if is_up and curr_bi["y1"] >= prev_bi["y1"]:
                is_new_extreme = True
            elif not is_up and curr_bi["y1"] <= prev_bi["y1"]:
                is_new_extreme = True
            
            if is_new_extreme:
                # 通过 momentum 面积比较
                prev_mom = prev_bi.get("momentum", {})
                curr_mom = curr_bi.get("momentum", {})
                prev_area = prev_mom.get("area", 0)
                curr_area = curr_mom.get("area", 0)
                
                if prev_area > 0 and curr_area > 0:
                    ratio = curr_area / prev_area
                    if ratio < 0.7:  # 面积缩减超过30%
                        div_type = "顶背驰" if is_up else "底背驰"
                        if ratio < 0.4:
                            patterns.append(f"{div_type}(高危)")
                        else:
                            patterns.append(f"{div_type}")
    
    return {
        "level": level,
        "state": state,
        "zd": zd,
        "zg": zg,
        "patterns": patterns,
    }


async def analyze_matrix_state(symbol: str) -> dict:
    """双轴跨级别融合计算
    
    体系 A: 日线 + 30分钟 + 5分钟  (短线维度)
    体系 B: 日线 + 60分钟 + 15分钟  (波段维度)
    """
    levels = ["day", "m60", "m30", "m15", "m5"]
    tasks = [_analyze_single_level(symbol, lvl) for lvl in levels]
    results = await asyncio.gather(*tasks)
    
    data_map = {r["level"]: r for r in results}
    
    matrix_a = [data_map["day"], data_map["m30"], data_map["m5"]]
    matrix_b = [data_map["day"], data_map["m60"], data_map["m15"]]
    
    return {
        "symbol": symbol,
        "matrix_a": matrix_a,
        "matrix_b": matrix_b,
    }


# ─── 向后兼容：保留 analyze_stock_chan_state 供 price_monitor 等使用 ───

async def analyze_stock_chan_state(symbol: str):
    """单级别日线状态（供旧 API 兼容）。返回 (state_str, zs_dict)。"""
    result = await _analyze_single_level(symbol, "day")
    last_zs = {"ZD": result["zd"], "ZG": result["zg"]} if result["zd"] > 0 else None
    return result["state"], last_zs
