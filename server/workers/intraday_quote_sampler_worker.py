"""Background sampler for intraday quote-derived preview bars."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from fastapi.concurrency import run_in_threadpool

from server import config
from server.db.database import get_connection
from server.domain.symbols import normalize_symbol, to_tencent_symbol
from server.services.intraday_observation_service import ingest_intraday_quote
from server.services.tdx_bridge_client import fetch_tdx_quotes, is_tdx_bridge_enabled

logger = logging.getLogger(__name__)


class IntradayQuoteSamplerWorker:
    """Sample watchboard quotes and feed the intraday preview cache."""

    def __init__(self, *, interval_seconds: float = 5.0, max_symbols: int = 60):
        self.interval_seconds = interval_seconds
        self.max_symbols = max_symbols
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_status: dict[str, Any] = {
            "enabled": bool(getattr(config, "INTRADAY_QUOTE_SAMPLER_ENABLED", True)),
            "running": False,
            "bridge_enabled": is_tdx_bridge_enabled(),
            "last_tick_at": "",
            "last_symbols": 0,
            "last_quotes": 0,
            "last_ingested": 0,
            "last_error": "",
        }

    def start(self):
        if not getattr(config, "INTRADAY_QUOTE_SAMPLER_ENABLED", True):
            self._last_status.update({"enabled": False, "running": False, "last_error": "SAMPLER_DISABLED"})
            logger.info("Intraday Quote Sampler 未启用，跳过启动")
            return
        if self._running:
            return
        self._running = True
        self._last_status.update({"enabled": True, "running": True, "bridge_enabled": is_tdx_bridge_enabled()})
        self._task = asyncio.create_task(self._loop())
        logger.info("Intraday Quote Sampler 启动，轮询间隔 %.1fs", self.interval_seconds)

    def stop(self):
        self._running = False
        self._last_status["running"] = False
        if self._task:
            self._task.cancel()
            logger.info("Intraday Quote Sampler 停止")

    def status(self) -> dict[str, Any]:
        return {
            **self._last_status,
            "interval_seconds": self.interval_seconds,
            "max_symbols": self.max_symbols,
        }

    async def _loop(self):
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._last_status.update({
                    "last_tick_at": _now_text(),
                    "last_error": str(exc),
                    "running": self._running,
                    "bridge_enabled": is_tdx_bridge_enabled(),
                })
                logger.exception("Intraday Quote Sampler 异常: %s", exc)
            await asyncio.sleep(self.interval_seconds)

    async def tick(self) -> dict:
        tick_at = _now_text()
        bridge_enabled = is_tdx_bridge_enabled()
        if not bridge_enabled:
            result = {"symbols": 0, "quotes": 0, "ingested": 0, "error": "TDX_BRIDGE_URL_NOT_CONFIGURED"}
            self._last_status.update({
                "last_tick_at": tick_at,
                "last_symbols": 0,
                "last_quotes": 0,
                "last_ingested": 0,
                "last_error": result["error"],
                "running": self._running,
                "bridge_enabled": False,
            })
            return result

        symbols = await run_in_threadpool(load_watchboard_symbols, self.max_symbols)
        if not symbols:
            result = {"symbols": 0, "quotes": 0, "ingested": 0, "error": "NO_WATCHBOARD_SYMBOLS"}
            self._last_status.update({
                "last_tick_at": tick_at,
                "last_symbols": 0,
                "last_quotes": 0,
                "last_ingested": 0,
                "last_error": result["error"],
                "running": self._running,
                "bridge_enabled": bridge_enabled,
            })
            return result
        prices = await fetch_tdx_quotes(symbols)
        ingested = 0
        for symbol in symbols:
            quote = _quote_for_symbol(prices, symbol)
            if not quote:
                continue
            if ingest_intraday_quote(symbol, quote):
                ingested += 1
        if ingested > 0:
            error = ""
        elif prices:
            error = "NO_VALID_TRADING_MINUTE_QUOTES"
        else:
            error = "NO_QUOTES_FROM_TDX_BRIDGE"
        result = {"symbols": len(symbols), "quotes": len(prices or {}), "ingested": ingested, "error": error}
        self._last_status.update({
            "last_tick_at": tick_at,
            "last_symbols": len(symbols),
            "last_quotes": len(prices or {}),
            "last_ingested": ingested,
            "last_error": error,
            "running": self._running,
            "bridge_enabled": bridge_enabled,
        })
        return result


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


def _now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


intraday_quote_sampler_worker = IntradayQuoteSamplerWorker(
    interval_seconds=float(getattr(config, "INTRADAY_QUOTE_SAMPLER_INTERVAL", 5.0)),
    max_symbols=int(getattr(config, "INTRADAY_QUOTE_SAMPLER_MAX_SYMBOLS", 60)),
)
