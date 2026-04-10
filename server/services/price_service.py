"""行情查询服务 — 腾讯行情 HTTP API

轻量级方案，不使用 PyTDX 或数据湖。
每次查询 <50ms，比维护 WebSocket 连接简单可靠。
"""

import logging
import httpx
from typing import Optional

from server.config import PRICE_API_TIMEOUT

logger = logging.getLogger(__name__)

# 腾讯行情 API 基地址
_QT_BASE = "https://qt.gtimg.cn/q="
_QT_KLINE_BASE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
_QT_MKLINE_BASE = "https://ifzq.gtimg.cn/appstock/app/kline/mkline?param="


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
    try:
        async with httpx.AsyncClient(timeout=PRICE_API_TIMEOUT) as client:
            resp = await client.get(f"{_QT_BASE}{symbol}")
            resp.raise_for_status()
            return _parse_qt_response(symbol, resp.text)
    except Exception as e:
        logger.warning("行情查询失败 %s: %s", symbol, e)
        return None


async def get_daily_klines(symbol: str, count: int = 60) -> list[dict]:
    """
    获取日线前复权数据，用于 ATR 计算。
    
    Args:
        symbol: 股票代码, 如 "sh600519"
        count: 需要获取的 K 线根数
        
    Returns:
        [
            {
                "date": "2023-10-01",
                "open": 100.0,
                "close": 102.0,
                "high": 105.0,
                "low": 98.0,
                "volume": 12345
            },
            ...
        ] 按日期正序排列（最旧的在前，最新的在后）
    """
    url = f"{_QT_KLINE_BASE}{symbol},day,,,{count},qfq"
    
    try:
        async with httpx.AsyncClient(timeout=PRICE_API_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            
            if data.get("code") != 0 or not data.get("data"):
                return []
                
            stock_data = data["data"].get(symbol, {})
            # 优先取前复权数据 qfqday，如果没有则取 day
            klinesraw = stock_data.get("qfqday", stock_data.get("day", []))
            
            klines = []
            for item in klinesraw:
                # 格式: [date, open, close, high, low, volume]
                if len(item) >= 6:
                    klines.append({
                        "date": item[0],
                        "open": float(item[1]),
                        "close": float(item[2]),
                        "high": float(item[3]),
                        "low": float(item[4]),
                        "volume": float(item[5])
                    })
            return klines
    except Exception as e:
        logger.warning("获取日线失败 %s: %s", symbol, e)
        return []

async def get_minute_klines(symbol: str, interval: str = "m30", count: int = 300) -> list[dict]:
    """
    获取分钟级别 K 线数据，用于多级别状态机推演。
    
    Args:
        symbol: 股票代码, 如 "sh600519"
        interval: 周期 ("m60", "m30", "m15", "m5")
        count: 需要获取的 K 线根数
    """
    url = f"{_QT_MKLINE_BASE}{symbol},{interval},,{count}"
    try:
        async with httpx.AsyncClient(timeout=PRICE_API_TIMEOUT) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
            
            if data.get("code") != 0 or not data.get("data"):
                return []
                
            stock_data = data["data"].get(symbol, {})
            klinesraw = stock_data.get(interval, [])
            
            klines = []
            for item in klinesraw:
                if len(item) >= 6:
                    d_str = str(item[0])
                    # 腾讯分钟线的时间格式通常是 YYYYMMDDhhmm 即 12 位
                    if len(d_str) >= 12:
                        formatted_date = f"{d_str[:4]}-{d_str[4:6]}-{d_str[6:8]} {d_str[8:10]}:{d_str[10:12]}"
                    else:
                        formatted_date = d_str

                    klines.append({
                        "date": formatted_date,
                        "open": float(item[1]),
                        "close": float(item[2]),
                        "high": float(item[3]),
                        "low": float(item[4]),
                        "volume": float(item[5])
                    })
            return klines
    except Exception as e:
        logger.warning("获取分钟线失败 %s (%s): %s", symbol, interval, e)
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

    try:
        query = ",".join(symbols)
        async with httpx.AsyncClient(timeout=PRICE_API_TIMEOUT) as client:
            resp = await client.get(f"{_QT_BASE}{query}")
            resp.raise_for_status()

        results = {}
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
        }
    except (ValueError, IndexError) as e:
        logger.warning("解析行情数据失败 %s: %s", symbol, e)
        return None
