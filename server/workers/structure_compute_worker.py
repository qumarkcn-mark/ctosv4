"""Background worker for shared Chan structure snapshots."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import uuid
from typing import Optional

from server import config
from server.engines.structure.chan_snapshot_cache import load_chan_snapshot_by_key_hash, save_chan_snapshot
from server.engines.structure.snapshot_query import build_formal_structure_key
from server.engines.structure.structure_jobs import (
    cancel_structure_job,
    claim_next_structure_job,
    complete_structure_job,
    enqueue_structure_job,
    fail_structure_job,
    skip_structure_job,
)
from server.engines.structure.structure_key import ADAPTER_VERSION, ENGINE_VERSION, FORMAL_SOURCE, StructureKey
from server.services.chan_detail_service import get_chan_detail


logger = logging.getLogger(__name__)


class StructureComputeWorker:
    def __init__(self, *, interval_seconds: float = 2.0):
        self.interval_seconds = interval_seconds
        self.worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if not config.STRUCTURE_WORKER_ENABLED:
            logger.info("Structure Compute Worker 未启用，跳过启动")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Structure Compute Worker 启动 worker_id=%s", self.worker_id)

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            logger.info("Structure Compute Worker 停止")

    async def _loop(self):
        while self._running:
            try:
                ran = await self.tick()
                if not ran:
                    await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Structure Compute Worker 异常: %s", exc)
                await asyncio.sleep(self.interval_seconds)

    async def tick(self) -> bool:
        job = claim_next_structure_job(worker_id=self.worker_id)
        if not job:
            return False
        await self._run_job(job)
        return True

    async def _run_job(self, job: dict):
        job_id = job["job_id"]
        try:
            original_key = _structure_key_from_job(job)
            current_key, context = build_formal_structure_key(
                symbol=original_key.symbol,
                freq=original_key.freq,
                cchan_preset=original_key.cchan_preset,
                compute_profile=original_key.compute_profile,
            )
            if current_key is None:
                fail_structure_job(job_id, code="NO_DATA", message="No formal BaoStock bars", retryable=False)
                return
            if current_key.hash != original_key.hash:
                cancel_structure_job(job_id, code="JOB_SUPERSEDED", message="data_signature changed before compute")
                enqueue_structure_job(current_key, priority=int(job.get("priority") or 50), reason="superseded_refresh")
                return

            fresh = load_chan_snapshot_by_key_hash(current_key.hash)
            if fresh is not None:
                skip_structure_job(job_id, reason="fresh snapshot already exists")
                return

            compute_bars = int(context["compute_bars"])
            result = await get_chan_detail(
                current_key.symbol,
                freq=current_key.freq,
                count=compute_bars,
                cchan_preset=current_key.cchan_preset,
                kline_source=current_key.source,
                adjustflag=current_key.adjustflag,
                max_compute_bars=compute_bars,
            )
            if result.get("error"):
                fail_structure_job(job_id, code="ENGINE_ERROR", message=str(result.get("error")), retryable=True)
                return
            provider = (result.get("data_source") or {}).get("provider")
            if provider and provider != FORMAL_SOURCE:
                fail_structure_job(job_id, code="DATA_STALE", message=f"non-formal provider: {provider}", retryable=False)
                return

            fingerprint = save_chan_snapshot(
                symbol=current_key.symbol,
                freq=current_key.freq,
                cchan_preset=current_key.cchan_preset,
                kline_source=current_key.source,
                adjustflag=current_key.adjustflag,
                end_date="",
                max_compute_bars=compute_bars,
                data_signature=current_key.data_signature,
                last_kline_time=context["freshness"].get("last_bar_at") or "",
                kline_count=int(context["freshness"].get("kline_count") or 0),
                compute_bars=int(result.get("compute_bars") or compute_bars),
                result=result,
                structure_key_hash=current_key.hash,
                compute_profile=current_key.compute_profile,
                engine_version=ENGINE_VERSION,
                adapter_version=ADAPTER_VERSION,
            )
            if not fingerprint:
                fail_structure_job(job_id, code="SNAPSHOT_SAVE_FAILED", message="snapshot save returned empty fingerprint", retryable=True)
                return
            complete_structure_job(job_id, structure_fingerprint=fingerprint)
        except Exception as exc:
            logger.exception("结构任务失败 job_id=%s", job_id)
            fail_structure_job(job_id, code="ENGINE_ERROR", message=str(exc), retryable=True)


def _structure_key_from_job(job: dict) -> StructureKey:
    payload = json.loads(job["structure_key"])
    return StructureKey(
        symbol=payload["symbol"],
        freq=payload["freq"],
        source=payload["source"],
        source_role=payload["source_role"],
        adjustflag=payload["adjustflag"],
        cchan_preset=payload["cchan_preset"],
        compute_profile=payload["compute_profile"],
        engine=payload["engine"],
        engine_version=payload["engine_version"],
        adapter_version=payload["adapter_version"],
        schema_version=payload["schema_version"],
        data_signature=payload["data_signature"],
    )


structure_compute_worker = StructureComputeWorker(interval_seconds=config.STRUCTURE_WORKER_INTERVAL)
