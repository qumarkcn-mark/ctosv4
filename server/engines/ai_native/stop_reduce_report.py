"""Read-only report builder for AI stop/reduce training."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any


DISCLAIMER = "仅供参考，不构成投资建议"


def build_stop_reduce_training_report(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    symbol: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Build a stable UI payload without mutating training state."""

    limit = max(1, min(int(limit or 50), 200))
    plans = _load_holding_plans(conn, user_id=user_id, symbol=symbol, limit=limit)
    intents = _load_intents(conn, user_id=user_id, symbol=symbol, limit=limit)
    case_memory = _load_case_memory(conn, user_id=user_id, symbol=symbol, limit=limit)
    calibration = _load_calibration(conn, user_id=user_id, limit=limit)
    overview = _build_overview(plans=plans, intents=intents, case_memory=case_memory)

    return {
        "overview": overview,
        "plans": plans,
        "intents": intents,
        "case_memory": case_memory,
        "calibration": calibration,
        "disclaimer": DISCLAIMER,
    }


def build_stop_reduce_training_status(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    today: str | None = None,
) -> dict[str, Any]:
    """Build the control-plane status for the report page."""
    today = today or date.today().isoformat()
    latest_run = _load_latest_daily_run(conn, user_id=user_id)
    today_run = _load_latest_daily_run(conn, user_id=user_id, run_date=today)
    today_counts = _load_today_counts(conn, user_id=user_id, today=today)
    return {
        "today": today,
        "has_run_today": bool(today_run),
        "today_run": today_run,
        "latest_run": latest_run,
        "today_counts": today_counts,
        "disclaimer": DISCLAIMER,
    }


