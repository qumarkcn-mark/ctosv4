"""Coach event log helpers.

The log is append-only and idempotent by dedupe_key. It records deterministic
coach evidence; it does not execute trades or rewrite strategy outcomes.
"""

import json
import uuid
from typing import Optional


def record_coach_event(
    conn,
    *,
    event_type: str,
    user_id: int,
    source: str,
    severity: str,
    dedupe_key: str,
    symbol: Optional[str] = None,
    strategy: Optional[dict] = None,
    data_source: Optional[dict] = None,
    freshness: Optional[dict] = None,
    structure_ref: Optional[dict] = None,
    evidence: Optional[dict] = None,
    message: Optional[dict] = None,
    user_response: Optional[dict] = None,
    outcome: Optional[dict] = None,
    metadata: Optional[dict] = None,
) -> str:
    existing = conn.execute(
        "SELECT event_id FROM coach_events WHERE dedupe_key = ?",
        (dedupe_key,),
    ).fetchone()
    if existing:
        return existing["event_id"]

    event_id = _new_id("evt")
    conn.execute(
        """
        INSERT INTO coach_events (
            event_id, event_type, user_id, symbol, source, severity, dedupe_key,
            strategy_json, data_source_json, freshness_json, structure_ref_json,
            evidence_json, message_json, user_response_json, outcome_json, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event_type,
            user_id,
            symbol,
            source,
            severity,
            dedupe_key,
            _json(strategy),
            _json(data_source),
            _json(freshness),
            _json(structure_ref),
            _json(evidence),
            _json(message),
            _json(user_response),
            _json(outcome),
            _json(metadata),
        ),
    )
    return event_id


def record_strategy_trigger(
    conn,
    *,
    event_id: str,
    user_id: int,
    strategy_id: str,
    strategy_version: str,
    condition_status: str,
    dedupe_key: str,
    symbol: Optional[str] = None,
    plan_id: Optional[str] = None,
    condition_id: Optional[str] = None,
    mode: Optional[str] = None,
    data_source: Optional[dict] = None,
    freshness: Optional[dict] = None,
    evidence: Optional[dict] = None,
) -> str:
    existing = conn.execute(
        "SELECT trigger_id FROM strategy_triggers WHERE dedupe_key = ?",
        (dedupe_key,),
    ).fetchone()
    if existing:
        return existing["trigger_id"]

    trigger_id = _new_id("trg")
    conn.execute(
        """
        INSERT INTO strategy_triggers (
            trigger_id, event_id, user_id, symbol, strategy_id, strategy_version,
            plan_id, condition_id, condition_status, mode, data_source_json,
            freshness_json, evidence_json, dedupe_key
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trigger_id,
            event_id,
            user_id,
            symbol,
            strategy_id,
            strategy_version,
            plan_id,
            condition_id,
            condition_status,
            mode,
            _json(data_source),
            _json(freshness),
            _json(evidence),
            dedupe_key,
        ),
    )
    return trigger_id


def record_alert_delivery(
    conn,
    *,
    event_id: str,
    user_id: int,
    channel: str,
    delivery_status: str,
    dedupe_key: str,
    alert_id: Optional[int] = None,
    symbol: Optional[str] = None,
    message: Optional[str] = None,
    error: Optional[str] = None,
) -> str:
    existing = conn.execute(
        "SELECT delivery_id FROM alert_deliveries WHERE dedupe_key = ?",
        (dedupe_key,),
    ).fetchone()
    if existing:
        return existing["delivery_id"]

    delivery_id = _new_id("dlv")
    conn.execute(
        """
        INSERT INTO alert_deliveries (
            delivery_id, event_id, alert_id, user_id, symbol, channel,
            delivery_status, message, error, dedupe_key
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            delivery_id,
            event_id,
            alert_id,
            user_id,
            symbol,
            channel,
            delivery_status,
            message,
            error,
            dedupe_key,
        ),
    )
    return delivery_id


def log_alert_candidate(
    conn,
    *,
    alert_id: int,
    user_id: int,
    symbol: str,
    alert_type: str,
    message_text: str,
    source: str = "price_monitor",
    strategy_contract: Optional[dict] = None,
    evidence: Optional[dict] = None,
) -> str:
    strategy = _strategy_payload(strategy_contract)
    severity = _severity_for_alert(alert_type)
    dedupe_key = f"alert:{user_id}:{symbol}:{alert_type}:{alert_id}"
    event_id = record_coach_event(
        conn,
        event_type="ALERT_CANDIDATE_CREATED",
        user_id=user_id,
        symbol=symbol,
        source=source,
        severity=severity,
        dedupe_key=dedupe_key,
        strategy=strategy,
        evidence=evidence or {"alert_type": alert_type},
        message={"title": alert_type, "body": message_text},
        metadata={"alert_id": alert_id},
    )
    if strategy:
        record_strategy_trigger(
            conn,
            event_id=event_id,
            user_id=user_id,
            symbol=symbol,
            strategy_id=strategy["strategy_id"],
            strategy_version=strategy["strategy_version"],
            condition_id=alert_type,
            condition_status="PASS",
            mode=(strategy_contract.get("mode") or [None])[0] if strategy_contract else None,
            evidence=evidence or {"alert_type": alert_type},
            dedupe_key=f"trigger:{user_id}:{symbol}:{alert_type}:{alert_id}",
        )
    record_alert_delivery(
        conn,
        event_id=event_id,
        alert_id=alert_id,
        user_id=user_id,
        symbol=symbol,
        channel="wechat_subscribe",
        delivery_status="CREATED",
        message=message_text,
        dedupe_key=f"delivery:{user_id}:{symbol}:{alert_type}:{alert_id}:created",
    )
    return event_id


def log_user_action(
    conn,
    *,
    user_id: int,
    action_type: str,
    dedupe_key: str,
    symbol: Optional[str] = None,
    source: str = "api",
    evidence: Optional[dict] = None,
    message: Optional[dict] = None,
) -> str:
    return record_coach_event(
        conn,
        event_type="USER_MARKED_ACTION",
        user_id=user_id,
        symbol=symbol,
        source=source,
        severity="INFO",
        dedupe_key=dedupe_key,
        evidence={"action_type": action_type, **(evidence or {})},
        message=message,
    )


def _strategy_payload(strategy_contract: Optional[dict]) -> Optional[dict]:
    if not strategy_contract:
        return None
    return {
        "strategy_id": strategy_contract.get("strategy_id"),
        "strategy_version": strategy_contract.get("strategy_version"),
        "strategy_type": strategy_contract.get("strategy_type"),
    }


def _severity_for_alert(alert_type: str) -> str:
    if alert_type in ("STOP_LOSS_BROKEN", "HOLDING_STAGE5", "TRAILING_STOP_BROKEN"):
        return "CRITICAL"
    if alert_type in ("STOP_LOSS_WARNING", "CHAN_30M_TOP_DIV", "HOLDING_STAGE4"):
        return "WARNING"
    return "WATCH"


def _json(value: Optional[dict]) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"
