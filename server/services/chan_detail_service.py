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
import copy
import time
import asyncio
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

import httpx
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

from server.db.kline_lake import query_klines, get_kline_window_signature
from server.config import PRICE_API_TIMEOUT
from server.domain.symbols import to_tencent_symbol
from server.engines.structure.chan_snapshot_cache import (
    load_chan_snapshot,
    load_latest_chan_snapshot,
    save_chan_snapshot,
)
from server.engines.structure.chan_config_presets import (
    get_chan_config_dict,
    get_chan_config_meta,
)
from server.services.baostock_service import fetch_klines_quick
from server.services.tdx_minute_service import read_tdx_1m_klines

logger = logging.getLogger(__name__)

# 低于此数量，触发 BaoStock 自动补数据
_MIN_KLINES = 120
DEFAULT_COMPUTE_BARS = 5000
FREQ_COMPUTE_BARS = {
    "1": 800,
    "5": 2000,
    "15": 2500,
    "30": 3000,
    "60": 3000,
    "day": 2500,
    "week": 1200,
}
DETAIL_CACHE_TTL_SECONDS = 120
DETAIL_CACHE_MAX_ITEMS = 64
DETAIL_RESPONSE_SCHEMA_VERSION = "zs-display-v2"
_detail_cache: dict[tuple, dict] = {}
_detail_cache_locks: dict[tuple, asyncio.Lock] = {}
_background_fetch_keys: set[tuple[str, str]] = set()
_background_fetch_lock = threading.Lock()
TAIL_RECOMPUTE_BARS = {
    "week": 120,
    "day": 200,
    "60": 500,
    "30": 500,
    "15": 500,
    "5": 800,
}

_QT_KLINE_BASE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
_QT_MKLINE_BASE = "https://ifzq.gtimg.cn/appstock/app/kline/mkline?param="
_TENCENT_FREQ_MAP = {
    "day": "day",
    "60": "m60",
    "30": "m30",
    "15": "m15",
    "5": "m5",
}


def _rows_are_fresh(rows: list[dict], *, stale_days: int = 10) -> bool:
    """判断短历史 K 线是否足够新鲜，可直接用于新股/次新股展示。"""
    if not rows:
        return False
    last_date_str = str(rows[-1].get("date") or rows[-1].get("time") or "").split(" ", 1)[0]
    try:
        last_date = datetime.strptime(last_date_str, "%Y-%m-%d")
    except (TypeError, ValueError):
        return False
    return (datetime.now() - last_date) <= timedelta(days=stale_days)


def _schedule_background_fetch(symbol: str, freq: str, *, reason: str) -> None:
    """首屏结构请求不等待 BaoStock；缺口数据交给后台静默补齐。"""
    key = (symbol, freq)
    with _background_fetch_lock:
        if key in _background_fetch_keys:
            return
        _background_fetch_keys.add(key)

    def _run() -> None:
        try:
            logger.info("后台补齐 K 线 %s/%s reason=%s", symbol, freq, reason)
            fetch_klines_quick(symbol, freq)
        except Exception as exc:
            logger.warning("后台 BaoStock 补齐失败 %s/%s: %s", symbol, freq, exc)
        finally:
            with _background_fetch_lock:
                _background_fetch_keys.discard(key)

    threading.Thread(target=_run, name=f"chan-kline-fetch-{symbol}-{freq}", daemon=True).start()


def resolve_chan_compute_bars(freq: str, requested_count: int = 0, max_compute_bars: Optional[int] = None) -> int:
    """按级别限制 CChan 计算深度，避免短周期默认读取 5000 根拖慢首屏。"""
    if max_compute_bars:
        return max(int(requested_count or 0), int(max_compute_bars))
    normalized = str(freq or "day").strip().lower()
    if normalized.startswith("m") and normalized[1:].isdigit():
        normalized = normalized[1:]
    target = FREQ_COMPUTE_BARS.get(normalized, DEFAULT_COMPUTE_BARS)
    return max(int(requested_count or 0), target, _MIN_KLINES)


def _fetch_tencent_fallback_klines(symbol: str, freq: str, count: int) -> list[dict]:
    """BaoStock 不可用时给图表首屏兜底；只读不落库，避免污染正式 BaoStock 缓存。"""
    interval = _TENCENT_FREQ_MAP.get(freq)
    if not interval:
        return []

    qt_symbol = to_tencent_symbol(symbol)
    try:
        if freq == "day":
            url = f"{_QT_KLINE_BASE}{qt_symbol},day,,,{count},qfq"
        else:
            url = f"{_QT_MKLINE_BASE}{qt_symbol},{interval},,{count}"

        with httpx.Client(timeout=PRICE_API_TIMEOUT) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()

        if data.get("code") != 0 or not data.get("data"):
            return []

        stock_data = data["data"].get(qt_symbol, {})
        raw_rows = stock_data.get("qfqday" if freq == "day" else interval)
        if raw_rows is None and freq == "day":
            raw_rows = stock_data.get("day", [])
        if not raw_rows:
            return []

        rows = []
        for item in raw_rows:
            if len(item) < 6:
                continue
            date_value = str(item[0])
            if freq != "day" and len(date_value) >= 12:
                date_value = f"{date_value[:4]}-{date_value[4:6]}-{date_value[6:8]} {date_value[8:10]}:{date_value[10:12]}"
            rows.append({
                "date": date_value,
                "open": float(item[1]),
                "close": float(item[2]),
                "high": float(item[3]),
                "low": float(item[4]),
                "volume": float(item[5]),
                "amount": 0.0,
            })
        return rows
    except Exception as exc:
        logger.warning("腾讯 K 线兜底失败 %s/%s: %s", symbol, freq, exc)
        return []


def _aggregate_1m_rows(rows: list[dict], target_freq: str, limit: int) -> list[dict]:
    """把本地 TDX 1分钟 CLOSED K 聚合为展示用分钟级别，不写入正式结构缓存。"""
    try:
        step = int(target_freq)
    except (TypeError, ValueError):
        return []
    if step <= 1:
        return rows[-limit:]

    aggregated = []
    current_day = ""
    bucket = []
    for row in rows:
        day = str(row.get("date", "")).split(" ", 1)[0]
        if current_day and day != current_day and bucket:
            aggregated.append(_merge_rows(bucket))
            bucket = []
        current_day = day
        bucket.append(row)
        if len(bucket) >= step:
            aggregated.append(_merge_rows(bucket))
            bucket = []
    if bucket:
        aggregated.append(_merge_rows(bucket))
    return aggregated[-limit:]


def _merge_rows(rows: list[dict]) -> dict:
    return {
        "date": rows[-1]["date"],
        "open": float(rows[0]["open"]),
        "high": max(float(row["high"]) for row in rows),
        "low": min(float(row["low"]) for row in rows),
        "close": float(rows[-1]["close"]),
        "volume": sum(float(row.get("volume", 0)) for row in rows),
        "amount": sum(float(row.get("amount", 0)) for row in rows),
    }


