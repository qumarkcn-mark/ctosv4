"""Observation and manual scoring loop for AI Native Radar."""

from __future__ import annotations

import json
import logging
from typing import Optional

from server.db.database import get_connection
from server.engines.ai_native.replay_evaluator import evaluate_reasoning_outcome, should_settle_created_at
from server.engines.ai_native.schemas import AINativeRunSummary, ObservationSummary

logger = logging.getLogger(__name__)

TARGET_REVIEW_COUNT = 20


def list_reasoning_runs(
    *,
    user_id: int,
    limit: int = 50,
    symbol: Optional[str] = None,
    replay_status: Optional[str] = None,
) -> list[AINativeRunSummary]:
    """List recent AI Native Radar samples for human review."""
    limit = max(1, min(limit, 200))
    clauses = ["user_id = ?"]
    params: list[object] = [user_id]
    if symbol:
        clauses.append("symbol = ?")
        params.append(symbol)
    if replay_status:
        clauses.append("replay_status = ?")
        params.append(replay_status)
    params.append(limit)

    sql = f"""
        SELECT id, user_id, symbol, mode, created_at, prompt_version, model_name,
               structure_fingerprint, ai_output_json, gate_result_json, gate_status, model_route_json,
               replay_status, replay_score, outcome_json
          FROM ai_reasoning_runs
         WHERE {' AND '.join(clauses)}
         ORDER BY created_at DESC, id DESC
         LIMIT ?
    """
    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_summary(row) for row in rows]
    finally:
        conn.close()


