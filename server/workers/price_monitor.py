import asyncio
import logging
from typing import Optional

from fastapi.concurrency import run_in_threadpool

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
        self._loop_count = 0
        self._multiverse_ran_today = None  # 记录今天是否已运行
        while self._running:
            try:
                await self._check_stop_losses()
                
                # 每隔 20 个周期 (约 10 分钟) 跑一次低频的缠论长线日线推演 (减少API负担)
                if self._loop_count % 20 == 0:
                    await self._check_chan_buys()
                
                # 多元宇宙日志：每天 20:30 自动拍快照+结算
                await self._check_multiverse_auto_run()
                    
                self._loop_count += 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("价格监控发生异常: %s", e)
            
            await asyncio.sleep(self.interval_seconds)

    async def _check_multiverse_auto_run(self):
        """每天 20:30 后自动运行多元宇宙快照+结算（仅工作日）"""
        from datetime import datetime
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        
        # 避免重复运行
        if self._multiverse_ran_today == today_str:
            return
        
        # 只在 20:30~21:00 之间触发，且是工作日(周一到周五)
        if now.weekday() >= 5:  # 周末跳过
            return
        if not (now.hour == 20 and now.minute >= 30):
            return
        
        self._multiverse_ran_today = today_str
        logger.info("🌌 多元宇宙日志 — 开始自动快照+结算")
        try:
            from server.services.multiverse_service import auto_daily_run
            await auto_daily_run()
            logger.info("🌌 多元宇宙日志 — 自动运行完成")
        except Exception as e:
            logger.error("多元宇宙自动运行失败: %s", e)

    def _db_get_positions(self):
        conn = get_connection()
        try:
            # 仅监控持仓数量 > 0 且设置了止损价的仓位
            # Phase 1/2 为了简化只做多头 (BUY) 的逻辑：现价 <= 止损价 时止损
            rows = conn.execute(
                "SELECT user_id, symbol, name, quantity, stop_loss_price, current_price FROM positions WHERE quantity > 0 AND stop_loss_price IS NOT NULL"
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
                stop_loss = pos["stop_loss_price"]
                
                # 更新 positions 表的 current_price (顺便维护数据新鲜度)
                conn.execute(
                    "UPDATE positions SET current_price = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND symbol = ?",
                    (current_price, pos["user_id"], sym)
                )

                # 检查止损击穿
                if current_price > 0 and current_price <= stop_loss:
                    msg = self._trigger_alert_db(conn, pos, current_price, "STOP_LOSS_BROKEN")
                    if msg: alerts_to_send.append((pos["user_id"], msg))
                # TODO: 魔法数字 1.03 应改由配置项或个股 ATR 振幅动态决定
                elif current_price > stop_loss and current_price <= stop_loss * 1.03:
                    msg = self._trigger_alert_db(conn, pos, current_price, "STOP_LOSS_WARNING")
                    if msg: alerts_to_send.append((pos["user_id"], msg))
                    
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

    async def _check_chan_buys(self):
        """低频轮询：进行缠论日线状态推演，发现三买给予推送"""
        from server.services.chan_service import analyze_stock_chan_state, ChanState
        
        positions = await run_in_threadpool(self._db_get_positions)
        if not positions:
            return
            
        symbols = list(set(p["symbol"] for p in positions))
        
        # 1. 纯粹纯异步网络请求测算形态，释放 event loop
        chan_signals = []
        for sym in symbols:
            try:
                state, zs = await analyze_stock_chan_state(sym)
                if state == ChanState.THIRD_BUY_CONFIRMED:
                    chan_signals.append(sym)
            except Exception as e:
                logger.error(f"处理 {sym} 的缠论解析时发生异常: {e}")

        if not chan_signals:
            return
            
        # 2. 将纯同步的 DB 写入扔进线程池，杜绝主事件循环阻塞
        alerts_to_send = await run_in_threadpool(self._db_save_chan_alerts, chan_signals, positions)
            
        for user_id, msg in alerts_to_send:
            asyncio.create_task(send_stop_loss_alert(user_id, msg))

    def _db_save_chan_alerts(self, chan_signals, positions):
        """线程池运行的同步DB存入"""
        conn = get_connection()
        alerts_to_send = []
        try:
            for sym in chan_signals:
                users_holding = [p for p in positions if p["symbol"] == sym]
                for pos in users_holding:
                    msg = self._trigger_alert_db(conn, pos, current_price=0.0, alert_type="CHAN_THIRD_BUY")
                    if msg: alerts_to_send.append((pos["user_id"], msg))
            conn.commit()
            return alerts_to_send
        finally:
            conn.close()

    def _trigger_alert_db(self, conn, pos, current_price: float, alert_type: str) -> Optional[str]:
        """记录提醒入库，如果今天已经发过则返回 None"""
        # 1. 检查今日是否已针对此股票发送过同类提醒 (简单防重)
        row = conn.execute(
            """SELECT id FROM alerts 
               WHERE user_id = ? AND symbol = ? AND alert_type = ? 
               AND date(created_at) = date('now')""",
            (pos["user_id"], pos["symbol"], alert_type)
        ).fetchone()
        
        if row:
            return None # 今天已经发过了
            
        msg = ""
        if alert_type == "STOP_LOSS_BROKEN":
            msg = f"【止损击穿】{pos['name']} 现价 {current_price} 已跌破止损价 {pos['stop_loss_price']}！"
            logger.warning(f"[预警触发] {msg}")
        elif alert_type == "STOP_LOSS_WARNING":
            msg = f"【接近止损】{pos['name']} 现价 {current_price} 逼近止损价 {pos['stop_loss_price']}，请持续关注。"
            logger.info(f"[预警触发] {msg}")
        elif alert_type == "CHAN_THIRD_BUY":
            msg = f"【绝佳买点】{pos['name']} 确立日线级别第三类买点！"
            logger.info(f"[预警触发] {msg}")

        # 写入 alerts 表
        conn.execute(
            """INSERT INTO alerts (user_id, symbol, alert_type, trigger_price, is_triggered, message)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (pos["user_id"], pos["symbol"], alert_type, current_price, 1, msg)
        )
        return msg

# 单例实例
monitor = PriceMonitor()
