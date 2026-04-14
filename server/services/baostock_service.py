"""CT-OS V4.0 BaoStock 数据拉取服务

数据范围说明（BaoStock 官方）：
  - 日/周/月 K 线：1990-12-19 至今
  - 5/15/30/60 分钟 K 线：2019-01-02 至今（近 5 年）
  - 每日 20:00 完成当日分钟线入库

使用方式：
  - 异步接口：await fetch_and_cache_klines(symbol, freq)
  - 离线脚本：直接调用 fetch_klines_sync(symbol, freq, ...)

性能优化（V4.1）：
  - 进程级会话连接池：login 仅在首次/断连时执行（~2s），后续复用
  - 智能分段拉取：冷启动只拉最近数据，后台异步补全历史
  - pandas 2.x 兼容性修复：monkey-patch DataFrame.append
  - 线程池扩容至 4 workers
"""

import logging
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Optional

import baostock as bs
import pandas as pd

from server.db.kline_lake import upsert_klines, get_last_sync_date, count_klines

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pandas 2.x 兼容性修复
# baostock 0.8.9 内部 resultset.py 使用 df.append()，该方法在 pandas 2.0 被移除
# ---------------------------------------------------------------------------
if not hasattr(pd.DataFrame, 'append'):
    def _compat_append(self, other, ignore_index=False, verify_integrity=False, sort=False):
        return pd.concat([self, other], ignore_index=ignore_index,
                         verify_integrity=verify_integrity, sort=sort)
    pd.DataFrame.append = _compat_append
    logger.info("已注入 pandas 2.x DataFrame.append 兼容补丁")


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

# 冷启动快速拉取：只拉最近 N 天，先满足前端即时需求
QUICK_FETCH_DAYS = {
    "day": 730,   # 2 年日线 ≈ 500 根
    "60":  180,   # 半年 60m ≈ 720 根
    "30":  120,   # 4 个月 30m ≈ 960 根
    "15":  90,    # 3 个月 15m ≈ 960 根
    "5":   60,    # 2 个月 5m  ≈ 960 根
}

# 线程池扩容至 4 workers（支持 Matrix 页面 5 级别并发）
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="baostock")


# ---------------------------------------------------------------------------
# 进程级 BaoStock 会话连接池与全局锁
# 避免每次查询都 login/logout（login 耗时 ~2 秒）
# 注意：baostock 的 socket 连接是非线程安全的，并发调用会导致数据错乱和死锁
# ---------------------------------------------------------------------------
_session_lock = threading.Lock()
_bs_lock = threading.Lock()  # 保护所有 bs.* 操作
_session_active = False


def _ensure_session():
    """确保 BaoStock 会话处于连接状态（进程级单例）"""
    global _session_active
    with _session_lock:
        if not _session_active:
            with _bs_lock:
                lg = bs.login()
            if lg.error_code != "0":
                logger.error("BaoStock login 失败: %s", lg.error_msg)
                raise ConnectionError(f"BaoStock login 失败: {lg.error_msg}")
            _session_active = True
            logger.info("BaoStock 会话已建立（进程级连接池）")


def _reset_session():
    """断连后重置会话状态，下次调用 _ensure_session 时重新 login"""
    global _session_active
    with _session_lock:
        _session_active = False
        try:
            with _bs_lock:
                bs.logout()
        except Exception:
            pass


def shutdown_baostock():
    """服务关闭时清理 BaoStock 连接（供 FastAPI lifespan 调用）"""
    global _session_active
    with _session_lock:
        if _session_active:
            try:
                with _bs_lock:
                    bs.logout()
            except Exception:
                pass
            _session_active = False
            logger.info("BaoStock 会话已关闭")


# ---------------------------------------------------------------------------
# 同步核心（在线程池中运行，避免阻塞 asyncio event loop）
# ---------------------------------------------------------------------------

