"""SQLite-backed memory for AI Native Radar shadow runs."""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Optional

from server.db.database import get_connection
from server.engines.ai_native.schemas import (
    AIReasoningOutput,
    GateResult,
    SimilarCaseSummary,
    StructureTranscript,
)

logger = logging.getLogger(__name__)


def find_similar_cases(transcript: StructureTranscript, *, limit: int = 20) -> SimilarCaseSummary:
    """Return compact memory summary for the same structure fingerprint."""
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT ai_output_json, gate_result_json, outcome_json
                  FROM ai_reasoning_runs
                 WHERE structure_fingerprint = ?
                 ORDER BY created_at DESC
                 LIMIT ?
                """,
                (transcript.structure_fingerprint, limit),
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("AI native case memory lookup failed: %s", exc)
        return SimilarCaseSummary()

    outcomes = Counter()
    failures = Counter()
    for row in rows:
        output = _loads(row["ai_output_json"])
        gate = _loads(row["gate_result_json"])
        outcome = _loads(row["outcome_json"])
        if outcome.get("path"):
            outcomes[outcome["path"]] += 1
        current = output.get("current_hypothesis")
        if current:
            outcomes[current] += 1
        for violation in gate.get("violations") or []:
            code = violation.get("code")
            if code:
                failures[code] += 1
    return SimilarCaseSummary(
        similar_case_count=len(rows),
        common_outcomes=[{"path": key, "count": count} for key, count in outcomes.most_common(5)],
        common_failure_reasons=[key for key, _ in failures.most_common(5)],
    )


def save_reasoning_run(
    *,
    user_id: int,
    symbol: str,
    mode: str,
    prompt_version: str,
    model_name: str,
    transcript: StructureTranscript,
    memory_context: SimilarCaseSummary,
    ai_output: Optional[AIReasoningOutput],
    gate_result: GateResult,
) -> Optional[int]:
    """Persist shadow run. Failure is non-blocking by design."""
    try:
        conn = get_connection()
        try:
            cursor = conn.execute(
                """
                INSERT INTO ai_reasoning_runs (
                    user_id, symbol, mode, prompt_version, model_name,
                    structure_fingerprint, transcript_json, memory_context_json,
                    ai_output_json, gate_result_json, gate_status, disclaimer
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    symbol,
                    mode,
                    prompt_version,
                    model_name,
                    transcript.structure_fingerprint,
                    transcript.model_dump_json(),
                    memory_context.model_dump_json(),
                    ai_output.model_dump_json() if ai_output else None,
                    gate_result.model_dump_json(),
                    gate_result.status,
                    transcript.disclaimer,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)
        finally:
            conn.close()
    except Exception as exc:
        logger.error("AI native reasoning run save failed: %s", exc)
        return None


def _loads(value: object) -> dict:
    if not value:
        return {}
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}

