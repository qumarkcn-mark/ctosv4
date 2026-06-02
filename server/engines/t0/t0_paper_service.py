"""T+0 做T教练纸盘撮合服务。

职责：
- 将 T0StateMachine 产生的信号翻译为 PaperIntent + PaperFill
- 使用 t0_friction 计算精确交易费用
- 写入 paper_intents / paper_fills 表
- 维护 t0_state_cache 的日累计 PnL

不做：
- 不管理 paper_positions（T0教练不改变真实/纸盘持仓）
- 不管理 paper_accounts.cash（T0教练只记录流水，不模拟资金账户）
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from server.db.database import get_connection
from server.engines.execution.paper_models import PaperFill, PaperIntent
from server.engines.execution.paper_store import save_paper_fill, save_paper_intent
from server.engines.t0.t0_friction import calculate_friction
from server.engines.t0.t0_state_machine import T0TickResult

logger = logging.getLogger(__name__)

STRATEGY_ID = "t0_bounded_v1"
STRATEGY_VERSION = "1.0.0"
T0_PAPER_ACCOUNT_PREFIX = "t0_coach_"


def get_or_create_t0_account(user_id: int) -> str:
    """获取或创建用户的 T0 教练纸盘账户。

    paper_account_id = "t0_coach_{user_id}"
    cash 设为 0（T0教练不模拟资金，只记流水）。

    Returns:
        paper_account_id 字符串
    """
    paper_account_id = f"{T0_PAPER_ACCOUNT_PREFIX}{user_id}"
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO paper_accounts
                (paper_account_id, user_id, cash, realized_pnl, trade_count, metadata_json, updated_at)
            VALUES (?, ?, 0, 0, 0, '{"source":"t0_coach"}', CURRENT_TIMESTAMP)
            """,
            (paper_account_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return paper_account_id


def record_t0_signal(
    user_id: int,
    symbol: str,
    signal: str,          # BUY_LONG / SELL_LONG / SELL_SHORT / BUY_SHORT / STOP_LONG / STOP_SHORT / SWEEP_LONG / SWEEP_SHORT
    signal_price: float,
    t0_qty: int,
    tick_result: T0TickResult,
    run_id: Optional[str] = None,
) -> dict:
    """将一个 T0 信号写入 paper_intents + paper_fills。

    Args:
        user_id: 用户 ID
        symbol: 标的代码
        signal: 信号类型
        signal_price: 成交价格
        t0_qty: 做T数量（股）
        tick_result: 状态机 tick 结果（用于记录元数据）
        run_id: 可选的回测 run_id

    Returns:
        { intent_id, fill_id, side, quantity, fill_price, net_pnl, fees }
    """
    # 确定买卖方向
    # BUY_LONG / BUY_SHORT / SWEEP_SHORT → "BUY"（实际买入操作）
    # SELL_LONG / SELL_SHORT / STOP_LONG / STOP_SHORT / SWEEP_LONG → "SELL"
    side = "BUY" if signal in ("BUY_LONG", "BUY_SHORT", "SWEEP_SHORT") else "SELL"

    # 幂等键：防止同一信号重复写入
    today = datetime.now().strftime("%Y-%m-%d")
    idempotency_key = f"t0:{user_id}:{symbol}:{today}:{signal}:{tick_result.daily_trades}"

    paper_account_id = f"{T0_PAPER_ACCOUNT_PREFIX}{user_id}"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    intent_id = f"t0_intent_{uuid.uuid4().hex[:12]}"
    fill_id = f"t0_fill_{uuid.uuid4().hex[:12]}"

    # 计算精确费用
    fee_detail = calculate_friction(signal_price, t0_qty, side)

    # 计算本笔净 PnL（仅对平仓信号有意义）
    net_pnl = 0.0
    if tick_result.entry_price and signal in ("SELL_LONG", "STOP_LONG", "SWEEP_LONG") and side == "SELL":
        gross = (signal_price - tick_result.entry_price) * t0_qty
        net_pnl = gross - fee_detail["total"]
    elif tick_result.entry_price and signal in ("BUY_SHORT", "STOP_SHORT", "SWEEP_SHORT") and side == "BUY":
        gross = (tick_result.entry_price - signal_price) * t0_qty
        net_pnl = gross - fee_detail["total"]

    conn = get_connection()
    try:
        # 检查幂等：已存在则直接返回
        existing = conn.execute(
            "SELECT fill_id FROM paper_fills WHERE reason LIKE ? LIMIT 1",
            (f"%{idempotency_key}%",),
        ).fetchone()
        if existing:
            logger.debug("[T0 Paper] 幂等跳过 key=%s", idempotency_key)
            return {
                "intent_id": None,
                "fill_id": existing[0],
                "side": side,
                "quantity": t0_qty,
                "fill_price": signal_price,
                "net_pnl": net_pnl,
                "fees": fee_detail["total"],
                "skipped": True,
            }

        # 写入 paper_intents
        intent = PaperIntent(
            intent_id=intent_id,
            idempotency_key=idempotency_key,
            user_id=user_id,
            paper_account_id=paper_account_id,
            symbol=symbol,
            side=side,
            quantity=t0_qty,
            created_at=now_str,
            strategy_id=STRATEGY_ID,
            strategy_version=STRATEGY_VERSION,
            status="FILLED",
            dry_run=True,
            simulator=True,
            price_policy={"source": "t0_engine_tick", "price": signal_price},
            reason={"signal": signal, "idempotency_key": idempotency_key},
        )
        save_paper_intent(conn, intent, run_id=run_id)

        # 写入 paper_fills
        fill = PaperFill(
            fill_id=fill_id,
            intent_id=intent_id,
            symbol=symbol,
            side=side,
            quantity=t0_qty,
            fill_price=signal_price,
            filled_at=now_str,
            status="FILLED",
            price_source="t0_engine_tick",
            commission=fee_detail["commission"],
            stamp_tax=fee_detail["stamp_duty"],
            transfer_fee=fee_detail["transfer_fee"],
            slippage=fee_detail["slippage"],
            reason=f"{idempotency_key} | {tick_result.reason}",
        )
        save_paper_fill(conn, fill, account=_dummy_account(user_id, paper_account_id), run_id=run_id)
        conn.commit()

        logger.info("[T0 Paper] 记录信号 %s %s %s@%.2f qty=%d fees=%.2f",
                    symbol, signal, side, signal_price, t0_qty, fee_detail["total"])
    finally:
        conn.close()

    return {
        "intent_id": intent_id,
        "fill_id": fill_id,
        "side": side,
        "quantity": t0_qty,
        "fill_price": signal_price,
        "net_pnl": net_pnl,
        "fees": fee_detail["total"],
        "skipped": False,
    }


def get_daily_t0_fills(user_id: int, date: Optional[str] = None) -> list[dict]:
    """查询某日所有 T0 纸盘成交记录。

    Args:
        user_id: 用户 ID
        date: 日期字符串 "YYYY-MM-DD"，默认今日

    Returns:
        成交记录列表
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    paper_account_id = f"{T0_PAPER_ACCOUNT_PREFIX}{user_id}"
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT fill_id, intent_id, symbol, side, quantity, fill_price,
                   commission, stamp_tax, transfer_fee, slippage,
                   (commission + stamp_tax + transfer_fee + slippage) AS total_fees,
                   price_source, reason, filled_at
              FROM paper_fills
             WHERE paper_account_id = ?
               AND filled_at LIKE ?
               AND reason LIKE '%t0:%'
             ORDER BY filled_at
            """,
            (paper_account_id, f"{date}%"),
        ).fetchall()
        cols = ["fill_id", "intent_id", "symbol", "side", "quantity", "fill_price",
                "commission", "stamp_tax", "transfer_fee", "slippage", "total_fees",
                "price_source", "reason", "filled_at"]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def get_daily_t0_summary(user_id: int, date: Optional[str] = None) -> dict:
    """汇总某日 T0 纸盘表现。

    Returns:
        { date, total_trades, win_count, loss_count, win_rate,
          gross_pnl, total_fees, net_pnl, best_trade, worst_trade,
          symbols_traded }
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    fills = get_daily_t0_fills(user_id, date)

    # 将成交配对（BUY-SELL 配对计算 PnL）
    # 简化处理：按 symbol 分组，用 state_cache 的 daily_pnl 汇总
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT symbol, daily_pnl, daily_trades, daily_stop_count
              FROM t0_state_cache
             WHERE user_id = ?
               AND daily_trades > 0
            """,
            (user_id,),
        ).fetchall()
    finally:
        conn.close()

    total_trades = sum(r[2] for r in rows)
    total_pnl = sum(r[1] for r in rows)
    total_stops = sum(r[3] for r in rows)
    total_fees = sum(f["total_fees"] for f in fills)

    # 胜负统计（基于 state_cache daily_pnl > 0 的标的数量，粗略统计）
    winning_symbols = [r for r in rows if r[1] > 0]
    losing_symbols = [r for r in rows if r[1] <= 0]

    best_symbol = max(rows, key=lambda r: r[1], default=None) if rows else None
    worst_symbol = min(rows, key=lambda r: r[1], default=None) if rows else None

    symbols_traded = [r[0] for r in rows]

    return {
        "date": date,
        "total_trades": total_trades,
        "win_count": len(winning_symbols),
        "loss_count": len(losing_symbols),
        "win_rate": len(winning_symbols) / len(rows) if rows else 0.0,
        "gross_pnl": round(total_pnl + total_fees, 2),
        "total_fees": round(total_fees, 2),
        "net_pnl": round(total_pnl, 2),
        "stop_count": total_stops,
        "best_trade": {
            "symbol": best_symbol[0],
            "net_pnl": round(best_symbol[1], 2),
        } if best_symbol else None,
        "worst_trade": {
            "symbol": worst_symbol[0],
            "net_pnl": round(worst_symbol[1], 2),
        } if worst_symbol else None,
        "symbols_traded": symbols_traded,
    }


# ------------------------------------------------------------------ #
# 内部辅助
# ------------------------------------------------------------------ #

def _dummy_account(user_id: int, paper_account_id: str):
    """创建最简 PaperAccount 对象（paper_store 需要它）。"""
    from server.engines.execution.paper_models import PaperAccount
    return PaperAccount(
        paper_account_id=paper_account_id,
        user_id=user_id,
        cash=0.0,
    )
