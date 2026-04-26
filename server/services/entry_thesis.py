"""Entry thesis helpers for position management.

An entry thesis records why a position was opened. Missing structure is stored
explicitly as unknown instead of being inferred later.
"""

import json
from datetime import datetime
from typing import Optional


UNKNOWN_STRATEGY = "未知"


def build_entry_thesis_from_trade(
    *,
    trade_id: Optional[int],
    symbol: str,
    source: str,
    traded_at: str,
    strategy_type: Optional[str] = None,
    entry_level: Optional[str] = None,
    entry_zg: Optional[float] = None,
    entry_zd: Optional[float] = None,
    m5_entry_zg: Optional[float] = None,
    original_stop_loss: Optional[float] = None,
    initial_target: Optional[float] = None,
    reason_text: Optional[str] = None,
    reason_category: Optional[str] = None,
    trend_direction: Optional[str] = None,
    trigger_conditions: Optional[list] = None,
) -> dict:
    resolved_strategy = strategy_type or UNKNOWN_STRATEGY
    resolved_level = entry_level or ("5m" if m5_entry_zg else "UNKNOWN")
    center_zg = entry_zg if entry_zg is not None else m5_entry_zg
    degradation = []
    if resolved_strategy == UNKNOWN_STRATEGY:
        degradation.append("missing_strategy")
    if center_zg is None and entry_zd is None:
        degradation.append("missing_structure")

    return {
        "schema_version": 1,
        "symbol": symbol,
        "source": source,
        "trade_id": trade_id,
        "created_at": datetime.now().isoformat(),
        "entry_at": traded_at,
        "strategy_type": resolved_strategy,
        "entry_level": resolved_level,
        "entry_center": {
            "zg": center_zg,
            "zd": entry_zd,
            "m5_zg": m5_entry_zg,
        },
        "original_stop_loss": original_stop_loss,
        "initial_target": initial_target,
        "trigger_conditions": trigger_conditions or _default_trigger_conditions(
            reason_text=reason_text,
            reason_category=reason_category,
            trend_direction=trend_direction,
        ),
        "degradation": degradation,
    }


def build_unknown_entry_thesis_from_first_buy(row: dict) -> dict:
    return build_entry_thesis_from_trade(
        trade_id=row.get("id"),
        symbol=row["symbol"],
        source=row.get("source") or "UNKNOWN",
        traded_at=row.get("traded_at") or datetime.now().isoformat(),
        strategy_type=UNKNOWN_STRATEGY,
        original_stop_loss=row.get("stop_loss_price"),
        reason_text=row.get("reason_text"),
        reason_category=row.get("reason_category"),
        trend_direction=row.get("trend_direction"),
    )


def persist_entry_thesis(
    conn,
    *,
    user_id: int,
    symbol: str,
    thesis: dict,
    strategy_type: Optional[str] = None,
    entry_date: Optional[str] = None,
    m5_entry_zg: Optional[float] = None,
) -> None:
    if not _positions_has_column(conn, "entry_thesis_json"):
        return

    existing = conn.execute(
        "SELECT entry_thesis_json FROM positions WHERE user_id = ? AND symbol = ?",
        (user_id, symbol),
    ).fetchone()
    thesis_json = json.dumps(thesis, ensure_ascii=False, sort_keys=True)
    existing_json = existing["entry_thesis_json"] if existing else None
    next_json = thesis_json if _should_replace_thesis(existing_json, thesis) else existing_json

    conn.execute(
        """
        UPDATE positions
           SET entry_thesis_json = ?,
               strategy_type = CASE
                   WHEN strategy_type IS NULL OR strategy_type = '未知' THEN ?
                   ELSE strategy_type
               END,
               entry_date = COALESCE(entry_date, ?),
               m5_entry_zg = COALESCE(m5_entry_zg, ?)
         WHERE user_id = ? AND symbol = ?
        """,
        (
            next_json,
            strategy_type or thesis.get("strategy_type") or UNKNOWN_STRATEGY,
            entry_date,
            m5_entry_zg,
            user_id,
            symbol,
        ),
    )


def ensure_unknown_entry_thesis(conn, *, user_id: int, symbol: str) -> None:
    if not _positions_has_column(conn, "entry_thesis_json"):
        return
    position = conn.execute(
        """
        SELECT entry_thesis_json
          FROM positions
         WHERE user_id = ? AND symbol = ? AND quantity > 0
        """,
        (user_id, symbol),
    ).fetchone()
    if not position or position["entry_thesis_json"]:
        return

    first_buy = conn.execute(
        """
        SELECT id, symbol, source, traded_at, stop_loss_price, reason_text,
               reason_category, trend_direction
          FROM trades
         WHERE user_id = ? AND symbol = ? AND direction = 'BUY'
         ORDER BY traded_at ASC
         LIMIT 1
        """,
        (user_id, symbol),
    ).fetchone()
    if not first_buy:
        return

    thesis = build_unknown_entry_thesis_from_first_buy(dict(first_buy))
    persist_entry_thesis(
        conn,
        user_id=user_id,
        symbol=symbol,
        thesis=thesis,
        strategy_type=UNKNOWN_STRATEGY,
        entry_date=(first_buy["traded_at"] or "")[:10],
        m5_entry_zg=None,
    )


def _default_trigger_conditions(
    *,
    reason_text: Optional[str],
    reason_category: Optional[str],
    trend_direction: Optional[str],
) -> list:
    conditions = []
    if reason_category:
        conditions.append({"type": "reason_category", "value": reason_category})
    if trend_direction:
        conditions.append({"type": "trend_direction", "value": trend_direction})
    if reason_text:
        conditions.append({"type": "reason_text", "value": reason_text})
    if not conditions:
        conditions.append({"type": "manual_record", "value": "未记录结构触发条件"})
    return conditions


def _positions_has_column(conn, column_name: str) -> bool:
    return any(row["name"] == column_name for row in conn.execute("PRAGMA table_info(positions)"))


def _should_replace_thesis(existing_json: Optional[str], new_thesis: dict) -> bool:
    if not existing_json:
        return True
    try:
        existing = json.loads(existing_json)
    except Exception:
        return True
    existing_strategy = existing.get("strategy_type") or UNKNOWN_STRATEGY
    new_strategy = new_thesis.get("strategy_type") or UNKNOWN_STRATEGY
    return existing_strategy == UNKNOWN_STRATEGY and new_strategy != UNKNOWN_STRATEGY
