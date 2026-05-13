import asyncio
import json
import logging
from typing import Optional

from fastapi.concurrency import run_in_threadpool

from server.db.database import get_connection
from server.engines.coach.event_log import log_alert_candidate
from server.engines.decision.push_rules import (
    build_alert_message,
    build_alert_strategy_contract as _build_alert_strategy_contract,
    evaluate_price_alerts,
)
from server.services.price_service import get_batch_prices
from server.services.push_service import send_stop_loss_alert

logger = logging.getLogger(__name__)


class PriceMonitor:
    def __init__(self, interval_seconds: int = 30):
        self.interval_seconds = interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._monitor_loop())
            logger.info("价格监控 Worker 启动，轮询间隔 %ds", self.interval_seconds)

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            logger.info("价格监控 Worker 停止")

    async def _monitor_loop(self):
        self._loop_count = 0
        self._multiverse_ran_today = None  # 记录今天是否已运行
        while self._running:
            try:
                await self._check_stop_losses()

                self._loop_count += 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("价格监控发生异常: %s", e)
            
            await asyncio.sleep(self.interval_seconds)

    def _db_get_positions(self):
        conn = get_connection()
        try:
            # 仅监控持仓数量 > 0 且设置了止损价的仓位
            # Phase 1/2 为了简化只做多头 (BUY) 的逻辑：现价 <= 止损价 时止损
            rows = conn.execute(
                """SELECT user_id, symbol, name, quantity, avg_cost, stop_loss_price,
                          trailing_stop_price, m5_entry_zg, entry_date, strategy_type,
                          current_price
                   FROM positions
                   WHERE quantity > 0
                     AND (stop_loss_price IS NOT NULL OR trailing_stop_price IS NOT NULL OR m5_entry_zg IS NOT NULL)"""
            ).fetchall()
            return [dict(r) for r in rows] if rows else []
        finally:
            conn.close()

    def _db_update_and_alert(self, positions, prices):
        conn = get_connection()
        alerts_to_send = []
        try:
            for pos in positions:
                sym = pos["symbol"]
                if sym not in prices:
                    continue
                    
                current_price = prices[sym]["price"]
                # 更新 positions 表的 current_price (顺便维护数据新鲜度)
                conn.execute(
                    "UPDATE positions SET current_price = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND symbol = ?",
                    (current_price, pos["user_id"], sym)
                )

                for candidate in evaluate_price_alerts(pos, current_price):
                    msg = self._trigger_alert_db(
                        conn,
                        pos,
                        candidate.trigger_price,
                        candidate.alert_type,
                        extra=candidate.extra,
                    )
                    if msg:
                        alerts_to_send.append((pos["user_id"], msg))
                    
            conn.commit()
            return alerts_to_send
        finally:
            conn.close()

    async def _check_stop_losses(self):
        """核心监控逻辑：扫描设置了止损的持仓，对比现价，触发提醒"""
        positions = await run_in_threadpool(self._db_get_positions)
        if not positions:
            return

        symbols = list(set(p["symbol"] for p in positions))
        
        # 批量获取最新价格
        prices = await get_batch_prices(symbols)
        if not prices:
            return
            
        alerts_to_send = await run_in_threadpool(self._db_update_and_alert, positions, prices)
        
        for user_id, msg in alerts_to_send:
            # 安全地在主事件循环内创建后台发送任务
            asyncio.create_task(send_stop_loss_alert(user_id, msg))

    def _trigger_alert_db(
        self, conn, pos, current_price: float,
        alert_type: str, extra: Optional[dict] = None
    ) -> Optional[str]:
        """记录提醒入库，如果今天已经发过则返回 None

        extra: 可选附加信息（如 stage_label、trailing_stop、_strategy_type、_m30_beichi_type）
        """
        extra = extra or {}
        # extra 字段也可直接从 pos dict 读取（调用方可将附加字段塞入 pos）
        strategy_type   = pos.get("_strategy_type", "") or extra.get("strategy_type", "")
        beichi_type     = pos.get("_m30_beichi_type", "") or extra.get("beichi_type", "")
        stage_label     = extra.get("stage_label", "")
        trailing_stop   = extra.get("trailing_stop", 0)
        m5_entry_zg     = extra.get("m5_entry_zg", pos.get("m5_entry_zg", 0) or 0)
        strategy_contract = _build_alert_strategy_contract(alert_type, strategy_type)

        # 1. 检查今日是否已针对此股票发送过同类提醒 (简单防重)
        row = conn.execute(
            """SELECT id FROM alerts
               WHERE user_id = ? AND symbol = ? AND alert_type = ?
               AND date(created_at) = date('now')""",
            (pos["user_id"], pos["symbol"], alert_type)
        ).fetchone()

        if row:
            return None  # 今天已经发过了

        name = pos.get("name", pos.get("symbol", ""))
        msg = build_alert_message(
            alert_type,
            name=name,
            current_price=current_price,
            stop_loss_price=extra.get("effective_stop", pos.get("stop_loss_price", 0) or 0),
            strategy_type=strategy_type,
            beichi_type=beichi_type,
            trailing_stop=trailing_stop,
            m5_entry_zg=m5_entry_zg,
        )
        if alert_type in ("STOP_LOSS_BROKEN", "TRAILING_STOP_BROKEN", "M5_STRUCTURE_BROKEN", "HOLDING_STAGE4", "HOLDING_STAGE5"):
            logger.warning("[预警触发] %s", msg)
        else:
            logger.info("[预警触发] %s", msg)

        # 写入 alerts 表，软处理 CHECK 约束（存量库可能未含新类型）
        try:
            alert_columns = {row["name"] for row in conn.execute("PRAGMA table_info(alerts)").fetchall()}
            if strategy_contract and {
                "strategy_id",
                "strategy_version",
                "strategy_contract",
            }.issubset(alert_columns):
                cur = conn.execute(
                    """INSERT INTO alerts (
                           user_id, symbol, alert_type, trigger_price, is_triggered,
                           message, strategy_id, strategy_version, strategy_contract
                       )
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        pos["user_id"],
                        pos["symbol"],
                        alert_type,
                        current_price,
                        1,
                        msg,
                        strategy_contract["strategy_id"],
                        strategy_contract["strategy_version"],
                        json.dumps(strategy_contract, ensure_ascii=False),
                    ),
                )
            else:
                cur = conn.execute(
                    """INSERT INTO alerts (user_id, symbol, alert_type, trigger_price, is_triggered, message)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (pos["user_id"], pos["symbol"], alert_type, current_price, 1, msg),
                )
            log_alert_candidate(
                conn,
                alert_id=cur.lastrowid,
                user_id=pos["user_id"],
                symbol=pos["symbol"],
                alert_type=alert_type,
                message_text=msg,
                strategy_contract=strategy_contract,
                evidence={
                    "trigger_price": current_price,
                    "strategy_type": strategy_type,
                    "beichi_type": beichi_type,
                    "stage_label": stage_label,
                    "trailing_stop": trailing_stop,
                },
            )
        except Exception as insert_err:
            logger.warning(
                "[alerts INSERT 失败] alert_type=%s symbol=%s error=%s",
                alert_type, pos.get("symbol"), insert_err
            )
            return None
        return msg

# 单例实例
monitor = PriceMonitor()


def _parse_hhmm(value: str):
    from datetime import time

    try:
        hour, minute = str(value).split(":", 1)
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        return None
