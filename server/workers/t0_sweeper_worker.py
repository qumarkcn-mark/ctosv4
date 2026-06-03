"""T+0 14:55 强制平仓扫尾 Worker。

每 60s 检查当前时间，在 14:55~15:00 窗口内强制平仓所有非 IDLE/LOCKDOWN 头寸。

环境变量门控: T0_ENGINE_ENABLED=true（与引擎共用）
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from fastapi.concurrency import run_in_threadpool

from server import config

logger = logging.getLogger(__name__)

# 强制平仓时间窗口
SWEEP_START = "14:55"
SWEEP_END = "15:00"


class T0SweeperWorker:
    """14:55 强制平仓扫尾器。

    每 60s 检查：
    - 若 14:55:00 ≤ now ≤ 15:00:00 且有非 IDLE/LOCKDOWN 的做T头寸
    - 获取当前价，调用 force_sweep()
    - 写入纸盘记录，更新 t0_state_cache
    """

    def __init__(self, *, check_interval: float = 60.0):
        self.check_interval = check_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._swept_today: set[str] = set()  # 防止重复强平

    def start(self):
        if not getattr(config, "T0_ENGINE_ENABLED", False):
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("T0 Sweeper Worker 启动，检查间隔 %.0fs", self.check_interval)

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
                logger.exception("T0 Sweeper 异常: %s", exc)
            await asyncio.sleep(self.check_interval)

    async def tick(self) -> None:
        now_str = datetime.now().strftime("%H:%M")
        today = datetime.now().strftime("%Y-%m-%d")

        # 每天重置已强平集合
        if not self._swept_today or not list(self._swept_today)[0].startswith(today):
            self._swept_today = set()

        # 只在 14:55~15:00 执行
        if not (SWEEP_START <= now_str <= SWEEP_END):
            return

        await run_in_threadpool(self._sweep_all, today)

    def _sweep_all(self, today: str) -> None:
        """强制平仓所有非 IDLE/LOCKDOWN 头寸。"""
        from server.db.database import get_connection
        from server.engines.t0.t0_state_machine import T0State, T0StateMachine
        from server.engines.t0.t0_paper_service import record_t0_signal, get_or_create_t0_account
        from server.workers.t0_engine_worker import _safe_get_price, _save_state_cache

        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT user_id, symbol, t0_qty, state, state_json
                  FROM t0_state_cache
                 WHERE state IN ('POSITION_LONG', 'POSITION_SHORT')
                """,
            ).fetchall()
        finally:
            conn.close()

        for row in rows:
            user_id, symbol, t0_qty, state_str, state_json_str = row
            sweep_key = f"{today}:{user_id}:{symbol}"
            if sweep_key in self._swept_today:
                continue

            # 获取当前价
            price_data = _safe_get_price(symbol)
            if not price_data:
                continue
            current_price = float(price_data.get("price") or price_data.get("current", 0))
            if current_price <= 0:
                continue

            # 恢复状态机
            try:
                import json
                data = json.loads(state_json_str or "{}")
                if not data:
                    data = {"symbol": symbol, "t0_qty": t0_qty, "state": state_str}
                machine = T0StateMachine.from_dict(data)
            except Exception:
                machine = T0StateMachine(symbol=symbol, t0_qty=t0_qty)
                try:
                    machine._state = T0State(state_str)
                except Exception:
                    logger.warning("[T0 Sweeper] 状态恢复失败 %s state=%s", symbol, state_str)
                    continue

            result = machine.force_sweep(current_price)

            if result.signal and result.signal.startswith("SWEEP"):
                try:
                    get_or_create_t0_account(user_id)
                    record_t0_signal(
                        user_id=user_id,
                        symbol=symbol,
                        signal=result.signal,
                        signal_price=current_price,
                        t0_qty=result.signal_qty or t0_qty,
                        tick_result=result,
                    )
                except Exception as exc:
                    logger.warning("[T0 Sweeper] paper记录失败 %s: %s", symbol, exc)

                _save_state_cache(user_id, symbol, result, t0_qty, machine.serialize())
                self._swept_today.add(sweep_key)
                logger.info("[T0 Sweeper] 强平 %s price=%.2f", symbol, current_price)