def review_reasoning_run(
    *,
    run_id: int,
    user_id: int,
    actual_hypothesis: str,
    quality_score: int,
    notes: str = "",
    outcome_path: Optional[str] = None,
    reviewer: str = "human",
) -> AINativeRunSummary:
    """Attach a human replay review to one run and compute replay_score."""
    if actual_hypothesis not in {"A", "B", "C", "D", "UNKNOWN"}:
        raise ValueError("actual_hypothesis must be A/B/C/D/UNKNOWN")
    if quality_score < 0 or quality_score > 10:
        raise ValueError("quality_score must be between 0 and 10")

    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT id, user_id, symbol, mode, created_at, prompt_version, model_name,
                   structure_fingerprint, ai_output_json, gate_result_json, gate_status, model_route_json,
                   replay_status, replay_score, outcome_json
              FROM ai_reasoning_runs
             WHERE id = ? AND user_id = ?
            """,
            (run_id, user_id),
        ).fetchone()
        if not row:
            raise LookupError("AI Native Radar run not found")

        current = _row_to_summary(row).current_hypothesis
        matched = current == actual_hypothesis if actual_hypothesis != "UNKNOWN" else None
        score = _replay_score(
            gate_score=_safe_int(_loads(row["gate_result_json"]).get("score")),
            quality_score=quality_score,
            matched=matched,
        )
        outcome = {
            "actual_hypothesis": actual_hypothesis,
            "predicted_hypothesis": current,
            "matched": matched,
            "path": outcome_path or actual_hypothesis,
            "quality_score": quality_score,
            "notes": notes,
            "reviewer": reviewer,
        }
        conn.execute(
            """
            UPDATE ai_reasoning_runs
               SET replay_status = 'REVIEWED',
                   replay_score = ?,
                   outcome_json = ?
             WHERE id = ? AND user_id = ?
            """,
            (score, json.dumps(outcome, ensure_ascii=False), run_id, user_id),
        )
        conn.commit()
        reviewed = conn.execute(
            """
            SELECT id, user_id, symbol, mode, created_at, prompt_version, model_name,
                   structure_fingerprint, ai_output_json, gate_result_json, gate_status, model_route_json,
                   replay_status, replay_score, outcome_json
              FROM ai_reasoning_runs
             WHERE id = ? AND user_id = ?
            """,
            (run_id, user_id),
        ).fetchone()
        return _row_to_summary(reviewed)
    finally:
        conn.close()


def summarize_observation(*, user_id: int, target_review_count: int = TARGET_REVIEW_COUNT) -> ObservationSummary:
    """Summarize whether the AI commander experience is stable enough."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT gate_status, gate_result_json, replay_status, replay_score
              FROM ai_reasoning_runs
             WHERE user_id = ?
            """,
            (user_id,),
        ).fetchall()
    finally:
        conn.close()

    total = len(rows)
    reviewed = sum(1 for row in rows if row["replay_status"] == "REVIEWED")
    pending = sum(1 for row in rows if row["replay_status"] == "PENDING")
    pass_runs = sum(1 for row in rows if row["gate_status"] == "PASS")
    fallback_runs = sum(1 for row in rows if row["gate_status"] == "FALLBACK")
    gate_scores = [_safe_int(_loads(row["gate_result_json"]).get("score")) for row in rows]
    replay_scores = [float(row["replay_score"]) for row in rows if row["replay_score"] is not None]

    avg_gate = round(sum(gate_scores) / len(gate_scores), 2) if gate_scores else 0.0
    avg_replay = round(sum(replay_scores) / len(replay_scores), 2) if replay_scores else 0.0
    pass_rate = round(pass_runs / total, 4) if total else 0.0
    fallback_rate = round(fallback_runs / total, 4) if total else 0.0
    ready = reviewed >= target_review_count and avg_replay >= 75 and fallback_rate <= 0.25
    reason = _readiness_reason(reviewed, target_review_count, avg_replay, fallback_rate)

    return ObservationSummary(
        total_runs=total,
        reviewed_runs=reviewed,
        pending_runs=pending,
        pass_runs=pass_runs,
        fallback_runs=fallback_runs,
        average_gate_score=avg_gate,
        average_replay_score=avg_replay,
        pass_rate=pass_rate,
        fallback_rate=fallback_rate,
        ready_for_ui_beta=ready,
        readiness_reason=reason,
        target_review_count=target_review_count,
    )


def pending_runs_for_auto_settlement(
    *,
    user_id: int,
    limit: int = 20,
    today: str,
    force: bool = False,
) -> list[dict]:
    """Load PENDING runs old enough for automatic next-session settlement."""
    limit = max(1, min(limit, 100))
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, user_id, symbol, mode, created_at, prompt_version, model_name,
                   structure_fingerprint, transcript_json, ai_output_json, gate_result_json,
                   gate_status, model_route_json, replay_status, replay_score, outcome_json
              FROM ai_reasoning_runs
             WHERE user_id = ? AND replay_status = 'PENDING'
             ORDER BY created_at ASC, id ASC
             LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [
            dict(row)
            for row in rows
            if should_settle_created_at(str(row["created_at"] or ""), today=today, force=force)
        ]
    finally:
        conn.close()


def settle_reasoning_run_with_radar(
    *,
    run_row: dict,
    current_radar_data: dict,
    reviewer: str = "auto",
) -> AINativeRunSummary:
    """Settle one pending run from current Radar facts."""
    outcome = evaluate_reasoning_outcome(run_row, current_radar_data, reviewer=reviewer)
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE ai_reasoning_runs
               SET replay_status = 'REVIEWED',
                   replay_score = ?,
                   outcome_json = ?
             WHERE id = ? AND user_id = ?
            """,
            (
                outcome["replay_score"],
                json.dumps(outcome, ensure_ascii=False),
                run_row["id"],
                run_row["user_id"],
            ),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id, user_id, symbol, mode, created_at, prompt_version, model_name,
                   structure_fingerprint, ai_output_json, gate_result_json, gate_status, model_route_json,
                   replay_status, replay_score, outcome_json
              FROM ai_reasoning_runs
             WHERE id = ? AND user_id = ?
            """,
            (run_row["id"], run_row["user_id"]),
        ).fetchone()
        return _row_to_summary(row)
    finally:
        conn.close()


def _row_to_summary(row) -> AINativeRunSummary:
    output = _loads(row["ai_output_json"])
    gate = _loads(row["gate_result_json"])
    violations = gate.get("violations") or []
    return AINativeRunSummary(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        symbol=row["symbol"],
        mode=row["mode"],
        created_at=row["created_at"],
        prompt_version=row["prompt_version"],
        model_name=row["model_name"],
        structure_fingerprint=row["structure_fingerprint"],
        gate_status=row["gate_status"],
        gate_score=_safe_int(gate.get("score")),
        replay_status=row["replay_status"],
        replay_score=row["replay_score"],
        outcome=_loads(row["outcome_json"]) or None,
        current_hypothesis=str(output.get("current_hypothesis") or "UNKNOWN"),
        diagnosis=str(output.get("diagnosis") or ""),
        violation_codes=[str(item.get("code")) for item in violations if item.get("code")],
        model_route=_loads(row["model_route_json"]) or None,
    )


def _replay_score(*, gate_score: int, quality_score: int, matched: Optional[bool]) -> float:
    score = quality_score * 10 * 0.7 + gate_score * 0.3
    if matched is False:
        score -= 20
    return round(max(0.0, min(100.0, score)), 2)


def _readiness_reason(reviewed: int, target: int, avg_replay: float, fallback_rate: float) -> str:
    if reviewed < target:
        return f"已复盘 {reviewed}/{target} 条，样本不足"
    if avg_replay < 75:
        return f"平均复盘分 {avg_replay:.2f} 低于 75"
    if fallback_rate > 0.25:
        return f"fallback 率 {fallback_rate:.2%} 高于 25%"
    return "已达到核心体验稳定门槛"


def _loads(value: object) -> dict:
    if not value:
        return {}
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
