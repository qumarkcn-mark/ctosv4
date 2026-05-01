"""Replay calibration for AI Native path scoring."""

from __future__ import annotations

import json
import logging
from collections import Counter

from server.db.database import get_connection
from server.engines.ai_native.schemas import StructureTranscript

logger = logging.getLogger(__name__)


def load_score_calibration(transcript: StructureTranscript, *, limit: int = 30) -> dict:
    """Load reviewed replay outcomes for the same structure fingerprint."""
    try:
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT outcome_json, replay_score
                  FROM ai_reasoning_runs
                 WHERE structure_fingerprint = ?
                   AND replay_status = 'REVIEWED'
                   AND outcome_json IS NOT NULL
                 ORDER BY created_at DESC, id DESC
                 LIMIT ?
                """,
                (transcript.structure_fingerprint, max(1, min(limit, 100))),
            ).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("AI native score calibration lookup failed: %s", exc)
        return empty_calibration()
    return summarize_score_calibration([dict(row) for row in rows])


def summarize_score_calibration(rows: list[dict]) -> dict:
    outcomes = Counter()
    tags = Counter()
    quality = Counter()
    scores = []
    effective_weight = 0.0
    for row in rows:
        outcome = _loads(row.get("outcome_json"))
        weight = _learning_weight(outcome)
        effective_weight += weight
        sample_quality = str(outcome.get("sample_quality") or "UNKNOWN")
        quality[sample_quality] += 1
        actual = outcome.get("actual_hypothesis") or outcome.get("path")
        actual = _normalize_path(actual)
        if actual:
            outcomes[actual] += weight
        for tag in outcome.get("tags") or []:
            tags[str(tag)] += weight
        replay_score = _optional_float(row.get("replay_score"))
        if replay_score is not None:
            scores.append(replay_score)

    if not rows or effective_weight <= 0:
        return empty_calibration()
    return {
        "sample_count": len(rows),
        "effective_sample_weight": round(effective_weight, 2),
        "outcome_counts": {key: round(value, 2) for key, value in outcomes.items()},
        "tag_counts": {key: round(value, 2) for key, value in tags.items()},
        "quality_counts": dict(quality),
        "average_replay_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
    }


def empty_calibration() -> dict:
    return {
        "sample_count": 0,
        "effective_sample_weight": 0.0,
        "outcome_counts": {},
        "tag_counts": {},
        "quality_counts": {},
        "average_replay_score": 0.0,
    }


def _learning_weight(outcome: dict) -> float:
    explicit = _optional_float(outcome.get("learning_weight"))
    if explicit is not None:
        return max(0.0, min(1.5, explicit))
    return {
        "HIGH": 1.0,
        "MEDIUM": 0.55,
        "LOW": 0.15,
    }.get(str(outcome.get("sample_quality") or "HIGH"), 1.0)


def _normalize_path(value: object) -> str:
    text = str(value or "").upper()
    if text.startswith("A"):
        return "A"
    if text.startswith("B"):
        return "B"
    if text.startswith("C"):
        return "C"
    if text.startswith("D"):
        return "D"
    return ""


def _loads(value: object) -> dict:
    if not value:
        return {}
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _optional_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
