"""Price monitor alert contract tests."""

import asyncio
import json
import os
import sqlite3
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.workers import price_monitor as price_monitor_module
from server.workers.price_monitor import PriceMonitor, _build_alert_strategy_contract, _parse_hhmm


def make_alert_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
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
        """
    )
    return conn


def test_alert_strategy_contract_maps_entry_signal_to_strategy_version():
    contract = _build_alert_strategy_contract("CHAN_ENTRY_SIGNAL", "战法二")

    assert contract["strategy_id"] == "war2_trend_step"
    assert contract["strategy_version"] == "1.0.0"
    assert contract["status"] == "TRIGGERED"


def test_parse_hhmm_accepts_valid_window_and_rejects_bad_value():
    assert str(_parse_hhmm("15:35")) == "15:35:00"
    assert _parse_hhmm("bad") is None


def test_trigger_alert_db_persists_strategy_contract_and_disclaimer():
    conn = make_alert_conn()
    monitor = PriceMonitor()

    message = monitor._trigger_alert_db(
        conn,
        {
            "user_id": 1,
            "symbol": "sh600519",
            "name": "贵州茅台",
            "stop_loss_price": 0,
            "_strategy_type": "战法一",
        },
        current_price=0,
        alert_type="CHAN_ENTRY_SIGNAL",
    )

    row = conn.execute("SELECT * FROM alerts").fetchone()
    contract = json.loads(row["strategy_contract"])
    assert row["strategy_id"] == "war1_third_buy"
    assert row["strategy_version"] == "1.0.0"
    assert contract["strategy_id"] == "war1_third_buy"
    assert "仅供参考" in row["message"]
    assert row["message"] == message
    event = conn.execute("SELECT * FROM coach_events").fetchone()
    trigger = conn.execute("SELECT * FROM strategy_triggers").fetchone()
    delivery = conn.execute("SELECT * FROM alert_deliveries").fetchone()
    assert event["event_type"] == "ALERT_CANDIDATE_CREATED"
    assert trigger["strategy_id"] == "war1_third_buy"
    assert delivery["delivery_status"] == "CREATED"


def test_price_monitor_scans_ai_structure_reminders_without_positions(monkeypatch):
    monitor = PriceMonitor()
    sent = []
    captured = {}

    async def fake_get_batch_prices(symbols):
        captured["symbols"] = symbols
        return {"sh.600519": {"price": 1342.17}}

    def fake_scan_structure_reminders(prices):
        captured["prices"] = prices
        return {
            "count": 1,
            "items": [
                {
                    "user_id": 1,
                    "symbol": "sh.600519",
                    "message": "sh.600519 跌破失败线，提醒复核。仅供参考，不构成投资建议",
                }
            ],
        }

    async def fake_send_stop_loss_alert(user_id, msg):
        sent.append((user_id, msg))

    monkeypatch.setattr(monitor, "_db_get_positions", lambda: [])
    monkeypatch.setattr(price_monitor_module, "list_active_reminder_symbols", lambda: ["sh.600519"])
    monkeypatch.setattr(price_monitor_module, "get_batch_prices", fake_get_batch_prices)
    monkeypatch.setattr(price_monitor_module, "scan_structure_reminders", fake_scan_structure_reminders)
    monkeypatch.setattr(price_monitor_module, "send_stop_loss_alert", fake_send_stop_loss_alert)

    async def run_check():
        await monitor._check_stop_losses()
        await asyncio.sleep(0)

    asyncio.run(run_check())

    assert captured["symbols"] == ["sh.600519"]
    assert captured["prices"] == {"sh.600519": {"price": 1342.17}}
    assert sent == [(1, "sh.600519 跌破失败线，提醒复核。仅供参考，不构成投资建议")]
