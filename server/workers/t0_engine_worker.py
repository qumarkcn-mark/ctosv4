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
        from server.engines.t0.ppe_t0_policy import derive_t0_policy_from_ppe
        from server.engines.ai_native.position_path_state_service import derive_position_path_state
        from server.engines.ai_native.unified_reasoning_service import get_latest_unified_reasoning
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

        # T+1 可用股数与日内风险预算。倒T只允许动用可卖底仓，不能把当天新买入也当成可卖。
        available_t0_qty = _get_available_t0_qty(user_id, symbol, t0_qty)
        machine.available_t0_qty = available_t0_qty
        machine.max_t0_qty = t0_qty
        machine.daily_loss_limit = _get_daily_loss_limit(t0_qty, current_price)

        # 获取最近 1M K线
        klines_1m = []
        try:
            klines_1m = query_klines(symbol, freq="1", adjustflag="3", source="qmt", limit=20)
        except Exception:
            pass

        klines_5m = []
        try:
            klines_5m = query_klines(symbol, freq="5", adjustflag="3", source="qmt", limit=20)
        except Exception:
            pass

        # 获取 5M 中枢 ZG/ZD + snapshot_json（含 bis 笔数据）
        pivot_zd, pivot_zg, snapshot_json = _get_latest_pivot_with_snapshot(symbol)
        pivot_id = _make_pivot_id(symbol, pivot_zd, pivot_zg, snapshot_json)

        # 获取 5M ATR
        atr_5m = _get_atr_5m(symbol)

        # 计算笔振幅衰减比（第三层过滤器）
        bi_strength_ratio = None
        if snapshot_json:
            try:
                from server.engines.t0.t0_fractal import calculate_bi_strength_ratio
                bi_strength_ratio = calculate_bi_strength_ratio(snapshot_json.get("bis", []), direction="down")
            except Exception:
                pass

        base_trend_gear = ""
        if snapshot_json:
            base_trend_gear = str(snapshot_json.get("base_trend_gear") or "").strip()

        latest_reasoning = get_latest_unified_reasoning(user_id=user_id, symbol=symbol) or {}
        reasoning_summary = latest_reasoning.get("summary") if isinstance(latest_reasoning.get("summary"), dict) else {}
        position_path = derive_position_path_state(
            summary=reasoning_summary,
            current_price=current_price,
            position=_get_position_context(user_id, symbol),
        )
        ppe_policy = derive_t0_policy_from_ppe(
            summary=reasoning_summary,
            position_path=position_path,
            source_run_id=str(latest_reasoning.get("run_id") or ""),
        )

        # 执行 tick
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        result = machine.tick(
            current_price=current_price,
            timestamp=timestamp,
            pivot_zd=pivot_zd,
            pivot_zg=pivot_zg,
            klines_1m=klines_1m,
            klines_5m=klines_5m,
            atr_5m=atr_5m,
            bi_strength_ratio=bi_strength_ratio,
            pressure_nearby=True,
            base_trend_gear=base_trend_gear,
            volume_surge_ratio=_calc_volume_surge_ratio(klines_1m),
            pivot_id=pivot_id,
            allowed_t0_direction=ppe_policy.allowed_t0_direction,
            size_multiplier=ppe_policy.size_multiplier,
            ppe_stage=ppe_policy.ppe_stage,
            policy_reason=ppe_policy.policy_reason,
            policy_source_run_id=ppe_policy.policy_source_run_id,
        )

        # 有真实成交信号时写入纸盘；REDUCE_LOCK 是减仓锁利状态事件，不是新成交。
        fill_signals = {"BUY_LONG", "SELL_LONG", "SELL_SHORT", "BUY_SHORT", "STOP_LONG", "SWEEP_LONG"}
        if result.signal in fill_signals:
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


def _get_latest_pivot_with_snapshot(symbol: str) -> tuple[Optional[float], Optional[float], Optional[dict]]:
    """获取最新 5M 中枢 ZD/ZG，同时返回完整 snapshot_json 供笔动力学计算。

    使用 canonical_structure_service.get_latest_structure 获取符合当前 K 线数据签名的最新标准 CZSC 结构快照。
    """
    from server.engines.structure.canonical_structure_service import get_latest_structure
    try:
        row = get_latest_structure(symbol=symbol, level="5", min_profile="chart_standard_v1", allow_bootstrap=True)
        if row and "snapshot" in row:
            snapshot = row["snapshot"]
            zd = snapshot.get("zd")
            zg = snapshot.get("zg")
            if zd is not None and zg is not None:
                return float(zd), float(zg), snapshot
    except Exception as exc:
        logger.warning("[T0 Worker] 获取 canonical 结构异常 %s: %s", symbol, exc)
    return None, None, None


# 向后兼容别名（sweeper_worker 使用）
def _get_latest_pivot(symbol: str) -> tuple[Optional[float], Optional[float]]:
    """获取最新 5M 中枢 ZD/ZG（兼容旧调用）。"""
    zd, zg, _ = _get_latest_pivot_with_snapshot(symbol)
    return zd, zg


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


