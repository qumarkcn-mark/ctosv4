"""AI Native V5 CZSC snapshot worker."""

from __future__ import annotations

import asyncio
import logging
import socket
import uuid
from typing import Optional

from server import config
from server.engines.ai_native.czsc_snapshot_service import claim_next_snapshot_job, run_snapshot_job


logger = logging.getLogger(__name__)


class AIStructureSnapshotWorker:
    def __init__(self, *, interval_seconds: float = 2.0):
        self.interval_seconds = interval_seconds
        self.worker_id = f"{socket.gethostname()}-v5snap-{uuid.uuid4().hex[:8]}"
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        # V5 Phase 1 uses the existing structure worker switch to avoid adding
        # another config flag before the contract is proven.
        if not getattr(config, "STRUCTURE_WORKER_ENABLED", False):
            logger.info("AI Structure Snapshot Worker 未启用，跳过启动")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("AI Structure Snapshot Worker 启动 worker_id=%s", self.worker_id)

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            logger.info("AI Structure Snapshot Worker 停止")

    async def _loop(self):
        while self._running:
            try:
                ran = await self.tick()
                if not ran:
                    await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("AI Structure Snapshot Worker 异常: %s", exc)
                await asyncio.sleep(self.interval_seconds)

    async def tick(self) -> bool:
        job = claim_next_snapshot_job(worker_id=self.worker_id)
        if not job:
            return False
        await run_snapshot_job(job)
        return True


ai_structure_snapshot_worker = AIStructureSnapshotWorker(
    interval_seconds=getattr(config, "STRUCTURE_WORKER_INTERVAL", 2.0)
)
