"""T+0 做T标的 1 分钟 K 线增量同步器。

仅同步启用做T的标的（通常 ≤6 只），每 60s 从 TDX 拉取最新 5 根 1M K线。
复用 kline_lake.upsert_klines()，不影响 kline_sync_worker 的全量同步。

决策 D3（方案 B）：独立 t0_kline_ticker，60s 间隔仅同步做T标的。

环境变量门控: T0_KLINE_TICKER_ENABLED=true（默认 false）
仅在交易时段 (9:30-15:00) 执行。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from fastapi.concurrency import run_in_threadpool

from server import config

logger = logging.getLogger(__name__)

TRADING_START = "09:30"
TRADING_END = "15:00"


class T0KlineTicker:
    """1 分钟 K 线增量同步器。"""

    def __init__(self, *, interval_seconds: float = 60.0):
        self.interval_seconds = interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if not getattr(config, "T0_KLINE_TICKER_ENABLED", False):
            logger.info("T0 Kline Ticker 未启用（T0_KLINE_TICKER_ENABLED=false），跳过启动")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("T0 Kline Ticker 启动，同步间隔 %.0fs", self.interval_seconds)

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self):
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("T0 Kline Ticker 异常: %s", exc)
            await asyncio.sleep(self.interval_seconds)

    async def tick(self) -> dict:
        """执行一次 1M K线增量同步。"""
        # 仅在交易时段执行
        now_str = datetime.now().strftime("%H:%M")
        if not (TRADING_START <= now_str <= TRADING_END):
            return {"skipped": True, "reason": "非交易时段"}

        return await run_in_threadpool(self._sync_all)

    def _sync_all(self) -> dict:
        """同步所有启用做T标的的最新 1M K线。"""
        from server.db.database import get_connection
        from server.db.kline_lake import upsert_klines

        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT wi.symbol
                  FROM watchlist_items wi
                  JOIN watchlist_groups wg ON wg.id = wi.group_id
                 WHERE wi.t0_enabled = 1 AND wi.t0_qty > 0
                """
            ).fetchall()
        finally:
            conn.close()

        symbols = [r[0] for r in rows]
        if not symbols:
            return {"symbols": 0, "updated": 0}

        updated = 0
        for symbol in symbols:
            try:
                bars = _fetch_latest_1m_klines(symbol, limit=5)
                if bars:
                    # 盘中 1M 是实时 preview 原始价，写入 qmt/raw 层，不污染正式结构湖。
                    upsert_klines(symbol, freq="1", rows=bars, adjustflag="3", source="qmt")
                    updated += 1
                    logger.debug("[T0 KlineTicker] 同步 %s 1M K线 %d根", symbol, len(bars))
            except Exception as exc:
                logger.warning("[T0 KlineTicker] 同步失败 %s: %s", symbol, exc)

        return {"symbols": len(symbols), "updated": updated}


def _fetch_latest_1m_klines(symbol: str, limit: int = 5) -> list[dict]:
    """从 TDX bridge 获取最新 N 根 1M K线。

    返回格式对齐 kline_lake 的 upsert_klines 期望格式：
    [{ "date": "2025-05-26 09:31:00", "open": ..., "high": ..., "low": ..., "close": ..., "volume": ... }]
    """
    try:
        from server.services.tdx_bridge_client import fetch_tdx_klines_sync
        bars = fetch_tdx_klines_sync(symbol, freq="1", limit=limit)
        return bars if bars else []
    except Exception:
        # TDX bridge 无 1M 支持时降级为静默失败
        return []
