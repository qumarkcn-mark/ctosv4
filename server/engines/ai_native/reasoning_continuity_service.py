"""Hydrate continuity facts for unified AI reasoning.

Continuity is not a trading rule. It is a compact factual bridge from the
previous reasoning run to the current market state.
"""

from __future__ import annotations

import json
from typing import Any

from server.db.database import get_connection
from server.domain.symbols import normalize_symbol
from server.engines.ai_native.scenario_outcome_service import get_memory_context_for_chat


def build_reasoning_continuity_context(
    *,
    user_id: int,
    symbol: str,
    current_price: float,
    intraday_observation: dict[str, Any] | None = None,
    prompt_versions: set[str] | None = None,
) -> dict[str, Any]:
    """Build a compact continuity object for second-stage reasoning."""
    canonical = normalize_symbol(symbol)
    previous = _previous_reasoning(
        user_id=user_id,
        symbol=canonical,
        prompt_versions=prompt_versions,
    )
    return {
        "version": "reasoning_continuity.v1",
        "previous_reasoning": previous,
        "trigger_status_since_last_run": _trigger_statuses(
            (previous.get("monitor_conditions") or {}).get("triggers") or [],
            current_price=current_price,
        ),
        "recent_user_observations": _recent_user_observations(user_id=user_id, symbol=canonical),
        "recent_outcomes": _recent_outcomes(user_id=user_id, symbol=canonical),
        "memory_context": get_memory_context_for_chat(user_id=user_id, symbol=canonical),
        "intraday_reference": _intraday_reference(intraday_observation or {}),
    }


def _previous_reasoning(
    *,
    user_id: int,
    symbol: str,
    prompt_versions: set[str] | None,
) -> dict[str, Any]:
    where = ["user_id = ?", "symbol = ?", "status = 'SUCCESS'"]
    params: list[Any] = [int(user_id), symbol]
    if prompt_versions:
        placeholders = ",".join("?" for _ in prompt_versions)
        where.append(f"prompt_version IN ({placeholders})")
        params.extend(sorted(prompt_versions))
    conn = get_connection()
    try:
        row = conn.execute(
            f"""
            SELECT run_id, context_id, prompt_version, summary_json,
                   source_snapshot_ids_json, updated_at
              FROM ai_structure_reasoning_runs
             WHERE {' AND '.join(where)}
             ORDER BY updated_at DESC, id DESC
             LIMIT 1
            """,
            params,
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {}
    summary = _loads(row["summary_json"], {})
    return {
        "run_id": row["run_id"] or "",
        "context_id": row["context_id"] or "",
        "prompt_version": row["prompt_version"] or "",
        "generated_at": summary.get("generated_at") or row["updated_at"] or "",
        "updated_at": row["updated_at"] or "",
        "data_as_of": summary.get("data_as_of") or "",
        "card_summary": summary.get("card_summary") or summary.get("coach_summary") or "",
        "card_action": summary.get("card_action") or "",
        "monitor_conditions": _normalize_monitor_conditions(summary.get("monitor_conditions") or {}),
        "source_snapshot_ids": _loads(row["source_snapshot_ids_json"], []),
    }


def _trigger_statuses(triggers: list[dict[str, Any]], *, current_price: float) -> list[dict[str, Any]]:
    price = _num(current_price)
    statuses = []
    for item in triggers[:6]:
        if not isinstance(item, dict):
            continue
        level = _num(item.get("level"))
        trigger_type = str(item.get("type") or "")
        if price <= 0 or level <= 0 or trigger_type not in {"price_above", "price_below"}:
            status = "unknown"
            distance_pct = None
        else:
            crossed = price >= level if trigger_type == "price_above" else price <= level
            status = "crossed" if crossed else "not_touched"
            distance_pct = round((price - level) / level * 100, 2)
        statuses.append(
            {
                "type": trigger_type,
                "level": level,
                "current_price": price,
                "status": status,
                "distance_pct": distance_pct,
                "message_on_trigger": item.get("message_on_trigger") or "",
                "action_on_trigger": item.get("action_on_trigger") or "",
            }
        )
    return statuses


def _recent_user_observations(*, user_id: int, symbol: str, limit: int = 5) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT message_id, question_text, intent_type, context_id, created_at
              FROM ai_structure_chat_messages
             WHERE user_id = ?
               AND symbol = ?
             ORDER BY created_at DESC, id DESC
             LIMIT ?
            """,
            (int(user_id), symbol, int(limit)),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "message_id": row["message_id"] or "",
            "created_at": row["created_at"] or "",
            "question_text": row["question_text"] or "",
            "intent_type": row["intent_type"] or "",
            "context_id": row["context_id"] or "",
        }
        for row in rows
        if row["question_text"]
    ]


def _recent_outcomes(*, user_id: int, symbol: str, limit: int = 3) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT outcome_id, checked_at, outcome, trigger_price, triggered_price,
                   invalidated_price, user_followed_plan, settlement_window
              FROM scenario_outcomes
             WHERE user_id = ?
               AND symbol = ?
             ORDER BY checked_at DESC, id DESC
             LIMIT ?
            """,
            (int(user_id), symbol, int(limit)),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "outcome_id": row["outcome_id"] or "",
            "checked_at": row["checked_at"] or "",
            "outcome": row["outcome"] or "",
            "trigger_price": row["trigger_price"],
            "triggered_price": row["triggered_price"],
            "invalidated_price": row["invalidated_price"],
            "user_followed_plan": None
            if row["user_followed_plan"] is None
            else bool(row["user_followed_plan"]),
            "settlement_window": row["settlement_window"] or "",
        }
        for row in rows
    ]


def _intraday_reference(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    return {
        "as_of": payload.get("as_of") or "",
        "coverage": payload.get("coverage") or {},
        "quote": payload.get("quote") or {},
    }


def _normalize_monitor_conditions(payload: dict[str, Any]) -> dict[str, Any]:
    triggers = payload.get("triggers") if isinstance(payload, dict) else []
    if not isinstance(triggers, list):
        triggers = []
    normalized = []
    for item in triggers[:6]:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "type": str(item.get("type") or ""),
                "level": _num(item.get("level")),
                "message_on_trigger": str(item.get("message_on_trigger") or ""),
                "action_on_trigger": str(item.get("action_on_trigger") or ""),
            }
        )
    return {"triggers": normalized}


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _num(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
