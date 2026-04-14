"""CT-OS V4.0 — 缠论全量几何解析服务

从本地数据湖读取 K 线，调用 chan_engine 解析完整结构（笔、线段、中枢），
并计算 MACD 指标，序列化为前端 KlineChart 可以直接消费的 JSON 格式。

输出结构概览：
{
    "symbol": "sh.600519",
    "freq":   "day",
    "klines": [...],            # 原始 OHLCV（lightweight-charts 格式）
    "bis":    [...],            # 笔：[{x0, y0, x1, y1, is_up, is_sure}, ...]
    "segs":   [...],            # 线段
    "zhongshus": [...],         # 中枢：[{begin_date, end_date, zg, zd, gg, dd}, ...]
    "macd":   {                 # MACD 数据（与 klines 等长）
        "dif": [...],
        "dea": [...],
        "hist": [...]
    }
}
"""

import logging
from typing import Optional
from fastapi.concurrency import run_in_threadpool

from server.db.kline_lake import query_klines, count_klines
from server.services.baostock_service import fetch_klines_sync, fetch_klines_quick
from chan_engine.models import KLine, Direction
from chan_engine.parser import ChanParser

logger = logging.getLogger(__name__)

# 低于此数量，触发 BaoStock 自动补数据
_MIN_KLINES = 120


# ---------------------------------------------------------------------------
# MACD 计算（纯 Python，不依赖 ta-lib）
# ---------------------------------------------------------------------------

def _ema(prices: list[float], period: int) -> list[float]:
    """指数移动平均线"""
    if not prices:
        return []
    k = 2.0 / (period + 1)
    result = [prices[0]]
    for p in prices[1:]:
        result.append(p * k + result[-1] * (1 - k))
    return result


def _compute_macd(closes: list[float], fast=12, slow=26, signal=9) -> dict:
    """
    计算 MACD 指标。
    返回与 closes 等长的三个序列：dif, dea, hist（红绿柱 = dif-dea）
    """
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea = _ema(dif, signal)
    hist = [(d - e) * 2 for d, e in zip(dif, dea)]  # ×2 是国内券商标准
    return {
        "dif":  [round(v, 4) for v in dif],
        "dea":  [round(v, 4) for v in dea],
        "hist": [round(v, 4) for v in hist],
    }


# ---------------------------------------------------------------------------
# 序列化工具
# ---------------------------------------------------------------------------

def _serialize_bis(bis, macd_data, date_to_idx) -> list[dict]:
    """将 Bi 列表序列化为前端可用格式，并打上 MACD 动能分"""
    from chan_engine.kinematics import KinematicsAnalyzer
    result = []
    for bi in bis:
        is_up = bi.direction == Direction.UP
        momentum = KinematicsAnalyzer.measure_bi_momentum(bi, macd_data, date_to_idx)
        result.append({
            "x0":    bi.start_fx.date,
            "y0":    bi.start_fx.low if is_up else bi.start_fx.high,
            "x1":    bi.end_fx.date,
            "y1":    bi.end_fx.high if is_up else bi.end_fx.low,
            "is_up": is_up,
            "is_sure": True,
            "momentum": momentum,
        })
    return result


def _serialize_segs(segs, macd_data, date_to_idx) -> list[dict]:
    """将线段序列化为前端可用格式，并打上 MACD 动能分
    
    端点逻辑：
      - 起点 = 第一笔的起始转折点（从第一笔方向确定取高还是低）
      - 终点 = 最后一笔的终点转折点（从最后一笔方向确定取高还是低）
      - is_up 从实际价格差推导
    """
    from chan_engine.kinematics import KinematicsAnalyzer
    result = []
    for seg in segs:
        first_bi = seg.bis[0]
        last_bi = seg.bis[-1]
        # 起点：第一笔的起始分型
        x0 = first_bi.start_fx.date
        if first_bi.direction == Direction.UP:
            y0 = first_bi.start_fx.low   # UP bi 起于底分型低点
        else:
            y0 = first_bi.start_fx.high  # DOWN bi 起于顶分型高点

        # 终点：最后一笔的结束分型
        x1 = last_bi.end_fx.date
        if last_bi.direction == Direction.UP:
            y1 = last_bi.end_fx.high     # UP bi 终于顶分型高点
        else:
            y1 = last_bi.end_fx.low      # DOWN bi 终于底分型低点

        # 从实际坐标推导 is_up
        is_up = y1 >= y0
        momentum = KinematicsAnalyzer.measure_segment_momentum(seg, macd_data, date_to_idx)
        result.append({
            "x0":    x0,
            "y0":    round(y0, 4),
            "x1":    x1,
            "y1":    round(y1, 4),
            "is_up": is_up,
            "is_sure": seg.is_sure,
            "momentum": momentum,
        })
    return result

