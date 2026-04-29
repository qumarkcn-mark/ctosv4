"""
screener_filter.py — 选股扫描器初筛层

策略：1次批量查询 kline_lake 近20日数据，Python内存分组过滤，避免 N+1 查询。
预期耗时：< 3秒（含 SQLite I/O）
"""

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from server.db.kline_lake import get_lake_connection

logger = logging.getLogger(__name__)

# ── 初筛参数 ────────────────────────────────────────────────────────────────
MIN_DAILY_AMOUNT   = 80_000_000   # 日均成交额下限（元）：8000万
MIN_PRICE          = 5.0          # 最高价下限（元）：排除仙股
MIN_RECENT_DAYS    = 5            # 近N日必须有成交（排除停牌）
MAX_GAIN_20D       = 0.60         # 近20日涨幅上限：60%（排除妖股）
LOOKBACK_DAYS      = 20           # 初筛回看天数
CALENDAR_MULTIPLIER = 2           # 日历天 = 交易日 × 1.5~2（保守取2）


def _calc_lookback_date(trading_days: int = LOOKBACK_DAYS) -> str:
    """计算回看起始日期（取日历天，保证覆盖足够交易日）"""
    start = date.today() - timedelta(days=trading_days * CALENDAR_MULTIPLIER)
    return start.strftime("%Y-%m-%d")


def batch_screen(symbols: Optional[list[str]] = None, adjustflag: str = "3") -> set[str]:
    """
    批量初筛：1次查询搞定所有股票。

    Args:
        symbols:    指定股票列表；None 表示全市场扫描
        adjustflag: kline_lake 中的复权标志，TDX数据为 '3'

    Returns:
        通过初筛的 symbol 集合
    """
    conn = get_lake_connection("tdx")
    start_date = _calc_lookback_date()
    # get_lake_connection() 返回线程本地读连接，不能在这里关闭；scanner 后续还会复用。
    # ── 1. 批量查询近20日数据（1次 SQL）──────────────────────────────────────
    if symbols:
        placeholders = ",".join("?" * len(symbols))
        sql = f"""
            SELECT symbol, date, high, close, volume, amount
            FROM klines
            WHERE freq = 'day'
              AND adjustflag = ?
              AND date >= ?
              AND symbol IN ({placeholders})
            ORDER BY symbol, date
        """
        params = [adjustflag, start_date] + symbols
    else:
        sql = """
            SELECT symbol, date, high, close, volume, amount
            FROM klines
            WHERE freq = 'day'
              AND adjustflag = ?
              AND date >= ?
            ORDER BY symbol, date
        """
        params = [adjustflag, start_date]

    rows = conn.execute(sql, params).fetchall()
    logger.info("初筛查询返回 %d 行，回看起始 %s", len(rows), start_date)

    # ── 2. Python内存分组 ────────────────────────────────────────────────────
    # symbol → list of (date, high, close, volume, amount)
    data: dict[str, list] = defaultdict(list)
    for row in rows:
        sym = row["symbol"] if hasattr(row, "keys") else row[0]
        dt  = row["date"]   if hasattr(row, "keys") else row[1]
        hi  = float(row["high"]   if hasattr(row, "keys") else row[2])
        cl  = float(row["close"]  if hasattr(row, "keys") else row[3])
        vol = float(row["volume"] if hasattr(row, "keys") else row[4])
        amt = float(row["amount"] if hasattr(row, "keys") else row[5])
        data[sym].append((dt, hi, cl, vol, amt))

    # ── 3. 逐只过滤 ─────────────────────────────────────────────────────────
    passed: set[str] = set()

    for sym, bars in data.items():
        if not bars:
            continue

        dates   = [b[0] for b in bars]
        highs   = [b[1] for b in bars]
        closes  = [b[2] for b in bars]
        volumes = [b[3] for b in bars]
        amounts = [b[4] for b in bars]

        # 条件①：近20日最高价 > MIN_PRICE（排除仙股）
        if max(highs) < MIN_PRICE:
            continue

        # 条件②：近20日日均成交额 > MIN_DAILY_AMOUNT
        avg_amount = sum(amounts) / len(amounts)
        if avg_amount < MIN_DAILY_AMOUNT:
            continue

        # 条件③：近N日均有成交（排除停牌）
        # 取最近 MIN_RECENT_DAYS 个交易日的成交量，全部 > 0
        recent_vols = volumes[-MIN_RECENT_DAYS:]
        if len(recent_vols) < MIN_RECENT_DAYS or any(v <= 0 for v in recent_vols):
            continue

        # 条件④：近20日涨幅 < MAX_GAIN_20D（排除妖股）
        first_close = closes[0]
        last_close  = closes[-1]
        if first_close > 0:
            gain_20d = (last_close - first_close) / first_close
            if gain_20d > MAX_GAIN_20D:
                continue

        passed.add(sym)

    logger.info(
        "初筛完成：%d 只股票中 %d 只通过（通过率 %.1f%%）",
        len(data), len(passed),
        100 * len(passed) / len(data) if data else 0
    )
    return passed


def load_all_symbols(adjustflag: str = "3") -> list[str]:
    """
    从 kline_lake 拉取全市场 symbol 列表（有日线数据的）
    """
    conn = get_lake_connection("tdx")
    # get_lake_connection() 返回线程本地读连接，不能在这里关闭；scanner 后续还会复用。
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM klines WHERE freq='day' AND adjustflag=?",
        (adjustflag,)
    ).fetchall()
    syms = [r[0] if not hasattr(r, "keys") else r["symbol"] for r in rows]
    logger.info("全市场股票总数: %d", len(syms))
    return syms
