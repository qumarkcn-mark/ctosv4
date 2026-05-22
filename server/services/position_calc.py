"""持仓计算服务 — 加权平均成本法"""

import sqlite3
from datetime import datetime

from server.domain.symbols import symbol_aliases, to_tencent_symbol
from server.services.entry_thesis import ensure_unknown_entry_thesis


def recalculate_position(conn: sqlite3.Connection, user_id: int, symbol: str):
    """
    根据交易记录重新计算某只股票的持仓。
    使用加权平均成本法:
      - 买入: avg_cost = (旧持仓金额 + 新买入金额) / 总数量
      - 卖出: 数量减少, avg_cost 不变
      - 全部卖出: 删除持仓记录
    """
    aliases = symbol_aliases(symbol)
    position_symbol = to_tencent_symbol(symbol)

    # 查询该股票所有交易, 按时间排序。兼容历史 sh600519 与 canonical sh.600519。
    rows = conn.execute(
        """
        SELECT direction, price, quantity, traded_at
        FROM trades
        WHERE user_id = ? AND symbol IN (?, ?, ?)
        ORDER BY traded_at ASC
        """,
        (user_id, *aliases),
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
            "DELETE FROM positions WHERE user_id = ? AND symbol IN (?, ?, ?)",
            (user_id, *aliases),
        )
        return None

    avg_cost = total_cost / total_qty if total_qty > 0 else 0

    # 查股票名称 (从最近的交易记录取)
    name_row = conn.execute(
        "SELECT name FROM trades WHERE user_id = ? AND symbol IN (?, ?, ?) AND name IS NOT NULL ORDER BY traded_at DESC LIMIT 1",
        (user_id, *aliases),
    ).fetchone()
    name = name_row["name"] if name_row else None

    # 计算持仓天数 (从第一笔买入算起)
    first_buy = conn.execute(
        "SELECT traded_at FROM trades WHERE user_id = ? AND symbol IN (?, ?, ?) AND direction = 'BUY' ORDER BY traded_at ASC LIMIT 1",
        (user_id, *aliases),
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
        (user_id, position_symbol, name, total_qty, round(avg_cost, 4), days_held,
         datetime.now().isoformat()),
    )
    ensure_unknown_entry_thesis(conn, user_id=user_id, symbol=position_symbol)

    return {
        "symbol": position_symbol,
        "quantity": total_qty,
        "avg_cost": round(avg_cost, 4),
        "days_held": days_held,
    }


def apply_trade_to_position(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    symbol: str,
    name: str | None,
    direction: str,
    price: float,
    quantity: int,
    traded_at: str,
):
    """基于当前持仓快照增量应用一笔交易。

    CT-OS 允许用券商持仓截图校准 positions。普通手工录入后不能再从历史
    trades 全量重算，否则会丢掉截图校准过的成本口径。
    """
    aliases = symbol_aliases(symbol)
    position_symbol = to_tencent_symbol(symbol)
    row = conn.execute(
        """
        SELECT * FROM positions
         WHERE user_id = ? AND symbol IN (?, ?, ?)
         ORDER BY CASE symbol
                  WHEN ? THEN 0
                  WHEN ? THEN 1
                  ELSE 2
                  END
         LIMIT 1
        """,
        (user_id, *aliases, aliases[0], aliases[1]),
    ).fetchone()
    current = dict(row) if row else None

    current_qty = int(current["quantity"]) if current else 0
    current_avg = float(current["avg_cost"]) if current else 0.0
    current_price = current["current_price"] if current else None

    if direction == "BUY":
        next_qty = current_qty + quantity
        next_cost = (current_avg * current_qty) + (price * quantity)
        next_avg = next_cost / next_qty if next_qty > 0 else 0.0
        next_price = current_price if current_price is not None else price
    elif direction == "SELL":
        next_qty = max(0, current_qty - quantity)
        next_avg = current_avg
        next_price = current_price
    else:
        raise ValueError(f"unsupported direction: {direction}")

    if next_qty <= 0:
        conn.execute(
            "DELETE FROM positions WHERE user_id = ? AND symbol IN (?, ?, ?)",
            (user_id, *aliases),
        )
        return None

    next_unrealized = None
    if next_price is not None:
        next_unrealized = round((float(next_price) - next_avg) * next_qty, 2)

    first_buy = current["entry_date"] if current and current.get("entry_date") else None
    entry_date = first_buy or (traded_at[:10] if direction == "BUY" else None)

    conn.execute(
        """
        INSERT INTO positions (
            user_id, symbol, name, quantity, avg_cost, current_price,
            unrealized_pnl, entry_date, days_held, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, 0), ?)
        ON CONFLICT(user_id, symbol) DO UPDATE SET
            name = excluded.name,
            quantity = excluded.quantity,
            avg_cost = excluded.avg_cost,
            current_price = excluded.current_price,
            unrealized_pnl = excluded.unrealized_pnl,
            entry_date = COALESCE(positions.entry_date, excluded.entry_date),
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            position_symbol,
            name or (current["name"] if current else None),
            next_qty,
            round(next_avg, 6),
            next_price,
            next_unrealized,
            entry_date,
            current["days_held"] if current else 0,
            datetime.now().isoformat(),
        ),
    )
    ensure_unknown_entry_thesis(conn, user_id=user_id, symbol=position_symbol)

    return {
        "symbol": position_symbol,
        "quantity": next_qty,
        "avg_cost": round(next_avg, 6),
        "current_price": next_price,
        "unrealized_pnl": next_unrealized,
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
