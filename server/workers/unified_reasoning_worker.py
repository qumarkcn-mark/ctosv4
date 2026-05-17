"""Scheduled unified reasoning refresher."""

from __future__ import annotations

import asyncio
import logging

from server import config
from server.engines.ai_native.unified_reasoning_service import trigger_unified_reasoning
from server.engines.ai_native.universe_resolver import list_ai_native_user_ids, resolve_ai_native_universe


logger = logging.getLogger(__name__)


class UnifiedReasoningWorker:
    def __init__(self, *, interval_seconds: float = 900.0):
        self.interval_seconds = interval_seconds
        self._running = False
        self._task: asyncio.Task | None = None

    def start(self):
        if not getattr(config, "AI_UNIFIED_REASONING_WORKER_ENABLED", False):
            logger.info("Unified Reasoning Worker 未启用，跳过启动")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Unified Reasoning Worker 启动，轮询间隔 %.1fs", self.interval_seconds)

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
                logger.exception("Unified Reasoning Worker 异常: %s", exc)
            await asyncio.sleep(self.interval_seconds)

    async def tick(self) -> dict:
        users = list_ai_native_user_ids(limit=20)
        generated = 0
        errors = []
        max_symbols = max(1, int(getattr(config, "AI_UNIFIED_REASONING_SYMBOLS_PER_USER", 3)))
        for user_id in users:
            universe = resolve_ai_native_universe(user_id, ["positions", "watchlist"])
            for item in universe[:max_symbols]:
                symbol = item["symbol"]
                try:
                    await trigger_unified_reasoning(user_id=user_id, symbol=symbol)
                    generated += 1
                except Exception as exc:
                    errors.append({"user_id": user_id, "symbol": symbol, "error": str(exc)[:160]})
        return {"generated": generated, "errors": errors}


unified_reasoning_worker = UnifiedReasoningWorker(
    interval_seconds=getattr(config, "AI_UNIFIED_REASONING_WORKER_INTERVAL", 900.0)
)
