"""Background sampler for intraday quote-derived preview bars."""

from __future__ import annotations

import asyncio
import logging

from fastapi.concurrency import run_in_threadpool

from server import config
from server.db.database import get_connection
from server.domain.symbols import normalize_symbol, to_tencent_symbol
from server.services.intraday_observation_service import ingest_intraday_quote
from server.services.tdx_bridge_client import fetch_tdx_quotes

logger = logging.getLogger(__name__)


class IntradayQuoteSamplerWorker:
    """Sample watchboard quotes and feed the intraday preview cache."""

    def __init__(self, *, interval_seconds: float = 5.0, max_symbols: int = 60):
        self.interval_seconds = interval_seconds
        self.max_symbols = max_symbols
        self._running = False
        self._task: asyncio.Task | None = None

    def start(self):
        if not getattr(config, "INTRADAY_QUOTE_SAMPLER_ENABLED", True):
            logger.info("Intraday Quote Sampler 未启用，跳过启动")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Intraday Quote Sampler 启动，轮询间隔 %.1fs", self.interval_seconds)

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            logger.info("Intraday Quote Sampler 停止")

    async def _loop(self):
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Intraday Quote Sampler 异常: %s", exc)
            await asyncio.sleep(self.interval_seconds)

    async def tick(self) -> dict:
        symbols = await run_in_threadpool(load_watchboard_symbols, self.max_symbols)
        if not symbols:
            return {"symbols": 0, "quotes": 0}
        prices = await fetch_tdx_quotes(symbols)
        ingested = 0
        for symbol in symbols:
            quote = _quote_for_symbol(prices, symbol)
            if not quote:
                continue
            ingest_intraday_quote(symbol, quote)
            ingested += 1
        return {"symbols": len(symbols), "quotes": ingested}


def load_watchboard_symbols(limit: int = 60) -> list[str]:
    """Load symbols from positions plus all watchboard groups."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT symbol FROM positions WHERE quantity > 0
            UNION
            SELECT wi.symbol
              FROM watchlist_items wi
              JOIN watchlist_groups wg ON wg.id = wi.group_id
             ORDER BY symbol
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()
    result = []
    seen = set()
    for row in rows:
        try:
            symbol = normalize_symbol(row["symbol"])
        except ValueError:
            continue
        if symbol in seen:
            continue
        result.append(symbol)
        seen.add(symbol)
    return result


def _quote_for_symbol(prices: dict[str, dict], symbol: str) -> dict | None:
    compact = to_tencent_symbol(symbol)
    canonical = normalize_symbol(symbol)
    return prices.get(compact) or prices.get(canonical)


intraday_quote_sampler_worker = IntradayQuoteSamplerWorker(
    interval_seconds=float(getattr(config, "INTRADAY_QUOTE_SAMPLER_INTERVAL", 5.0)),
    max_symbols=int(getattr(config, "INTRADAY_QUOTE_SAMPLER_MAX_SYMBOLS", 60)),
)
