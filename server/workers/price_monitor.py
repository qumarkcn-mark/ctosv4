import asyncio
import json
import logging
from typing import Optional

from fastapi.concurrency import run_in_threadpool

from server.api import radar as radar_api
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

                # 每隔 20 个周期 (约 10 分钟) 跑一次低频缠论推演。
                # 启动首轮先跳过，避免用户打开页面时和 Radar/CChan 结构计算抢 CPU。
                if self._loop_count > 0 and self._loop_count % 20 == 0:
                    await self._check_chan_buys()

                # Task #10：每日收盘后（15:05-15:30 窗口）自动更新台阶止损
                await self._check_trailing_stop_update()

                # 多元宇宙日志：每天 20:30 自动拍快照+结算
                await self._check_multiverse_auto_run()
                    
                self._loop_count += 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("价格监控发生异常: %s", e)
            
            await asyncio.sleep(self.interval_seconds)

    async def _check_trailing_stop_update(self):
        """Task #10：每日收盘后（15:05-15:30）自动更新台阶止损。

        逻辑：
          - 仅在 15:05-15:30 时间窗口内执行（每天最多一次）
          - 取持仓股的30分钟中枢ZG，与现有台阶止损比较
          - 台阶只上移（MAX取高），持久化到 positions.trailing_stop_price
        """
        import datetime as dt
        from server.api.chan import _persist_trailing_stop

        now = dt.datetime.now()
        # 仅工作日 15:05-15:30 执行
        if now.weekday() >= 5:
            return
        t = now.time()
        if not (dt.time(15, 5) <= t <= dt.time(15, 30)):
            return

        # 今日已跑过则跳过（用日期标记）
        today_str = now.strftime("%Y-%m-%d")
        last_ran = getattr(self, "_trailing_stop_ran_date", None)
        if last_ran == today_str:
            return

        try:
            positions = await run_in_threadpool(self._db_get_positions)
            if not positions:
                return

            for pos in positions:
                sym = pos["symbol"]
                try:
                    response = await radar_api.get_radar(sym, user_id=pos.get("user_id"))
                    new_m30_zg = _m30_trailing_stop_from_radar(response.get("data") or {})
                    if new_m30_zg > 0:
                        _persist_trailing_stop(sym, new_m30_zg)
                        logger.info(
                            "[台阶止损] %s 收盘更新 trailing_stop → max(存储, %.2f)",
                            sym, new_m30_zg
                        )
                except Exception as e:
                    logger.warning("[台阶止损更新失败] %s error=%s", sym, e)

            self._trailing_stop_ran_date = today_str
            logger.info("[台阶止损] 每日收盘更新完成，共处理 %d 只持仓股", len(positions))

        except Exception as e:
            logger.error("[台阶止损] 定时任务异常: %s", e)

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

    async def _check_chan_buys(self):
        """低频轮询：进行缠论日线状态推演，触发以下四类推送（每类每股每天最多一次）：
        ① 台阶止损触发（STOP_LOSS_BROKEN 已在 _check_stop_losses 处理，此处增强消息）
        ② 30分顶背驰预警（持仓股）→ 关注出局信号
        ③ 30分底背驰（空仓关注股）→ 入场条件进展
        ④ 入场五条件全满足（关注股）→ 入场信号触发
        """
        from server.services.chan_service import analyze_matrix_state

        positions = await run_in_threadpool(self._db_get_positions)
        watchlist = await run_in_threadpool(self._db_get_watchlist)

        holding_symbols  = list(set(p["symbol"] for p in positions))
        watchlist_symbols = list(set(w["symbol"] for w in watchlist if w["symbol"] not in holding_symbols))
        all_symbols = holding_symbols + watchlist_symbols

        if not all_symbols:
            return

        chan_results = {}
        for sym in all_symbols:
            try:
                # 获取完整矩阵数据（含 m30 patterns）
                result = await analyze_matrix_state(sym, holding=None)
                chan_results[sym] = result
            except Exception as e:
                logger.error("[ERROR] CHAN_ENGINE_FALLBACK: %s  error=%s", sym, e, exc_info=True)

        alerts_to_send = await run_in_threadpool(
            self._db_save_chan_alerts_v2, chan_results, positions, watchlist
        )

        for user_id, msg in alerts_to_send:
            asyncio.create_task(send_stop_loss_alert(user_id, msg))

    def _db_get_watchlist(self):
        """获取自选股列表（无持仓的关注股），用于底背驰/入场信号推送。"""
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT wg.user_id, wi.symbol, wi.name
                  FROM watchlist_items wi
                  JOIN watchlist_groups wg ON wg.id = wi.group_id
                 ORDER BY wg.user_id, wg.sort_order, wi.sort_order, wi.id
                """
            ).fetchall()
            return [dict(r) for r in rows] if rows else []
        except Exception:
            return []
        finally:
            conn.close()

    def _db_save_chan_alerts(self, chan_signals, positions):
        """向后兼容旧接口（保留，供其他调用方使用）"""
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

    def _db_save_chan_alerts_v2(self, chan_results: dict, positions: list, watchlist: list):
        """V2 推送逻辑：处理七类推送触发条件，每类每股每天最多一次（24h 冷却）。

        触发类型及冷却键：
          STAGE_VALIDATION_FAIL  ① 预案失效（持仓）
          STAGE_TIME_EXPIRED     ① 走势验证超时（持仓）
          HOLDING_STAGE4         ① 30分顶背驰转折确认 → 建议减仓50%（持仓）
          HOLDING_STAGE5         ① 日线顶背驰/台阶止损破 → 建议清仓（持仓）
          CHAN_30M_TOP_DIV       ② 30分顶背驰预警（持仓）
          CHAN_30M_BOT_DIV       ③ 30分底背驰（空仓关注股）
          CHAN_ENTRY_SIGNAL      ④ 战法一/战法二入场条件全满足（含战法类型标注）
        """
        from server.services.chan_service import _classify_strategy, _detect_holding_stage

        conn = get_connection()
        alerts_to_send = []
        try:
            for sym, result in chan_results.items():
                matrix_a = result.get("matrix_a", [])
                day  = matrix_a[0] if len(matrix_a) > 0 else {}
                m30  = matrix_a[1] if len(matrix_a) > 1 else {}
                m5   = matrix_a[2] if len(matrix_a) > 2 else {}
                week = matrix_a[3] if len(matrix_a) > 3 else {}

                m30_patterns = " ".join(m30.get("patterns", []))

                # ── ① 持仓股：Stage 状态机推送 ──
                holding_users = [p for p in positions if p["symbol"] == sym]
                if holding_users:
                    from server.api.chan import _compute_holding_status
                    for pos in holding_users:
                        _holding = {
                            "cost": pos.get("avg_cost", 0),
                            "qty":  pos.get("quantity", 0),
                            "entry_date": pos.get("entry_date"),
                            "trailing_stop_price": pos.get("trailing_stop_price"),
                            "stop_loss_price": pos.get("stop_loss_price", 0),
                        }
                        if _holding["cost"] <= 0 or _holding["qty"] <= 0:
                            continue

                        _fake_pos = {**pos, "name": pos.get("name", sym)}

                        # Stage 0：走势验证
                        _hs = _compute_holding_status(day, m30, _holding, {})
                        _val = _hs.get("validation", {})
                        if _val.get("status") == "预案失效":
                            _msg = self._trigger_alert_db(
                                conn, _fake_pos, current_price=0.0,
                                alert_type="STAGE_VALIDATION_FAIL"
                            )
                            if _msg:
                                alerts_to_send.append((pos["user_id"], _msg))
                        elif _val.get("status") == "时间失效":
                            _msg = self._trigger_alert_db(
                                conn, _fake_pos, current_price=0.0,
                                alert_type="STAGE_TIME_EXPIRED"
                            )
                            if _msg:
                                alerts_to_send.append((pos["user_id"], _msg))

                        # Stage 4/5：六阶段状态机（减仓/清仓信号）
                        try:
                            m30_bis = m30.get("bi_list", [])
                            day_bis  = day.get("bi_list", [])
                            _stage = _detect_holding_stage(
                                _holding,
                                l1=day_bis,
                                l2=m30_bis,
                                l3=m5.get("bi_list", [])
                            )
                            if _stage.get("should_notify"):
                                stage_num = _stage.get("stage", 0)
                                if stage_num == 4:
                                    _msg = self._trigger_alert_db(
                                        conn, _fake_pos, current_price=0.0,
                                        alert_type="HOLDING_STAGE4",
                                        extra={"stage_label": _stage.get("label", ""),
                                               "trailing_stop": _stage.get("trailing_stop", 0)}
                                    )
                                    if _msg:
                                        alerts_to_send.append((pos["user_id"], _msg))
                                elif stage_num >= 5:
                                    _msg = self._trigger_alert_db(
                                        conn, _fake_pos, current_price=0.0,
                                        alert_type="HOLDING_STAGE5",
                                        extra={"stage_label": _stage.get("label", ""),
                                               "trailing_stop": _stage.get("trailing_stop", 0)}
                                    )
                                    if _msg:
                                        alerts_to_send.append((pos["user_id"], _msg))
                        except Exception as e_stage:
                            logger.warning("[Stage4/5推送] %s error=%s", sym, e_stage)

                # ── ② 30分顶背驰预警（持仓股，区分中继/转折） ──
                if any(kw in m30_patterns for kw in ("顶背驰", "1卖")):
                    # 判断中继还是转折（利用 m30 的 beichi 分型）
                    m30_beichi_type = m30.get("latest_top_beichi_type", "")  # "中继" | "转折" | ""
                    for pos in holding_users:
                        _fake_pos = {**pos, "name": pos.get("name", sym),
                                     "_m30_beichi_type": m30_beichi_type}
                        msg = self._trigger_alert_db(
                            conn, _fake_pos, current_price=0.0,
                            alert_type="CHAN_30M_TOP_DIV"
                        )
                        if msg:
                            alerts_to_send.append((pos["user_id"], msg))

                # ── ③ 30分底背驰（空仓关注股） ──
                if any(kw in m30_patterns for kw in ("底背驰", "二买", "类二买")):
                    watch_users = [w for w in watchlist if w["symbol"] == sym]
                    for wu in watch_users:
                        fake_pos = {"user_id": wu["user_id"], "symbol": sym,
                                    "name": wu.get("name", sym), "stop_loss_price": 0.0}
                        msg = self._trigger_alert_db(
                            conn, fake_pos, current_price=0.0, alert_type="CHAN_30M_BOT_DIV"
                        )
                        if msg:
                            alerts_to_send.append((wu["user_id"], msg))

                # ── ④ 战法入场信号：使用新的 strategy_classification ──
                # 直接读 result 中已计算好的 strategy_classification（由 analyze_matrix_state 提供）
                sc = result.get("strategy_classification") or {}
                strategy_type = sc.get("strategy_type", "观察中")
                if strategy_type in ("战法一", "战法二", "双战法"):
                    all_interested = [
                        {"user_id": p["user_id"], "symbol": sym,
                         "name": p.get("name", sym), "stop_loss_price": 0.0,
                         "_strategy_type": strategy_type}
                        for p in positions if p["symbol"] == sym
                    ] + [
                        {"user_id": w["user_id"], "symbol": sym,
                         "name": w.get("name", sym), "stop_loss_price": 0.0,
                         "_strategy_type": strategy_type}
                        for w in watchlist if w["symbol"] == sym
                    ]
                    seen_users: set = set()
                    for u in all_interested:
                        uid = u["user_id"]
                        if uid in seen_users:
                            continue
                        seen_users.add(uid)
                        msg = self._trigger_alert_db(
                            conn, u, current_price=0.0, alert_type="CHAN_ENTRY_SIGNAL"
                        )
                        if msg:
                            alerts_to_send.append((uid, msg))

            conn.commit()
            return alerts_to_send
        finally:
            conn.close()

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


def _m30_trailing_stop_from_radar(radar_data: dict) -> float:
    """从 Radar contract 提取 30 分钟台阶止损候选价。"""
    levels = ((radar_data.get("structure") or {}).get("levels") or {})
    m30 = levels.get("30") or {}
    active_zs = m30.get("active_zhongshu") or {}
    return (
        m30.get("zs_operative_zg")
        or m30.get("zg")
        or active_zs.get("zg")
        or 0
    )
