import asyncio
import logging
from typing import Optional

from server.db.database import get_connection
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
        while self._running:
            try:
                await self._check_stop_losses()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("价格监控发生异常: %s", e)
            
            await asyncio.sleep(self.interval_seconds)

    async def _check_stop_losses(self):
        """核心监控逻辑：扫描设置了止损的持仓，对比现价，触发提醒"""
        conn = get_connection()
        try:
            # 仅监控持仓数量 > 0 且设置了止损价的仓位
            # Phase 1/2 为了简化只做多头 (BUY) 的逻辑：现价 <= 止损价 时止损
            rows = conn.execute(
                "SELECT user_id, symbol, name, quantity, stop_loss_price, current_price FROM positions WHERE quantity > 0 AND stop_loss_price IS NOT NULL"
            ).fetchall()
            
            if not rows:
                return

            positions = [dict(r) for r in rows]
            symbols = list(set(p["symbol"] for p in positions))
            
            # 批量获取最新价格
            prices = await get_batch_prices(symbols)
            if not prices:
                return
                
            for pos in positions:
                sym = pos["symbol"]
                if sym not in prices:
                    continue
                    
                current_price = prices[sym]["price"]
                stop_loss = pos["stop_loss_price"]
                
                # 更新 positions 表的 current_price (顺便维护数据新鲜度)
                conn.execute(
                    "UPDATE positions SET current_price = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND symbol = ?",
                    (current_price, pos["user_id"], sym)
                )

                # 检查止损击穿
                if current_price > 0 and current_price <= stop_loss:
                    # 检查是否已经触发过紧急预警，避免频繁发送 (可选: 依赖 alerts 表状态)
                    # 这里记录到 alerts 数据库表，并走 push_service 推送
                    self._trigger_alert(conn, pos, current_price, "STOP_LOSS_BROKEN")
                elif current_price > stop_loss and current_price <= stop_loss * 1.03:
                    # 接近止损 (距离止损位不到 3%)
                    self._trigger_alert(conn, pos, current_price, "STOP_LOSS_WARNING")
                    
            conn.commit()
            
        finally:
            conn.close()

    def _trigger_alert(self, conn, pos, current_price: float, alert_type: str):
        """记录提醒并尝试推送"""
        # 1. 检查今日是否已针对此股票发送过同类提醒 (简单防重)
        row = conn.execute(
            """SELECT id FROM alerts 
               WHERE user_id = ? AND symbol = ? AND alert_type = ? 
               AND date(created_at) = date('now')""",
            (pos["user_id"], pos["symbol"], alert_type)
        ).fetchone()
        
        if row:
            return # 今天已经发过了
            
        msg = ""
        if alert_type == "STOP_LOSS_BROKEN":
            msg = f"【止损击穿】{pos['name']} 现价 {current_price} 已跌破止损价 {pos['stop_loss_price']}！"
            logger.warning(f"[预警触发] {msg}")
        elif alert_type == "STOP_LOSS_WARNING":
            msg = f"【接近止损】{pos['name']} 现价 {current_price} 逼近止损价 {pos['stop_loss_price']}，请持续关注。"
            logger.info(f"[预警触发] {msg}")

        # 写入 alerts 表
        conn.execute(
            """INSERT INTO alerts (user_id, symbol, alert_type, trigger_price, is_triggered, message)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (pos["user_id"], pos["symbol"], alert_type, current_price, 1, msg)
        )
        
        # 触发推送 (Phase 2 的 push_service)
        asyncio.create_task(send_stop_loss_alert(pos["user_id"], msg))

# 单例实例
monitor = PriceMonitor()