def _fetch_tdx_minute_fallback_klines(symbol: str, freq: str, count: int) -> list[dict]:
    """TDX 本地分钟兜底只用于图表展示/回放，不污染 BaoStock 正式结构缓存。"""
    if freq not in {"5", "15", "30", "60"}:
        return []
    try:
        step = int(freq)
        read_limit = min(20000, max(count * step * 2, _MIN_KLINES * step))
        rows_1m = read_tdx_1m_klines(symbol, limit=read_limit)
        if len(rows_1m) < step:
            return []
        return _aggregate_1m_rows(rows_1m, freq, max(count, _MIN_KLINES))
    except Exception as exc:
        logger.warning("TDX 本地分钟兜底失败 %s/%s: %s", symbol, freq, exc)
        return []


def _source_badge(provider: str, freq: str, rows: list[dict]) -> dict:
    last_date = str(rows[-1].get("date") or rows[-1].get("time") or "") if rows else ""
    if provider == "baostock":
        return {"label": f"{freq} · BaoStock", "detail": f"前复权 · {last_date}", "tone": "history"}
    if provider == "tdx":
        return {"label": f"{freq} · TDX本地", "detail": f"不复权 · {last_date}", "tone": "history"}
    if provider == "tdx_minute":
        return {"label": f"{freq} · TDX本地分钟", "detail": f"不复权 · {last_date}", "tone": "history"}
    if provider == "tencent":
        return {"label": f"{freq} · 腾讯兜底", "detail": f"临时数据 · {last_date}", "tone": "live"}
    return {"label": f"{freq} · 未知来源", "detail": last_date, "tone": "history"}

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


def _iter_line_klus(line):
    """遍历一笔/一段覆盖的原始 K 线，供显示边界精确落到 K 线。"""
    if line is None:
        return
    begin_klu = line.get_begin_klu()
    end_klu = line.get_end_klu()
    current = begin_klu
    end_idx = getattr(end_klu, "idx", None)
    seen = 0
    while current is not None:
        yield current
        seen += 1
        if current is end_klu or (end_idx is not None and getattr(current, "idx", None) >= end_idx):
            break
        current = getattr(current, "next", None)
        if seen > 10000:
            break


def _line_value_at_klu(line, klu) -> Optional[float]:
    """按笔/段几何线在指定 K 线位置插值得到价格。"""
    if line is None or klu is None:
        return None
    if not hasattr(line, "get_begin_val") or not hasattr(line, "get_end_val"):
        return None
    begin_klu = line.get_begin_klu()
    end_klu = line.get_end_klu()
    x0 = getattr(begin_klu, "idx", None)
    x1 = getattr(end_klu, "idx", None)
    x = getattr(klu, "idx", None)
    if x0 is None or x1 is None or x is None:
        return None
    y0 = float(line.get_begin_val())
    y1 = float(line.get_end_val())
    if x1 == x0:
        return y1
    ratio = (x - x0) / (x1 - x0)
    return y0 + (y1 - y0) * ratio


def _first_klu_entering_range(line, zd: float, zg: float):
    """找到进中枢段几何线第一次进入 [ZD, ZG] 的 K 线。"""
    if line is None:
        return None
    is_up = bool(line.is_up()) if hasattr(line, "is_up") else False
    is_down = bool(line.is_down()) if hasattr(line, "is_down") else False
    was_outside = False
    for klu in _iter_line_klus(line):
        value = _line_value_at_klu(line, klu)
        candle_high = float(getattr(klu, "high", 0) or 0)
        candle_low = float(getattr(klu, "low", 0) or 0)
        inside = zd <= value <= zg if value is not None else candle_low <= zg and candle_high >= zd
        if not inside:
            was_outside = True
            continue
        if not was_outside:
            continue
        if value is None:
            return klu
        if is_up and value >= zd:
            return klu
        if is_down and value <= zg:
            return klu
        if not is_up and not is_down and zd <= value <= zg:
            return klu
    return None


def _first_klu_fully_outside(line, zd: float, zg: float):
    """找到出中枢段几何线第一次离开 [ZD, ZG] 的 K 线。"""
    if line is None:
        return None
    is_up = bool(line.is_up()) if hasattr(line, "is_up") else False
    is_down = bool(line.is_down()) if hasattr(line, "is_down") else False
    was_inside = False
    for klu in _iter_line_klus(line):
        value = _line_value_at_klu(line, klu)
        candle_high = float(getattr(klu, "high", 0) or 0)
        candle_low = float(getattr(klu, "low", 0) or 0)
        inside = zd <= value <= zg if value is not None else candle_low <= zg and candle_high >= zd
        if inside:
            was_inside = True
            continue
        if not was_inside:
            continue
        if value is None:
            if is_up and candle_low > zg:
                return klu
            if is_down and candle_high < zd:
                return klu
            if not is_up and not is_down and (candle_low > zg or candle_high < zd):
                return klu
            continue
        if is_up and value > zg:
            return klu
        if is_down and value < zd:
            return klu
        if not is_up and not is_down and (value > zg or value < zd):
            return klu
    return None


def _resolve_zhongshu_display_dates(zs, ctime_to_date_str) -> tuple[str, str]:
    """
    计算中枢矩形的视觉起止点。

    算法 begin/end 保留结构语义；display_* 更贴近盘面阅读：
    进入时按进中枢段的几何线穿过 ZD/ZG 的 K 线画起，
    离开时按出中枢段的几何线穿出 ZD/ZG 的 K 线画止。
    """
    zd = float(zs.low)
    zg = float(zs.high)
    begin_fallback = _format_time(zs.begin.time, ctime_to_date_str)
    end_fallback = _format_time(zs.end.time, ctime_to_date_str)

    enter_line = getattr(zs, "bi_in", None) or getattr(zs, "begin_bi", None)
    enter_klu = _first_klu_entering_range(enter_line, zd, zg)
    display_begin = _format_time(enter_klu.time, ctime_to_date_str) if enter_klu is not None else begin_fallback

    out_line = getattr(zs, "bi_out", None)
    out_klu = _first_klu_fully_outside(out_line, zd, zg)
    display_end = _format_time(out_klu.time, ctime_to_date_str) if out_klu is not None else end_fallback

    return display_begin or begin_fallback, display_end or end_fallback

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

def _serialize_bsps(bsp_list, ctime_to_date_str: dict) -> list[dict]:
    """
    序列化买卖点（Buy/Sell Points）列表为前端可用格式。

    bsp.klu 是该买卖点所在笔的末端 K 线单元：
      - 买点（is_buy=True）：笔向下结束，取 klu.low 作为标注价
      - 卖点（is_buy=False）：笔向上结束，取 klu.high 作为标注价

    返回格式：
      [{ "time": "2024-01-05", "price": 123.4, "type": "1", "is_buy": true }, ...]

    type 值对应 BSP_TYPE.value：
      "1"  → 一买/一卖
      "1p" → 类一买/卖
      "2"  → 二买/卖
      "2s" → 类二买/卖
      "3a" → 三买/卖（中枢在1类后）
      "3b" → 三买/卖（中枢在1类前）
    """
    result = []
    for bsp in bsp_list:
        try:
            klu = bsp.klu
            time_str = _format_time(klu.time, ctime_to_date_str)
            if not time_str:
                continue
            # 买点标注在低点，卖点标注在高点
            price = float(klu.low) if bsp.is_buy else float(klu.high)
            # 取首要类型（type2str 可能返回 "1,2s" 这样的多重类型，取首位）
            primary_type = bsp.type[0].value if bsp.type else "1"
            result.append({
                "time":    time_str,
                "price":   round(price, 4),
                "type":    primary_type,
                "is_buy":  bsp.is_buy,
            })
        except Exception:
            pass
    return result


