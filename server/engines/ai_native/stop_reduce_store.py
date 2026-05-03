"""Persistence helpers for AI stop/reduce shadow training."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, is_dataclass
from typing import Any

from server.engines.ai_native.stop_reduce_training import (
    RebalanceIntent,
    StopReduceCondition,
    StopReduceConditions,
    StopReduceScore,
)


def save_rebalance_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    user_id: int,
    symbol: str,
    as_of: str,
    radar_run_id: int | None = None,
    technical_view: dict | None = None,
    fundamental_snapshot_id: str = "",
    calibration_summary: dict | None = None,
    status: str = "CREATED",
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO ai_rebalance_runs (
            run_id, user_id, symbol, as_of, mode, radar_run_id, technical_view_json,
            fundamental_snapshot_id, calibration_summary_json, status
        )
        VALUES (?, ?, ?, ?, 'STOP_REDUCE', ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            user_id,
            symbol,
            as_of,
            radar_run_id,
            _json(technical_view or {}),
            fundamental_snapshot_id,
            _json(calibration_summary or {}),
            status,
        ),
    )


def save_rebalance_intent(conn: sqlite3.Connection, intent: RebalanceIntent, *, run_id: str) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO ai_rebalance_intents (
            intent_id, run_id, user_id, symbol, intent_type, action, current_weight_pct,
            target_weight_pct, quantity_policy, idempotency_key, as_of, conditions_json,
            reason_json, evidence_refs_json, disclaimer
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            intent.intent_id,
            run_id,
            intent.user_id,
            intent.symbol,
            intent.intent_type,
            intent.action,
            intent.current_weight_pct,
            intent.target_weight_pct,
            intent.quantity_policy,
            intent.idempotency_key,
            intent.as_of,
            _json(intent.conditions),
            _json(intent.reason),
            _json(intent.evidence_refs),
            intent.disclaimer,
        ),
    )


def load_pending_rebalance_intents(
    conn: sqlite3.Connection,
    *,
    user_id: int | None = None,
    symbol: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Load stop/reduce intents that do not have a score yet."""
    limit = max(1, min(limit, 500))
    clauses = ["i.intent_type = 'STOP_REDUCE'", "s.score_id IS NULL"]
    params: list[Any] = []
    if user_id is not None:
        clauses.append("i.user_id = ?")
        params.append(user_id)
    if symbol:
        variants = _symbol_variants(symbol)
        clauses.append(f"i.symbol IN ({','.join('?' for _ in variants)})")
        params.extend(variants)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT i.*, r.status AS run_status
          FROM ai_rebalance_intents i
          LEFT JOIN ai_stop_reduce_scores s ON s.intent_id = i.intent_id
          LEFT JOIN ai_rebalance_runs r ON r.run_id = i.run_id
         WHERE {' AND '.join(clauses)}
         ORDER BY i.as_of ASC, i.created_at ASC
         LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def intent_from_row(row: dict[str, Any]) -> RebalanceIntent:
    conditions = _conditions_from_json(_loads(row.get("conditions_json")))
    return RebalanceIntent(
        intent_type="STOP_REDUCE",
        intent_id=str(row["intent_id"]),
        idempotency_key=str(row["idempotency_key"]),
        user_id=int(row["user_id"]),
        symbol=str(row["symbol"]),
        action=row["action"],
        current_weight_pct=float(row["current_weight_pct"] or 0),
        target_weight_pct=float(row["target_weight_pct"] or 0),
        quantity_policy=str(row["quantity_policy"] or ""),
        as_of=str(row["as_of"]),
        conditions=conditions,
        reason=_loads(row.get("reason_json")),
        evidence_refs=_loads(row.get("evidence_refs_json")),
        disclaimer=str(row.get("disclaimer") or "仅供参考，不构成投资建议"),
    )


