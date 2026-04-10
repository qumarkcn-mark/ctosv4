"""CT-OS V4.0 BaoStock 数据拉取服务

数据范围说明（BaoStock 官方）：
  - 日/周/月 K 线：1990-12-19 至今
  - 5/15/30/60 分钟 K 线：2019-01-02 至今（近 5 年）
  - 每日 20:00 完成当日分钟线入库

使用方式：
  - 异步接口：await fetch_and_cache_klines(symbol, freq)
  - 离线脚本：直接调用 fetch_klines_sync(symbol, freq, ...)
"""

import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional

import baostock as bs
import pandas as pd

from server.db.kline_lake import upsert_klines, get_last_sync_date, count_klines

logger = logging.getLogger(__name__)

# BaoStock 的 freq 映射（我们内部 -> baostock 参数）
FREQ_MAP = {
    "day": "d",
    "60":  "60",
    "30":  "30",
    "15":  "15",
    "5":   "5",
}

# 分钟线历史起点（BaoStock 不提供更早的数据）
MIN_MINUTE_DATE = "2019-01-02"
# 日线历史起点（默认拉取多少年）
DEFAULT_DAY_START = "2015-01-01"

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="baostock")


# ---------------------------------------------------------------------------
# 同步核心（在线程池中运行，避免阻塞 asyncio event loop）
# ---------------------------------------------------------------------------

def _bs_login_and_fetch(symbol: str, freq: str, start_date: str, end_date: str, adjustflag: str) -> list[dict]:
    """
    直接调用 BaoStock 同步 API 拉取一段 K 线数据。
    注意：必须在非 asyncio 线程中调用。

    Args:
        symbol: BaoStock 格式，如 "sh.600519"
        freq: "d" / "60" / "30" / "15" / "5"
        start_date: "2023-01-01"
        end_date: "2024-01-01"
        adjustflag: "1"=后复权 "2"=前复权 "3"=不复权

    Returns:
        list of dicts with keys: date, open, high, low, close, volume, amount
    """
    lg = bs.login()
    if lg.error_code != "0":
        logger.error("BaoStock login 失败: %s", lg.error_msg)
        return []

    try:
        if freq == "d":
            fields = "date,open,high,low,close,volume,amount,adjustflag,turn,tradestatus,pctChg"
            rs = bs.query_history_k_data_plus(
                symbol,
                fields,
                start_date=start_date,
                end_date=end_date,
                frequency=freq,
                adjustflag=adjustflag,
            )
        else:
            # 分钟线字段略有不同
            fields = "date,time,open,high,low,close,volume,amount,adjustflag"
            rs = bs.query_history_k_data_plus(
                symbol,
                fields,
                start_date=start_date,
                end_date=end_date,
                frequency=freq,
                adjustflag=adjustflag,
            )

        if rs.error_code != "0":
            logger.warning("查询失败 %s/%s: %s", symbol, freq, rs.error_msg)
            return []

        df = rs.get_data()
        if df is None or df.empty:
            return []

        # 统一时间字段
        if freq != "d" and "time" in df.columns:
            # 分钟线时间格式是 "20240102090000000"，转为 "2024-01-02 09:00:00"
            def fmt_time(row_date: str, row_time: str) -> str:
                if len(row_time) >= 14:
                    t = row_time[:14]
                    return f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}:{t[12:14]}"
                return row_date
            df["date"] = df.apply(lambda r: fmt_time(r["date"], r["time"]), axis=1)

        # 转换数值列，空字符串 -> 0
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

        # 过滤掉无效行（open == 0 通常是停牌/非交易日）
        df = df[df["open"] > 0]

        result = []
        for _, row in df.iterrows():
            result.append({
                "date":   row["date"],
                "open":   float(row["open"]),
                "high":   float(row["high"]),
                "low":    float(row["low"]),
                "close":  float(row["close"]),
                "volume": float(row.get("volume", 0)),
                "amount": float(row.get("amount", 0)),
            })

        return result

    finally:
        bs.logout()


def fetch_klines_sync(
    symbol: str,
    freq: str = "day",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    adjustflag: str = "2",
) -> int:
    """
    同步拉取并缓存 K 线数据（供离线脚本/CLI 调用）。
    返回写入行数。

    增量策略：
    - 如果本地有缓存，从 last_sync_date + 1 天开始拉取
    - 如果本地没有缓存，从 start_date（默认 DEFAULT_DAY_START）开始全量拉取
    """
    bs_freq = FREQ_MAP.get(freq)
    if not bs_freq:
        raise ValueError(f"不支持的频率: {freq}，可用: {list(FREQ_MAP.keys())}")

    # 确定拉取起点（增量）
    last_date = get_last_sync_date(symbol, freq)
    if last_date:
        # 增量：从上次同步日期的下一天开始
        next_day = (datetime.strptime(last_date[:10], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        effective_start = next_day
        logger.info("增量拉取 %s/%s: %s ~ 今天", symbol, freq, effective_start)
    else:
        # 全量：按频率决定起点
        if freq == "day":
            effective_start = start_date or DEFAULT_DAY_START
        else:
            # 分钟线 BaoStock 最早数据为 2019-01-02
            effective_start = start_date or MIN_MINUTE_DATE
        logger.info("全量拉取 %s/%s: %s ~ 今天", symbol, freq, effective_start)

    effective_end = end_date or datetime.today().strftime("%Y-%m-%d")

    if effective_start > effective_end:
        logger.info("%s/%s 数据已是最新，无需拉取", symbol, freq)
        return 0

    rows = _bs_login_and_fetch(symbol, bs_freq, effective_start, effective_end, adjustflag)

    if not rows:
        logger.warning("BaoStock 返回空数据 %s/%s [%s ~ %s]", symbol, freq, effective_start, effective_end)
        return 0

    count = upsert_klines(symbol, freq, rows, adjustflag)
    logger.info("写入 %d 条 K 线: %s/%s", count, symbol, freq)
    return count


async def fetch_and_cache_klines(
    symbol: str,
    freq: str = "day",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    adjustflag: str = "2",
) -> int:
    """
    异步版本（在 FastAPI 中调用，通过线程池桥接同步 BaoStock）。
    返回写入行数。
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _executor,
        fetch_klines_sync,
        symbol, freq, start_date, end_date, adjustflag
    )


async def ensure_klines_cached(
    symbol: str,
    freq: str = "day",
    min_count: int = 200,
) -> bool:
    """
    确保本地有足够的 K 线缓存，不足时触发自动拉取。
    适合在 API handler 中调用（冷启动自动加载）。

    Returns:
        True 如果最终有足够数据，False 如果拉取后仍然不足
    """
    current = count_klines(symbol, freq)
    if current >= min_count:
        return True

    logger.info("本地缓存不足 %s/%s (%d < %d)，开始从 BaoStock 拉取...", symbol, freq, current, min_count)
    await fetch_and_cache_klines(symbol, freq)

    new_count = count_klines(symbol, freq)
    return new_count >= min_count
