"""AI Native V5 reminder bridge.

Creates normal alerts plus coach_events from chat evidence. The bridge table
only stores V5 metadata and dedupe keys; alerts remain the reminder source.
"""

from __future__ import annotations

import json
from typing import Any

from server.db.database import get_connection
from server.domain.symbols import normalize_symbol, to_tencent_symbol
from server.engines.ai_native.czsc_snapshot_service import now_text, stable_hash
from server.engines.ai_native.scenario_outcome_service import settle_scenario_branch
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


def list_structure_reminders(
    *,
    user_id: int,
    symbol: str | None = None,
    statuses: tuple[str, ...] = ("ACTIVE", "TRIGGERED"),
    limit: int = 50,
) -> dict[str, Any]:
    """List user-scoped AI Structure reminders for the workspace."""
    if not statuses or limit <= 0:
        return {"count": 0, "items": []}
    clauses = ["l.user_id = ?"]
    params: list[Any] = [int(user_id)]
    if symbol:
        clauses.append("l.symbol = ?")
        params.append(normalize_symbol(symbol))
    placeholders = ",".join("?" for _ in statuses)
    clauses.append(f"l.status IN ({placeholders})")
    params.extend(statuses)
    params.append(int(limit))
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT l.*, a.is_triggered, a.triggered_at, a.message
              FROM ai_structure_reminder_links l
              JOIN alerts a ON a.id = l.alert_id
             WHERE {' AND '.join(clauses)}
             ORDER BY
                CASE l.status WHEN 'ACTIVE' THEN 0 WHEN 'TRIGGERED' THEN 1 ELSE 2 END,
                l.updated_at DESC,
                l.id DESC
             LIMIT ?
            """,
            params,
        ).fetchall()
        return {"count": len(rows), "items": [_link_row(row) for row in rows]}
    finally:
        conn.close()


def list_active_reminder_symbols() -> list[str]:
    """Return symbols that have active AI Structure reminders."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT l.symbol
              FROM ai_structure_reminder_links l
              JOIN alerts a ON a.id = l.alert_id
             WHERE l.status = 'ACTIVE'
               AND a.is_triggered = 0
             ORDER BY l.symbol
            """
        ).fetchall()
        return [row["symbol"] for row in rows]
    finally:
        conn.close()


def scan_structure_reminders(price_by_symbol: dict[str, dict]) -> dict[str, Any]:
    """Trigger active AI Structure reminders using a current-price map.

    The scan only marks reminders and logs coach events. It never executes a
    trade and keeps the normal alerts table as the reminder source of truth.
    """
    normalized_prices = _normalize_price_map(price_by_symbol)
    if not normalized_prices:
        return {"count": 0, "items": []}

    conn = get_connection()
    triggered: list[dict[str, Any]] = []
    try:
        now = now_text()
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT l.*, a.message
              FROM ai_structure_reminder_links l
              JOIN alerts a ON a.id = l.alert_id
             WHERE l.status = 'ACTIVE'
               AND a.is_triggered = 0
             ORDER BY l.updated_at ASC, l.id ASC
            """
        ).fetchall()
        for row in rows:
            price = normalized_prices.get(row["symbol"])
            if price is None or not _is_triggered(price, row["trigger_price"], row["direction"]):
                continue
            message_text = row["message"] or _trigger_message(row=row, current_price=price)
            conn.execute(
                """
                UPDATE alerts
                   SET is_triggered = 1,
                       triggered_at = ?
                 WHERE id = ?
                """,
                (now, row["alert_id"]),
            )
            conn.execute(
                """
                UPDATE ai_structure_reminder_links
                   SET status = 'TRIGGERED',
                       updated_at = ?
                 WHERE id = ?
                """,
                (now, row["id"]),
            )
            dedupe_key = f"{row['dedupe_key']}:trigger:{now}"
            event_id = record_coach_event(
                conn,
                event_type="AI_STRUCTURE_REMINDER_TRIGGERED",
                user_id=int(row["user_id"]),
                symbol=row["symbol"],
                source="price_monitor",
                severity="WARNING",
                dedupe_key=dedupe_key,
                strategy={
                    "strategy_id": "ai_structure_v5",
                    "strategy_version": "v1",
                    "strategy_type": "AI_STRUCTURE_REMINDER",
                },
                structure_ref={
                    "context_id": row["context_id"],
                    "evidence_id": row["evidence_id"],
                    "session_id": row["session_id"],
                    "message_id": row["message_id"],
                },
                evidence={
                    "current_price": price,
                    "trigger_price": row["trigger_price"],
                    "direction": row["direction"],
                    "alert_id": row["alert_id"],
                },
                message={"title": "AI Structure Reminder Triggered", "body": message_text},
                metadata={"alert_id": row["alert_id"], "link_id": row["id"]},
            )
            record_alert_delivery(
                conn,
                event_id=event_id,
                alert_id=int(row["alert_id"]),
                user_id=int(row["user_id"]),
                symbol=row["symbol"],
                channel="wechat_subscribe",
                delivery_status="TRIGGERED",
                message=message_text,
                dedupe_key=f"{row['dedupe_key']}:delivery:triggered:{now}",
            )
            triggered.append({
                "user_id": int(row["user_id"]),
                "symbol": row["symbol"],
                "alert_id": int(row["alert_id"]),
                "context_id": row["context_id"],
                "evidence_id": row["evidence_id"],
                "trigger_price": float(row["trigger_price"]),
                "current_price": price,
                "direction": row["direction"],
                "message": message_text,
                "settled_outcome": None,
            })
        conn.commit()
        for item in triggered:
            try:
                item["settled_outcome"] = _auto_settle_branch_for_reminder(
                    user_id=item["user_id"],
                    symbol=item["symbol"],
                    context_id=item["context_id"],
                    evidence_id=item["evidence_id"],
                    direction=item["direction"],
                    current_price=item["current_price"],
                    checked_at=now,
                )
            except Exception as exc:
                item["settled_outcome_error"] = str(exc)[:200]
        return {"count": len(triggered), "items": triggered}
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
    data["triggered"] = bool(data.get("is_triggered"))
    return data