def _serialize_zhongshus(zhongshus) -> list[dict]:
    """将 ZhongShu 列表序列化为前端矩形框数据"""
    result = []
    for zs in zhongshus:
        comps = zs._components
        if not comps:
            continue
        begin_date = comps[0].start_date if hasattr(comps[0], 'start_date') else comps[0].start_fx.date
        end_date   = comps[-1].end_date if hasattr(comps[-1], 'end_date') else comps[-1].end_fx.date
        result.append({
            "begin_date": begin_date,
            "end_date":   end_date,
            "zg":  round(zs.ZG, 4),
            "zd":  round(zs.ZD, 4),
            "gg":  round(zs.GG, 4),
            "dd":  round(zs.DD, 4),
        })
    return result


# ---------------------------------------------------------------------------
# 核心解析流程（同步，在线程池中运行）
# ---------------------------------------------------------------------------

def _parse_chan_detail_sync(
    symbol: str,
    freq: str,
    count: int,
) -> dict:
    """
    同步版本的缠论结构解析。调用方应通过 run_in_threadpool 包装。
    """
    # 1. 读取 K 线（优先本地缓存）
    rows = query_klines(symbol, freq, limit=count)

    if len(rows) < _MIN_KLINES:
        logger.info("本地数据不足 %s/%s，触发 BaoStock 快速拉取...", symbol, freq)
        try:
            fetch_klines_quick(symbol, freq)
            rows = query_klines(symbol, freq, limit=count)
        except Exception as e:
            logger.warning("BaoStock 拉取失败: %s", e)

    if not rows:
        return {"error": f"无可用 K 线数据: {symbol}/{freq}"}

    # 2. 限制条数并构建 KLine 对象
    kline_objs = [
        KLine(
            date=r["date"],
            open=r["open"],
            close=r["close"],
            high=r["high"],
            low=r["low"],
            volume=r["volume"],
        )
        for r in rows
    ]

    # 3. 缠论结构解析流水线
    merged   = ChanParser.merge_klines(kline_objs)
    fenxings = ChanParser.find_fenxings(merged, validate_bottom=True)
    bis      = ChanParser.build_bis(fenxings, merged)
    segs     = ChanParser.build_segments(bis)

    # 4. 识别中枢（双级别：笔中枢 + 线段中枢）
    bi_zhongshus      = _extract_zhongshus(bis, is_seg=False)          # 全局扫描
    bi_zhongshus_decomp = _extract_bi_zhongshus_by_seg(bis, segs)      # ★ 同级别分解
    seg_zhongshus     = _extract_zhongshus(segs, is_seg=True) if segs else []

    # 5. MACD 计算
    closes = [r["close"] for r in rows]
    macd   = _compute_macd(closes)

    # 6. K 线格式化（lightweight-charts 标准格式）
    klines_out = []
    for r in rows:
        date_val = r["date"]
        # 日线用 "YYYY-MM-DD"，分钟线用 Unix timestamp（lightweight-charts 要求）
        klines_out.append({
            "time":   date_val,
            "open":   r["open"],
            "high":   r["high"],
            "low":    r["low"],
            "close":  r["close"],
            "volume": r["volume"],
        })

    # 构建 MACD 查询索引
    dates = [r["date"] for r in rows]
    date_to_idx = {d: i for i, d in enumerate(dates)}

    return {
        "symbol":     symbol,
        "freq":       freq,
        "klines":     klines_out,
        "bis":        _serialize_bis(bis, macd, date_to_idx),
        "segs":       _serialize_segs(segs, macd, date_to_idx),
        "bi_zhongshus":       _serialize_zhongshus(bi_zhongshus),
        "bi_zhongshus_decomp": _serialize_zhongshus(bi_zhongshus_decomp),  # ★ 同级别分解
        "seg_zhongshus":      _serialize_zhongshus(seg_zhongshus),
        "zhongshus":  _serialize_zhongshus(bi_zhongshus),  # 后向兼容
        "macd":       {
            "dif":   macd["dif"],
            "dea":   macd["dea"],
            "hist":  macd["hist"],
            "dates": [r["date"] for r in rows],
        },
        "stats": {
            "kline_count":    len(rows),
            "bi_count":       len(bis),
            "seg_count":      len(segs),
            "bi_zs_count":    len(bi_zhongshus),
            "bi_zs_decomp_count": len(bi_zhongshus_decomp),
            "seg_zs_count":   len(seg_zhongshus),
        }
    }


