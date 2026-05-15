"""AI Native V5 scheduled scenario outcome settlement."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from server.db.database import get_connection
from server.domain.symbols import normalize_symbol, to_tencent_symbol
from server.engines.ai_native.czsc_snapshot_service import now_text
from server.engines.ai_native.scenario_outcome_service import settle_scenario_branch

DEFAULT_SETTLEMENT_WINDOWS = ("same_day", "next_day", "3d", "5d")
MIN_SAME_DAY_SETTLEMENT_AGE = timedelta(minutes=30)


def list_due_outcome_symbols(
    *,
    windows: tuple[str, ...] = DEFAULT_SETTLEMENT_WINDOWS,
    checked_at: str | None = None,
    limit: int = 200,
) -> list[str]:
    """Return symbols with active branches that have at least one due settlement window."""
    due = _due_branch_windows(windows=windows, checked_at=checked_at, limit=limit)
    return sorted({item["symbol"] for item in due})


def settle_due_scenario_outcomes(
    price_by_symbol: dict[str, dict],
    *,
    windows: tuple[str, ...] = DEFAULT_SETTLEMENT_WINDOWS,
    checked_at: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Settle due active branches using a current-price map.

    This worker settlement is behavior-neutral: it updates branch outcomes and
    memory stats, but never marks whether the user followed a plan. Mistake
    memory still only comes from explicit user follow-up.
    """
    prices = _normalize_price_map(price_by_symbol)
    if not prices:
        return {"count": 0, "items": []}
    checked = checked_at or now_text()
    due = _due_branch_windows(windows=windows, checked_at=checked, limit=limit)
    settled: list[dict[str, Any]] = []
    for item in due:
        price = prices.get(item["symbol"])
        if price is None:
            continue
        outcome = settle_scenario_branch(
            user_id=int(item["user_id"]),
            branch_id=item["branch_id"],
            current_price=price,
            settlement_window=item["settlement_window"],
            checked_at=checked,
            user_followed_plan=None,
        )
        if not outcome:
            continue
        settled.append({
            "user_id": int(item["user_id"]),
            "symbol": item["symbol"],
            "branch_id": item["branch_id"],
            "settlement_window": item["settlement_window"],
            "current_price": price,
            "outcome": outcome,
        })
    return {"count": len(settled), "items": settled}


def _due_branch_windows(
    *,
    windows: tuple[str, ...],
    checked_at: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    if not windows or limit <= 0:
        return []
    checked = _parse_time(checked_at or now_text()) or datetime.now()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT b.*
              FROM scenario_branches b
             WHERE b.status = 'ACTIVE'
             ORDER BY b.updated_at ASC, b.id ASC
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        due: list[dict[str, Any]] = []
        for row in rows:
            branch = dict(row)
            created = _parse_time(branch.get("created_at")) or checked
            existing = _existing_windows(
                conn,
                user_id=int(branch["user_id"]),
                branch_id=branch["branch_id"],
            )
            for window in windows:
                normalized_window = str(window or "").strip()
                if not normalized_window or normalized_window in existing:
                    continue
                if not _is_window_due(normalized_window, created_at=created, checked_at=checked):
                    continue
                due.append({
                    "user_id": int(branch["user_id"]),
                    "symbol": normalize_symbol(branch["symbol"]),
                    "branch_id": branch["branch_id"],
                    "settlement_window": normalized_window,
                })
                break
        return due
    finally:
        conn.close()


def _existing_windows(conn, *, user_id: int, branch_id: str) -> set[str]:
    rows = conn.execute(
        """
        SELECT settlement_window
          FROM scenario_outcomes
         WHERE user_id = ? AND branch_id = ?
        """,
        (int(user_id), branch_id),
    ).fetchall()
    return {row["settlement_window"] for row in rows}


def _is_window_due(window: str, *, created_at: datetime, checked_at: datetime) -> bool:
    if checked_at < created_at:
        return False
    if window == "same_day":
        return checked_at.date() > created_at.date() or checked_at >= created_at + MIN_SAME_DAY_SETTLEMENT_AGE
    if window == "next_day":
        return checked_at >= created_at + timedelta(days=1)
    if window.endswith("d"):
        try:
            days = int(window[:-1])
        except ValueError:
            return False
        return checked_at >= created_at + timedelta(days=days)
    if window.startswith("bars:"):
        try:
            bars = int(window.split(":", 1)[1])
        except ValueError:
            return False
        return checked_at >= created_at + timedelta(days=max(bars, 0))
    return False


def _normalize_price_map(price_by_symbol: dict[str, dict]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for raw_symbol, payload in (price_by_symbol or {}).items():
        price = _num((payload or {}).get("price"))
        if price <= 0:
            continue
        symbol = normalize_symbol(raw_symbol)
        normalized[symbol] = price
        normalized[to_tencent_symbol(symbol)] = price
    return normalized


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
