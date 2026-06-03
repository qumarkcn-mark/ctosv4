"""AI Native V5 universe resolver.

V5 的股票池只来自用户数据源，不读取旧结构结果。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from server.db.database import get_connection
from server.domain.symbols import normalize_symbol


DEFAULT_SOURCES = ("positions", "recent_chat", "watchlist")
RECENT_CHAT_LOOKBACK_DAYS = 30
MAX_RECENT_CHAT_SYMBOLS_PER_USER = 20
WATCHBOARD_VISIBLE_GROUP_NAMES = {"持仓", "自选", "备选"}

SOURCE_PRIORITIES = {
    "pin": 120,
    "position_watchlist": 110,
    "positions": 100,
    "recent_chat": 80,
    "watchlist": 60,
    "discovery": 40,
}


def resolve_ai_native_universe(user_id: int, sources: Iterable[str] | None = None) -> list[dict]:
    """Resolve user-scoped symbols for AI Native V5 background jobs."""
    selected = {str(item).strip().lower() for item in (sources or DEFAULT_SOURCES) if str(item).strip()}
    items: dict[str, dict] = {}
    if "positions" in selected:
        for row in _position_rows(user_id):
            _merge_item(
                items,
                symbol=row["symbol"],
                name=row.get("name"),
                source="positions",
                priority=SOURCE_PRIORITIES["positions"],
                has_position=True,
            )
    if "recent_chat" in selected:
        for row in _recent_chat_rows(user_id):
            canonical = normalize_symbol(row["symbol"])
            existing = items.get(canonical)
            _merge_item(
                items,
                symbol=canonical,
                name=row.get("symbol"),
                source="recent_chat",
                priority=SOURCE_PRIORITIES["recent_chat"],
                has_position=bool(existing and existing["has_position"]),
                last_chat_at=row.get("updated_at"),
            )
    if "watchlist" in selected:
        for row in _watchlist_rows(user_id):
            canonical = normalize_symbol(row["symbol"])
            existing = items.get(canonical)
            priority = SOURCE_PRIORITIES["position_watchlist"] if existing and existing["has_position"] else SOURCE_PRIORITIES["watchlist"]
            _merge_item(
                items,
                symbol=canonical,
                name=row.get("name"),
                source="watchlist",
                priority=priority,
                has_position=bool(existing and existing["has_position"]),
                watchlist_group=row.get("group_name"),
            )
    return sorted(items.values(), key=lambda item: (-int(item["priority"]), item["symbol"]))


def resolve_watchboard_universe(user_id: int) -> list[dict]:
    """Resolve symbols visible on the V5 watchboard.

    Auto full reasoning should stay inside the user's coach watchlist. Trading
    positions are only an overlay on those symbols, not membership by itself.
    """
    items: dict[str, dict] = {}
    position_symbols = {normalize_symbol(row["symbol"]) for row in _position_rows(user_id)}
    for row in _watchlist_rows(user_id):
        if str(row.get("group_name") or "") not in WATCHBOARD_VISIBLE_GROUP_NAMES:
            continue
        canonical = normalize_symbol(row["symbol"])
        has_position = canonical in position_symbols
        priority = SOURCE_PRIORITIES["position_watchlist"] if has_position else SOURCE_PRIORITIES["watchlist"]
        _merge_item(
            items,
            symbol=canonical,
            name=row.get("name"),
            source="watchboard",
            priority=priority,
            has_position=has_position,
            watchlist_group=row.get("group_name"),
        )
    return sorted(items.values(), key=lambda item: (-int(item["priority"]), item["symbol"]))


def list_ai_native_user_ids(limit: int | None = None) -> list[int]:
    """List users whose watchlist or active positions can drive V5 background jobs."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT user_id
              FROM positions
            WHERE quantity > 0
            UNION
            SELECT DISTINCT user_id
              FROM ai_structure_chat_sessions
             WHERE status = 'ACTIVE' AND updated_at >= ?
            UNION
            SELECT DISTINCT wg.user_id
              FROM watchlist_groups wg
              JOIN watchlist_items wi ON wi.group_id = wg.id
             ORDER BY user_id
            """,
            (_recent_chat_cutoff(),),
        ).fetchall()
    finally:
        conn.close()
    user_ids = [int(row["user_id"]) for row in rows if row["user_id"] is not None]
    return user_ids[: int(limit)] if limit is not None else user_ids


def list_watchboard_user_ids(limit: int | None = None) -> list[int]:
    """List users with visible watchboard symbols."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT wg.user_id
              FROM watchlist_groups wg
              JOIN watchlist_items wi ON wi.group_id = wg.id
             WHERE wg.name IN ('持仓', '自选', '备选')
             ORDER BY user_id
            """,
        ).fetchall()
    finally:
        conn.close()
    user_ids = [int(row["user_id"]) for row in rows if row["user_id"] is not None]
    return user_ids[: int(limit)] if limit is not None else user_ids


def list_interested_user_ids_for_symbol(symbol: str) -> list[int]:
    """Find users whose positions or watchlist include the symbol.

    Symbols may be stored as sh600519, sh.600519, or other UI variants, so
    normalize in Python instead of relying on raw SQL equality.
    """
    canonical = normalize_symbol(symbol)
    interested: set[int] = set()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT user_id, symbol
              FROM positions
             WHERE quantity > 0
            """
        ).fetchall()
        for row in rows:
            if normalize_symbol(row["symbol"]) == canonical:
                interested.add(int(row["user_id"]))

        rows = conn.execute(
            """
            SELECT wg.user_id, wi.symbol
              FROM watchlist_items wi
              JOIN watchlist_groups wg ON wg.id = wi.group_id
            """
        ).fetchall()
        for row in rows:
            if normalize_symbol(row["symbol"]) == canonical:
                interested.add(int(row["user_id"]))

        rows = conn.execute(
            """
            SELECT user_id, symbol
              FROM ai_structure_chat_sessions
             WHERE status = 'ACTIVE' AND updated_at >= ?
            """,
            (_recent_chat_cutoff(),),
        ).fetchall()
        for row in rows:
            if normalize_symbol(row["symbol"]) == canonical:
                interested.add(int(row["user_id"]))
    finally:
        conn.close()
    return sorted(interested)