def _load_latest_daily_run(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    run_date: str | None = None,
) -> dict[str, Any] | None:
    clauses = ["user_id = ?"]
    params: list[Any] = [user_id]
    if run_date:
        clauses.append("run_date = ?")
        params.append(run_date)
    row = conn.execute(
        f"""
        SELECT run_id, user_id, run_date, trigger, mode, status, started_at,
               completed_at, plans_saved, intents_enqueued, intents_settled,
               case_memory_writes, error, summary_json, disclaimer
          FROM ai_stop_reduce_daily_runs
         WHERE {' AND '.join(clauses)}
         ORDER BY started_at DESC
         LIMIT 1
        """,
        params,
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    item["summary"] = _json_dict(item.pop("summary_json", ""))
    return item


def _load_today_counts(conn: sqlite3.Connection, *, user_id: int, today: str) -> dict[str, int]:
    plan_count = conn.execute(
        "SELECT COUNT(*) AS count FROM ai_holding_plans WHERE user_id = ? AND trade_date = ?",
        (user_id, today),
    ).fetchone()["count"]
    intent_count = conn.execute(
        "SELECT COUNT(*) AS count FROM ai_rebalance_intents WHERE user_id = ? AND substr(as_of, 1, 10) = ?",
        (user_id, today),
    ).fetchone()["count"]
    score_count = conn.execute(
        """
        SELECT COUNT(*) AS count
          FROM ai_stop_reduce_scores
         WHERE user_id = ? AND substr(scored_at, 1, 10) = ?
        """,
        (user_id, today),
    ).fetchone()["count"]
    return {
        "plans": int(plan_count or 0),
        "intents": int(intent_count or 0),
        "scores": int(score_count or 0),
    }


def _load_holding_plans(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    symbol: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    clauses = ["user_id = ?"]
    params: list[Any] = [user_id]
    if symbol:
        variants = _symbol_variants(symbol)
        clauses.append(f"symbol IN ({','.join('?' for _ in variants)})")
        params.extend(variants)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT plan_id, user_id, symbol, trade_date, as_of, radar_run_id,
               plan_status, current_script, target_weight_pct, max_position_pct,
               defense_line, repair_line, trigger_conditions_json,
               cancel_conditions_json, observation_focus_json, evidence_refs_json,
               disclaimer, created_at
          FROM ai_holding_plans
         WHERE {' AND '.join(clauses)}
         ORDER BY trade_date DESC, as_of DESC, created_at DESC
         LIMIT ?
        """,
        params,
    ).fetchall()
    return [_normalize_plan(dict(row)) for row in rows]


def _load_intents(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    symbol: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    clauses = ["i.user_id = ?", "i.intent_type = 'STOP_REDUCE'"]
    params: list[Any] = [user_id]
    if symbol:
        variants = _symbol_variants(symbol)
        clauses.append(f"i.symbol IN ({','.join('?' for _ in variants)})")
        params.extend(variants)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT i.intent_id, i.run_id, i.source_plan_id, i.user_id, i.symbol,
               i.intent_type, i.action, i.current_weight_pct, i.target_weight_pct,
               i.quantity_policy, i.idempotency_key, i.as_of, i.conditions_json,
               i.reason_json, i.evidence_refs_json, i.disclaimer, i.created_at,
               s.score_id, s.outcome_score, s.process_score, s.final_score,
               s.settlement_window, s.settlement_source, s.settlement_prices_json,
               s.tags_json, s.lesson_candidate, s.notes,
               r.status AS run_status
          FROM ai_rebalance_intents i
          LEFT JOIN ai_stop_reduce_scores s ON s.intent_id = i.intent_id
          LEFT JOIN ai_rebalance_runs r ON r.run_id = i.run_id
         WHERE {' AND '.join(clauses)}
         ORDER BY i.as_of DESC, i.created_at DESC
         LIMIT ?
        """,
        params,
    ).fetchall()
    return [_normalize_intent(dict(row)) for row in rows]


def _load_case_memory(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    symbol: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    clauses = ["user_id = ?"]
    params: list[Any] = [user_id]
    if symbol:
        variants = _symbol_variants(symbol)
        clauses.append(f"symbol IN ({','.join('?' for _ in variants)})")
        params.extend(variants)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT case_id, case_key, user_id, symbol, intent_id, mistake_type,
               original_action, better_action, outcome, loss_delta_pct, lesson,
               context_hint, metadata_json, created_at
          FROM ai_case_memory
         WHERE {' AND '.join(clauses)}
         ORDER BY created_at DESC
         LIMIT ?
        """,
        params,
    ).fetchall()
    return [_normalize_case(dict(row)) for row in rows]


def _load_calibration(conn: sqlite3.Connection, *, user_id: int, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT calibration_key, user_id, total_count, mistake_count,
               avg_loss_if_hold_pct, avg_benefit_if_reduce_pct,
               latest_mistake_case_id, updated_at
          FROM ai_calibration_stats
         WHERE user_id = ?
         ORDER BY updated_at DESC
         LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def _build_overview(
    *,
    plans: list[dict[str, Any]],
    intents: list[dict[str, Any]],
    case_memory: list[dict[str, Any]],
) -> dict[str, Any]:
    settled = [item for item in intents if item.get("score")]
    waiting = [item for item in intents if not item.get("score")]
    alert_plans = [
        item for item in plans
        if item.get("plan_status") in {"REDUCE_ALERT", "EXIT_ALERT"}
    ]
    lesson_intents = [
        item for item in intents
        if (item.get("score") or {}).get("lesson_candidate")
    ]
    avg_score = None
    if settled:
        avg_score = round(sum((item["score"] or {}).get("final_score") or 0 for item in settled) / len(settled), 1)
    return {
        "plans": len(plans),
        "alert_plans": len(alert_plans),
        "intents": len(intents),
        "settled": len(settled),
        "waiting": len(waiting),
        "case_memory_writes": len(case_memory),
        "lesson_candidates": len(lesson_intents),
        "avg_final_score": avg_score,
    }


def _normalize_plan(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "trigger_conditions": _json_list(row.pop("trigger_conditions_json", "")),
        "cancel_conditions": _json_list(row.pop("cancel_conditions_json", "")),
        "observation_focus": _json_list(row.pop("observation_focus_json", "")),
        "evidence_refs": _json_dict(row.pop("evidence_refs_json", "")),
    }


def _normalize_intent(row: dict[str, Any]) -> dict[str, Any]:
    score = None
    if row.get("score_id"):
        score = {
            "score_id": row.pop("score_id"),
            "outcome_score": row.pop("outcome_score"),
            "process_score": row.pop("process_score"),
            "final_score": row.pop("final_score"),
            "settlement_window": row.pop("settlement_window"),
            "settlement_source": row.pop("settlement_source"),
            "settlement_prices": _json_list(row.pop("settlement_prices_json", "")),
            "tags": _json_list(row.pop("tags_json", "")),
            "lesson_candidate": bool(row.pop("lesson_candidate") or 0),
            "notes": row.pop("notes"),
        }
    else:
        for key in (
            "score_id", "outcome_score", "process_score", "final_score",
            "settlement_window", "settlement_source", "settlement_prices_json",
            "tags_json", "lesson_candidate", "notes",
        ):
            row.pop(key, None)
    return {
        **row,
        "conditions": _json_dict(row.pop("conditions_json", "")),
        "reason": _json_dict(row.pop("reason_json", "")),
        "evidence_refs": _json_dict(row.pop("evidence_refs_json", "")),
        "score": score,
        "settlement_status": "SETTLED" if score else "WAITING",
    }


def _normalize_case(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "metadata": _json_dict(row.pop("metadata_json", "")),
    }


def _json_dict(value: Any) -> dict[str, Any]:
    loaded = _loads(value)
    return loaded if isinstance(loaded, dict) else {}


def _json_list(value: Any) -> list[Any]:
    loaded = _loads(value)
    return loaded if isinstance(loaded, list) else []


def _loads(value: Any) -> Any:
    if not value:
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}


def _symbol_variants(symbol: str) -> list[str]:
    raw = str(symbol or "").strip()
    variants = []
    for item in (
        raw,
        f"{raw[:2].lower()}.{raw[2:]}" if len(raw) == 8 and raw[:2].lower() in {"sh", "sz"} else "",
        raw.replace(".", "") if "." in raw else "",
    ):
        if item and item not in variants:
            variants.append(item)
    return variants or [raw]
