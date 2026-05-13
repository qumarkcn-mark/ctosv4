"""AI Native V5 universe resolver.

V5 的股票池只来自用户数据源，不读取旧结构结果。
"""

from __future__ import annotations

from typing import Iterable

from server.db.database import get_connection
from server.domain.symbols import normalize_symbol


DEFAULT_SOURCES = ("positions", "watchlist")

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


def _merge_item(
    items: dict[str, dict],
    *,
    symbol: str,
    name: str | None,
    source: str,
    priority: int,
    has_position: bool,
    watchlist_group: str | None = None,
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