def _serialize_zhongshus(zs_list, ctime_to_date_str) -> list[dict]:
    """
    将 CZS 列表序列化为前端矩形框数据。

    begin/end 保留 chan.py 的结构语义；display_begin/display_end 专门服务前端视觉：
      - 起点 = 进入中枢区间 [ZD, ZG] 的第一根 K 线
      - 终点 = 出中枢笔/段里第一根完全离开 [ZD, ZG] 的 K 线
      - 若仍在延伸或无法定位，回退到结构 begin/end
    """
    result = []
    for zs in zs_list:
        try:
            begin_date = _format_time(zs.begin.time, ctime_to_date_str)

            # 优先使用 bi_out 的结束时刻：出中枢那一笔完成，中枢才算"结案"
            if zs.bi_out is not None:
                end_klu  = zs.bi_out.get_end_klu()
                end_date = _format_time(end_klu.time, ctime_to_date_str)
                # bi_out 时刻可能超出当前数据切片映射范围（末端实时笔），
                # 此时回退到 zs.end（最后内部笔）
                if not end_date:
                    end_date = _format_time(zs.end.time, ctime_to_date_str)
            else:
                end_date = _format_time(zs.end.time, ctime_to_date_str)

            if not begin_date or not end_date:
                continue

            display_begin_date, display_end_date = _resolve_zhongshu_display_dates(zs, ctime_to_date_str)

            result.append({
                "begin_date": begin_date,
                "end_date":   end_date,
                "display_begin_date": display_begin_date,
                "display_end_date": display_end_date,
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
    end_date: Optional[str] = None,
    cchan_preset: str = "live_tolerant",
    kline_source: Optional[str] = None,
    adjustflag: str = "2",
    max_compute_bars: Optional[int] = None,
) -> dict:
    """
    同步版本的缠论结构解析。调用方应通过 run_in_threadpool 包装。
    """
    # 按级别限制计算深度：短周期降低 CChan 输入量，日/周线保留足够上下文。
    COMPUTATION_COUNT = resolve_chan_compute_bars(freq, count, max_compute_bars)

    # 1. 读取 K 线（优先本地缓存）
    rows = query_klines(
        symbol,
        freq,
        end_date=end_date,
        limit=max(count, COMPUTATION_COUNT),
        source=kline_source,
        adjustflag=adjustflag,
    )
    data_provider = "baostock" if not kline_source else str(kline_source)

    if len(rows) < _MIN_KLINES and not _rows_are_fresh(rows):
        logger.info("本地数据不足 %s/%s，后台触发 BaoStock 补齐，当前请求继续兜底...", symbol, freq)
        _schedule_background_fetch(symbol, freq, reason="chan_detail_short_cache")

    if freq == "day" and len(rows) < _MIN_KLINES and not _rows_are_fresh(rows) and kline_source is None:
        tdx_rows = query_klines(
            symbol,
            "day",
            end_date=end_date,
            limit=max(count, COMPUTATION_COUNT),
            source="tdx",
            adjustflag="3",
        )
        if len(tdx_rows) >= _MIN_KLINES:
            rows = tdx_rows
            data_provider = "tdx"
            logger.warning("使用 TDX 日线兜底渲染图表: %s/day rows=%d", symbol, len(rows))

    if freq in {"5", "15", "30", "60"} and len(rows) < _MIN_KLINES and not _rows_are_fresh(rows) and kline_source is None:
        tdx_minute_rows = _fetch_tdx_minute_fallback_klines(
            symbol,
            freq,
            count=max(count, _MIN_KLINES),
        )
        if len(tdx_minute_rows) >= _MIN_KLINES:
            rows = tdx_minute_rows
            data_provider = "tdx_minute"
            logger.warning("使用 TDX 本地分钟兜底渲染图表: %s/%s rows=%d", symbol, freq, len(rows))

    if len(rows) < _MIN_KLINES and not _rows_are_fresh(rows):
        rows = _fetch_tencent_fallback_klines(symbol, freq, count=max(count, _MIN_KLINES))
        if rows:
            data_provider = "tencent"
            logger.warning("使用腾讯 K 线兜底渲染图表: %s/%s rows=%d", symbol, freq, len(rows))

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
    # bi_fx_check 说明：
    #   "strict" / "totally" - 验证分型三根K线 + 前后相邻K线，要求最高
    #   "half"               - 验证分型K线 + 单侧一根相邻K线（原默认）
    #   "loss"               - 只验证分型K线本身，最宽松，最接近缠论原文
    #
    # 选用 "loss" 的原因：
    #   涨停板封板时多根30分一字K线（O=H=L=C）合并后，相邻 CKLine 的
    #   high/low 数据异常（涨停合并K线 high=low=limit_price），导致 "half"
    #   模式扩展到邻近K线后条件无法满足，合法的顶/底分型被错误拒绝。
    #   "loss" 模式只检验分型K线本身的高低关系，完全对应缠论原文定义，
    #   消除涨停板导致的超大笔（如 8.70→11.90 应为三笔却被识别为一笔）。
    config_meta = get_chan_config_meta(cchan_preset)
    config = CChanConfig(get_chan_config_dict(cchan_preset))

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

    # 提取买卖点（cal_seg_and_zs 已在上方调用，bs_point_lst 已填充）
    try:
        bi_bsps  = kl_data.bs_point_lst.getSortedBspList()
        seg_bsps = kl_data.seg_bs_point_lst.getSortedBspList()
        # 按 klu.idx 去重合并（同一根K线上笔和段买卖点取一个）
        seen_idx: set = set()
        all_bsps = []
        for bsp in sorted(list(bi_bsps) + list(seg_bsps), key=lambda b: b.klu.idx):
            if bsp.klu.idx not in seen_idx:
                seen_idx.add(bsp.klu.idx)
                all_bsps.append(bsp)
    except Exception as e:
        logger.warning("BSP 提取失败: %s", e)
        all_bsps = []

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
    serialized_bis  = _serialize_bis(bi_list, ctime_to_date_str, macd, date_to_idx)
    serialized_segs = _serialize_segs(seg_list, ctime_to_date_str, macd, date_to_idx)
    serialized_bi_zs  = _serialize_zhongshus(zs_list, ctime_to_date_str)
    serialized_seg_zs = _serialize_zhongshus(segzs_list, ctime_to_date_str)
    serialized_bsps   = _serialize_bsps(all_bsps, ctime_to_date_str)

    # 6. V6 升级：根据前端请求的 count 进行数据截断，确保响应速度
    slice_count = count if count > 0 else len(rows)
    klines_out_sliced = klines_out[-slice_count:]
    macd_sliced = {
        "dif":   macd["dif"][-slice_count:],
        "dea":   macd["dea"][-slice_count:],
        "hist":  macd["hist"][-slice_count:],
        "dates": dates[-slice_count:],
    }

    if klines_out_sliced:
        cutoff_date = klines_out_sliced[0]["time"]
        serialized_bis    = [b for b in serialized_bis    if b["x1"]   >= cutoff_date]
        serialized_segs   = [s for s in serialized_segs   if s["x1"]   >= cutoff_date]
        serialized_bi_zs  = [z for z in serialized_bi_zs  if z["end_date"] >= cutoff_date]
        serialized_seg_zs = [z for z in serialized_seg_zs if z["end_date"] >= cutoff_date]
        serialized_bsps   = [p for p in serialized_bsps   if p["time"] >= cutoff_date]

    return {
        "symbol":     symbol,
        "freq":       freq,
        "compute_bars": COMPUTATION_COUNT,
        "klines":     klines_out_sliced,
        "bis":        serialized_bis,
        "segs":       serialized_segs,
        "bi_zhongshus":        serialized_bi_zs,
        "bi_zhongshus_decomp": serialized_bi_zs,  # 原版内部直接生成最好质量的中枢
        "seg_zhongshus":       serialized_seg_zs,
        "zhongshus":  serialized_bi_zs,  # 后向兼容
        "bsps":       serialized_bsps,
        "macd":       macd_sliced,
        "config":     config_meta,
        "data_source": {
            "provider": data_provider,
            "freq": freq,
            "adjustflag": "3" if data_provider in {"tdx", "tdx_minute"} else adjustflag,
            "last_date": str(rows[-1].get("date", "")) if rows else "",
        },
        "dataBadge": _source_badge(data_provider, freq, rows),
        "stats": {
            "kline_count":        len(klines_out_sliced),
            "bi_count":           len(serialized_bis),
            "seg_count":          len(serialized_segs),
            "bi_zs_count":        len(serialized_bi_zs),
            "bi_zs_decomp_count": len(serialized_bi_zs),
            "seg_zs_count":       len(serialized_seg_zs),
            "bsp_count":          len(serialized_bsps),
            "computation_klines": len(rows),
        }
    }


# ---------------------------------------------------------------------------
# 异步公共接口
# ---------------------------------------------------------------------------

async def get_chan_detail(
    symbol: str,
    freq: str = "day",
    count: int = 500,
    end_date: Optional[str] = None,
    cchan_preset: str = "live_tolerant",
    kline_source: Optional[str] = None,
    adjustflag: str = "2",
    max_compute_bars: Optional[int] = None,
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

    cache_key = _detail_cache_key(
        symbol_bs,
        freq,
        count,
        end_date,
        cchan_preset,
        kline_source,
        adjustflag,
        max_compute_bars,
    )
    cached = _detail_cache_get(cache_key)
    if cached is not None:
        cached["cache"] = {"hit": True, "ttl_seconds": DETAIL_CACHE_TTL_SECONDS}
        return cached

    snapshot_context = _build_snapshot_context(
        symbol=symbol_bs,
        freq=freq,
        count=count,
        end_date=end_date,
        cchan_preset=cchan_preset,
        kline_source=kline_source,
        adjustflag=adjustflag,
        max_compute_bars=max_compute_bars,
    )
    snapshot = _load_persistent_snapshot(snapshot_context)
    if snapshot is not None:
        snapshot["cache"] = {
            "hit": True,
            "tier": "persistent_snapshot",
            "ttl_seconds": DETAIL_CACHE_TTL_SECONDS,
        }
        _detail_cache_set(cache_key, snapshot)
        return snapshot

    lock = _detail_cache_locks.setdefault(cache_key, asyncio.Lock())
    async with lock:
        cached = _detail_cache_get(cache_key)
        if cached is not None:
            cached["cache"] = {"hit": True, "ttl_seconds": DETAIL_CACHE_TTL_SECONDS}
            return cached
        snapshot = _load_persistent_snapshot(snapshot_context)
        if snapshot is not None:
            snapshot["cache"] = {
                "hit": True,
                "tier": "persistent_snapshot",
                "ttl_seconds": DETAIL_CACHE_TTL_SECONDS,
            }
            _detail_cache_set(cache_key, snapshot)
            return snapshot

        incremental_result = await _try_incremental_chan_detail(
            snapshot_context=snapshot_context,
            symbol=symbol_bs,
            freq=freq,
            count=count,
            end_date=end_date,
            cchan_preset=cchan_preset,
            kline_source=kline_source,
            adjustflag=adjustflag,
            max_compute_bars=max_compute_bars,
        )
        if incremental_result is not None:
            _save_persistent_snapshot(snapshot_context, incremental_result)
            _detail_cache_set(cache_key, incremental_result)
            return incremental_result

        started = time.perf_counter()
        result = await run_in_threadpool(
            _parse_chan_detail_sync,
            symbol_bs,
            freq,
            count,
            end_date,
            cchan_preset,
            kline_source,
            adjustflag,
            max_compute_bars,
        )
        result["cache"] = {
            "hit": False,
            "ttl_seconds": DETAIL_CACHE_TTL_SECONDS,
            "compute_ms": round((time.perf_counter() - started) * 1000),
        }
        if not result.get("error"):
            save_context = snapshot_context or _build_snapshot_context(
                symbol=symbol_bs,
                freq=freq,
                count=count,
                end_date=end_date,
                cchan_preset=cchan_preset,
                kline_source=kline_source,
                adjustflag=adjustflag,
                max_compute_bars=max_compute_bars,
            )
            _save_persistent_snapshot(save_context, result)
            _detail_cache_set(cache_key, result)
        return result


async def prewarm_chan_details(
    *,
    symbols: list[str],
    freqs: list[str],
    count: int = 500,
    cchan_preset: str = "live_tolerant",
    concurrency: int = 2,
) -> dict:
    """Pre-generate Chan detail snapshots for a small symbol/frequency batch."""
    safe_symbols = [item for item in symbols if item][:30]
    safe_freqs = [item for item in freqs if item][:6]
    semaphore = asyncio.Semaphore(max(1, min(int(concurrency or 1), 4)))
    items = []

    async def run_one(symbol: str, freq: str) -> None:
        async with semaphore:
            try:
                result = await get_chan_detail(
                    symbol,
                    freq=freq,
                    count=count,
                    cchan_preset=cchan_preset,
                )
                cache = result.get("cache") or {}
                snapshot = result.get("snapshot") or {}
                items.append({
                    "symbol": result.get("symbol") or symbol,
                    "freq": result.get("freq") or freq,
                    "status": "error" if result.get("error") else "ok",
                    "error": result.get("error"),
                    "cache_tier": cache.get("tier") or ("memory" if cache.get("hit") else "computed"),
                    "snapshot_source": snapshot.get("source"),
                    "last_kline_time": snapshot.get("last_kline_time") or result.get("data_source", {}).get("last_date"),
                    "stats": result.get("stats") or {},
                })
            except Exception as exc:
                items.append({
                    "symbol": symbol,
                    "freq": freq,
                    "status": "error",
                    "error": str(exc),
                })

    await asyncio.gather(*(run_one(symbol, freq) for symbol in safe_symbols for freq in safe_freqs))
    ok_count = sum(1 for item in items if item.get("status") == "ok")
    return {
        "status": "success" if ok_count == len(items) else "partial",
        "requested": len(safe_symbols) * len(safe_freqs),
        "ok": ok_count,
        "items": sorted(items, key=lambda item: (item.get("symbol") or "", item.get("freq") or "")),
    }


def _build_snapshot_context(
    *,
    symbol: str,
    freq: str,
    count: int,
    end_date: Optional[str],
    cchan_preset: str,
    kline_source: Optional[str],
    adjustflag: str,
    max_compute_bars: Optional[int],
) -> Optional[dict]:
    compute_bars = resolve_chan_compute_bars(freq, count, max_compute_bars)
    try:
        signature = get_kline_window_signature(
            symbol,
            freq,
            end_date=end_date,
            limit=max(int(count or 0), compute_bars),
            adjustflag=adjustflag,
            source=kline_source,
        )
    except Exception as exc:
        logger.warning("Chan snapshot signature skipped: %s", exc)
        return None

    if int(signature.get("row_count") or 0) <= 0 or not signature.get("signature"):
        return None
    return {
        "symbol": symbol,
        "freq": freq,
        "count": int(count or 0),
        "end_date": end_date or "",
        "cchan_preset": cchan_preset,
        "kline_source": signature.get("source") or (kline_source or ""),
        "adjustflag": adjustflag,
        "max_compute_bars": int(max_compute_bars or 0),
        "compute_bars": compute_bars,
        "data_signature": signature["signature"],
        "last_kline_time": signature.get("last_date") or "",
        "kline_count": int(signature.get("row_count") or 0),
    }


def _load_persistent_snapshot(snapshot_context: Optional[dict]) -> Optional[dict]:
    if not snapshot_context:
        return None
    snapshot = load_chan_snapshot(
        symbol=snapshot_context["symbol"],
        freq=snapshot_context["freq"],
        cchan_preset=snapshot_context["cchan_preset"],
        kline_source=snapshot_context["kline_source"],
        adjustflag=snapshot_context["adjustflag"],
        end_date=snapshot_context["end_date"],
        max_compute_bars=snapshot_context["max_compute_bars"],
        data_signature=snapshot_context["data_signature"],
    )
    result = (snapshot or {}).get("result") or snapshot
    if result and not _result_has_display_zhongshu_dates(result):
        return None
    return snapshot


def _result_has_display_zhongshu_dates(result: dict) -> bool:
    """旧 snapshot 没有 display_*，会让前端中枢框位置继续使用旧算法坐标。"""
    center_groups = (
        result.get("bi_zhongshus") or [],
        result.get("bi_zhongshus_decomp") or [],
        result.get("seg_zhongshus") or [],
    )
    centers = [item for group in center_groups for item in group]
    if not centers:
        return True
    return all("display_begin_date" in item and "display_end_date" in item for item in centers)


def _save_persistent_snapshot(snapshot_context: Optional[dict], result: dict) -> None:
    if not snapshot_context:
        return
    provider = result.get("data_source", {}).get("provider")
    if provider and provider != snapshot_context["kline_source"]:
        return
    fingerprint = save_chan_snapshot(
        symbol=snapshot_context["symbol"],
        freq=snapshot_context["freq"],
        cchan_preset=snapshot_context["cchan_preset"],
        kline_source=snapshot_context["kline_source"],
        adjustflag=snapshot_context["adjustflag"],
        end_date=snapshot_context["end_date"],
        max_compute_bars=snapshot_context["max_compute_bars"],
        data_signature=snapshot_context["data_signature"],
        last_kline_time=snapshot_context["last_kline_time"],
        kline_count=snapshot_context["kline_count"],
        compute_bars=int(result.get("compute_bars") or snapshot_context["compute_bars"]),
        result=result,
    )
    if fingerprint:
        result["snapshot"] = {
            "hit": False,
            "source": "generated",
            "data_signature": snapshot_context["data_signature"],
            "structure_fingerprint": fingerprint,
            "last_kline_time": snapshot_context["last_kline_time"],
            "kline_count": snapshot_context["kline_count"],
        }


async def _try_incremental_chan_detail(
    *,
    snapshot_context: Optional[dict],
    symbol: str,
    freq: str,
    count: int,
    end_date: Optional[str],
    cchan_preset: str,
    kline_source: Optional[str],
    adjustflag: str,
    max_compute_bars: Optional[int],
) -> Optional[dict]:
    if not snapshot_context or end_date:
        return None

    latest = load_latest_chan_snapshot(
        symbol=snapshot_context["symbol"],
        freq=snapshot_context["freq"],
        cchan_preset=snapshot_context["cchan_preset"],
        kline_source=snapshot_context["kline_source"],
        adjustflag=snapshot_context["adjustflag"],
        end_date=snapshot_context["end_date"],
        max_compute_bars=snapshot_context["max_compute_bars"],
    )
    if not latest:
        return None

    previous = latest.get("result") or {}
    previous_meta = latest.get("snapshot") or {}
    if not _result_has_display_zhongshu_dates(previous):
        return None
    old_last_time = str(previous_meta.get("last_kline_time") or "")
    new_last_time = str(snapshot_context.get("last_kline_time") or "")
    if not old_last_time or not new_last_time or new_last_time <= old_last_time:
        return None

    tail_bars = _tail_recompute_bars(freq)
    tail_rows = query_klines(
        symbol,
        freq,
        end_date=end_date,
        limit=tail_bars,
        source=kline_source,
        adjustflag=adjustflag,
    )
    tail_dates = [str(row.get("date")) for row in tail_rows]
    if old_last_time not in tail_dates:
        return None
    if not any(date > old_last_time for date in tail_dates):
        return None

    started = time.perf_counter()
    tail_result = await run_in_threadpool(
        _parse_chan_detail_sync,
        symbol,
        freq,
        tail_bars,
        end_date,
        cchan_preset,
        kline_source,
        adjustflag,
        tail_bars,
    )
    if tail_result.get("error") or not tail_result.get("klines"):
        return None

    merged = _merge_incremental_chan_result(
        previous=previous,
        tail_result=tail_result,
        old_last_time=old_last_time,
        requested_count=int(count or 0),
        snapshot_context=snapshot_context,
    )
    if not merged:
        return None

    merged["cache"] = {
        "hit": False,
        "tier": "incremental_tail",
        "ttl_seconds": DETAIL_CACHE_TTL_SECONDS,
        "compute_ms": round((time.perf_counter() - started) * 1000),
        "tail_bars": tail_bars,
    }
    merged["snapshot"] = {
        "hit": False,
        "source": "incremental_tail",
        "previous_data_signature": previous_meta.get("data_signature"),
        "data_signature": snapshot_context["data_signature"],
        "last_kline_time": snapshot_context["last_kline_time"],
        "kline_count": snapshot_context["kline_count"],
        "tail_bars": tail_bars,
    }
    return merged


def _merge_incremental_chan_result(
    *,
    previous: dict,
    tail_result: dict,
    old_last_time: str,
    requested_count: int,
    snapshot_context: dict,
) -> Optional[dict]:
    tail_klines = tail_result.get("klines") or []
    tail_times = [str(item.get("time")) for item in tail_klines]
    if old_last_time not in tail_times:
        return None
    cutoff_time = str(tail_klines[0].get("time") or "")
    if not cutoff_time:
        return None

    previous_klines = previous.get("klines") or []
    new_klines = [item for item in tail_klines if str(item.get("time")) > old_last_time]
    klines = [*previous_klines, *new_klines]
    if requested_count > 0:
        klines = klines[-requested_count:]
    if not klines:
        return None
    visible_cutoff = str(klines[0].get("time") or "")

    def stable_by_end(items: list[dict], key: str) -> list[dict]:
        return [item for item in (items or []) if str(item.get(key) or "") < cutoff_time]

    bis = [
        *stable_by_end(previous.get("bis") or [], "x1"),
        *(tail_result.get("bis") or []),
    ]
    segs = [
        *stable_by_end(previous.get("segs") or [], "x1"),
        *(tail_result.get("segs") or []),
    ]
    bi_zhongshus = [
        *stable_by_end(previous.get("bi_zhongshus") or [], "end_date"),
        *(tail_result.get("bi_zhongshus") or []),
    ]
    bi_zhongshus_decomp = [
        *stable_by_end(previous.get("bi_zhongshus_decomp") or [], "end_date"),
        *(tail_result.get("bi_zhongshus_decomp") or tail_result.get("bi_zhongshus") or []),
    ]
    seg_zhongshus = [
        *stable_by_end(previous.get("seg_zhongshus") or [], "end_date"),
        *(tail_result.get("seg_zhongshus") or []),
    ]
    bsps = [
        *stable_by_end(previous.get("bsps") or [], "time"),
        *(tail_result.get("bsps") or []),
    ]

    bis = [item for item in bis if str(item.get("x1") or "") >= visible_cutoff]
    segs = [item for item in segs if str(item.get("x1") or "") >= visible_cutoff]
    bi_zhongshus = [item for item in bi_zhongshus if str(item.get("end_date") or "") >= visible_cutoff]
    bi_zhongshus_decomp = [item for item in bi_zhongshus_decomp if str(item.get("end_date") or "") >= visible_cutoff]
    seg_zhongshus = [item for item in seg_zhongshus if str(item.get("end_date") or "") >= visible_cutoff]
    bsps = [item for item in bsps if str(item.get("time") or "") >= visible_cutoff]

    macd = _merge_incremental_macd(previous.get("macd") or {}, tail_result.get("macd") or {}, old_last_time, requested_count)
    if macd:
        macd = _slice_macd_to_klines(macd, klines)

    merged = {
        **previous,
        "symbol": snapshot_context["symbol"],
        "freq": snapshot_context["freq"],
        "compute_bars": _tail_recompute_bars(snapshot_context["freq"]),
        "klines": klines,
        "bis": bis,
        "segs": segs,
        "bi_zhongshus": bi_zhongshus,
        "bi_zhongshus_decomp": bi_zhongshus_decomp,
        "seg_zhongshus": seg_zhongshus,
        "zhongshus": bi_zhongshus,
        "bsps": bsps,
        "macd": macd or previous.get("macd", {}),
        "config": tail_result.get("config") or previous.get("config"),
        "data_source": tail_result.get("data_source") or previous.get("data_source"),
        "dataBadge": tail_result.get("dataBadge") or previous.get("dataBadge"),
        "stats": {
            "kline_count": len(klines),
            "bi_count": len(bis),
            "seg_count": len(segs),
            "bi_zs_count": len(bi_zhongshus),
            "bi_zs_decomp_count": len(bi_zhongshus_decomp),
            "seg_zs_count": len(seg_zhongshus),
            "bsp_count": len(bsps),
            "computation_klines": len(tail_result.get("klines") or []),
            "incremental_tail": True,
        },
    }
    return merged if _validate_incremental_merge(merged) else None


def _merge_incremental_macd(previous_macd: dict, tail_macd: dict, old_last_time: str, requested_count: int) -> dict:
    previous_dates = [str(item) for item in previous_macd.get("dates") or []]
    tail_dates = [str(item) for item in tail_macd.get("dates") or []]
    if old_last_time not in tail_dates:
        return previous_macd

    append_indexes = [index for index, date in enumerate(tail_dates) if date > old_last_time]
    merged = {}
    for key in ("dif", "dea", "hist", "dates"):
        previous_values = list(previous_macd.get(key) or [])
        tail_values = list(tail_macd.get(key) or [])
        append_values = [tail_values[index] for index in append_indexes if index < len(tail_values)]
        values = [*previous_values, *append_values]
        if requested_count > 0:
            values = values[-requested_count:]
        merged[key] = values
    return merged


def _slice_macd_to_klines(macd: dict, klines: list[dict]) -> dict:
    dates = [str(item.get("time")) for item in klines]
    if not dates or not macd.get("dates"):
        return macd
    index_by_date = {str(date): index for index, date in enumerate(macd.get("dates") or [])}
    indexes = [index_by_date.get(date) for date in dates]
    if any(index is None for index in indexes):
        return macd
    return {
        key: [values[index] for index in indexes if index is not None and index < len(values)]
        for key, values in macd.items()
    }


def _validate_incremental_merge(result: dict) -> bool:
    klines = result.get("klines") or []
    times = [str(item.get("time") or "") for item in klines]
    if not times or len(times) != len(set(times)) or times != sorted(times):
        return False
    visible = set(times)

    def refs_visible(items: list[dict], fields: tuple[str, ...]) -> bool:
        for item in items or []:
            for field in fields:
                value = str(item.get(field) or "")
                if value and value not in visible:
                    return False
        return True

    def ordered_refs(items: list[dict], start_field: str, end_field: str) -> bool:
        for item in items or []:
            start = str(item.get(start_field) or "")
            end = str(item.get(end_field) or "")
            if not start or not end or start > end:
                return False
        return True

    def unique_keys(items: list[dict], fields: tuple[str, ...]) -> bool:
        keys = []
        for item in items or []:
            key = tuple(str(item.get(field) or "") for field in fields)
            if any(key):
                keys.append(key)
        return len(keys) == len(set(keys))

    checks = [
        ordered_refs(result.get("bis") or [], "x0", "x1"),
        ordered_refs(result.get("segs") or [], "x0", "x1"),
        ordered_refs(result.get("bi_zhongshus") or [], "begin_date", "end_date"),
        ordered_refs(result.get("bi_zhongshus_decomp") or [], "begin_date", "end_date"),
        ordered_refs(result.get("seg_zhongshus") or [], "begin_date", "end_date"),
        refs_visible(result.get("bis") or [], ("x1",)),
        refs_visible(result.get("segs") or [], ("x1",)),
        refs_visible(result.get("bi_zhongshus") or [], ("end_date",)),
        refs_visible(result.get("bi_zhongshus_decomp") or [], ("end_date",)),
        refs_visible(result.get("seg_zhongshus") or [], ("end_date",)),
        refs_visible(result.get("bsps") or [], ("time",)),
        unique_keys(result.get("bis") or [], ("x0", "x1", "y0", "y1")),
        unique_keys(result.get("segs") or [], ("x0", "x1", "y0", "y1")),
        unique_keys(result.get("bi_zhongshus") or [], ("begin_date", "end_date", "zg", "zd")),
        unique_keys(result.get("bi_zhongshus_decomp") or [], ("begin_date", "end_date", "zg", "zd")),
        unique_keys(result.get("seg_zhongshus") or [], ("begin_date", "end_date", "zg", "zd")),
        unique_keys(result.get("bsps") or [], ("time", "price", "type", "is_buy")),
    ]
    if not all(checks):
        return False

    macd_dates = [str(item) for item in (result.get("macd") or {}).get("dates") or []]
    if macd_dates and macd_dates != times:
        return False
    return True


def _tail_recompute_bars(freq: str) -> int:
    normalized = str(freq or "day").strip().lower()
    if normalized.startswith("m") and normalized[1:].isdigit():
        normalized = normalized[1:]
    return TAIL_RECOMPUTE_BARS.get(normalized, 500)


def _detail_cache_key(
    symbol: str,
    freq: str,
    count: int,
    end_date: Optional[str],
    cchan_preset: str,
    kline_source: Optional[str],
    adjustflag: str,
    max_compute_bars: Optional[int],
) -> tuple:
    return (
        DETAIL_RESPONSE_SCHEMA_VERSION,
        symbol,
        freq,
        int(count or 0),
        end_date or "",
        cchan_preset,
        kline_source or "",
        adjustflag,
        int(max_compute_bars or 0),
    )


def _detail_cache_get(cache_key: tuple) -> Optional[dict]:
    now = time.monotonic()
    cached = _detail_cache.get(cache_key)
    if not cached:
        return None
    if now - cached["cached_at"] >= DETAIL_CACHE_TTL_SECONDS:
        _detail_cache.pop(cache_key, None)
        return None
    return copy.deepcopy(cached["result"])


def _detail_cache_set(cache_key: tuple, result: dict) -> None:
    _detail_cache[cache_key] = {
        "cached_at": time.monotonic(),
        "result": copy.deepcopy(result),
    }
    _trim_detail_cache(time.monotonic())


def _trim_detail_cache(now: float) -> None:
    expired = [
        key for key, value in _detail_cache.items()
        if now - value["cached_at"] >= DETAIL_CACHE_TTL_SECONDS
    ]
    for key in expired:
        _detail_cache.pop(key, None)

    while len(_detail_cache) > DETAIL_CACHE_MAX_ITEMS:
        oldest_key = min(
            _detail_cache,
            key=lambda item: _detail_cache[item]["cached_at"],
        )
        _detail_cache.pop(oldest_key, None)


# ---------------------------------------------------------------------------
# 多级别联动解析（第一阶段：真实级别关系）
# ---------------------------------------------------------------------------

# 级别优先级（从高到低，CChan 要求顺序）
_LEVEL_ORDER = {
    KL_TYPE.K_WEEK:  0,
    KL_TYPE.K_DAY:   1,
    KL_TYPE.K_60M:   2,
    KL_TYPE.K_30M:   3,
    KL_TYPE.K_15M:   4,
    KL_TYPE.K_5M:    5,
    KL_TYPE.K_1M:    6,
}


def _build_kline_units(symbol: str, freq: str,
                       count: int = 5000) -> tuple[list, dict, list]:
    """从数据湖读取 K 线，返回 (units, ctime_to_date_str, rows)。"""
    rows = query_klines(symbol, freq, limit=max(count, 5000))
    if len(rows) < _MIN_KLINES:
        _schedule_background_fetch(symbol, freq, reason="multi_level_units_short_cache")

    units = []
    ctime_to_date_str = {}
    for r in rows:
        dt_str = str(r["date"])
        date_part, time_part = (dt_str.split(" ", 1) if " " in dt_str
                                else (dt_str, "09:30:00"))
        ymd = date_part.split("-")
        hms = time_part.split(":")
        try:
            y, m, d = int(ymd[0]), int(ymd[1]), int(ymd[2])
            h, mi = int(hms[0]), int(hms[1])
            ctime = CTime(y, m, d, h, mi)
            key = f"{y}-{m}-{d}-{h}-{mi}"
            ctime_to_date_str[key] = dt_str
            units.append(CKLine_Unit({
                DATA_FIELD.FIELD_TIME:   ctime,
                DATA_FIELD.FIELD_OPEN:   float(r["open"]),
                DATA_FIELD.FIELD_HIGH:   float(r["high"]),
                DATA_FIELD.FIELD_LOW:    float(r["low"]),
                DATA_FIELD.FIELD_CLOSE:  float(r["close"]),
                DATA_FIELD.FIELD_VOLUME: float(r["volume"]),
            }))
        except Exception:
            continue
    return units, ctime_to_date_str, rows


def _serialize_one_level(kl_data, ctime_to_date_str: dict,
                         rows: list, freq: str, count: int) -> dict:
    """序列化单个级别数据（与 _parse_chan_detail_sync 后半段等价）。"""
    bi_list    = kl_data.bi_list
    seg_list   = getattr(kl_data, "seg_list", [])
    zs_list    = kl_data.zs_list
    segzs_list = getattr(kl_data, "segzs_list", [])

    closes    = [r["close"] for r in rows]
    macd      = _compute_macd(closes)
    dates     = [r["date"] for r in rows]
    date_to_idx = {d: i for i, d in enumerate(dates)}

    klines_out = [{"time": r["date"], "open": r["open"], "high": r["high"],
                   "low": r["low"], "close": r["close"], "volume": r["volume"]}
                  for r in rows]

    s_bis    = _serialize_bis(bi_list, ctime_to_date_str, macd, date_to_idx)
    s_segs   = _serialize_segs(seg_list, ctime_to_date_str, macd, date_to_idx)
    s_bi_zs  = _serialize_zhongshus(zs_list, ctime_to_date_str)
    s_seg_zs = _serialize_zhongshus(segzs_list, ctime_to_date_str)

    # 提取买卖点
    try:
        bi_bsps  = kl_data.bs_point_lst.getSortedBspList()
        seg_bsps = kl_data.seg_bs_point_lst.getSortedBspList()
        seen_idx: set = set()
        all_bsps = []
        for bsp in sorted(list(bi_bsps) + list(seg_bsps), key=lambda b: b.klu.idx):
            if bsp.klu.idx not in seen_idx:
                seen_idx.add(bsp.klu.idx)
                all_bsps.append(bsp)
    except Exception as e:
        logger.warning("BSP 提取失败 (multi-level) %s: %s", freq, e)
        all_bsps = []
    s_bsps = _serialize_bsps(all_bsps, ctime_to_date_str)

    slice_count = count if count > 0 else len(rows)
    klines_sliced = klines_out[-slice_count:]
    macd_sliced = {
        "dif":   macd["dif"][-slice_count:],
        "dea":   macd["dea"][-slice_count:],
        "hist":  macd["hist"][-slice_count:],
        "dates": dates[-slice_count:],
    }

    if klines_sliced:
        cutoff = klines_sliced[0]["time"]
        s_bis   = [b for b in s_bis   if b["x1"]       >= cutoff]
        s_segs  = [s for s in s_segs  if s["x1"]       >= cutoff]
        s_bi_zs = [z for z in s_bi_zs if z["end_date"] >= cutoff]
        s_seg_zs = [z for z in s_seg_zs if z["end_date"] >= cutoff]
        s_bsps  = [p for p in s_bsps  if p["time"]     >= cutoff]

    return {
        "freq":                freq,
        "klines":              klines_sliced,
        "bis":                 s_bis,
        "segs":                s_segs,
        "bi_zhongshus":        s_bi_zs,
        "bi_zhongshus_decomp": s_bi_zs,
        "seg_zhongshus":       s_seg_zs,
        "zhongshus":           s_bi_zs,
        "bsps":                s_bsps,
        "macd":                macd_sliced,
        "stats": {
            "kline_count":        len(klines_sliced),
            "bi_count":           len(s_bis),
            "seg_count":          len(s_segs),
            "bi_zs_count":        len(s_bi_zs),
            "seg_zs_count":       len(s_seg_zs),
            "bsp_count":          len(s_bsps),
            "computation_klines": len(rows),
        },
    }


def _extract_level_relations(details: dict[str, dict]) -> dict:
    """
    基于时间区间推断笔层面的跨级别归属关系。

    思路：高级别的每一笔（bi）定义了一个时间区间 [x0, x1]；
    低级别在此区间内的所有笔都"属于"这根高级别笔。
    这是缠论中"高级别笔 = 低级别完整走势"的近似实现。

    返回:
        {
          "m30_in_day": [{"m30_bi_idx": i, "day_bi_idx": j}, ...],
          "m5_in_m30":  [{"m5_bi_idx":  i, "m30_bi_idx": j}, ...],
          "m5_in_day":  [{"m5_bi_idx":  i, "day_bi_idx": j}, ...],
        }
    """
    relations = {}
    freq_pairs = []

    # 自动推断哪些级别要做包含关系
    available = list(details.keys())
    level_priority = {"week": 0, "day": 1, "m60": 2, "m30": 3, "m15": 4, "m5": 5}
    available_sorted = sorted(available, key=lambda x: level_priority.get(x, 99))

    for i in range(len(available_sorted) - 1):
        high_lv = available_sorted[i]
        low_lv  = available_sorted[i + 1]
        freq_pairs.append((high_lv, low_lv))

    for high_lv, low_lv in freq_pairs:
        high_bis = details[high_lv].get("bis", [])
        low_bis  = details[low_lv].get("bis", [])

        if not high_bis or not low_bis:
            continue

        key = f"{low_lv}_in_{high_lv}"
        mapping = []
        for li, lb in enumerate(low_bis):
            lb_start = lb.get("x0", "")
            lb_end   = lb.get("x1", "")
            # 找到包含此低级别笔时间区间的高级别笔
            for hi, hb in enumerate(high_bis):
                hb_start = hb.get("x0", "")
                hb_end   = hb.get("x1", "")
                if hb_start <= lb_start and lb_end <= hb_end:
                    mapping.append({
                        f"{low_lv}_bi_idx":  li,
                        f"{high_lv}_bi_idx": hi,
                        "parent_is_up": hb.get("is_up"),  # 高级别笔方向
                    })
                    break  # 一根低级别笔只属于一根高级别笔
        relations[key] = mapping

    return relations


def _parse_chan_multi_level_sync(
    symbol: str,
    system_freqs: list[str],   # 例如 ["day", "30", "5"]（从高到低）
    count: int = 800,
) -> dict:
    """
    多级别联动解析：一次 CChan 调用同时处理所有级别，
    获得真实的级别包含关系（parent K-line ↔ child K-line）。

    相较于分别调用 _parse_chan_detail_sync 5 次，此函数：
    1. CChan 内部自动建立 K 线的 parent-child 链接
    2. 级别包含关系通过 _extract_level_relations 在笔层面计算
    3. 各级别的分析逻辑（笔/中枢/背驰）保持不变

    Returns:
        {
          "day": {serialized level data},
          "30":  {serialized level data},
          "5":   {serialized level data},
          "_level_relations": {crossing-level bi containment},
        }
    """
    # ── 1. 读取各级别 K 线数据 ──
    freq_map = {
        "day": "day", "week": "week",
        "m60": "60", "m30": "30", "m15": "15", "m5": "5",
        # 兼容数字格式
        "60": "60", "30": "30", "15": "15", "5": "5",
    }
    kl_type_list = []
    units_per_level   = {}   # KL_TYPE → units
    ctime_maps        = {}   # freq_str → ctime_to_date_str
    rows_per_level    = {}   # freq_str → rows

    for freq in system_freqs:
        raw_freq = freq_map.get(freq, freq)          # "m30" → "30", "day" → "day"
        kl_type  = PERIOD_MAP.get(raw_freq, KL_TYPE.K_DAY)
        if kl_type in units_per_level:
            continue  # 避免重复

        units, ctime_map, rows = _build_kline_units(symbol, raw_freq, count=count)
        if not units:
            logger.warning("多级别解析：%s/%s 无数据", symbol, raw_freq)
            continue

        kl_type_list.append(kl_type)
        units_per_level[kl_type]  = units
        ctime_maps[freq]          = ctime_map
        rows_per_level[freq]      = rows

    if not kl_type_list:
        return {"error": f"无可用K线数据: {symbol}"}

    # 按级别从高到低排序（CChan 要求）
    kl_type_list.sort(key=lambda x: _LEVEL_ORDER.get(x, 99))

    # ── 2. 单次 CChan 调用（多级别联动）──
    config = CChanConfig(get_chan_config_dict("live_tolerant"))

    chan = CChan(
        code=symbol,
        data_src=DATA_SRC.CUSTOM,
        lv_list=kl_type_list,       # ← 多个级别同时传入，建立真实层级关系
        config=config,
        autype=AUTYPE.QFQ,
    )

    chan.trigger_load(units_per_level)

    # ── 3. 各级别独立后处理（cal_seg_and_zs）+ 序列化 ──
    result = {}
    freq_to_kl_type = {freq_map.get(f, f): PERIOD_MAP.get(freq_map.get(f, f), KL_TYPE.K_DAY)
                       for f in system_freqs}

    for freq in system_freqs:
        raw_freq = freq_map.get(freq, freq)
        kl_type  = PERIOD_MAP.get(raw_freq, KL_TYPE.K_DAY)
        if kl_type not in chan.kl_datas:
            result[freq] = {"error": f"CChan 未生成 {freq} 级别数据"}
            continue

        kl_data = chan.kl_datas[kl_type]
        try:
            kl_data.cal_seg_and_zs()
        except Exception as e:
            logger.warning("cal_seg_and_zs 失败 %s/%s: %s", symbol, freq, e)

        rows = rows_per_level.get(freq, [])
        ctime_map = ctime_maps.get(freq, {})

        if not rows:
            result[freq] = {"error": f"无行数据 {freq}"}
            continue

        try:
            level_data = _serialize_one_level(kl_data, ctime_map, rows, raw_freq, count)
            level_data["symbol"] = symbol
            result[freq] = level_data
        except Exception as e:
            logger.error("序列化失败 %s/%s: %s", symbol, freq, e)
            result[freq] = {"error": str(e)}

    # ── 4. 跨级别 bi 包含关系 ──
    result["_level_relations"] = _extract_level_relations(result)

    return result


async def get_chan_multi_level(
    symbol: str,
    system_freqs: list[str],
    count: int = 800,
) -> dict:
    """
    异步多级别联动接口。

    Args:
        symbol:       股票代码（sh600519 或 sh.600519）
        system_freqs: 级别列表，从高到低，如 ["day", "m30", "m5"]
        count:        每级别返回的 K 线根数（计算始终用 5000 根）

    Returns:
        {freq: {klines, bis, segs, ...}, "_level_relations": {...}}
    """
    symbol_bs = symbol.replace("-", ".")
    if len(symbol_bs) > 2 and symbol_bs[2] != ".":
        symbol_bs = f"{symbol_bs[:2]}.{symbol_bs[2:]}"

    return await run_in_threadpool(
        _parse_chan_multi_level_sync, symbol_bs, system_freqs, count
    )
