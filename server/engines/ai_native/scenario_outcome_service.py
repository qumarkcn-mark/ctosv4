"""AI Native V5 scenario outcome and memory settlement."""

from __future__ import annotations

import json
from typing import Any

from server.db.database import get_connection
from server.domain.symbols import normalize_symbol
from server.engines.ai_native.czsc_snapshot_service import now_text, stable_hash


def settle_scenario_branch(
    *,
    user_id: int,
    branch_id: str,
    current_price: float | None = None,
    settlement_window: str = "manual",
    checked_at: str | None = None,
    user_followed_plan: bool | None = None,
    expired: bool = False,
) -> dict[str, Any] | None:
    branch = _load_branch(user_id=user_id, branch_id=branch_id)
    if not branch:
        return None
    price = _num(current_price)
    outcome = _outcome_for_branch(branch, price=price, expired=expired)
    checked = checked_at or now_text()
    outcome_id = f"v5out_{stable_hash({'user_id': user_id, 'branch_id': branch_id, 'window': settlement_window, 'checked_at': checked})[:16]}"
    score = _outcome_score(outcome)
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO scenario_outcomes (
                outcome_id, user_id, branch_id, symbol, checked_at, outcome,
                outcome_score, settlement_window, trigger_price, triggered_price,
                invalidated_price, expired_at, user_followed_plan, notes, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, branch_id, settlement_window, checked_at)
            DO UPDATE SET
                outcome = excluded.outcome,
                outcome_score = excluded.outcome_score,
                trigger_price = excluded.trigger_price,
                triggered_price = excluded.triggered_price,
                invalidated_price = excluded.invalidated_price,
                expired_at = excluded.expired_at,
                user_followed_plan = excluded.user_followed_plan,
                notes = excluded.notes
            """,
            (
                outcome_id,
                int(user_id),
                branch_id,
                branch["symbol"],
                checked,
                outcome,
                score,
                settlement_window,
                price if price > 0 else None,
                price if outcome == "triggered" else None,
                price if outcome == "invalidated" else None,
                checked if outcome == "expired" else None,
                None if user_followed_plan is None else int(bool(user_followed_plan)),
                _notes(branch, outcome, price),
                now_text(),
            ),
        )
        conn.execute(
            "UPDATE scenario_branches SET status = ?, updated_at = ? WHERE branch_id = ? AND user_id = ?",
            (_branch_status(outcome), now_text(), branch_id, int(user_id)),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM scenario_outcomes WHERE outcome_id = ?", (outcome_id,)).fetchone()
    finally:
        conn.close()
    result = _outcome_row(row)
    update_symbol_memory_profile(user_id=user_id, symbol=branch["symbol"])
    return result


def get_symbol_memory_profile(*, user_id: int, symbol: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM ai_symbol_memory_profiles WHERE user_id = ? AND symbol = ?",
            (int(user_id), normalize_symbol(symbol)),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["profile"] = json.loads(data.pop("profile_json") or "{}")
        data["stats"] = json.loads(data.pop("stats_json") or "{}")
        return data
    finally:
        conn.close()


def update_symbol_memory_profile(*, user_id: int, symbol: str) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT outcome, outcome_score, user_followed_plan
              FROM scenario_outcomes
             WHERE user_id = ? AND symbol = ?
            """,
            (int(user_id), canonical),
        ).fetchall()
        total = len(rows)
        counts = {"triggered": 0, "invalidated": 0, "expired": 0, "pending": 0}
        followed = 0
        followed_known = 0
        score_total = 0.0
        for row in rows:
            outcome = row["outcome"]
            counts[outcome] = counts.get(outcome, 0) + 1
            score_total += float(row["outcome_score"] or 0)
            if row["user_followed_plan"] is not None:
                followed_known += 1
                followed += int(row["user_followed_plan"] or 0)
        stats = {
            "total_outcomes": total,
            "triggered": counts.get("triggered", 0),
            "invalidated": counts.get("invalidated", 0),
            "expired": counts.get("expired", 0),
            "avg_outcome_score": round(score_total / total, 4) if total else 0,
            "plan_follow_rate": round(followed / followed_known, 4) if followed_known else None,
        }
        profile = {
            "symbol": canonical,
            "memory_version": "ai_symbol_memory.v1",
            "summary": _memory_summary(stats),
        }
        now = now_text()
        conn.execute(
            """
            INSERT INTO ai_symbol_memory_profiles (
                user_id, symbol, profile_json, stats_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, symbol)
            DO UPDATE SET
                profile_json = excluded.profile_json,
                stats_json = excluded.stats_json,
                updated_at = excluded.updated_at
            """,
            (
                int(user_id),
                canonical,
                json.dumps(profile, ensure_ascii=False, sort_keys=True),
                json.dumps(stats, ensure_ascii=False, sort_keys=True),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_symbol_memory_profile(user_id=user_id, symbol=canonical)


def _load_branch(*, user_id: int, branch_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM scenario_branches WHERE user_id = ? AND branch_id = ?",
            (int(user_id), branch_id),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["trigger_condition"] = json.loads(data.pop("trigger_condition_json") or "{}")
        data["invalidate_condition"] = json.loads(data.pop("invalidate_condition_json") or "{}")
        data["evidence_refs"] = json.loads(data.pop("evidence_refs_json") or "[]")
        return data
    finally:
        conn.close()


def _outcome_for_branch(branch: dict[str, Any], *, price: float, expired: bool) -> str:
    if expired:
        return "expired"
    trigger = branch.get("trigger_condition") or {}
    invalid = branch.get("invalidate_condition") or {}
    if _condition_met(trigger, price):
        return "triggered"
    if _condition_met(invalid, price):
        return "invalidated"
    return "pending"


def _condition_met(condition: dict[str, Any], price: float) -> bool:
    if price <= 0:
        return False
    target = _num(condition.get("price"))
    if target <= 0:
        return False
    kind = condition.get("type")
    if kind == "price_above":
        return price >= target
    if kind == "price_below":
        return price <= target
    return False


def _outcome_score(outcome: str) -> float:
    return {"triggered": 1.0, "invalidated": -1.0, "expired": 0.0, "pending": 0.0}.get(outcome, 0.0)


def _branch_status(outcome: str) -> str:
    return {"triggered": "TRIGGERED", "invalidated": "INVALIDATED", "expired": "EXPIRED"}.get(outcome, "ACTIVE")


def _notes(branch: dict[str, Any], outcome: str, price: float) -> str:
    return f"{branch['branch_type']} settled as {outcome} at {price:.2f}" if price > 0 else f"{branch['branch_type']} settled as {outcome}"


def _memory_summary(stats: dict[str, Any]) -> str:
    total = stats.get("total_outcomes") or 0
    if total <= 0:
        return "暂无可用结构复盘样本。"
    return f"已记录 {total} 次结构分支复盘，触发 {stats.get('triggered', 0)} 次，失效 {stats.get('invalidated', 0)} 次。"


def _outcome_row(row) -> dict[str, Any]:
    return dict(row)


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
