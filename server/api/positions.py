"""持仓查询 API"""

from typing import Optional
from fastapi import APIRouter, HTTPException

from server.db.database import get_connection

router = APIRouter()


@router.get("")
def list_positions(user_id: int = 1):
    """查询用户所有持仓"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT p.*,
                   CASE WHEN p.current_price IS NOT NULL AND p.avg_cost > 0
                        THEN round((p.current_price - p.avg_cost) / p.avg_cost * 100, 2)
                        ELSE NULL
                   END as pnl_pct
            FROM positions p
            WHERE p.user_id = ? AND p.quantity > 0
            ORDER BY (p.quantity * p.avg_cost) DESC
            """,
            (user_id,),
        ).fetchall()

        positions = [dict(r) for r in rows]

        # 汇总统计
        total_market_value = sum(
            (p["current_price"] or p["avg_cost"]) * p["quantity"]
            for p in positions
        )
        total_cost = sum(p["avg_cost"] * p["quantity"] for p in positions)
        total_pnl = total_market_value - total_cost

        return {
            "count": len(positions),
            "total_market_value": round(total_market_value, 2),
            "total_cost": round(total_cost, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl / total_cost * 100, 2) if total_cost > 0 else 0,
            "positions": positions,
        }
    finally:
        conn.close()


@router.get("/overview")
async def position_overview(user_id: int = 1):
    """仓位透视镜 — 武器 1 的数据源"""
    from server.services.analysis.concentration import analyze_concentration
    from datetime import date
    res = await analyze_concentration(user_id)

    # 查询今日买入的标的（T+1锁定列表）
    today_str = date.today().isoformat()
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT DISTINCT t.symbol, t.name FROM trades t
               WHERE t.user_id=? AND t.direction='BUY' AND date(t.traded_at)=?""",
            (user_id, today_str),
        ).fetchall()
        t1_locked = [{"symbol": r["symbol"], "name": r["name"] or r["symbol"]} for r in rows]
    finally:
        conn.close()

    return {
        "total_value": res["total_market_value"],
        "position_count": len(res["positions"]),
        "health_score": res["health_score"],
        "positions": res["positions"],
        "warnings": res["warnings"],
        "t1_locked": t1_locked,
    }


@router.get("/{symbol}")
def get_position(symbol: str, user_id: int = 1):
    """查询单只持仓"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM positions WHERE user_id = ? AND symbol = ? AND quantity > 0",
            (user_id, symbol),
        ).fetchone()
        if not row:
            raise HTTPException(404, f"未持有 {symbol}")
        return dict(row)
    finally:
        conn.close()
