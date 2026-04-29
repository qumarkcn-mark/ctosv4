"""Coach event log tests."""

import os
import sqlite3
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.coach.event_log import log_user_action, record_coach_event


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE coach_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            symbol TEXT,
            occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            source TEXT NOT NULL,
            severity TEXT NOT NULL,
            dedupe_key TEXT UNIQUE NOT NULL,
            strategy_json TEXT,
            data_source_json TEXT,
            freshness_json TEXT,
            structure_ref_json TEXT,
            evidence_json TEXT,
            message_json TEXT,
            user_response_json TEXT,
            outcome_json TEXT,
            metadata_json TEXT
        );
        """
    )
    return conn


def test_record_coach_event_is_idempotent_by_dedupe_key():
    conn = make_conn()

    first = record_coach_event(
        conn,
        event_type="RISK_NOTE_RECORDED",
        user_id=1,
        symbol="sh600519",
        source="test",
        severity="INFO",
        dedupe_key="same-key",
        evidence={"note": "first"},
    )
    second = record_coach_event(
        conn,
        event_type="RISK_NOTE_RECORDED",
        user_id=1,
        symbol="sh600519",
        source="test",
        severity="INFO",
        dedupe_key="same-key",
        evidence={"note": "second"},
    )

    assert first == second
    assert conn.execute("SELECT COUNT(*) AS c FROM coach_events").fetchone()["c"] == 1


def test_log_user_action_records_structured_evidence():
    conn = make_conn()

    event_id = log_user_action(
        conn,
        user_id=1,
        symbol="sz000001",
        action_type="SCAN_RESULT_OBSERVED",
        source="scanner_api",
        dedupe_key="observe:1",
        evidence={"group_name": "观察"},
        message={"title": "加入观察库", "body": "sz000001 已加入观察。"},
    )

    row = conn.execute("SELECT * FROM coach_events WHERE event_id = ?", (event_id,)).fetchone()
    assert row["event_type"] == "USER_MARKED_ACTION"
    assert row["source"] == "scanner_api"
    assert "SCAN_RESULT_OBSERVED" in row["evidence_json"]