def _extract_bi_zhongshus_by_seg(bis, segs) -> list:
    """
    按同级别分解原则构建笔中枢（参考 chan.py ZSList.cal_bi_zs）：
    1. 按线段分组 → 每段内只取反向笔
    2. 反向笔做重叠检测
    3. 最后一段之后的自由笔也参与识别
    """
    from chan_engine.models import ZhongShu, Direction

    all_zs = []

    for seg in segs:
        # 只取段内的反向笔（DOWN 段取 UP 笔，UP 段取 DOWN 笔）
        anti_bis = [bi for bi in seg.bis if bi.direction != seg.direction]
        if len(anti_bis) >= 3:
            all_zs.extend(_extract_zhongshus(anti_bis, is_seg=False))

    # 处理最后一段之后的自由笔
    if segs:
        last_seg_end_date = segs[-1].bis[-1].end_fx.date
        free_bis = [bi for bi in bis if bi.start_fx.date > last_seg_end_date]
        if len(free_bis) >= 3:
            all_zs.extend(_extract_zhongshus(free_bis, is_seg=False))
    elif len(bis) >= 3:
        # 没有线段时退化为全局扫描
        all_zs = _extract_zhongshus(bis, is_seg=False)

    return all_zs


def _extract_zhongshus(components, is_seg=False) -> list:
    """
    从组件（笔或线段）列表中识别中枢。
    标准算法：连续三个组件的价格区间存在重叠，则构成一个中枢。
    继续延伸：如果第 4、5... 个组件仍在重叠区，则并入同一中枢。
    """
    from chan_engine.models import ZhongShu

    zhongshus = []
    if len(components) < 3:
        return []

    i = 0
    while i <= len(components) - 3:
        c1, c2, c3 = components[i], components[i + 1], components[i + 2]

        highs = [c.high for c in [c1, c2, c3]]
        lows  = [c.low  for c in [c1, c2, c3]]

        overlap_high = min(highs)
        overlap_low  = max(lows)

        if overlap_high > overlap_low:
            zs_comps = [c1, c2, c3]
            j = i + 3
            while j < len(components):
                cj = components[j]
                if cj.low <= overlap_high and cj.high >= overlap_low:
                    zs_comps.append(cj)
                    j += 1
                else:
                    break

            if is_seg:
                zhongshus.append(ZhongShu(segs=zs_comps))
            else:
                zhongshus.append(ZhongShu(bis=zs_comps))
            i = j
        else:
            i += 1

    return zhongshus


# ---------------------------------------------------------------------------
# 异步公共接口
# ---------------------------------------------------------------------------

async def get_chan_detail(
    symbol: str,
    freq: str = "day",
    count: int = 500,
) -> dict:
    """
    异步版本，供 FastAPI 路由调用。
    通过 run_in_threadpool 将 CPU 密集型计算偏移到线程池，
    确保不阻塞 asyncio event loop。
    """
    return await run_in_threadpool(_parse_chan_detail_sync, symbol, freq, count)
