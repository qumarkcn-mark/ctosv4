"""行情查询服务

优先级策略：
  1. 本地 SQLite 数据湖（< 5ms，baostock 落盘）
  2. Fallback: 腾讯行情 HTTP API（冷启动时自动触发 baostock 拉取）
"""

import logging
import httpx
from typing import Optional

from server.config import PRICE_API_TIMEOUT
from server.db.kline_lake import query_klines, count_klines
from server.domain.symbols import normalize_symbol, to_tencent_symbol
from server.services.baostock_service import fetch_klines_sync, fetch_klines_quick, FREQ_MAP, _executor as bs_executor
from server.services.tdx_bridge_client import fetch_tdx_quote, fetch_tdx_quotes

logger = logging.getLogger(__name__)

# 腾讯行情 API 基地址（仅作 fallback）
_QT_BASE = "https://qt.gtimg.cn/q="
_QT_KLINE_BASE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
_QT_MKLINE_BASE = "https://ifzq.gtimg.cn/appstock/app/kline/mkline?param="

# 如果本地缓存少于此数量，触发 BaoStock 拉取
_MIN_CACHE_ROWS = 100

# 缓存新鲜度阈值（天）：超过此天数的缓存视为过期，强制刷新
_STALE_DAYS = 2


def _is_data_stale(rows: list, stale_days: int = _STALE_DAYS) -> bool:
    """检查缓存数据的最后一条是否过期。

    如果最后一条数据的日期距今超过 stale_days 个日历日，则视为过期。
    这防止了矩阵状态机使用数年前的陈旧数据计算中枢。
    """
    if not rows:
        return True
    from datetime import datetime, timedelta
    last_date_str = str(rows[-1].get("date", "")).split(" ")[0]  # 取日期部分
    try:
        last_date = datetime.strptime(last_date_str, "%Y-%m-%d")
        return (datetime.now() - last_date) > timedelta(days=stale_days)
    except (ValueError, TypeError):
        return True


def _tencent_to_baostock_symbol(symbol: str) -> str:
    """将腾讯格式 (sh600519) 转换为 BaoStock 格式 (sh.600519)"""
    return normalize_symbol(symbol)


def _baostock_to_tencent_symbol(symbol: str) -> str:
    """将 BaoStock 格式 (sh.600519) 转换为腾讯格式 (sh600519)"""
    return to_tencent_symbol(symbol)


async def get_current_price(symbol: str) -> Optional[dict]:
    """
    查询单只股票当前价格。

    Args:
        symbol: 股票代码, 如 "sh600519" 或 "sz000001"

    Returns:
        {
            "symbol": "sh600519",
            "name": "贵州茅台",
            "price": 1752.00,
            "change": 12.50,
            "change_pct": 0.72,
            "volume": 12345,
            "high": 1760.00,
            "low": 1740.00,
            "open": 1745.00,
            "prev_close": 1739.50,
        }
    """
    qt_symbol = to_tencent_symbol(symbol)
    tdx_quote = await fetch_tdx_quote(symbol)
    if tdx_quote:
        return tdx_quote

    try:
        async with httpx.AsyncClient(timeout=PRICE_API_TIMEOUT) as client:
            resp = await client.get(f"{_QT_BASE}{qt_symbol}")
            resp.raise_for_status()
            return _parse_qt_response(qt_symbol, resp.text)
    except Exception as e:
        logger.warning("行情查询失败 %s: %s", symbol, e)
        return None


