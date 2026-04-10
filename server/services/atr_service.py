import logging
from typing import Optional
from server.services.price_service import get_daily_klines

logger = logging.getLogger(__name__)

async def get_atr(symbol: str, period: int = 14, kline_count: int = 60) -> Optional[float]:
    """
    获取指定股票的 ATR (Average True Range)。
    默认使用最近 60 根日线数据计算 ATR(14)。
    """
    klines = await get_daily_klines(symbol, count=kline_count)
    if not klines or len(klines) < period + 1:
        logger.warning(f"无法计算 ATR，{symbol} K 线数据不足（需至少 {period + 1} 根，实际 {len(klines)} 根）")
        return None

    return calculate_atr_from_klines(klines, period)

def calculate_atr_from_klines(klines: list[dict], period: int = 14) -> float:
    """
    根据给定的 K 线数组计算 ATR（使用 Wilder 移动平均法）。
    要求 klines 数组按时间正序排列（最老的在开头，最新的在最后）。
    """
    if len(klines) <= period:
        return 0.0

    true_ranges = []
    
    # 1. 计算每一天的 True Range
    # 第一天的 TR = High - Low
    true_ranges.append(klines[0]["high"] - klines[0]["low"])
    
    for i in range(1, len(klines)):
        current = klines[i]
        previous = klines[i - 1]
        
        tr1 = current["high"] - current["low"]
        tr2 = abs(current["high"] - previous["close"])
        tr3 = abs(current["low"] - previous["close"])
        
        true_range = max(tr1, tr2, tr3)
        true_ranges.append(true_range)

    # 2. 计算初始 ATR（前 period 个 TR 的简单平均）
    atr = sum(true_ranges[:period]) / period

    # 3. 按 Wilder 移动平均平滑序列
    for i in range(period, len(true_ranges)):
        atr = (atr * (period - 1) + true_ranges[i]) / period

    return round(atr, 3)

async def calculate_stop_loss(symbol: str, entry_price: float, direction: str = "BUY", atr_multiplier: float = 3.0) -> Optional[float]:
    """
    根据入场价和当前 ATR，计算建议的初始止损价。
    默认计算 3倍 ATR 止损。
    BUY 止损 = 入场价 - ATR * multiplier
    SELL 止损 = 入场价 + ATR * multiplier
    """
    atr = await get_atr(symbol)
    if atr is None:
        return None
        
    if direction.upper() == "BUY":
        stop_loss = entry_price - (atr * atr_multiplier)
    else:
        stop_loss = entry_price + (atr * atr_multiplier)
        
    # 防止止损价小于等于0
    return max(round(stop_loss, 3), 0.0)