def _normalize_price_map(price_by_symbol: dict[str, dict]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for raw_symbol, payload in (price_by_symbol or {}).items():
        price = _num((payload or {}).get("price"))
        if price <= 0:
            continue
        symbol = normalize_symbol(raw_symbol)
        normalized[symbol] = price
        normalized[to_tencent_symbol(symbol)] = price
    return normalized


def _is_triggered(current_price: float, trigger_price: float, direction: str) -> bool:
    if str(direction or "").upper() == "ABOVE":
        return current_price >= float(trigger_price)
    if str(direction or "").upper() == "BELOW":
        return current_price <= float(trigger_price)
    return False


def _trigger_message(*, row, current_price: float) -> str:
    direction = "上破" if row["direction"] == "ABOVE" else "跌破"
    return (
        f"{row['symbol']} 已{direction} {float(row['trigger_price']):.2f}，"
        f"当前价 {current_price:.2f}，请复核 AI 结构提醒。提醒不下单，{RISK_DISCLAIMER}"
    )


def _auto_settle_branch_for_reminder(
    *,
    user_id: int,
    symbol: str,
    context_id: str,
    evidence_id: str,
    direction: str,
    current_price: float,
    checked_at: str,
) -> dict[str, Any] | None:
    branch_id = _find_branch_for_reminder(
        user_id=user_id,
        symbol=symbol,
        context_id=context_id,
        evidence_id=evidence_id,
        direction=direction,
    )
    if not branch_id:
        return None
    return settle_scenario_branch(
        user_id=user_id,
        branch_id=branch_id,
        current_price=current_price,
        settlement_window="ai_reminder_trigger",
        checked_at=checked_at,
        user_followed_plan=None,
    )


def _find_branch_for_reminder(
    *,
    user_id: int,
    symbol: str,
    context_id: str,
    evidence_id: str,
    direction: str,
) -> str:
    expected_condition = "price_above" if str(direction).upper() == "ABOVE" else "price_below"
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT branch_id, branch_type, trigger_condition_json, invalidate_condition_json, evidence_refs_json
              FROM scenario_branches
             WHERE user_id = ?
               AND symbol = ?
               AND context_id = ?
               AND status = 'ACTIVE'
             ORDER BY
               CASE branch_type
                 WHEN 'observe_breakout' THEN 0
                 WHEN 'invalidation_watch' THEN 1
                 WHEN 'holding_defense' THEN 2
                 ELSE 3
               END,
               updated_at DESC,
               id DESC
            """,
            (int(user_id), normalize_symbol(symbol), context_id),
        ).fetchall()
        for row in rows:
            evidence_refs = json.loads(row["evidence_refs_json"] or "[]")
            if evidence_id not in evidence_refs:
                continue
            trigger = json.loads(row["trigger_condition_json"] or "{}")
            invalid = json.loads(row["invalidate_condition_json"] or "{}")
            if trigger.get("type") == expected_condition or invalid.get("type") == expected_condition:
                return row["branch_id"]
        return ""
    finally:
        conn.close()


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
