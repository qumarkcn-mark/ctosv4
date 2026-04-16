"""CT-OS V4.0 — 缠论全量几何解析服务

从本地数据湖读取 K 线，调用原版 chan.py 引擎解析完整结构（笔、线段、中枢），
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

import sys
import os
import logging
from typing import Optional
from fastapi.concurrency import run_in_threadpool

# 引入官方开源版的 chan.py
_VENDOR_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "vendor", "chan_py"))
if _VENDOR_ROOT not in sys.path:
    sys.path.insert(0, _VENDOR_ROOT)

try:
    from Chan import CChan
    from ChanConfig import CChanConfig
    from Common.CEnum import AUTYPE, DATA_SRC, DATA_FIELD, KL_TYPE
    from KLine.KLine_Unit import CKLine_Unit
    from Common.CTime import CTime
except ImportError as e:
    logging.error(f"无法导入 chan_py: {e}")

from server.db.kline_lake import query_klines
from server.services.baostock_service import fetch_klines_quick

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
# 动能提取工具
# ---------------------------------------------------------------------------

def _measure_momentum(start_date: str, end_date: str, is_up: bool, macd_data: dict, date_to_idx: dict) -> dict:
    start_idx = date_to_idx.get(start_date)
    end_idx = date_to_idx.get(end_date)
    
    if start_idx is None or end_idx is None or start_idx >= end_idx:
        return {"area": 0.0, "dif_extreme": 0.0}
        
    hist = macd_data["hist"][start_idx:end_idx+1]
    dif = macd_data["dif"][start_idx:end_idx+1]
    
    if is_up:
        area = sum(max(h, 0) for h in hist)
        dif_extreme = max(dif) if dif else 0.0
    else:
        area = sum(abs(min(h, 0)) for h in hist)
        dif_extreme = abs(min(dif)) if dif else 0.0
        
    return {"area": round(area, 4), "dif_extreme": round(dif_extreme, 4)}

# ---------------------------------------------------------------------------
# 序列化工具
# ---------------------------------------------------------------------------

def _format_time(ctime: CTime, ctime_to_date_str: dict) -> str:
    """完美映射 CTime 回原始的 date_str，保障前端不出现查不到 index 的错误"""
    key = f"{ctime.year}-{ctime.month}-{ctime.day}-{ctime.hour}-{ctime.minute}"
    return ctime_to_date_str.get(key, "")

def _serialize_bis(bi_list, ctime_to_date_str, macd_data, date_to_idx) -> list[dict]:
    """将 CBi 列表序列化为前端可用格式，并打上 MACD 动能分"""
    result = []
    for bi in bi_list:
        is_up = str(bi.dir).endswith("UP")
        x0 = _format_time(bi.get_begin_klu().time, ctime_to_date_str)
        x1 = _format_time(bi.get_end_klu().time, ctime_to_date_str)
        y0 = bi.get_begin_val()
        y1 = bi.get_end_val()
        
        momentum = _measure_momentum(x0, x1, is_up, macd_data, date_to_idx)
        result.append({
            "x0": x0,
            "y0": round(y0, 4),
            "x1": x1,
            "y1": round(y1, 4),
            "is_up": is_up,
            "is_sure": bi.is_sure if hasattr(bi, 'is_sure') else True,
            "momentum": momentum,
        })
    return result


def _serialize_segs(seg_list, ctime_to_date_str, macd_data, date_to_idx) -> list[dict]:
    """将 CSeg 序列化为前端可用格式，并打上 MACD 动能分"""
    result = []
    for seg in seg_list:
        is_up = str(seg.dir).endswith("UP")
        x0 = _format_time(seg.get_begin_klu().time, ctime_to_date_str)
        x1 = _format_time(seg.get_end_klu().time, ctime_to_date_str)
        y0 = seg.get_begin_val()
        y1 = seg.get_end_val()
        
        momentum = _measure_momentum(x0, x1, is_up, macd_data, date_to_idx)
        result.append({
            "x0": x0,
            "y0": round(y0, 4),
            "x1": x1,
            "y1": round(y1, 4),
            "is_up": is_up,
            "is_sure": seg.is_sure if hasattr(seg, 'is_sure') else True,
            "momentum": momentum,
        })
    return result

def _serialize_zhongshus(zs_list, ctime_to_date_str) -> list[dict]:
    """将 CZS 列表序列化为前端矩形框数据"""
    result = []
    for zs in zs_list:
        try:
            begin_date = _format_time(zs.begin.time, ctime_to_date_str)
            end_date   = _format_time(zs.end.time, ctime_to_date_str)
            result.append({
                "begin_date": begin_date,
                "end_date":   end_date,
                "zg":  round(zs.high, 4),
                "zd":  round(zs.low, 4),
                "gg":  round(zs.peak_high, 4),
                "dd":  round(zs.peak_low, 4),
            })
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# 核心解析流程（同步，在线程池中运行）
# ---------------------------------------------------------------------------

PERIOD_MAP = {
    # 纯数字格式（kline_lake / 前端 / chan_service 使用）
    "1":   KL_TYPE.K_1M,
    "5":   KL_TYPE.K_5M,
    "15":  KL_TYPE.K_15M,
    "30":  KL_TYPE.K_30M,
    "60":  KL_TYPE.K_60M,
    "day": KL_TYPE.K_DAY,
    "week": KL_TYPE.K_WEEK,
    # XM 格式（兼容）
    "1M":  KL_TYPE.K_1M,
    "5M":  KL_TYPE.K_5M,
    "15M": KL_TYPE.K_15M,
    "30M": KL_TYPE.K_30M,
    "60M": KL_TYPE.K_60M,
    "W":   KL_TYPE.K_WEEK,
}

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
    
    # ─── 周线实时补全：用日线合成本周未完成的周K ───
    if freq == "week" and rows:
        last_week_date = str(rows[-1]["date"]).split(" ")[0]
        try:
            daily_rows = query_klines(symbol, "day", limit=10)
            if daily_rows:
                # 找出比最后一根完整周K更新的日线
                current_week_days = [
                    r for r in daily_rows
                    if str(r["date"]).split(" ")[0] > last_week_date
                ]
                if current_week_days:
                    synth = {
                        "date": str(current_week_days[-1]["date"]),
                        "open": float(current_week_days[0]["open"]),
                        "high": max(float(r["high"]) for r in current_week_days),
                        "low": min(float(r["low"]) for r in current_week_days),
                        "close": float(current_week_days[-1]["close"]),
                        "volume": sum(float(r["volume"]) for r in current_week_days),
                    }
                    rows.append(synth)
                    logger.debug("周线补全: 用 %d 根日线合成本周K线 %s", len(current_week_days), synth["date"])
        except Exception as e:
            logger.warning("周线补全失败: %s", e)
        
    kl_type = PERIOD_MAP.get(freq, KL_TYPE.K_DAY)
    
    units = []
    ctime_to_date_str = {}  # 原样保留 CTime 指向具体的前端 Date 字符串映射

    for r in rows:
        dt_str = str(r["date"])
        if " " in dt_str:
            date_part, time_part = dt_str.split(" ", 1)
        else:
            date_part, time_part = dt_str, "09:30:00"

        ymd = date_part.split("-")
        hms = time_part.split(":")
        
        try:
            year = int(ymd[0])
            month = int(ymd[1])
            day = int(ymd[2])
            hour = int(hms[0])
            minute = int(hms[1])
        except Exception:
            continue

        ctime = CTime(year, month, day, hour, minute)
        ctime_key = f"{year}-{month}-{day}-{hour}-{minute}"
        ctime_to_date_str[ctime_key] = dt_str
        
        item_dict = {
            DATA_FIELD.FIELD_TIME: ctime,
            DATA_FIELD.FIELD_OPEN: float(r["open"]),
            DATA_FIELD.FIELD_HIGH: float(r["high"]),
            DATA_FIELD.FIELD_LOW: float(r["low"]),
            DATA_FIELD.FIELD_CLOSE: float(r["close"]),
            DATA_FIELD.FIELD_VOLUME: float(r["volume"]),
        }
        units.append(CKLine_Unit(item_dict))

    # 3. 载入官方原生 CChan 引擎计算
    config = CChanConfig({
        "trigger_step": True,
        "kl_data_check": False,
        "bi_strict": True,
        "print_warning": False,
        "print_err_time": False,
        "auto_skip_illegal_sub_lv": True,
    })

    chan = CChan(
        code=symbol,
        data_src=DATA_SRC.CUSTOM,
        lv_list=[kl_type],
        config=config,
        autype=AUTYPE.QFQ,
    )
    
    # 将K线喂入并计算分段、中枢
    chan.trigger_load({kl_type: units})
    chan.kl_datas[kl_type].cal_seg_and_zs()

    kl_data = chan.kl_datas[kl_type]
    
    bi_list = kl_data.bi_list
    seg_list = getattr(kl_data, "seg_list", [])
    zs_list = kl_data.zs_list
    segzs_list = getattr(kl_data, "segzs_list", [])

    # 4. MACD 计算和索引 (供 KinematicsAnalyzer 面积使用)
    closes = [r["close"] for r in rows]
    macd   = _compute_macd(closes)
    
    klines_out = []
    for r in rows:
        klines_out.append({
            "time":   r["date"],
            "open":   r["open"],
            "high":   r["high"],
            "low":    r["low"],
            "close":  r["close"],
            "volume": r["volume"],
        })

    dates = [r["date"] for r in rows]
    date_to_idx = {d: i for i, d in enumerate(dates)}

    # 5. 进行兼容性结构序列化
    serialized_bis = _serialize_bis(bi_list, ctime_to_date_str, macd, date_to_idx)
    serialized_segs = _serialize_segs(seg_list, ctime_to_date_str, macd, date_to_idx)
    serialized_bi_zs = _serialize_zhongshus(zs_list, ctime_to_date_str)
    serialized_seg_zs = _serialize_zhongshus(segzs_list, ctime_to_date_str)

    return {
        "symbol":     symbol,
        "freq":       freq,
        "klines":     klines_out,
        "bis":        serialized_bis,
        "segs":       serialized_segs,
        "bi_zhongshus":       serialized_bi_zs,
        "bi_zhongshus_decomp": serialized_bi_zs,  # 原版内部直接生成最好质量的中枢
        "seg_zhongshus":      serialized_seg_zs,
        "zhongshus":  serialized_bi_zs,  # 后向兼容
        "macd":       {
            "dif":   macd["dif"],
            "dea":   macd["dea"],
            "hist":  macd["hist"],
            "dates": dates,
        },
        "stats": {
            "kline_count":    len(rows),
            "bi_count":       len(bi_list),
            "seg_count":      len(seg_list),
            "bi_zs_count":    len(zs_list),
            "bi_zs_decomp_count": len(zs_list),
            "seg_zs_count":   len(segzs_list),
        }
    }


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
    # ★ 统一 symbol 格式：sh600519 → sh.600519（BaoStock/kline_lake 要求）
    symbol_bs = symbol.replace("-", ".")
    if len(symbol_bs) > 2 and symbol_bs[2] != ".":
        symbol_bs = f"{symbol_bs[:2]}.{symbol_bs[2:]}"

    return await run_in_threadpool(_parse_chan_detail_sync, symbol_bs, freq, count)