def save_stop_reduce_score(
    conn: sqlite3.Connection,
    score: StopReduceScore,
    *,
    user_id: int,
    symbol: str,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO ai_stop_reduce_scores (
            score_id, intent_id, user_id, symbol, outcome_score, process_score,
            final_score, settlement_window, settlement_source, settlement_prices_json,
            tags_json, lesson_candidate, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            score.score_id,
            score.intent_id,
            user_id,
            symbol,
            score.outcome_score,
            score.process_score,
            score.final_score,
            score.settlement_window,
            score.settlement_source,
            _json(score.settlement_prices),
            _json(score.tags),
            1 if score.lesson_candidate else 0,
            score.notes,
        ),
    )


def save_case_memory(
    conn: sqlite3.Connection,
    *,
    case_id: str,
    case_key: str,
    user_id: int,
    symbol: str,
    intent_id: str,
    mistake_type: str,
    original_action: str,
    better_action: str,
    outcome: str,
    loss_delta_pct: float,
    lesson: str,
    context_hint: str = "",
    metadata: dict | None = None,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO ai_case_memory (
            case_id, case_key, user_id, symbol, intent_id, mistake_type,
            original_action, better_action, outcome, loss_delta_pct, lesson,
            context_hint, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            case_id,
            case_key,
            user_id,
            symbol,
            intent_id,
            mistake_type,
            original_action,
            better_action,
            outcome,
            loss_delta_pct,
            lesson,
            context_hint,
            _json(metadata or {}),
        ),
    )


def upsert_calibration_stats(
    conn: sqlite3.Connection,
    *,
    calibration_key: str,
    user_id: int,
    total_count: int,
    mistake_count: int,
    avg_loss_if_hold_pct: float,
    avg_benefit_if_reduce_pct: float = 0.0,
    latest_mistake_case_id: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO ai_calibration_stats (
            calibration_key, user_id, total_count, mistake_count, avg_loss_if_hold_pct,
            avg_benefit_if_reduce_pct, latest_mistake_case_id, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(calibration_key) DO UPDATE SET
            user_id=excluded.user_id,
            total_count=excluded.total_count,
            mistake_count=excluded.mistake_count,
            avg_loss_if_hold_pct=excluded.avg_loss_if_hold_pct,
            avg_benefit_if_reduce_pct=excluded.avg_benefit_if_reduce_pct,
            latest_mistake_case_id=excluded.latest_mistake_case_id,
            updated_at=CURRENT_TIMESTAMP
        """,
        (
            calibration_key,
            user_id,
            total_count,
            mistake_count,
            avg_loss_if_hold_pct,
            avg_benefit_if_reduce_pct,
            latest_mistake_case_id,
        ),
    )


def load_calibration_stats(conn: sqlite3.Connection, calibration_key: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT calibration_key, user_id, total_count, mistake_count,
               avg_loss_if_hold_pct, avg_benefit_if_reduce_pct,
               latest_mistake_case_id, updated_at
          FROM ai_calibration_stats
         WHERE calibration_key = ?
        """,
        (calibration_key,),
    ).fetchone()
    return dict(row) if row else {}


def load_latest_case(conn: sqlite3.Connection, *, user_id: int, case_key: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT case_id, case_key, user_id, symbol, intent_id, mistake_type,
               original_action, better_action, outcome, loss_delta_pct,
               lesson, context_hint, metadata_json, created_at
          FROM ai_case_memory
         WHERE user_id = ? AND case_key = ?
         ORDER BY created_at DESC
         LIMIT 1
        """,
        (user_id, case_key),
    ).fetchone()
    return dict(row) if row else {}


def _conditions_from_json(payload: dict[str, Any]) -> StopReduceConditions:
    return StopReduceConditions(
        activate_if=[
            StopReduceCondition(**item)
            for item in (payload.get("activate_if") or [])
            if isinstance(item, dict)
        ],
        cancel_if=[
            StopReduceCondition(**item)
            for item in (payload.get("cancel_if") or [])
            if isinstance(item, dict)
        ],
        expires_on=str(payload.get("expires_on") or ""),
    )


def _loads(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


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


def _json(value: Any) -> str:
    if is_dataclass(value):
        value = asdict(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
