"""Scanner worker push loop tests."""

import json
import os
import sqlite3
import sys
from types import SimpleNamespace

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services.chan_scanner import ScanResult
from server.workers import scanner
from server.workers.scanner import (
    enqueue_structure_jobs_for_scan_candidates,
    notify_scanner_top_candidates,
    upsert_scan_result,
)


def make_scanner_push_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            score REAL DEFAULT 0,
            close REAL,
            stop_loss REAL,
            target REAL,
            rr_ratio REAL,
            atr_pct REAL,
            volume_ratio REAL,
            chan_desc TEXT,
            retry_count INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scan_date, symbol, strategy)
        );
        CREATE TABLE alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            trigger_price REAL,
            is_triggered INTEGER DEFAULT 0,
            message TEXT,
            strategy_id TEXT,
            strategy_version TEXT,
            strategy_contract TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
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
        CREATE TABLE strategy_triggers (
            trigger_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            symbol TEXT,
            strategy_id TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            plan_id TEXT,
            condition_id TEXT,
            condition_status TEXT NOT NULL,
            triggered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            mode TEXT,
            data_source_json TEXT,
            freshness_json TEXT,
            evidence_json TEXT,
            dedupe_key TEXT UNIQUE NOT NULL
        );
        CREATE TABLE alert_deliveries (
            delivery_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            alert_id INTEGER,
            user_id INTEGER NOT NULL,
            symbol TEXT,
            channel TEXT NOT NULL,
            delivery_status TEXT NOT NULL,
            attempted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            message TEXT,
            error TEXT,
            dedupe_key TEXT UNIQUE NOT NULL
        );
        INSERT INTO scan_results
            (scan_date, symbol, strategy, status, score, close, stop_loss, target, rr_ratio, chan_desc)
        VALUES
            ('2026-04-24', 'sz000001', 'war2', 'ready', 88, 12.3, 11.2, 15.5, 2.1, '30分突破'),
            ('2026-04-24', 'sz000002', 'war1', 'ready', 70, 8.8, 8.0, 10.5, 1.8, '分数不足'),
            ('2026-04-24', 'sz000003', 'war1', 'pending', 95, 9.9, 9.0, 12.0, 2.0, '未ready');
        """
    )
    return conn


def test_notify_scanner_top_candidates_creates_alert_and_event_once():
    conn = make_scanner_push_conn()

    assert notify_scanner_top_candidates(conn, "2026-04-24", user_id=1) == 1
    assert notify_scanner_top_candidates(conn, "2026-04-24", user_id=1) == 0

    alert = conn.execute("SELECT * FROM alerts").fetchone()
    assert alert["symbol"] == "sz000001"
    assert alert["alert_type"] == "SCANNER_TOP_CANDIDATE"
    assert alert["strategy_id"] == "war2_trend_step"
    assert "仅供参考" in alert["message"]
    assert "d1_zs_above_breakout_medium" in alert["message"]

    contract = json.loads(alert["strategy_contract"])
    assert contract["strategy_id"] == "war2_trend_step"

    event = conn.execute("SELECT * FROM coach_events").fetchone()
    delivery = conn.execute("SELECT * FROM alert_deliveries").fetchone()
    assert event["event_type"] == "ALERT_CANDIDATE_CREATED"
    assert event["source"] == "scanner_worker"
    assert delivery["delivery_status"] == "CREATED"
    evidence = json.loads(event["evidence_json"])
    assert evidence["signal_code"] == "d1_zs_above_breakout_medium"
    assert evidence["signal_context"]["risk_reward_ratio"] == 2.1


def test_enqueue_structure_jobs_for_scan_candidates_low_priority(monkeypatch):
    built = []
    enqueued = []

    def fake_build_formal_structure_key(symbol, freq, **kwargs):
        built.append((symbol, freq))
        return SimpleNamespace(symbol=f"bs.{symbol}", freq=freq, hash=f"hash-{symbol}-{freq}"), {}

    def fake_enqueue_structure_job(structure_key, **kwargs):
        enqueued.append((structure_key.symbol, structure_key.freq, kwargs))
        return {
            "status": "PENDING",
            "job_id": f"job-{structure_key.freq}",
            "enqueued": True,
            "bumped": False,
        }

    monkeypatch.setattr(scanner, "build_formal_structure_key", fake_build_formal_structure_key)
    monkeypatch.setattr(scanner, "enqueue_structure_job", fake_enqueue_structure_job)

    result = enqueue_structure_jobs_for_scan_candidates(["sz000001", "sz000001"], priority=25)

    assert result["count"] == 3
    assert built == [("sz000001", "day"), ("sz000001", "30"), ("sz000001", "5")]
    assert [item[1] for item in enqueued] == ["day", "30", "5"]
    assert all(item[2]["priority"] == 25 for item in enqueued)
    assert all(item[2]["reason"] == "scanner_candidate" for item in enqueued)


def test_upsert_scan_result_preserves_ready_status_without_force():
    conn = make_scanner_push_conn()
    result = ScanResult(
        symbol="sz000001",
        strategy="war2",
        score=91,
        close=12.8,
        stop_loss=11.4,
        target=16.2,
        rr_ratio=2.4,
        atr_pct=0.05,
        volume_ratio=0.9,
        chan_desc="更新后的结构",
    )

    upsert_scan_result(conn, "2026-04-24", result, force=False)

    row = conn.execute(
        "SELECT status, score, chan_desc FROM scan_results WHERE symbol='sz000001'"
    ).fetchone()
    assert row["status"] == "ready"
    assert row["score"] == 91
    assert row["chan_desc"] == "更新后的结构"


def test_upsert_scan_result_resets_status_with_force():
    conn = make_scanner_push_conn()
    result = ScanResult(
        symbol="sz000001",
        strategy="war2",
        score=91,
        close=12.8,
        stop_loss=11.4,
        target=16.2,
        rr_ratio=2.4,
        atr_pct=0.05,
        volume_ratio=0.9,
        chan_desc="更新后的结构",
    )

    upsert_scan_result(conn, "2026-04-24", result, force=True)

    row = conn.execute(
        "SELECT status, score, chan_desc FROM scan_results WHERE symbol='sz000001'"
    ).fetchone()
    assert row["status"] == "pending"
    assert row["score"] == 91