async def get_daily_klines(
    symbol: str,
    count: int = 500,
    allow_short_fresh_cache: bool = False,
) -> list[dict]:
    """
    获取日线前复权数据。
    优先级：本地 SQLite 数据湖 → BaoStock 拉取 → 腾讯 API fallback

    Args:
        symbol: 股票代码, 如 "sh600519" 或 "sh.600519"
        count: 需要获取的 K 线根数

    Returns:
        [{"date", "open", "close", "high", "low", "volume"}, ...] 按日期正序
    """
    bs_symbol = _tencent_to_baostock_symbol(symbol)

    # 1️⃣ 尝试从本地数据湖读取
    cached = query_klines(bs_symbol, "day", limit=count)
    has_enough_cache = len(cached) >= min(count, _MIN_CACHE_ROWS)
    if cached and allow_short_fresh_cache:
        logger.debug("本地数据湖短缓存命中: %s/day (%d 条)", bs_symbol, len(cached))
        return cached
    if cached and not _is_data_stale(cached) and (has_enough_cache or allow_short_fresh_cache):
        logger.debug("本地数据湖命中: %s/day (%d 条)", bs_symbol, len(cached))
        return cached

    if _is_data_stale(cached):
        logger.info("本地缓存过期 %s/day，最后日期: %s，触发增量拉取", bs_symbol, cached[-1]["date"] if cached else "N/A")

    # 2️⃣ 本地不足，触发 BaoStock 快速拉取（通过共享线程池隔离）
    logger.info("本地缓存不足 %s/day，触发 BaoStock 快速拉取...", bs_symbol)
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(bs_executor, fetch_klines_quick, bs_symbol, "day")
        cached = query_klines(bs_symbol, "day", limit=count)
        if cached:
            return cached
    except Exception as e:
        logger.warning("BaoStock 拉取失败 %s/day: %s, 降级到腾讯 API", bs_symbol, e)

    # 3️⃣ 最后降级：腾讯行情 API
    logger.warning("降级到腾讯 API: %s/day", symbol)
    qt_symbol = _baostock_to_tencent_symbol(bs_symbol)
    url = f"{_QT_KLINE_BASE}{qt_symbol},day,,,{count},qfq"
    try:
        async with httpx.AsyncClient(timeout=PRICE_API_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0 or not data.get("data"):
                return []
            stock_data = data["data"].get(qt_symbol, {})
            klinesraw = stock_data.get("qfqday", stock_data.get("day", []))
            klines = []
            for item in klinesraw:
                if len(item) >= 6:
                    klines.append({
                        "date": item[0], "open": float(item[1]),
                        "close": float(item[2]), "high": float(item[3]),
                        "low": float(item[4]), "volume": float(item[5])
                    })
            return klines
    except Exception as e:
        logger.warning("腾讯 API 日线失败 %s: %s", symbol, e)
        return []


async def get_weekly_klines(
    symbol: str,
    count: int = 200,
    allow_short_fresh_cache: bool = False,
) -> list[dict]:
    """
    获取周线前复权数据。
    优先级：本地 SQLite 数据湖 → BaoStock 拉取 → 腾讯 API fallback
    """
    bs_symbol = _tencent_to_baostock_symbol(symbol)

    # 1️⃣ 尝试从本地数据湖读取
    cached = query_klines(bs_symbol, "week", limit=count)
    has_enough_cache = len(cached) >= min(count, 50)
    if cached and allow_short_fresh_cache:
        logger.debug("本地数据湖短缓存命中: %s/week (%d 条)", bs_symbol, len(cached))
        return cached
    if cached and not _is_data_stale(cached, stale_days=10) and (has_enough_cache or allow_short_fresh_cache):
        logger.debug("本地数据湖命中: %s/week (%d 条)", bs_symbol, len(cached))
        return cached

    # 2️⃣ 本地不足，触发 BaoStock 快速拉取
    logger.info("本地缓存不足 %s/week，触发 BaoStock 快速拉取...", bs_symbol)
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(bs_executor, fetch_klines_quick, bs_symbol, "week")
        cached = query_klines(bs_symbol, "week", limit=count)
        if cached:
            return cached
    except Exception as e:
        logger.warning("BaoStock 拉取失败 %s/week: %s, 降级到腾讯 API", bs_symbol, e)

    # 3️⃣ 最后降级：腾讯行情 API
    logger.warning("降级到腾讯 API: %s/week", symbol)
    qt_symbol = _baostock_to_tencent_symbol(bs_symbol)
    url = f"{_QT_KLINE_BASE}{qt_symbol},week,,,{count},qfq"
    try:
        async with httpx.AsyncClient(timeout=PRICE_API_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0 or not data.get("data"):
                return []
            stock_data = data["data"].get(qt_symbol, {})
            klinesraw = stock_data.get("qfqweek", stock_data.get("week", []))
            klines = []
            for item in klinesraw:
                if len(item) >= 6:
                    klines.append({
                        "date": item[0], "open": float(item[1]),
                        "close": float(item[2]), "high": float(item[3]),
                        "low": float(item[4]), "volume": float(item[5])
                    })
            return klines
    except Exception as e:
        logger.warning("腾讯 API 周线失败 %s: %s", symbol, e)
        return []

# 腾讯分钟线 interval -> baostock freq 映射
_TENCENT_INTERVAL_MAP = {"m60": "60", "m30": "30", "m15": "15", "m5": "5"}


async def get_minute_klines(
    symbol: str,
    interval: str = "m30",
    count: int = 1000,
    allow_short_fresh_cache: bool = False,
) -> list[dict]:
    """
    获取分钟级别 K 线数据，用于多级别状态机推演。
    优先级：本地 SQLite 数据湖 → BaoStock 拉取 → 腾讯 API fallback

    Args:
        symbol: 股票代码, 如 "sh600519" 或 "sh.600519"
        interval: 周期 ("m60", "m30", "m15", "m5")
        count: 需要获取的 K 线根数
    """
    bs_symbol = _tencent_to_baostock_symbol(symbol)
    bs_freq = _TENCENT_INTERVAL_MAP.get(interval, "30")

    # 1️⃣ 本地数据湖（含新鲜度检查）
    cached = query_klines(bs_symbol, bs_freq, limit=count)
    has_enough_cache = len(cached) >= min(count, _MIN_CACHE_ROWS)
    if cached and allow_short_fresh_cache:
        logger.debug("本地数据湖短缓存命中: %s/%s (%d 条)", bs_symbol, bs_freq, len(cached))
        return cached
    if cached and not _is_data_stale(cached) and (has_enough_cache or allow_short_fresh_cache):
        logger.debug("本地数据湖命中: %s/%s (%d 条)", bs_symbol, bs_freq, len(cached))
        return cached
    
    if _is_data_stale(cached):
        logger.info("本地缓存过期 %s/%s，最后日期: %s，触发增量拉取", bs_symbol, bs_freq, cached[-1]["date"] if cached else "N/A")

    # 2️⃣ BaoStock 快速拉取
    logger.info("本地缓存不足 %s/%s，触发 BaoStock 快速拉取...", bs_symbol, bs_freq)
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(bs_executor, fetch_klines_quick, bs_symbol, bs_freq)
        cached = query_klines(bs_symbol, bs_freq, limit=count)
        if cached:
            return cached
    except Exception as e:
        logger.warning("BaoStock 拉取失败 %s/%s: %s, 降级到腾讯 API", bs_symbol, bs_freq, e)

    # 3️⃣ 腾讯 API fallback
    logger.warning("降级到腾讯 API: %s/%s", symbol, interval)
    qt_symbol = _baostock_to_tencent_symbol(bs_symbol)
    url = f"{_QT_MKLINE_BASE}{qt_symbol},{interval},,{count}"
    try:
        async with httpx.AsyncClient(timeout=PRICE_API_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0 or not data.get("data"):
                return []
            stock_data = data["data"].get(qt_symbol, {})
            klinesraw = stock_data.get(interval, [])
            klines = []
            for item in klinesraw:
                if len(item) >= 6:
                    d_str = str(item[0])
                    if len(d_str) >= 12:
                        formatted_date = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]} {d_str[8:10]}:{d_str[10:12]}"
                    else:
                        formatted_date = d_str
                    klines.append({
                        "date": formatted_date,
                        "open": float(item[1]), "close": float(item[2]),
                        "high": float(item[3]), "low": float(item[4]),
                        "volume": float(item[5])
                    })
            return klines
    except Exception as e:
        logger.warning("腾讯 API 分钟线失败 %s (%s): %s", symbol, interval, e)
        return []

async def get_batch_prices(symbols: list[str]) -> dict[str, dict]:
    """
    批量查询多只股票当前价格。
    腾讯 API 支持逗号分隔批量查询。

    Args:
        symbols: ["sh600519", "sz000001", ...]

    Returns:
        {"sh600519": {...}, "sz000001": {...}}
    """
    if not symbols:
        return {}
    qt_symbols = [to_tencent_symbol(symbol) for symbol in symbols]

    tdx_results = await fetch_tdx_quotes(symbols)
    missing_qt_symbols = [symbol for symbol in qt_symbols if symbol not in tdx_results]
    if not missing_qt_symbols:
        return tdx_results

    try:
        query = ",".join(missing_qt_symbols)
        async with httpx.AsyncClient(timeout=PRICE_API_TIMEOUT) as client:
            resp = await client.get(f"{_QT_BASE}{query}")
            resp.raise_for_status()

        results = dict(tdx_results)
        # 响应是多行，每行一只股票
        for line in resp.text.strip().split("\n"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            # 解析 v_sh600519="..." 格式
            var_name = line.split("=")[0]
            sym = var_name.replace("v_", "")
            parsed = _parse_qt_response(sym, line)
            if parsed:
                results[sym] = parsed

        return results
    except Exception as e:
        logger.warning("批量行情查询失败: %s", e)
        return {}


def _parse_qt_response(symbol: str, raw: str) -> Optional[dict]:
    """
    解析腾讯行情 API 响应。
    格式: v_sh600519="1~贵州茅台~600519~1752.00~1739.50~1745.00~12345~..."
    """
    try:
        # 提取引号内的数据
        start = raw.find('"')
        end = raw.rfind('"')
        if start == -1 or end == -1 or start >= end:
            return None

        data = raw[start + 1 : end]
        parts = data.split("~")

        if len(parts) < 35:
            return None

        price = float(parts[3]) if parts[3] else 0
        prev_close = float(parts[4]) if parts[4] else 0
        open_price = float(parts[5]) if parts[5] else 0
        quote_time = _format_qt_quote_time(parts[30] if len(parts) > 30 else "")

        return {
            "symbol": symbol,
            "name": parts[1],
            "price": price,
            "change": round(price - prev_close, 2) if prev_close else 0,
            "change_pct": round((price - prev_close) / prev_close * 100, 2) if prev_close else 0,
            "volume": int(parts[6]) if parts[6] else 0,
            "high": float(parts[33]) if parts[33] else 0,
            "low": float(parts[34]) if parts[34] else 0,
            "open": open_price,
            "prev_close": prev_close,
            "quote_time": quote_time,
        }
    except (ValueError, IndexError) as e:
        logger.warning("解析行情数据失败 %s: %s", symbol, e)
        return None


def _format_qt_quote_time(raw: str) -> str:
    """腾讯行情时间戳格式为 YYYYMMDDHHMMSS，前端只展示盘中时分秒。"""
    value = str(raw or "").strip()
    if len(value) < 14 or not value[:14].isdigit():
        return ""
    return f"{value[8:10]}:{value[10:12]}:{value[12:14]}"