def _bs_query(symbol: str, freq: str, start_date: str, end_date: str, adjustflag: str) -> list[dict]:
    """
    使用连接池会话查询 K 线数据。
    自动处理会话断连重试（最多 1 次）。

    Args:
        symbol: BaoStock 格式，如 "sh.600519"
        freq: "d" / "60" / "30" / "15" / "5"
        start_date: "2023-01-01"
        end_date: "2024-01-01"
        adjustflag: "1"=后复权 "2"=前复权 "3"=不复权

    Returns:
        list of dicts with keys: date, open, high, low, close, volume, amount
    """
    for attempt in range(2):
        try:
            _ensure_session()

            if freq == "d":
                fields = "date,open,high,low,close,volume,amount,adjustflag,turn,tradestatus,pctChg"
            else:
                # 分钟线字段略有不同
                fields = "date,time,open,high,low,close,volume,amount,adjustflag"

            with _bs_lock:
                rs = bs.query_history_k_data_plus(
                    symbol, fields,
                    start_date=start_date,
                    end_date=end_date,
                    frequency=freq,
                    adjustflag=adjustflag,
                )

            if rs.error_code != "0":
                error_msg = rs.error_msg or "unknown"
                # 网络错误需要重置会话
                if "网络" in error_msg or "connect" in error_msg.lower():
                    logger.warning("BaoStock 网络错误，重置会话: %s", error_msg)
                    _reset_session()
                    continue
                logger.warning("查询失败 %s/%s: %s", symbol, freq, error_msg)
                return []

            df = rs.get_data()
            if df is None or df.empty:
                return []

            return _parse_dataframe(df, freq)

        except ConnectionError:
            if attempt == 0:
                logger.warning("BaoStock 连接失败，重试中...")
                _reset_session()
                continue
            raise
        except Exception as e:
            if attempt == 0 and ("网络" in str(e) or "connect" in str(e).lower()):
                logger.warning("BaoStock 异常，重置会话: %s", e)
                _reset_session()
                continue
            logger.error("BaoStock 查询异常 %s/%s: %s", symbol, freq, e)
            return []

    return []


def _parse_dataframe(df: pd.DataFrame, freq: str) -> list[dict]:
    """将 BaoStock 返回的 DataFrame 解析为标准 dict 列表"""
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

    rows = _bs_query(symbol, bs_freq, effective_start, effective_end, adjustflag)

    if not rows:
        logger.warning("BaoStock 返回空数据 %s/%s [%s ~ %s]", symbol, freq, effective_start, effective_end)
        return 0

    count = upsert_klines(symbol, freq, rows, adjustflag)
    logger.info("写入 %d 条 K 线: %s/%s", count, symbol, freq)
    return count


def fetch_klines_quick(
    symbol: str,
    freq: str = "day",
    adjustflag: str = "2",
) -> int:
    """
    快速拉取最近数据（供冷启动使用）。
    只拉取最近 N 天的数据，满足前端即时展示需求。
    完整历史由调用方异步触发 fetch_klines_sync 补全。

    Returns:
        写入行数
    """
    bs_freq = FREQ_MAP.get(freq)
    if not bs_freq:
        raise ValueError(f"不支持的频率: {freq}，可用: {list(FREQ_MAP.keys())}")

    # 如果已有增量同步记录，直接走增量
    last_date = get_last_sync_date(symbol, freq)
    if last_date:
        return fetch_klines_sync(symbol, freq, adjustflag=adjustflag)

    # 冷启动：只拉最近 N 天
    quick_days = QUICK_FETCH_DAYS.get(freq, 365)
    start_date = (datetime.today() - timedelta(days=quick_days)).strftime("%Y-%m-%d")

    # 分钟线不早于 2019-01-02
    if freq != "day" and start_date < MIN_MINUTE_DATE:
        start_date = MIN_MINUTE_DATE

    end_date = datetime.today().strftime("%Y-%m-%d")

    logger.info("快速拉取 %s/%s: 最近 %d 天 (%s ~ %s)", symbol, freq, quick_days, start_date, end_date)

    rows = _bs_query(symbol, bs_freq, start_date, end_date, adjustflag)

    if not rows:
        logger.warning("BaoStock 快速拉取返回空数据 %s/%s", symbol, freq)
        return 0

    count = upsert_klines(symbol, freq, rows, adjustflag)
    logger.info("快速写入 %d 条 K 线: %s/%s", count, symbol, freq)
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

    策略优化（V4.1）：
    - 冷启动时使用 fetch_klines_quick 先拉最近数据（~1-2s）
    - 后台异步触发 fetch_klines_sync 补全完整历史

    Returns:
        True 如果最终有足够数据，False 如果拉取后仍然不足
    """
    current = count_klines(symbol, freq)
    if current >= min_count:
        return True

    logger.info("本地缓存不足 %s/%s (%d < %d)，开始从 BaoStock 拉取...", symbol, freq, current, min_count)

    # 阶段 1: 快速拉取最近数据
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(_executor, fetch_klines_quick, symbol, freq)

    new_count = count_klines(symbol, freq)

    # 阶段 2: 后台异步补全完整历史（不阻塞前端响应）
    if new_count > 0:
        asyncio.create_task(_backfill_history(symbol, freq))

    return new_count >= min_count


async def _backfill_history(symbol: str, freq: str):
    """后台异步补全完整历史数据"""
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(_executor, fetch_klines_sync, symbol, freq)
        logger.info("后台历史补全完成: %s/%s", symbol, freq)
    except Exception as e:
        logger.warning("后台历史补全失败 %s/%s: %s", symbol, freq, e)
