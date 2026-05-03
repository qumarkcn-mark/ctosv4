"""Case Memory feedback for AI stop/reduce intent generation."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any, Literal


FeedbackBias = Literal["NONE", "TIGHTEN_STOP", "WAIT_FOR_CONFIRMATION"]
DEFAULT_STOP_REDUCE_CASE_KEY = "holding:loss:structure_breakdown:near_stop"


@dataclass(frozen=True)
class StopReduceFeedback:
    case_key: str
    total_count: int = 0
    mistake_count: int = 0
    mistake_rate: float = 0.0
    avg_loss_if_hold_pct: float = 0.0
    avg_benefit_if_reduce_pct: float = 0.0
    latest_mistake_type: str = ""
    latest_lesson: str = ""
    latest_outcome: str = ""
    context_hint: str = ""
    action_bias: FeedbackBias = "NONE"
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_stop_reduce_feedback(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    case_key: str,
) -> StopReduceFeedback:
    """Load compact feedback for the next same-structure stop/reduce intent."""
    stats = _load_stats(conn, user_id=user_id, case_key=case_key)
    latest = _load_latest_case(conn, user_id=user_id, case_key=case_key)
    return build_stop_reduce_feedback(case_key=case_key, stats=stats, latest_case=latest)


def build_stop_reduce_feedback(
    *,
    case_key: str,
    stats: dict[str, Any] | None = None,
    latest_case: dict[str, Any] | None = None,
) -> StopReduceFeedback:
    """Convert persisted sparse mistakes into a bounded policy hint."""
    stats = stats or {}
    latest_case = latest_case or {}
    total_count = max(0, int(stats.get("total_count") or 0))
    mistake_count = max(0, int(stats.get("mistake_count") or 0))
    mistake_rate = round(mistake_count / total_count, 4) if total_count else 0.0
    latest_mistake_type = str(latest_case.get("mistake_type") or "")
    avg_loss_if_hold_pct = _num(stats.get("avg_loss_if_hold_pct"))
    avg_benefit_if_reduce_pct = _num(stats.get("avg_benefit_if_reduce_pct"))
    action_bias = _choose_bias(
        total_count=total_count,
        mistake_rate=mistake_rate,
        avg_loss_if_hold_pct=avg_loss_if_hold_pct,
        avg_benefit_if_reduce_pct=avg_benefit_if_reduce_pct,
        latest_mistake_type=latest_mistake_type,
    )
    return StopReduceFeedback(
        case_key=case_key,
        total_count=total_count,
        mistake_count=mistake_count,
        mistake_rate=mistake_rate,
        avg_loss_if_hold_pct=avg_loss_if_hold_pct,
        avg_benefit_if_reduce_pct=avg_benefit_if_reduce_pct,
        latest_mistake_type=latest_mistake_type,
        latest_lesson=str(latest_case.get("lesson") or ""),
        latest_outcome=str(latest_case.get("outcome") or ""),
        context_hint=str(latest_case.get("context_hint") or ""),
        action_bias=action_bias,
        confidence=_confidence(total_count=total_count, mistake_rate=mistake_rate),
    )


def render_feedback_hint(feedback: StopReduceFeedback | None) -> str:
    """Render a short prompt-safe hint for audit/debug surfaces."""
    if feedback is None or feedback.total_count <= 0:
        return "暂无同类错误记忆。"
    lines = [
        f"同类结构 {feedback.case_key}: {feedback.total_count} 次样本，{feedback.mistake_count} 次高价值错误。",
        f"反哺倾向：{feedback.action_bias}，置信度 {feedback.confidence:.2f}。",
    ]
    if feedback.latest_lesson:
        lines.append(f"最近教训：{feedback.latest_lesson}")
    return "\n".join(lines)


def _choose_bias(
    *,
    total_count: int,
    mistake_rate: float,
    avg_loss_if_hold_pct: float,
    avg_benefit_if_reduce_pct: float,
    latest_mistake_type: str,
) -> FeedbackBias:
    if total_count < 2 or mistake_rate <= 0:
        return "NONE"
    if latest_mistake_type == "AI_HELD_AFTER_STOP_BROKEN" and avg_loss_if_hold_pct <= -2.0:
        return "TIGHTEN_STOP"
    if latest_mistake_type == "REDUCE_TOO_EARLY" and avg_benefit_if_reduce_pct <= 0.5:
        return "WAIT_FOR_CONFIRMATION"
    if mistake_rate >= 0.6 and avg_loss_if_hold_pct <= -3.0:
        return "TIGHTEN_STOP"
    return "NONE"


def _confidence(*, total_count: int, mistake_rate: float) -> float:
    sample_weight = min(total_count / 10.0, 1.0)
    return round(sample_weight * mistake_rate, 4)


def _load_stats(conn: sqlite3.Connection, *, user_id: int, case_key: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT calibration_key, user_id, total_count, mistake_count,
               avg_loss_if_hold_pct, avg_benefit_if_reduce_pct,
               latest_mistake_case_id, updated_at
          FROM ai_calibration_stats
         WHERE user_id = ? AND calibration_key = ?
        """,
        (user_id, case_key),
    ).fetchone()
    return dict(row) if row else {}


def _load_latest_case(conn: sqlite3.Connection, *, user_id: int, case_key: str) -> dict[str, Any]:
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
    if not row:
        return {}
    payload = dict(row)
    payload["metadata"] = _loads(payload.pop("metadata_json", ""))
    return payload


def _loads(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        loaded = json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
