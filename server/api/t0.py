"""T+0 做T教练 API 路由。"""
from __future__ import annotations

import logging
import json
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
                   signal, signal_price, reason, state_json, updated_at
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
            "signal", "signal_price", "reason", "state_json", "updated_at",
        ]
        last_fills = _load_last_t0_fills(conn, user_id)
        states = {
            row[1]: _enrich_t0_state(dict(zip(cols, row)), last_fills.get(row[1]))
            for row in rows
        }
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
                   signal, signal_price, reason, state_json, updated_at
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
            "signal", "signal_price", "reason", "state_json", "updated_at",
        ]
        last_fill = _load_last_t0_fills(conn, user_id, symbol=symbol).get(symbol)
        return _enrich_t0_state(dict(zip(cols, row)), last_fill)
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


def _load_last_t0_fills(conn, user_id: int, *, symbol: str | None = None) -> dict[str, dict]:
    """Return latest T0 paper fill per symbol."""
    paper_account_id = f"t0_coach_{user_id}"
    params: list[object] = [paper_account_id]
    symbol_clause = ""
    if symbol:
        symbol_clause = " AND symbol = ?"
        params.append(symbol)
    rows = conn.execute(
        f"""
        SELECT fill_id, symbol, side, quantity, fill_price,
               commission, stamp_tax, transfer_fee, slippage,
               reason, filled_at
          FROM paper_fills
         WHERE paper_account_id = ?
           AND reason LIKE '%t0:%'
           {symbol_clause}
         ORDER BY filled_at DESC
        """,
        tuple(params),
    ).fetchall()
    cols = [
        "fill_id", "symbol", "side", "quantity", "fill_price",
        "commission", "stamp_tax", "transfer_fee", "slippage", "reason", "filled_at",
    ]
    result: dict[str, dict] = {}
    for row in rows:
        data = dict(zip(cols, row))
        result.setdefault(data["symbol"], data)
    return result


def _enrich_t0_state(state: dict, last_fill: dict | None = None) -> dict:
    """Add UI-friendly deterministic fields without changing the DB schema."""
    state_json = _loads_json(state.get("state_json") or "{}")
    if state_json:
        state["signal_qty"] = state.get("signal_qty") or state_json.get("current_open_qty")
    state.pop("state_json", None)
    state["data_quality"] = _t0_data_quality(state)
    state["action_window"] = _t0_action_window(state)
    state["next_step"] = _t0_next_step(state)
    state["last_fill"] = last_fill
    return state


def _t0_data_quality(state: dict) -> str:
    reason = str(state.get("reason") or "")
    if not state.get("pivot_zd") or not state.get("pivot_zg"):
        return "missing"
    if "不足" in reason or "无效" in reason:
        return "partial"
    return "ready"


def _t0_action_window(state: dict) -> str:
    t0_state = str(state.get("state") or "")
    signal = str(state.get("signal") or "")
    reason = str(state.get("reason") or "")
    if t0_state == "LOCKDOWN" or "STOP" in signal:
        return "locked"
    if t0_state == "POSITION_LONG" or signal == "BUY_LONG":
        return "long_open"
    if t0_state == "POSITION_SHORT" or signal == "SELL_SHORT":
        return "short_open"
    if "ZD" in reason or "底分型" in reason:
        return "near_zd"
    if "ZG" in reason or "顶分型" in reason:
        return "near_zg"
    return "none"


def _t0_next_step(state: dict) -> str:
    if state.get("signal"):
        signal_map = {
            "BUY_LONG": "1分钟底分型确认，纸盘低吸",
            "SELL_LONG": "到达上沿，纸盘正T卖出",
            "SELL_SHORT": "1分钟顶分型确认，纸盘高抛",
            "BUY_SHORT": "回到下沿，纸盘倒T买回",
            "STOP_LONG": "正T止损，今日锁定",
            "STOP_SHORT": "倒T止损，今日锁定",
            "SWEEP_LONG": "尾盘扫尾，纸盘卖出",
            "SWEEP_SHORT": "尾盘扫尾，纸盘买回",
        }
        return signal_map.get(str(state.get("signal")), str(state.get("reason") or "T0信号已触发"))
    reason = str(state.get("reason") or "").strip()
    if reason:
        return reason
    if state.get("data_quality") == "missing":
        return "缺少5分钟中枢或1分钟数据"
    if state.get("action_window") == "near_zd":
        return "进入下沿区域，等待1分钟底分型"
    if state.get("action_window") == "near_zg":
        return "进入上沿区域，等待1分钟顶分型"
    return "等待5分钟中枢边界与1分钟分型确认"


def _loads_json(value: str | None) -> dict:
    try:
        data = json.loads(value or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}
