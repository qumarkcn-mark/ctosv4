"""持仓计算服务 — 加权平均成本法"""

import sqlite3
from datetime import datetime


def recalculate_position(conn: sqlite3.Connection, user_id: int, symbol: str):
    """
    根据交易记录重新计算某只股票的持仓。
    使用加权平均成本法:
      - 买入: avg_cost = (旧持仓金额 + 新买入金额) / 总数量
      - 卖出: 数量减少, avg_cost 不变
      - 全部卖出: 删除持仓记录
    """
    # 查询该股票所有交易, 按时间排序
    rows = conn.execute(
        """
        SELECT direction, price, quantity, traded_at
        FROM trades
        WHERE user_id = ? AND symbol = ?
        ORDER BY traded_at ASC
        """,
        (user_id, symbol),
    ).fetchall()

    # 逐笔计算
    total_qty = 0
    total_cost = 0.0  # 总成本 (qty * avg_cost)

    for row in rows:
        direction = row["direction"]
        price = row["price"]
        qty = row["quantity"]

        if direction == "BUY":
            total_cost += price * qty
            total_qty += qty
        elif direction == "SELL":
            if total_qty > 0:
                # 卖出不改变平均成本, 只减少数量
                sell_qty = min(qty, total_qty)
                avg = total_cost / total_qty if total_qty > 0 else 0
                total_qty -= sell_qty
                total_cost = avg * total_qty

    if total_qty <= 0:
        # 清仓: 删除持仓记录
        conn.execute(
            "DELETE FROM positions WHERE user_id = ? AND symbol = ?",
            (user_id, symbol),
        )
        return None

    avg_cost = total_cost / total_qty if total_qty > 0 else 0

    # 查股票名称 (从最近的交易记录取)
    name_row = conn.execute(
        "SELECT name FROM trades WHERE user_id = ? AND symbol = ? AND name IS NOT NULL ORDER BY traded_at DESC LIMIT 1",
        (user_id, symbol),
    ).fetchone()
    name = name_row["name"] if name_row else None

    # 计算持仓天数 (从第一笔买入算起)
    first_buy = conn.execute(
        "SELECT traded_at FROM trades WHERE user_id = ? AND symbol = ? AND direction = 'BUY' ORDER BY traded_at ASC LIMIT 1",
        (user_id, symbol),
    ).fetchone()
    days_held = 0
    if first_buy:
        try:
            first_date = datetime.fromisoformat(first_buy["traded_at"])
            days_held = (datetime.now() - first_date).days
        except (ValueError, TypeError):
            days_held = 0

    # Upsert 持仓
    conn.execute(
        """
        INSERT INTO positions (user_id, symbol, name, quantity, avg_cost, days_held, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, symbol) DO UPDATE SET
            name = excluded.name,
            quantity = excluded.quantity,
            avg_cost = excluded.avg_cost,
            days_held = excluded.days_held,
            updated_at = excluded.updated_at
        """,
        (user_id, symbol, name, total_qty, round(avg_cost, 4), days_held,
         datetime.now().isoformat()),
    )

    return {
        "symbol": symbol,
        "quantity": total_qty,
        "avg_cost": round(avg_cost, 4),
        "days_held": days_held,
    }


def recalculate_all_positions(conn: sqlite3.Connection, user_id: int):
    """重算用户所有持仓 (CSV 导入后使用)"""
    symbols = conn.execute(
        "SELECT DISTINCT symbol FROM trades WHERE user_id = ?",
        (user_id,),
    ).fetchall()

    results = []
    for row in symbols:
        result = recalculate_position(conn, user_id, row["symbol"])
        if result:
            results.append(result)

    return results
