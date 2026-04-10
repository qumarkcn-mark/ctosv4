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
from server.services.baostock_service import fetch_klines_sync
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

def _serialize_bis(bis) -> list[dict]:
    """将 Bi 列表序列化为前端可用格式"""
    result = []
    for bi in bis:
        # 向上笔：从底分型起点 → 顶分型终点
        is_up = bi.direction == Direction.UP
        result.append({
            "x0":    bi.start_fx.date,
            "y0":    bi.start_fx.low if is_up else bi.start_fx.high,
            "x1":    bi.end_fx.date,
            "y1":    bi.end_fx.high if is_up else bi.end_fx.low,
            "is_up": is_up,
            "is_sure": True,   # chan_engine 目前不区分虚笔，全标为确定
        })
    return result


def _serialize_zhongshus(zhongshus) -> list[dict]:
    """将 ZhongShu 列表序列化为前端矩形框数据"""
    result = []
    for zs in zhongshus:
        if not zs.bis:
            continue
        begin_date = zs.bis[0].start_fx.date
        end_date   = zs.bis[-1].end_fx.date
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
        logger.info("本地数据不足 %s/%s，触发 BaoStock 拉取...", symbol, freq)
        try:
            fetch_klines_sync(symbol, freq)
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

    # 4. 识别中枢（连续 3+ 笔重叠区域）
    zhongshus = _extract_zhongshus(bis)

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

    return {
        "symbol":     symbol,
        "freq":       freq,
        "klines":     klines_out,
        "bis":        _serialize_bis(bis),
        "segs":       [],          # 线段识别 TODO：在 chan_engine 中尚未实现
        "zhongshus":  _serialize_zhongshus(zhongshus),
        "macd":       {
            "dif":   macd["dif"],
            "dea":   macd["dea"],
            "hist":  macd["hist"],
            "dates": [r["date"] for r in rows],
        },
        "stats": {
            "kline_count":    len(rows),
            "bi_count":       len(bis),
            "zhongshu_count": len(zhongshus),
        }
    }


def _extract_zhongshus(bis) -> list:
    """
    从笔列表中识别中枢。
    标准算法：连续三笔的价格区间存在重叠，则构成一个中枢。
    继续延伸：如果第 4、5... 笔仍在重叠区，则并入同一中枢。
    """
    from chan_engine.models import ZhongShu

    zhongshus = []
    if len(bis) < 3:
        return []

    i = 0
    while i <= len(bis) - 3:
        b1, b2, b3 = bis[i], bis[i + 1], bis[i + 2]

        # 计算三笔的高低范围
        highs = [max(b.start_fx.high, b.end_fx.high) for b in [b1, b2, b3]]
        lows  = [min(b.start_fx.low,  b.end_fx.low)  for b in [b1, b2, b3]]

        overlap_high = min(highs)
        overlap_low  = max(lows)

        # 三笔有重叠则成中枢
        if overlap_high > overlap_low:
            zs_bis = [b1, b2, b3]
            # 尝试延伸更多的笔
            j = i + 3
            while j < len(bis):
                bj = bis[j]
                bj_high = max(bj.start_fx.high, bj.end_fx.high)
                bj_low  = min(bj.start_fx.low,  bj.end_fx.low)
                # 仍在重叠区内，延伸
                if bj_low <= overlap_high and bj_high >= overlap_low:
                    zs_bis.append(bj)
                    j += 1
                else:
                    break

            zhongshus.append(ZhongShu(bis=zs_bis))
            i = j  # 跳到中枢之后的笔继续找
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
