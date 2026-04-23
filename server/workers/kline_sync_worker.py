"""CT-OS V4.0 K线数据自动同步 Worker

启动时自动更新所有自选股（kline_lake 中的全部 symbol + 持仓 symbol）的 K 线数据。
之后每 30 分钟检查一次是否有需要更新的数据。

BaoStock 更新时间表：
  - 日线：每个交易日 17:30 完成入库
  - 分钟线 (5/15/30/60)：每个交易日 20:30 完成入库
  - 周线：每周最后一个交易日更新
"""

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from fastapi.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

# 同步的频率列表
ALL_FREQS = ["day", "60", "30", "15", "5"]

# 检查间隔（秒）：30 分钟
CHECK_INTERVAL = 30 * 60

# 启动后延迟（秒）：等待其他服务就绪
STARTUP_DELAY = 5


def _get_all_tracked_symbols() -> list[str]:
    """收集所有需要同步的股票代码（去重）

    来源：
    1. kline_sync_meta 表中所有已有记录的 symbol
    2. positions 表中的持仓 symbol（转换为 baostock 格式）
    """
    symbols = set()

    # 来源 1: kline_lake 中已有的 symbol（读操作，复用线程本地连接）
    try:
        from server.db.kline_lake import get_lake_connection
        conn = get_lake_connection()
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM kline_sync_meta"
        ).fetchall()
        for row in rows:
            symbols.add(row["symbol"])
    except Exception as e:
        logger.warning("读取 kline_sync_meta 失败: %s", e)

    # 来源 2: 持仓表中的 symbol
    try:
        from server.db.database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM positions WHERE quantity > 0"
            ).fetchall()
            for row in rows:
                raw = row["symbol"]
                # 转换格式: sh600519 -> sh.600519
                if "." not in raw and len(raw) >= 7:
                    raw = f"{raw[:2]}.{raw[2:]}"
                symbols.add(raw)
        finally:
            conn.close()
    except Exception as e:
        logger.warning("读取 positions 失败: %s", e)

    return sorted(symbols)


def _is_trading_day(dt: datetime) -> bool:
    """简单判断是否为交易日（排除周末，不处理节假日）"""
    return dt.weekday() < 5  # 0=周一 ~ 4=周五


def _sync_all_symbols(symbols: list[str], freqs: list[str]) -> dict:
    """同步拉取所有 symbol 的 K 线数据（同步版本，在线程池中运行）

    Returns:
        {"total_symbols": int, "updated_symbols": int, "total_written": int, "errors": int}
    """
    from server.services.baostock_service import fetch_klines_sync
    from server.db.kline_lake import get_last_sync_date

    today = datetime.today().strftime("%Y-%m-%d")
    total_written = 0
    updated_symbols = 0
    errors = 0

    for symbol in symbols:
        symbol_updated = False
        for freq in freqs:
            try:
                # 检查是否已经是最新
                last_date = get_last_sync_date(symbol, freq)
                if last_date and last_date[:10] >= today:
                    continue  # 已经是最新，跳过

                written = fetch_klines_sync(symbol, freq)
                total_written += written
                if written > 0:
                    symbol_updated = True
            except Exception as e:
                logger.error("同步失败 %s/%s: %s", symbol, freq, e)
                errors += 1

        if symbol_updated:
            updated_symbols += 1

    return {
        "total_symbols": len(symbols),
        "updated_symbols": updated_symbols,
        "total_written": total_written,
        "errors": errors,
    }


class KlineSyncWorker:
    """K线数据自动同步后台任务"""

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_sync_time: Optional[datetime] = None

    def start(self):
        """启动后台同步任务"""
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._sync_loop())
            logger.info("📊 K线自动同步 Worker 已启动")

    def stop(self):
        """停止后台同步任务"""
        self._running = False
        if self._task:
            self._task.cancel()
            logger.info("📊 K线自动同步 Worker 已停止")

    async def _sync_loop(self):
        """主循环：启动后立即同步一次，之后定期检查"""
        # 启动延迟，等待其他服务就绪
        await asyncio.sleep(STARTUP_DELAY)

        # 启动时立即执行一次同步
        await self._do_sync("启动同步")

        while self._running:
            try:
                await asyncio.sleep(CHECK_INTERVAL)

                if not self._running:
                    break

                # 判断是否需要同步
                now = datetime.now()

                # 非交易日不同步
                if not _is_trading_day(now):
                    continue

                # 只在 17:30 之后同步（BaoStock 日线入库时间）
                if now.hour < 17 or (now.hour == 17 and now.minute < 30):
                    continue

                # 今天已经同步过就跳过
                if (self._last_sync_time
                        and self._last_sync_time.date() == now.date()):
                    continue

                await self._do_sync("定时同步")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("K线同步循环异常: %s", e)
                await asyncio.sleep(60)  # 异常后等 1 分钟再重试

    async def _do_sync(self, trigger: str):
        """执行一次完整同步"""
        try:
            symbols = await run_in_threadpool(_get_all_tracked_symbols)
            if not symbols:
                logger.info("📊 [%s] 无需同步：没有跟踪的股票", trigger)
                return

            now = datetime.now()

            # 决定同步哪些频率
            # 20:30 之后同步全部（含分钟线），之前只同步日线
            if now.hour >= 21 or (now.hour == 20 and now.minute >= 30):
                freqs = ALL_FREQS
                freq_desc = "全部级别"
            else:
                freqs = ["day"]
                freq_desc = "仅日线"

            logger.info(
                "📊 [%s] 开始同步 %d 只股票 (%s): %s",
                trigger, len(symbols), freq_desc,
                ", ".join(symbols[:5]) + ("..." if len(symbols) > 5 else ""),
            )

            result = await run_in_threadpool(_sync_all_symbols, symbols, freqs)

            self._last_sync_time = datetime.now()

            logger.info(
                "📊 [%s] 同步完成: %d/%d 只股票已更新, 写入 %d 条, 错误 %d",
                trigger,
                result["updated_symbols"],
                result["total_symbols"],
                result["total_written"],
                result["errors"],
            )

            # 同步完成后执行 WAL checkpoint，防止 WAL 文件无限积累
            # 用 run_in_threadpool 包裹，避免同步 I/O 阻塞事件循环
            def _do_checkpoint():
                with sqlite3.connect(LAKE_PATH) as conn:
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

            try:
                from server.db.kline_lake import LAKE_PATH
                await run_in_threadpool(_do_checkpoint)
                logger.debug("📊 [%s] WAL checkpoint 完成", trigger)
            except Exception as wal_err:
                logger.warning("📊 [%s] WAL checkpoint 失败（非致命）: %s", trigger, wal_err)

        except Exception as e:
            logger.error("📊 [%s] 同步失败: %s", trigger, e)

    async def force_sync(self) -> dict:
        """手动触发一次立即同步（供 API 调用）"""
        symbols = await run_in_threadpool(_get_all_tracked_symbols)
        if not symbols:
            return {"message": "没有需要同步的股票", "total_written": 0}

        result = await run_in_threadpool(_sync_all_symbols, symbols, ALL_FREQS)
        self._last_sync_time = datetime.now()
        return result

    @property
    def status(self) -> dict:
        """返回当前同步状态"""
        return {
            "running": self._running,
            "last_sync_time": self._last_sync_time.isoformat() if self._last_sync_time else None,
        }


# 单例实例
kline_sync = KlineSyncWorker()
