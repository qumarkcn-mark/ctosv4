"""持仓查询 API"""

from typing import Optional
from fastapi import APIRouter, HTTPException

from server.db.database import get_connection

router = APIRouter()


@router.get("")
async def list_positions(user_id: int = 1):
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
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT symbol, name, quantity, avg_cost, current_price
            FROM positions
            WHERE user_id = ? AND quantity > 0
            ORDER BY (quantity * avg_cost) DESC
            """,
            (user_id,),
        ).fetchall()

        positions = []
        total_value = 0

        for r in rows:
            value = r["quantity"] * (r["current_price"] or r["avg_cost"])
            total_value += value
            positions.append({
                "symbol": r["symbol"],
                "name": r["name"],
                "quantity": r["quantity"],
                "avg_cost": r["avg_cost"],
                "current_price": r["current_price"],
                "market_value": round(value, 2),
            })

        # 计算每只的占比
        for p in positions:
            p["weight_pct"] = round(p["market_value"] / total_value * 100, 2) if total_value > 0 else 0

        # 预警检查
        warnings = []
        small_positions = [p for p in positions if p["weight_pct"] < 5]
        if len(small_positions) >= 3:
            small_total_pct = sum(p["weight_pct"] for p in small_positions)
            warnings.append({
                "type": "SCATTERED",
                "message": f"{len(small_positions)} 只小票合计占 {round(small_total_pct, 1)}%",
                "severity": "warning" if small_total_pct > 20 else "info",
            })

        if len(positions) > 5:
            warnings.append({
                "type": "TOO_MANY",
                "message": f"持仓 {len(positions)} 只 (建议 ≤5)",
                "severity": "warning",
            })

        return {
            "total_value": round(total_value, 2),
            "position_count": len(positions),
            "positions": positions,
            "warnings": warnings,
        }
    finally:
        conn.close()


@router.get("/{symbol}")
async def get_position(symbol: str, user_id: int = 1):
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
