"""T+0 做T引擎主循环 Worker。

每 30s（正常）或 10s（临界区）tick 所有启用做T的标的。
遵循 intraday_quote_sampler_worker.py 的类结构。

环境变量门控: T0_ENGINE_ENABLED=true（默认 false）
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi.concurrency import run_in_threadpool

from server import config
from server.db.database import get_connection

logger = logging.getLogger(__name__)


class T0EngineWorker:
    """做T引擎主循环 Worker。

    职责：
    1. 每 30s（正常）或 10s（临界区）tick 所有启用做T的标的
    2. 维护每只股票的 T0StateMachine 实例（内存 dict）
    3. 若产生信号 → 调用 t0_paper_service.record_t0_signal()
    4. 每 tick 结束将状态写入 t0_state_cache 表（供 API 读取）
    5. 若任一标的进入触发区（距 ZD/ZG ≤1%）→ 下一轮用 alert_interval
    """

    def __init__(
        self,
        *,
        normal_interval: float = 30.0,
        alert_interval: float = 10.0,
    ):
        self.normal_interval = normal_interval
        self.alert_interval = alert_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._machines: dict[str, object] = {}  # symbol → T0StateMachine
        self._next_interval = normal_interval

    def start(self):
        if not getattr(config, "T0_ENGINE_ENABLED", False):
            logger.info("T0 Engine Worker 未启用（T0_ENGINE_ENABLED=false），跳过启动")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("T0 Engine Worker 启动，正常间隔 %.0fs 临界区间隔 %.0fs",
                    self.normal_interval, self.alert_interval)

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            logger.info("T0 Engine Worker 停止")

    async def _loop(self):
        while self._running:
            try:
                in_alert = await self.tick()
                self._next_interval = self.alert_interval if in_alert else self.normal_interval
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("T0 Engine Worker 异常: %s", exc)
                self._next_interval = self.normal_interval
            await asyncio.sleep(self._next_interval)

    async def tick(self) -> bool:
        """执行一次 tick，返回是否有标的处于临界区。"""
        return await run_in_threadpool(self._tick_sync)

    def _tick_sync(self) -> bool:
        """同步 tick 实现（在线程池中执行）。"""
        conn = get_connection()
        try:
            # 1. 加载所有启用做T的标的
            rows = conn.execute(
                """
                SELECT wi.symbol, wi.t0_qty, wg.user_id
                  FROM watchlist_items wi
                  JOIN watchlist_groups wg ON wg.id = wi.group_id
                 WHERE wi.t0_enabled = 1 AND wi.t0_qty > 0
                """,
            ).fetchall()
        finally:
            conn.close()

        if not rows:
            return False

        in_alert = False

        for row in rows:
            symbol, t0_qty, user_id = row[0], row[1], row[2]
            try:
                in_alert |= self._tick_symbol(
                    user_id=user_id,
                    symbol=symbol,
                    t0_qty=t0_qty,
                )
            except Exception as exc:
                logger.warning("[T0 Worker] tick 异常 %s: %s", symbol, exc)

        return in_alert

    def _tick_symbol(self, user_id: int, symbol: str, t0_qty: int) -> bool:
        """tick 单只标的，返回是否处于临界区。"""
        from server.engines.t0.t0_state_machine import T0StateMachine
        from server.engines.t0.t0_paper_service import record_t0_signal, get_or_create_t0_account
        from server.db.kline_lake import query_klines

        # 获取或恢复状态机
        machine_key = f"{user_id}:{symbol}"
        if machine_key not in self._machines:
            self._machines[machine_key] = self._load_or_create_machine(user_id, symbol, t0_qty)
        machine: T0StateMachine = self._machines[machine_key]

        # 更新 t0_qty（可能被 API 改变）
        machine.t0_qty = t0_qty

        # 获取当前价格
        price_data = _safe_get_price(symbol)
        if not price_data:
            return False
        current_price = float(price_data.get("price") or price_data.get("current", 0))
        if current_price <= 0:
            return False

        # 获取最近 1M K线
        klines_1m = []
        try:
            klines_1m = query_klines(symbol, freq="1", adjustflag="3", source="qmt", limit=20)
        except Exception:
            pass

        # 获取 5M 中枢 ZG/ZD
        pivot_zd, pivot_zg = _get_latest_pivot(symbol)

        # 获取 5M ATR
        atr_5m = _get_atr_5m(symbol)

        # 执行 tick
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result = machine.tick(
            current_price=current_price,
            timestamp=timestamp,
            pivot_zd=pivot_zd,
            pivot_zg=pivot_zg,
            klines_1m=klines_1m,
            atr_5m=atr_5m,
        )

        # 有信号时写入纸盘
        if result.signal:
            try:
                get_or_create_t0_account(user_id)
                record_t0_signal(
                    user_id=user_id,
                    symbol=symbol,
                    signal=result.signal,
                    signal_price=current_price,
                    t0_qty=t0_qty,
                    tick_result=result,
                )
            except Exception as exc:
                logger.warning("[T0 Worker] paper记录失败 %s: %s", symbol, exc)

        # 写入状态缓存
        _save_state_cache(user_id, symbol, result, t0_qty, machine.serialize())

        # 判断是否处于临界区（距 ZD/ZG ≤1%）
        if pivot_zd and pivot_zg:
            dist_zd = abs(current_price - pivot_zd) / pivot_zd
            dist_zg = abs(current_price - pivot_zg) / pivot_zg
            if dist_zd <= 0.01 or dist_zg <= 0.01:
                return True

        return False

    def _load_or_create_machine(self, user_id: int, symbol: str, t0_qty: int):
        """从 t0_state_cache 恢复状态机，或创建新实例。"""
        from server.engines.t0.t0_state_machine import T0StateMachine
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT state_json FROM t0_state_cache WHERE user_id=? AND symbol=?",
                (user_id, symbol),
            ).fetchone()
            if row and row[0]:
                try:
                    data = json.loads(row[0])
                    if data:
                        return T0StateMachine.from_dict(data)
                except Exception:
                    pass
        finally:
            conn.close()
        return T0StateMachine(symbol=symbol, t0_qty=t0_qty)


# ------------------------------------------------------------------ #
# 模块级辅助函数
# ------------------------------------------------------------------ #

def _safe_get_price(symbol: str) -> Optional[dict]:
    """安全获取当前价格（同步版本）。"""
    try:
        # price_service 使用 async，这里通过 TDX bridge 同步获取
        from server.services.tdx_bridge_client import fetch_tdx_quotes_sync
        prices = fetch_tdx_quotes_sync([symbol])
        return prices.get(symbol)
    except Exception:
        # 降级：从 kline_lake 最后一根 K 线取收盘价
        try:
            from server.db.kline_lake import query_klines
            bars = query_klines(symbol, freq="1", adjustflag="3", source="qmt", limit=1)
            if not bars:
                bars = query_klines(symbol, freq="1", limit=1)
            if bars:
                return {"price": bars[-1]["close"]}
        except Exception:
            pass
        return None


def _get_latest_pivot(symbol: str) -> tuple[Optional[float], Optional[float]]:
    """获取最新 5M 中枢 ZD/ZG。"""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT json_extract(snapshot_json, '$.zd'),
                   json_extract(snapshot_json, '$.zg')
              FROM structure_snapshots
             WHERE symbol = ? AND level = '5' AND status = 'fresh'
             ORDER BY updated_at DESC
             LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        if row and row[0] and row[1]:
            return float(row[0]), float(row[1])
        return None, None
    finally:
        conn.close()


def _get_atr_5m(symbol: str) -> float:
    """从 kline_lake 计算近期 5M ATR。"""
    try:
        from server.db.kline_lake import query_klines
        bars = query_klines(symbol, freq="5", limit=30)
        if len(bars) < 2:
            return 0.0
        trs = []
        for i in range(1, len(bars)):
            prev_c = bars[i - 1]["close"]
            b = bars[i]
            tr = max(b["high"] - b["low"], abs(b["high"] - prev_c), abs(b["low"] - prev_c))
            trs.append(tr)
        period = 14
        if len(trs) < period:
            return sum(trs) / len(trs) if trs else 0.0
        atr = sum(trs[:period]) / period
        for tr in trs[period:]:
            atr = (atr * (period - 1) + tr) / period
        return round(atr, 4)
    except Exception:
        return 0.0


def _save_state_cache(user_id: int, symbol: str, result, t0_qty: int, state_data: Optional[dict] = None) -> None:
    """将 tick 结果写入 t0_state_cache 表。"""
    conn = get_connection()
    try:
        # 构建 state_json（用于下次重启恢复）
        state_json = json.dumps(state_data or {}, ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO t0_state_cache
                (user_id, symbol, state, pivot_zd, pivot_zg, entry_price, target_price,
                 stop_structural, stop_catastrophic, t0_qty, friction_per_share,
                 is_grid_viable, daily_pnl, daily_trades, daily_stop_count,
                 signal, signal_price, reason, state_json, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, symbol) DO UPDATE SET
                state=excluded.state,
                pivot_zd=excluded.pivot_zd,
                pivot_zg=excluded.pivot_zg,
                entry_price=excluded.entry_price,
                target_price=excluded.target_price,
                stop_structural=excluded.stop_structural,
                stop_catastrophic=excluded.stop_catastrophic,
                t0_qty=excluded.t0_qty,
                friction_per_share=excluded.friction_per_share,
                is_grid_viable=excluded.is_grid_viable,
                daily_pnl=excluded.daily_pnl,
                daily_trades=excluded.daily_trades,
                daily_stop_count=excluded.daily_stop_count,
                signal=excluded.signal,
                signal_price=excluded.signal_price,
                reason=excluded.reason,
                state_json=excluded.state_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                user_id, symbol, result.state,
                result.pivot_zd, result.pivot_zg,
                result.entry_price, result.target_price,
                result.stop_structural, result.stop_catastrophic,
                t0_qty, result.friction_per_share,
                1 if result.is_grid_viable else 0,
                result.daily_pnl, result.daily_trades, result.daily_stop_count,
                result.signal, result.signal_price, result.reason,
                state_json,
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("[T0 Worker] 写入 state_cache 失败 %s: %s", symbol, exc)
    finally:
        conn.close()
