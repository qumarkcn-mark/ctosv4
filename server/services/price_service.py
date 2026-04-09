"""行情查询服务 — 腾讯行情 HTTP API

轻量级方案，不使用 PyTDX 或数据湖。
每次查询 <50ms，比维护 WebSocket 连接简单可靠。
"""

import httpx
from typing import Optional

from server.config import PRICE_API_TIMEOUT

# 腾讯行情 API 基地址
_QT_BASE = "https://qt.gtimg.cn/q="


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
        print(f"⚠️ 行情查询失败 {symbol}: {e}")
        return None


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
        print(f"⚠️ 批量行情查询失败: {e}")
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
        print(f"⚠️ 解析行情数据失败 {symbol}: {e}")
        return None
