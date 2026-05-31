"""T+0 做T教练 API 路由。"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from server.db.database import get_connection
from server.engines.t0.t0_paper_service import get_daily_t0_fills, get_daily_t0_summary

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/t0", tags=["t0 coach"])


class EnableT0Request(BaseModel):
    t0_qty: int


@router.get("/states")
def get_all_t0_states(user_id: int = 1):
    """批量返回所有启用做T标的的状态机快照。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT user_id, symbol, state, pivot_zd, pivot_zg,
                   entry_price, target_price, stop_structural, stop_catastrophic,
                   t0_qty, friction_per_share, is_grid_viable,
                   daily_pnl, daily_trades, daily_stop_count,
                   signal, signal_price, reason, updated_at
              FROM t0_state_cache
             WHERE user_id = ?
            """,
            (user_id,),
        ).fetchall()
        cols = [
            "user_id", "symbol", "state", "pivot_zd", "pivot_zg",
            "entry_price", "target_price", "stop_structural", "stop_catastrophic",
            "t0_qty", "friction_per_share", "is_grid_viable",
            "daily_pnl", "daily_trades", "daily_stop_count",
            "signal", "signal_price", "reason", "updated_at",
        ]
        states = {row[1]: dict(zip(cols, row)) for row in rows}
        return {"states": states, "count": len(states)}
    finally:
        conn.close()


@router.get("/state/{symbol}")
def get_t0_state(symbol: str, user_id: int = 1):
    """单只标的的 T0 状态。"""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT user_id, symbol, state, pivot_zd, pivot_zg,
                   entry_price, target_price, stop_structural, stop_catastrophic,
                   t0_qty, friction_per_share, is_grid_viable,
                   daily_pnl, daily_trades, daily_stop_count,
                   signal, signal_price, reason, updated_at
              FROM t0_state_cache
             WHERE user_id = ? AND symbol = ?
            """,
            (user_id, symbol),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"未找到 {symbol} 的 T0 状态（可能未启用做T）")
        cols = [
            "user_id", "symbol", "state", "pivot_zd", "pivot_zg",
            "entry_price", "target_price", "stop_structural", "stop_catastrophic",
            "t0_qty", "friction_per_share", "is_grid_viable",
            "daily_pnl", "daily_trades", "daily_stop_count",
            "signal", "signal_price", "reason", "updated_at",
        ]
        return dict(zip(cols, row))
    finally:
        conn.close()


@router.post("/enable/{symbol}")
def enable_t0(symbol: str, body: EnableT0Request, user_id: int = 1):
    """启用做T教练。

    校验：
    1. t0_qty % 100 == 0（必须为一手整数倍）
    2. t0_qty <= position.quantity（不超底仓）
    3. symbol 在 watchlist_items 中存在
    """
    t0_qty = body.t0_qty

    # 校验 1：整手
    if t0_qty <= 0 or t0_qty % 100 != 0:
        raise HTTPException(status_code=400, detail="t0_qty 必须为 100 的正整数倍（最少 1 手 = 100 股）")

    conn = get_connection()
    try:
        # 校验 3：watchlist 存在
        wi_row = conn.execute(
            """
            SELECT wi.id
              FROM watchlist_items wi
              JOIN watchlist_groups wg ON wg.id = wi.group_id
             WHERE wg.user_id = ? AND wi.symbol = ?
             LIMIT 1
            """,
            (user_id, symbol),
        ).fetchone()
        if not wi_row:
            raise HTTPException(status_code=404, detail=f"{symbol} 不在自选股中，请先添加到自选股")
        watchlist_item_id = wi_row[0]

        # 校验 2：底仓数量
        pos_row = conn.execute(
            "SELECT quantity FROM positions WHERE user_id = ? AND symbol = ? AND quantity > 0 LIMIT 1",
            (user_id, symbol),
        ).fetchone()
        if not pos_row:
            raise HTTPException(status_code=400, detail=f"{symbol} 无底仓，无法启用做T教练")
        base_qty = pos_row[0]
        if t0_qty > base_qty:
            raise HTTPException(
                status_code=400,
                detail=f"t0_qty={t0_qty} 超过底仓 {base_qty} 股，请减小做T数量",
            )

        # 写入
        conn.execute(
            """
            UPDATE watchlist_items
               SET t0_enabled = 1, t0_qty = ?
             WHERE id = ?
            """,
            (t0_qty, watchlist_item_id),
        )
        conn.commit()
        logger.info("[T0 API] 启用做T 用户%d %s qty=%d", user_id, symbol, t0_qty)
        return {"status": "ok", "symbol": symbol, "t0_qty": t0_qty, "base_qty": base_qty}
    finally:
        conn.close()


@router.post("/disable/{symbol}")
def disable_t0(symbol: str, user_id: int = 1):
    """关闭做T教练。"""
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE watchlist_items
               SET t0_enabled = 0, t0_qty = 0
             WHERE id IN (
                SELECT wi.id
                  FROM watchlist_items wi
                  JOIN watchlist_groups wg ON wg.id = wi.group_id
                 WHERE wg.user_id = ? AND wi.symbol = ?
             )
            """,
            (user_id, symbol),
        )
        # 重置状态缓存为 IDLE
        conn.execute(
            """
            UPDATE t0_state_cache
               SET state = 'IDLE', signal = NULL, entry_price = NULL,
                   target_price = NULL, reason = '用户关闭做T教练', updated_at = CURRENT_TIMESTAMP
             WHERE user_id = ? AND symbol = ?
            """,
            (user_id, symbol),
        )
        conn.commit()
        logger.info("[T0 API] 关闭做T 用户%d %s", user_id, symbol)
        return {"status": "ok", "symbol": symbol}
    finally:
        conn.close()


@router.get("/daily-report")
def get_daily_report(
    user_id: int = 1,
    date: Optional[str] = Query(default=None, description="日期 YYYY-MM-DD，默认今日"),
):
    """今日做T纸盘汇总。"""
    summary = get_daily_t0_summary(user_id=user_id, date=date)
    return summary


@router.get("/history")
def get_t0_history(
    user_id: int = 1,
    symbol: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=500),
):
    """历史做T纸盘记录。"""
    conn = get_connection()
    try:
        paper_account_id = f"t0_coach_{user_id}"
        if symbol:
            rows = conn.execute(
                """
                SELECT fill_id, symbol, side, quantity, fill_price,
                       commission, stamp_tax, transfer_fee, slippage,
                       (commission + stamp_tax + transfer_fee + slippage) AS total_fees,
                       reason, filled_at
                  FROM paper_fills
                 WHERE paper_account_id = ?
                   AND symbol = ?
                   AND reason LIKE '%t0:%'
                 ORDER BY filled_at DESC
                 LIMIT ?
                """,
                (paper_account_id, symbol, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT fill_id, symbol, side, quantity, fill_price,
                       commission, stamp_tax, transfer_fee, slippage,
                       (commission + stamp_tax + transfer_fee + slippage) AS total_fees,
                       reason, filled_at
                  FROM paper_fills
                 WHERE paper_account_id = ?
                   AND reason LIKE '%t0:%'
                 ORDER BY filled_at DESC
                 LIMIT ?
                """,
                (paper_account_id, limit),
            ).fetchall()

        cols = ["fill_id", "symbol", "side", "quantity", "fill_price",
                "commission", "stamp_tax", "transfer_fee", "slippage", "total_fees",
                "reason", "filled_at"]
        return {"fills": [dict(zip(cols, r)) for r in rows], "count": len(rows)}
    finally:
        conn.close()
