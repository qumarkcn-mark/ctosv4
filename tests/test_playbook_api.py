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
            source TEXT,
            source_json TEXT,
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

        CREATE TABLE ai_reasoning_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            mode TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            prompt_version TEXT NOT NULL,
            model_name TEXT NOT NULL,
            structure_fingerprint TEXT NOT NULL,
            transcript_json TEXT NOT NULL,
            memory_context_json TEXT,
            ai_output_json TEXT,
            gate_result_json TEXT NOT NULL,
            gate_status TEXT NOT NULL,
            model_route_json TEXT,
            replay_status TEXT NOT NULL DEFAULT 'PENDING',
            replay_score REAL,
            outcome_json TEXT,
            disclaimer TEXT NOT NULL DEFAULT '仅供参考，不构成投资建议'
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
    assert [item["source"] for item in first["items"]] == ["positions", "scanner"]
    assert first["items"][0]["source_json"]["position"]["quantity"] == 100
    assert first["items"][1]["source_json"]["scanner"]["strategy"] == "war1"
    assert first["items"][0]["mode"] == "HOLDING"
    assert first["items"][1]["mode"] == "EMPTY"
    assert conn.execute("SELECT COUNT(*) FROM daily_playbook_items").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM coach_events").fetchone()[0] == 2


def test_generate_today_playbook_embeds_ai_native_battle_focus(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr(playbook, "get_connection", lambda: ConnWrapper(conn))

    async def risky_holding_radar(symbol, user_id):
        return {
            "status": "success",
            "data": {
                "symbol": symbol,
                "mode": "HOLDING",
                "freshness": {"is_stale": False},
                "structure": {
                    "levels": {
                        "day": {"level": "day", "price": 9.85, "state": "UPWARD_LEAVING", "zg": 10.8, "zd": 9.2},
                        "30": {"level": "30", "price": 9.85, "state": "IN_CENTER_OSC", "zg": 10.4, "zd": 9.7, "dd": 9.6},
                        "5": {"level": "5", "price": 9.85, "state": "DOWN", "zg": 10.1, "zd": 9.8},
                    }
                },
                "algorithm_v2": {
                    "path": "PULLBACK",
                    "phase": "DEFENSE",
                    "current_scenario_id": "B",
                    "boundaries": {
                        "confirm": [{"label": "确认", "value": 10.4}],
                        "maintain": [{"label": "观察", "value": 9.8}],
                        "invalidate": [{"label": "失效", "value": 9.7}],
                    },
                },
                "strategy": {"strategy_id": "holding_stage_manager"},
                "position_context": {
                    "is_holding": True,
                    "label": "持仓观察",
                    "avg_cost": 10.0,
                    "current_price": 9.85,
                    "pnl_pct": -6.0,
                    "risk_flags": ["STRUCTURE_AGAINST_POSITION"],
                },
                "coach_action": {
                    "priority": "HIGH",
                    "focus": "只看防守线是否被收回",
                    "risk_lines": [{"label": "风控边界", "price": 9.7, "distance_pct": -1.52}],
                    "nearest_risk_line": {"label": "风控边界", "price": 9.7, "distance_pct": -1.52},
                },
                "holding_plan": {
                    "plan_id": "holding_stage_manager",
                    "status": "WATCHING",
                    "risk": {"invalid_if": "跌破台阶止损"},
                },
            },
        }

    monkeypatch.setattr(playbook, "_load_radar_contract", risky_holding_radar)
    conn.execute(
        "INSERT INTO positions (user_id, symbol, name, quantity, avg_cost) VALUES (1, 'sz300394', '天孚通信', 100, 10.0)"
    )
    conn.commit()

    request = playbook.GeneratePlaybookRequest(user_id=1, sources=["positions"], max_items=8)
    payload = asyncio.run(playbook.generate_today_playbook(request))["data"]
    item = payload["items"][0]

    assert item["status"] == "TRIGGERED"
    assert item["trigger"]["ai_native"]["priority"] == "HIGH"
    assert item["trigger"]["ai_native"]["nearest_risk_line"]["price"] == 9.7
    assert "持仓先看" in item["trigger"]["ai_native"]["next_focus"]


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


def test_generate_today_report_persists_summary(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr(playbook, "get_connection", lambda: ConnWrapper(conn))
    today = playbook._today()
    ai_native = {
        "primary_path": "C",
        "primary_name": "转弱失效",
        "primary_score": 52,
        "priority": "HIGH",
        "nearest_risk_line": {"label": "风控边界", "price": 9.7},
    }
    conn.execute("INSERT INTO daily_playbooks (id, user_id, trade_date) VALUES (1, 1, ?)", (today,))
    conn.execute(
        """
        INSERT INTO daily_playbook_items (
            id, playbook_id, user_id, symbol, mode, status, trigger_json, response_json
        )
        VALUES (9, 1, 1, 'sz300394', 'HOLDING', 'IGNORED', ?, ?)
        """,
        (
            playbook._json({"ai_native": ai_native}),
            playbook._json({"response": "IGNORED"}),
        ),
    )
    conn.execute(
        """
        INSERT INTO trades (user_id, symbol, direction, price, quantity, amount, plan_relationship, traded_at)
        VALUES (1, 'sz300394', 'BUY', 10.0, 100, 1000.0, 'UNPLANNED', ?)
        """,
        (f"{today}T10:00:00",),
    )
    conn.execute(
        """
        INSERT INTO ai_reasoning_runs (
            user_id, symbol, mode, created_at, prompt_version, model_name,
            structure_fingerprint, transcript_json, ai_output_json, gate_result_json,
            gate_status, replay_status, replay_score, outcome_json
        )
        VALUES (1, 'sz300394', 'HOLDING', ?, 'ai_native_radar.v1', 'deepseek-v4-pro',
                'fp', '{}', '{}', '{"score": 90}', 'PASS', 'REVIEWED', 43.0, ?)
        """,
        (
            f"{today}T15:05:00",
            playbook._json({
                "predicted_hypothesis": "A",
                "actual_hypothesis": "C",
                "matched": False,
                "tags": ["OVER_OPTIMISTIC", "REPEATED_DIVERGENCE_RISK"],
                "sample_quality": "HIGH",
                "learning_weight": 1.2,
                "notes": "跌破失效边界",
            }),
        ),
    )
    conn.commit()

    response = playbook.generate_today_report(playbook.PlaybookReportRequest(user_id=1))

    report = response["data"]
    assert report["persisted"] is True
    assert report["summary"]["high_priority_items"] == 1
    assert report["summary"]["unplanned_trades"] == 1
    assert report["summary"]["ai_reviewed_runs"] == 1
    assert report["summary"]["ai_wrong_runs"] == 1
    assert report["ai_path_distribution"]["C"] == 1
    assert report["ai_settlement"]["wrong_runs"] == 1
    assert report["ai_settlement"]["tag_counts"]["REPEATED_DIVERGENCE_RISK"] == 1
    assert report["ai_settlement"]["quality_counts"]["HIGH"] == 1
    assert "高优先级风险项被忽略" in report["discipline_flags"]
    assert "存在 AI 推演未兑现" in report["discipline_flags"]
    stored = conn.execute("SELECT status, summary_json FROM daily_playbooks WHERE id = 1").fetchone()
    assert stored["status"] == "REVIEWED"
    assert "daily_playbook_report.v1" in stored["summary_json"]


def test_playbook_payload_backfills_legacy_item_source():
    conn = make_conn()
    today = playbook._today()
    conn.execute(
        "INSERT INTO positions (user_id, symbol, name, quantity, avg_cost) VALUES (1, 'sh600519', '贵州茅台', 100, 100.0)"
    )
    conn.execute("INSERT INTO daily_playbooks (id, user_id, trade_date) VALUES (1, 1, ?)", (today,))
    conn.execute(
        """
        INSERT INTO daily_playbook_items (id, playbook_id, user_id, symbol, mode, status)
        VALUES (8, 1, 1, 'sh600519', 'HOLDING', 'WATCHING')
        """
    )
    conn.commit()

    payload = playbook._playbook_payload(conn, 1, today)

    assert payload["items"][0]["source"] == "positions"
    assert payload["items"][0]["source_json"]["position"]["avg_cost"] == 100.0
    stored = conn.execute("SELECT source, source_json FROM daily_playbook_items WHERE id = 8").fetchone()
    assert stored["source"] == "positions"
    assert "avg_cost" in stored["source_json"]


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
