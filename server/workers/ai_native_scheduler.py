"""AI Native scheduler worker.

Coach-only automation. It generates playbooks, refreshes rebalance contracts,
and writes reports. It never places trades.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Optional

from fastapi.concurrency import run_in_threadpool

from server import config
from server.engines.ai_native.market_time import CN_TZ, THIRTY_MINUTE_CLOSES
from server.engines.ai_native.trading_calendar import is_trading_day

logger = logging.getLogger(__name__)


PREMARKET_WINDOW = (time(9, 0), time(9, 20))
POSTMARKET_WINDOW = (time(15, 5), time(15, 30))
BAR_CLOSE_GRACE = timedelta(minutes=3)


class AINativeScheduler:
    def __init__(
        self,
        *,
        interval_seconds: int = 30,
        user_id: int = 1,
        enabled: bool = False,
        max_rebalance_items: int = 8,
    ):
        self.interval_seconds = interval_seconds
        self.user_id = user_id
        self.enabled = enabled
        self.max_rebalance_items = max_rebalance_items
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._ran_keys: set[str] = set()

    def start(self):
        if not self.enabled:
            logger.info("AI Native Scheduler 未启用，跳过启动")
            return
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("AI Native Scheduler 启动，轮询间隔 %ds", self.interval_seconds)

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            logger.info("AI Native Scheduler 停止")

    async def _loop(self):
        while self._running:
            try:
                await self.tick(datetime.now(CN_TZ))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("AI Native Scheduler 异常: %s", exc, exc_info=True)
            await asyncio.sleep(self.interval_seconds)

    async def tick(self, now: datetime) -> list[str]:
        """Run due jobs once for the supplied timestamp. Returns executed job keys."""
        local_now = _localize(now)
        if not is_trading_day(local_now):
            return []

        executed = []
        for job in self._due_jobs(local_now):
            if job.key in self._ran_keys:
                continue
            self._ran_keys.add(job.key)
            await job.run(self)
            executed.append(job.key)
        return executed

    def _due_jobs(self, now: datetime) -> list["_ScheduledJob"]:
        jobs: list[_ScheduledJob] = []
        day = now.strftime("%Y-%m-%d")
        current = now.time()
        if _in_window(current, PREMARKET_WINDOW):
            jobs.append(_ScheduledJob(f"{day}:premarket_playbook", _run_premarket_playbook))

        bar_slot = _current_30m_close_slot(now)
        if bar_slot:
            jobs.append(_ScheduledJob(f"{day}:rebalance:{bar_slot}", _run_rebalance_refresh))

        if _in_window(current, POSTMARKET_WINDOW):
            jobs.append(_ScheduledJob(f"{day}:postmarket_report", _run_postmarket_report))
        return jobs


class _ScheduledJob:
    def __init__(self, key, runner):
        self.key = key
        self._runner = runner

    async def run(self, scheduler: AINativeScheduler) -> None:
        await self._runner(scheduler)


async def _run_premarket_playbook(scheduler: AINativeScheduler) -> None:
    from server.api import playbook

    logger.info("[AI Native Scheduler] 盘前生成作战台 user_id=%s", scheduler.user_id)
    await playbook.generate_today_playbook(
        playbook.GeneratePlaybookRequest(
            user_id=scheduler.user_id,
            sources=["positions", "scanner", "watchlist"],
            max_items=8,
        )
    )


async def _run_rebalance_refresh(scheduler: AINativeScheduler) -> None:
    from server.api import agent, playbook

    logger.info("[AI Native Scheduler] 刷新 Rebalance user_id=%s", scheduler.user_id)
    response = await agent.ai_native_rebalance(
        agent.AINativeRebalanceRequest(
            user_id=scheduler.user_id,
            sources=["positions", "watchlist"],
            max_items=scheduler.max_rebalance_items,
            refresh_trigger="NEXT_30M_CLOSE",
        )
    )
    if response.get("status") != "success":
        logger.warning("[AI Native Scheduler] Rebalance 未成功: %s", response)
        return
    await run_in_threadpool(
        playbook.import_rebalance_to_playbook,
        playbook.ImportRebalanceRequest(user_id=scheduler.user_id, contract=response.get("data") or {}),
    )


async def _run_postmarket_report(scheduler: AINativeScheduler) -> None:
    from server.api import playbook

    logger.info("[AI Native Scheduler] 盘后生成作战报告 user_id=%s", scheduler.user_id)
    await run_in_threadpool(
        playbook.generate_today_report,
        playbook.PlaybookReportRequest(user_id=scheduler.user_id),
    )


def _current_30m_close_slot(now: datetime) -> Optional[str]:
    for close_time in THIRTY_MINUTE_CLOSES:
        close_at = datetime.combine(now.date(), close_time, tzinfo=CN_TZ)
        if close_at <= now < close_at + BAR_CLOSE_GRACE:
            return close_at.strftime("%H%M")
    return None


def _in_window(value: time, window: tuple[time, time]) -> bool:
    return window[0] <= value <= window[1]


def _localize(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=CN_TZ)
    return value.astimezone(CN_TZ)


ai_native_scheduler = AINativeScheduler(
    interval_seconds=config.AI_NATIVE_SCHEDULER_INTERVAL,
    user_id=config.AI_NATIVE_SCHEDULER_USER_ID,
    enabled=config.AI_NATIVE_SCHEDULER_ENABLED,
    max_rebalance_items=config.AI_NATIVE_REBALANCE_MAX_ITEMS,
)
