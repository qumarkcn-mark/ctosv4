"""Unified gate for AI reasoning triggers.

This service is the only place automatic jobs should enter full AI reasoning.
It records every generated/skipped/error decision so token use can be audited.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from server import config
from server.db.database import get_connection
from server.domain.symbols import normalize_symbol
from server.engines.ai_native.unified_reasoning_service import DEFAULT_COMPUTE_PROFILE, DEFAULT_LEVELS, trigger_unified_reasoning
from server.engines.ai_native.universe_resolver import resolve_watchboard_universe


MODE_FULL_REASONING = "full_reasoning"
MODE_SHORT_ANSWER = "short_answer"

TRIGGER_NEW_WATCHBOARD_SYMBOL = "new_watchboard_symbol"
TRIGGER_MANUAL_FULL_REASONING = "manual_full_reasoning"
TRIGGER_POST_TDX_REFRESH = "post_tdx_refresh"
TRIGGER_WATCHBOARD_WORKER = "watchboard_worker"
TRIGGER_INTRADAY_KEY_EVENT = "intraday_key_event"
TRIGGER_USER_QUESTION = "user_question"

AUTO_FULL_REASONING_REASONS = {
    TRIGGER_POST_TDX_REFRESH,
    TRIGGER_WATCHBOARD_WORKER,
    TRIGGER_INTRADAY_KEY_EVENT,
}
MANUAL_FULL_REASONING_REASONS = {
    TRIGGER_MANUAL_FULL_REASONING,
    TRIGGER_NEW_WATCHBOARD_SYMBOL,
}


async def request_ai_reasoning(
    *,
    user_id: int,
    symbol: str,
    trigger_reason: str,
    mode: str = MODE_FULL_REASONING,
    force: bool = False,
    levels: list[str] | None = None,
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Gate and execute AI reasoning requests.

    First version supports full unified reasoning only. User-initiated triggers
    may run outside the watchboard universe; automatic triggers must stay inside
    the watchboard universe.
    """
    canonical = normalize_symbol(symbol)
    started_at = _now_text()
    meta = dict(metadata or {})

    decision = _decide(
        user_id=user_id,
        symbol=canonical,
        trigger_reason=trigger_reason,
        mode=mode,
        force=force,
    )
    if decision["decision"] == "skipped":
        return _insert_trigger_log(
            user_id=user_id,
            symbol=canonical,
            mode=mode,
            trigger_reason=trigger_reason,
            decision="skipped",
            skip_reason=decision["skip_reason"],
            metadata=meta,
            started_at=started_at,
        )

    try:
        if mode != MODE_FULL_REASONING:
            return _insert_trigger_log(
                user_id=user_id,
                symbol=canonical,
                mode=mode,
                trigger_reason=trigger_reason,
                decision="skipped",
                skip_reason="UNSUPPORTED_MODE",
                metadata=meta,
                started_at=started_at,
            )
        result = await trigger_unified_reasoning(
            user_id=user_id,
            symbol=canonical,
            levels=levels or list(DEFAULT_LEVELS),
            compute_profile=compute_profile,
        )
        log = _insert_trigger_log(
            user_id=user_id,
            symbol=canonical,
            mode=mode,
            trigger_reason=trigger_reason,
            decision="generated",
            run_id=str(result.get("run_id") or ""),
            context_id=str(result.get("context_id") or ""),
            metadata={**meta, "data_as_of": result.get("data_as_of") or ""},
            started_at=started_at,
        )
        return {**result, "trigger": log}
    except Exception as exc:
        log = _insert_trigger_log(
            user_id=user_id,
            symbol=canonical,
            mode=mode,
            trigger_reason=trigger_reason,
            decision="error",
            error_message=str(exc)[:500],
            metadata=meta,
            started_at=started_at,
        )
        return {"symbol": canonical, "status": "error", "error": str(exc), "trigger": log}


def _decide(
    *,
    user_id: int,
    symbol: str,
    trigger_reason: str,
    mode: str,
    force: bool,
) -> dict[str, str]:
    if not getattr(config, "AI_TRIGGER_ENABLED", True):
        return {"decision": "skipped", "skip_reason": "AI_TRIGGER_DISABLED"}
    if mode != MODE_FULL_REASONING:
        return {"decision": "skipped", "skip_reason": "UNSUPPORTED_MODE"}
    if trigger_reason in AUTO_FULL_REASONING_REASONS:
        if not getattr(config, "AI_AUTO_FULL_REASONING_ENABLED", False):
            return {"decision": "skipped", "skip_reason": "AI_AUTO_FULL_REASONING_DISABLED"}
        if not _is_watchboard_symbol(user_id=user_id, symbol=symbol):
            return {"decision": "skipped", "skip_reason": "NOT_WATCHBOARD_SYMBOL"}
    else:
        if not getattr(config, "AI_MANUAL_FULL_REASONING_ENABLED", True):
            return {"decision": "skipped", "skip_reason": "AI_MANUAL_FULL_REASONING_DISABLED"}
    if not force and _in_cooldown(user_id=user_id, symbol=symbol, mode=mode):
        return {"decision": "skipped", "skip_reason": "COOLDOWN"}
    return {"decision": "generated", "skip_reason": ""}


def _is_watchboard_symbol(*, user_id: int, symbol: str) -> bool:
    return normalize_symbol(symbol) in {item["symbol"] for item in resolve_watchboard_universe(user_id)}


def _in_cooldown(*, user_id: int, symbol: str, mode: str) -> bool:
    seconds = int(getattr(config, "AI_TRIGGER_COOLDOWN_SECONDS", 1800) or 0)
    if seconds <= 0:
        return False
    cutoff = (datetime.now(timezone.utc).astimezone() - timedelta(seconds=seconds)).isoformat(timespec="seconds")
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT id
              FROM ai_trigger_logs
             WHERE user_id = ?
               AND symbol = ?
               AND mode = ?
               AND decision = 'generated'
               AND created_at >= ?
             ORDER BY created_at DESC, id DESC
             LIMIT 1
            """,
            (int(user_id), normalize_symbol(symbol), mode, cutoff),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def insert_ai_trigger_log(
    *,
    user_id: int,
    symbol: str,
    mode: str,
    trigger_reason: str,
    decision: str,
    skip_reason: str = "",
    run_id: str = "",
    context_id: str = "",
    error_message: str = "",
    metadata: dict[str, Any] | None = None,
    started_at: str | None = None,
) -> dict[str, Any]:
    trigger_id = f"aitrig_{uuid.uuid4().hex[:16]}"
    started = started_at or _now_text()
    finished = _now_text()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO ai_trigger_logs (
                trigger_id, user_id, symbol, mode, trigger_reason, decision,
                skip_reason, run_id, context_id, error_message, metadata_json,
                started_at, finished_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trigger_id,
                int(user_id),
                normalize_symbol(symbol),
                mode,
                trigger_reason,
                decision,
                skip_reason,
                run_id,
                context_id,
                error_message,
                json.dumps(metadata or {}, ensure_ascii=False),
                started,
                finished,
                finished,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "trigger_id": trigger_id,
        "user_id": int(user_id),
        "symbol": normalize_symbol(symbol),
        "mode": mode,
        "trigger_reason": trigger_reason,
        "decision": decision,
        "skip_reason": skip_reason,
        "run_id": run_id,
        "context_id": context_id,
        "error_message": error_message,
        "started_at": started,
        "finished_at": finished,
    }


_insert_trigger_log = insert_ai_trigger_log


def _now_text() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
