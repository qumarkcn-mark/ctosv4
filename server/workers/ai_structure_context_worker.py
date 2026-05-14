"""AI Native V5 user-scoped structure context worker."""

from __future__ import annotations

import asyncio
import logging
import socket
import uuid
from typing import Optional

from server import config
from server.engines.ai_native.structure_context_service import claim_next_context_job, run_context_job


logger = logging.getLogger(__name__)


class AIStructureContextWorker:
    def __init__(self, *, interval_seconds: float = 2.0):
        self.interval_seconds = interval_seconds
        self.worker_id = f"{socket.gethostname()}-v5ctx-{uuid.uuid4().hex[:8]}"
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if not getattr(config, "STRUCTURE_WORKER_ENABLED", False) or not getattr(config, "AI_STRUCTURE_CONTEXT_WORKER_ENABLED", False):
            logger.info("AI Structure Context Worker 未启用，跳过启动")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("AI Structure Context Worker 启动 worker_id=%s", self.worker_id)

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            logger.info("AI Structure Context Worker 停止")

    async def _loop(self):
        while self._running:
            try:
                ran = await self.tick()
                if not ran:
                    await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("AI Structure Context Worker 异常: %s", exc)
                await asyncio.sleep(self.interval_seconds)

    async def tick(self) -> bool:
        job = claim_next_context_job(worker_id=self.worker_id)
        if not job:
            return False
        await run_context_job(job)
        return True


ai_structure_context_worker = AIStructureContextWorker(
    interval_seconds=getattr(config, "STRUCTURE_WORKER_INTERVAL", 2.0)
)