def _make_pivot_id(symbol: str, pivot_zd: Optional[float], pivot_zg: Optional[float], snapshot: Optional[dict]) -> str:
    """生成 5M 中枢窗口 ID，用于一窗一做。"""
    if pivot_zd is None or pivot_zg is None:
        return ""
    signature = ""
    if isinstance(snapshot, dict):
        signature = str(
            snapshot.get("data_signature")
            or snapshot.get("structure_signature")
            or snapshot.get("updated_at")
            or snapshot.get("dt")
            or ""
        )
    return f"5m:{symbol}:{float(pivot_zd):.4f}:{float(pivot_zg):.4f}:{signature}"


def _get_position_context(user_id: int, symbol: str) -> dict:
    """给 PPE 策略投影提供最小持仓上下文。"""
    conn = get_connection()
    try:
        aliases = _symbol_aliases(symbol)
        placeholders = ",".join("?" for _ in aliases)
        row = conn.execute(
            f"""
            SELECT quantity, avg_cost
              FROM positions
             WHERE user_id=? AND symbol IN ({placeholders})
             ORDER BY updated_at DESC LIMIT 1
            """,
            [user_id, *aliases],
        ).fetchone()
        if not row:
            return {}
        return {"shares": int(row[0] or 0), "cost": float(row[1] or 0)}
    except Exception:
        return {}
    finally:
        conn.close()


def _get_available_t0_qty(user_id: int, symbol: str, configured_qty: int) -> int:
    """计算倒T可用底仓数量。

    V1 语义：positions 当前底仓 - 今日买入数量，再与用户配置额度取小。
    这里不做任何真实交易，只给状态机一个保守的可卖数量上限。
    """
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    try:
        aliases = _symbol_aliases(symbol)
        placeholders = ",".join("?" for _ in aliases)
        pos = conn.execute(
            f"""
            SELECT quantity FROM positions
             WHERE user_id=? AND symbol IN ({placeholders}) AND quantity > 0
             ORDER BY updated_at DESC LIMIT 1
            """,
            [user_id, *aliases],
        ).fetchone()
        held_qty = int(pos[0]) if pos else 0
        bought_today = conn.execute(
            f"""
            SELECT COALESCE(SUM(quantity), 0) FROM trades
             WHERE user_id=? AND symbol IN ({placeholders})
               AND direction='BUY'
               AND substr(traded_at, 1, 10)=?
            """,
            [user_id, *aliases, today],
        ).fetchone()[0]
        available = max(0, held_qty - int(bought_today or 0))
        return _floor_lot(min(int(configured_qty or 0), available))
    except Exception:
        logger.warning("[T0 Worker] 计算可用T0数量失败 %s", symbol)
        return _floor_lot(configured_qty)
    finally:
        conn.close()


def _get_daily_loss_limit(t0_qty: int, current_price: float) -> float:
    """日内亏损预算。

    可通过 T0_DAILY_LOSS_LIMIT 覆盖；默认按做T名义金额的 1.5%，且不低于 100 元。
    """
    import os

    raw = os.getenv("T0_DAILY_LOSS_LIMIT", "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return round(max(100.0, float(t0_qty or 0) * float(current_price or 0) * 0.015), 2)


def _calc_volume_surge_ratio(klines_1m: list[dict]) -> Optional[float]:
    """最近一分钟量能相对前5根均量的倍率。"""
    if not klines_1m or len(klines_1m) < 6:
        return None
    try:
        last_volume = float(klines_1m[-1].get("volume") or 0)
        prev = [float(b.get("volume") or 0) for b in klines_1m[-6:-1]]
        avg = sum(prev) / len(prev) if prev else 0.0
        if avg <= 0:
            return None
        return round(last_volume / avg, 4)
    except Exception:
        return None


def _floor_lot(qty: int) -> int:
    """A股整手向下取整。"""
    try:
        return max(0, (int(qty or 0) // 100) * 100)
    except Exception:
        return 0


def _symbol_aliases(symbol: str) -> list[str]:
    """兼容 sh.600000 / SH600000 / 600000.SH 等常见代码写法。"""
    value = str(symbol or "").strip()
    if not value:
        return [value]
    aliases = {value, value.lower(), value.upper()}
    if "." in value:
        left, right = value.split(".", 1)
        if left.lower() in {"sh", "sz"}:
            code = right
            market = left.lower()
            aliases.update({code, f"{market}.{code}", f"{market.upper()}{code}", f"{code}.{market.upper()}"})
        elif right.lower() in {"sh", "sz"}:
            code = left
            market = right.lower()
            aliases.update({code, f"{market}.{code}", f"{market.upper()}{code}", f"{code}.{market.upper()}"})
    elif len(value) == 6 and value.isdigit():
        market = "sh" if value.startswith("6") else "sz"
        aliases.update({f"{market}.{value}", f"{market.upper()}{value}", f"{value}.{market.upper()}"})
    return sorted(aliases)


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
