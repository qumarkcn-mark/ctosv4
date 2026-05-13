"""AI Native V5 reminder bridge.

Creates normal alerts plus coach_events from chat evidence. The bridge table
only stores V5 metadata and dedupe keys; alerts remain the reminder source.
"""

from __future__ import annotations

import json
from typing import Any

from server.db.database import get_connection
from server.domain.symbols import normalize_symbol
from server.engines.ai_native.czsc_snapshot_service import now_text, stable_hash
from server.engines.ai_native.structure_chat_service import RISK_DISCLAIMER
from server.engines.coach.event_log import record_alert_delivery, record_coach_event


def create_reminder_from_chat_evidence(
    *,
    user_id: int,
    session_id: str,
    message_id: str,
    evidence_id: str,
) -> dict[str, Any] | None:
    message = _load_message(user_id=user_id, session_id=session_id, message_id=message_id)
    if not message:
        return None
    answer = message["answer"]
    candidate = _candidate_for_evidence(answer.get("suggested_reminders") or [], evidence_id)
    if not candidate:
        raise ValueError("reminder candidate not found for evidence")
    symbol = normalize_symbol(message["symbol"])
    context_id = message["context_id"]
    trigger_price = _num(candidate.get("trigger_price"))
    direction = str(candidate.get("direction") or "").upper()
    if trigger_price <= 0 or direction not in {"ABOVE", "BELOW"}:
        raise ValueError("invalid reminder candidate")
    dedupe_key = reminder_dedupe_key(
        user_id=user_id,
        symbol=symbol,
        context_id=context_id,
        evidence_id=evidence_id,
        trigger_price=trigger_price,
        direction=direction,
    )
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT * FROM ai_structure_reminder_links WHERE dedupe_key = ?",
            (dedupe_key,),
        ).fetchone()
        if existing:
            conn.commit()
            return _link_row(existing, duplicate=True)
        message_text = _message_text(symbol=symbol, candidate=candidate)
        strategy_contract = {
            "strategy_id": "ai_structure_v5",
            "strategy_version": "v1",
            "strategy_type": "AI_STRUCTURE_REMINDER",
            "context_id": context_id,
            "session_id": session_id,
            "message_id": message_id,
            "evidence_id": evidence_id,
            "risk_disclaimer": RISK_DISCLAIMER,
        }
        cur = conn.execute(
            """
            INSERT INTO alerts (
                user_id, symbol, alert_type, trigger_price, trigger_direction,
                is_triggered, message, strategy_id, strategy_version, strategy_contract
            )
            VALUES (?, ?, 'SIGNAL', ?, ?, 0, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                symbol,
                trigger_price,
                direction,
                message_text,
                strategy_contract["strategy_id"],
                strategy_contract["strategy_version"],
                json.dumps(strategy_contract, ensure_ascii=False, sort_keys=True),
            ),
        )
        alert_id = int(cur.lastrowid)
        event_id = record_coach_event(
            conn,
            event_type="AI_STRUCTURE_REMINDER_CREATED",
            user_id=int(user_id),
            symbol=symbol,
            source="ai_structure_chat",
            severity="INFO",
            dedupe_key=dedupe_key,
            strategy=strategy_contract,
            structure_ref={
                "context_id": context_id,
                "evidence_id": evidence_id,
                "session_id": session_id,
                "message_id": message_id,
            },
            evidence={
                "trigger_price": trigger_price,
                "direction": direction,
                "candidate": candidate,
            },
            message={"title": "AI Structure Reminder", "body": message_text},
            metadata={"alert_id": alert_id},
        )
        record_alert_delivery(
            conn,
            event_id=event_id,
            alert_id=alert_id,
            user_id=int(user_id),
            symbol=symbol,
            channel="wechat_subscribe",
            delivery_status="CREATED",
            message=message_text,
            dedupe_key=f"{dedupe_key}:delivery:created",
        )
        now = now_text()
        conn.execute(
            """
            INSERT INTO ai_structure_reminder_links (
                dedupe_key, user_id, symbol, alert_id, coach_event_id,
                session_id, message_id, context_id, evidence_id, trigger_price,
                direction, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
            """,
            (
                dedupe_key,
                int(user_id),
                symbol,
                alert_id,
                event_id,
                session_id,
                message_id,
                context_id,
                evidence_id,
                trigger_price,
                direction,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM ai_structure_reminder_links WHERE dedupe_key = ?", (dedupe_key,)).fetchone()
        return _link_row(row, duplicate=False)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reminder_dedupe_key(
    *,
    user_id: int,
    symbol: str,
    context_id: str,
    evidence_id: str,
    trigger_price: float,
    direction: str,
) -> str:
    return stable_hash({
        "user_id": int(user_id),
        "symbol": normalize_symbol(symbol),
        "context_id": context_id,
        "evidence_id": evidence_id,
        "trigger_price": round(float(trigger_price), 4),
        "direction": direction.upper(),
    })


def _load_message(*, user_id: int, session_id: str, message_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
              FROM ai_structure_chat_messages
             WHERE user_id = ? AND session_id = ? AND message_id = ?
             LIMIT 1
            """,
            (int(user_id), session_id, message_id),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["answer"] = json.loads(data.pop("answer_json") or "{}")
        data["evidence_refs"] = json.loads(data.pop("evidence_refs_json") or "[]")
        data["reminder_candidates"] = json.loads(data.pop("reminder_candidates_json") or "[]")
        return data
    finally:
        conn.close()


def _candidate_for_evidence(candidates: list[dict[str, Any]], evidence_id: str) -> dict[str, Any] | None:
    for candidate in candidates:
        if candidate.get("evidence_id") == evidence_id:
            return candidate
    return None


def _message_text(*, symbol: str, candidate: dict[str, Any]) -> str:
    price = _num(candidate.get("trigger_price"))
    direction = "上破" if candidate.get("direction") == "ABOVE" else "跌破"
    message = candidate.get("message") or "AI 结构提醒"
    return f"{symbol} {direction} {price:.2f}：{message}。提醒不下单，{RISK_DISCLAIMER}"


def _link_row(row, *, duplicate: bool = False) -> dict[str, Any]:
    data = dict(row)
    data["duplicate"] = duplicate
    return data


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
