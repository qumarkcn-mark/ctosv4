"""AI Native V5 scheduled outcome settlement worker."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi.concurrency import run_in_threadpool

from server import config
from server.engines.ai_native.outcome_settlement_service import (
    list_due_outcome_symbols,
    settle_due_scenario_outcomes,
)
from server.services.price_service import get_batch_prices


logger = logging.getLogger(__name__)


class AIStructureOutcomeWorker:
    def __init__(self, *, interval_seconds: float = 60.0):
        self.interval_seconds = interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if not getattr(config, "STRUCTURE_WORKER_ENABLED", False):
            logger.info("AI Structure Outcome Worker 未启用，跳过启动")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("AI Structure Outcome Worker 启动，轮询间隔 %.1fs", self.interval_seconds)

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            logger.info("AI Structure Outcome Worker 停止")

    async def _loop(self):
        while self._running:
            try:
                await self.tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("AI Structure Outcome Worker 异常: %s", exc)
            await asyncio.sleep(self.interval_seconds)

    async def tick(self) -> dict:
        symbols = await run_in_threadpool(list_due_outcome_symbols)
        if not symbols:
            return {"count": 0, "items": []}
        prices = await get_batch_prices(symbols)
        if not prices:
            return {"count": 0, "items": []}
        result = await run_in_threadpool(settle_due_scenario_outcomes, prices)
        if result.get("count"):
            logger.info("AI Structure Outcome Worker 结算 %d 条分支", result["count"])
        return result


ai_structure_outcome_worker = AIStructureOutcomeWorker(
    interval_seconds=getattr(config, "AI_STRUCTURE_OUTCOME_WORKER_INTERVAL", 60.0)
)