def has_active_position_for_symbol(symbol: str) -> bool:
    """Return whether any user currently holds the symbol."""
    canonical = normalize_symbol(symbol)
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT symbol
              FROM positions
             WHERE quantity > 0
            """
        ).fetchall()
    finally:
        conn.close()
    return any(normalize_symbol(row["symbol"]) == canonical for row in rows)


def _position_rows(user_id: int) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT symbol, name
              FROM positions
             WHERE user_id = ? AND quantity > 0
             ORDER BY updated_at DESC, id DESC
            """,
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _watchlist_rows(user_id: int) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT wi.symbol, wi.name, wg.name AS group_name
              FROM watchlist_items wi
              JOIN watchlist_groups wg ON wg.id = wi.group_id
             WHERE wg.user_id = ?
             ORDER BY wg.sort_order, wi.sort_order, wi.id
            """,
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _recent_chat_rows(user_id: int) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT symbol, MAX(updated_at) AS updated_at
              FROM ai_structure_chat_sessions
             WHERE user_id = ? AND status = 'ACTIVE' AND updated_at >= ?
             GROUP BY symbol
             ORDER BY MAX(updated_at) DESC, MAX(id) DESC
             LIMIT ?
            """,
            (user_id, _recent_chat_cutoff(), MAX_RECENT_CHAT_SYMBOLS_PER_USER),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _recent_chat_cutoff() -> str:
    return (datetime.now().astimezone() - timedelta(days=RECENT_CHAT_LOOKBACK_DAYS)).isoformat(timespec="seconds")


def _merge_item(
    items: dict[str, dict],
    *,
    symbol: str,
    name: str | None,
    source: str,
    priority: int,
    has_position: bool,
    watchlist_group: str | None = None,
    last_chat_at: str | None = None,
) -> None:
    canonical = normalize_symbol(symbol)
    item = items.get(canonical)
    if item is None:
        item = {
            "symbol": canonical,
            "name": name or canonical,
            "sources": [],
            "priority": int(priority),
            "has_position": bool(has_position),
        }
        items[canonical] = item
    if source not in item["sources"]:
        item["sources"].append(source)
    if name and (not item.get("name") or item["name"] == canonical):
        item["name"] = name
    item["priority"] = max(int(item["priority"]), int(priority))
    item["has_position"] = bool(item["has_position"] or has_position)
    if watchlist_group:
        groups = item.setdefault("watchlist_groups", [])
        if watchlist_group not in groups:
            groups.append(watchlist_group)
    if last_chat_at:
        item["last_chat_at"] = max(str(item.get("last_chat_at") or ""), str(last_chat_at))
