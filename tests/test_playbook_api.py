"""Daily playbook API tests."""

import asyncio
import os
import sqlite3
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import playbook


class ConnWrapper:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, *args, **kwargs):
        return self.conn.execute(*args, **kwargs)

    def commit(self):
        return self.conn.commit()

    def close(self):
        pass


def make_conn():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            openid TEXT UNIQUE NOT NULL
        );
        INSERT INTO users (id, openid) VALUES (1, 'dev_user');

        CREATE TABLE positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT,
            quantity INTEGER NOT NULL,
            avg_cost REAL NOT NULL,
            updated_at DATETIME,
            UNIQUE(user_id, symbol)
        );

        CREATE TABLE scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            score REAL DEFAULT 0,
            close REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE watchlist_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0
        );

        CREATE TABLE watchlist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT,
            sort_order INTEGER DEFAULT 0
        );

        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            price REAL NOT NULL,
            quantity INTEGER NOT NULL,
            amount REAL NOT NULL,
            playbook_item_id INTEGER,
            plan_relationship TEXT DEFAULT 'UNKNOWN',
            discipline_tag TEXT,
            coach_event_id TEXT,
            traded_at DATETIME NOT NULL
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

        CREATE TABLE daily_playbooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            trade_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            generated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            source_json TEXT,
            summary_json TEXT,
            UNIQUE(user_id, trade_date)
        );

        CREATE TABLE daily_playbook_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playbook_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT,
            mode TEXT NOT NULL,
            plan_id TEXT,
            strategy_id TEXT,
            status TEXT NOT NULL DEFAULT 'WATCHING',
            trigger_json TEXT,
            invalidation_json TEXT,
            radar_snapshot_json TEXT,
            response_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    return conn


async def fake_radar(symbol, user_id):
    mode = "HOLDING" if symbol == "sh600519" else "EMPTY"
    return {
        "status": "success",
        "data": {
            "api_version": "radar.v1",
            "symbol": symbol,
            "mode": mode,
            "freshness": {"is_stale": False},
            "strategy": {"strategy_id": "holding_stage_manager" if mode == "HOLDING" else "war1_third_buy"},
            "entry_plan": None if mode == "HOLDING" else {
                "plan_id": "radar_empty_entry_plan",
                "status": "WATCHING",
                "title": "空仓入场观察",
                "conditions": [{"condition_id": "m5", "status": "WATCH"}],
                "risk": {"invalid_if": "跌回中枢"},
            },
            "holding_plan": {
                "plan_id": "holding_stage_manager",
                "status": "WATCHING",
                "risk": {"invalid_if": "跌破台阶止损"},
            } if mode == "HOLDING" else None,
        },
    }


def test_generate_today_playbook_is_idempotent_and_positions_first(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr(playbook, "get_connection", lambda: ConnWrapper(conn))
    monkeypatch.setattr(playbook, "_load_radar_contract", fake_radar)

    today = playbook._today()
    conn.execute(
        "INSERT INTO positions (user_id, symbol, name, quantity, avg_cost) VALUES (1, 'sh600519', '贵州茅台', 100, 100.0)"
    )
    conn.execute(
        "INSERT INTO scan_results (scan_date, symbol, strategy, status, score, close) VALUES (?, 'sz000001', 'war1', 'ready', 80, 12.3)",
        (today,),
    )
    conn.commit()

    request = playbook.GeneratePlaybookRequest(user_id=1, sources=["positions", "scanner"], max_items=8)
    first = asyncio.run(playbook.generate_today_playbook(request))["data"]
    second = asyncio.run(playbook.generate_today_playbook(request))["data"]

    assert first["id"] == second["id"]
    assert [item["symbol"] for item in first["items"]] == ["sh600519", "sz000001"]
    assert first["items"][0]["mode"] == "HOLDING"
    assert first["items"][1]["mode"] == "EMPTY"
    assert conn.execute("SELECT COUNT(*) FROM daily_playbook_items").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM coach_events").fetchone()[0] == 2


def test_record_item_response_updates_item_and_logs_event(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr(playbook, "get_connection", lambda: ConnWrapper(conn))
    conn.execute("INSERT INTO daily_playbooks (id, user_id, trade_date) VALUES (1, 1, ?)", (playbook._today(),))
    conn.execute(
        """
        INSERT INTO daily_playbook_items (id, playbook_id, user_id, symbol, mode, status)
        VALUES (7, 1, 1, 'sh600519', 'HOLDING', 'WATCHING')
        """
    )
    conn.commit()

    response = playbook.record_item_response(
        7,
        playbook.PlaybookResponseRequest(response="EXECUTED", note="按计划减仓"),
        user_id=1,
    )

    assert response["data"]["item"]["status"] == "EXECUTED"
    event = conn.execute("SELECT * FROM coach_events").fetchone()
    assert event["event_type"] == "USER_MARKED_ACTION"
    assert "PLAYBOOK_EXECUTED" in event["evidence_json"]


def test_classify_trade_plan_updates_trade_and_logs_event(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr(playbook, "get_connection", lambda: ConnWrapper(conn))
    conn.execute(
        """
        INSERT INTO trades (id, user_id, symbol, direction, price, quantity, amount, traded_at)
        VALUES (3, 1, 'sz000001', 'BUY', 10.0, 100, 1000.0, '2026-04-26T10:00:00')
        """
    )
    conn.commit()

    response = playbook.classify_trade_plan(
        3,
        playbook.TradePlanClassifyRequest(
            plan_relationship="UNPLANNED",
            discipline_tag="追高",
            playbook_item_id=None,
        ),
        user_id=1,
    )

    assert response["data"]["plan_relationship"] == "UNPLANNED"
    assert response["data"]["discipline_tag"] == "追高"
    event = conn.execute("SELECT * FROM coach_events").fetchone()
    assert event["event_type"] == "USER_MARKED_ACTION"
