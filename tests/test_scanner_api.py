"""选股扫描器 API contract 测试。"""

import os
import sqlite3
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException
from fastapi import BackgroundTasks

from server.api import scanner


def make_conn():
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
            fundamental TEXT,
            llm_verdict TEXT,
            llm_summary TEXT,
            llm_pros TEXT,
            llm_cons TEXT,
            llm_red_flags TEXT,
            fundamental_at DATETIME,
            retry_count INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE watchlist_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, name)
        );
        CREATE TABLE watchlist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT,
            sort_order INTEGER DEFAULT 0,
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(group_id, symbol)
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
        INSERT INTO scan_results
            (scan_date, symbol, strategy, status, score, close,
             stop_loss, target, rr_ratio, atr_pct, volume_ratio,
             chan_desc, fundamental, llm_verdict, llm_summary,
             llm_pros, llm_cons, llm_red_flags)
        VALUES
            ('2026-04-24', 'sz000001', 'war1', 'ready', 82, 10.5,
             9.8, 12.0, 2.1, 0.05, 0.7,
             '日线三买', '{"industry":"银行"}', '支持', '基本面稳定',
             '["ROE稳定"]', '["弹性一般"]', '[]'),
            ('2026-04-24', 'sh600519', 'war2', 'pending', 70, 1500,
             1400, 1800, 2.0, 0.04, 0.8,
             '趋势台阶', NULL, NULL, NULL, NULL, NULL, NULL);
        """
    )
    return conn


class ConnWrapper:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, *args, **kwargs):
        return self.conn.execute(*args, **kwargs)

    def commit(self):
        return self.conn.commit()

    def rollback(self):
        return self.conn.rollback()

    def close(self):
        pass


def reset_scan_job():
    scanner._update_scan_job(
        running=False,
        last_status="idle",
        last_scan_date=None,
        last_candidate_count=0,
        last_error=None,
    )


def test_list_scan_results_returns_ready_with_parsed_research(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr(scanner, "get_connection", lambda: ConnWrapper(conn))

    response = scanner.list_scan_results(scan_date="2026-04-24", status="ready", limit=50)

    assert response["count"] == 1
    item = response["results"][0]
    assert item["symbol"] == "sz000001"
    assert item["fundamental"] == {"industry": "银行"}
    assert item["llm_pros"] == ["ROE稳定"]
    assert item["llm_cons"] == ["弹性一般"]
    assert item["llm_red_flags"] == []
    assert item["strategy"] == "war1"
    assert item["strategy_code"] == "war1"
    assert item["strategy_id"] == "war1_third_buy"
    assert item["strategy_version"] == "1.0.0"
    assert item["strategy_name"] == "战法一：日线三买"
    assert item["strategy_type"] == "战法一"
    assert item["strategy_contract"]["status"] == "TRIGGERED"
    assert "plans" in item["strategy_contract"]["outputs"]


def test_list_scan_results_maps_pending_strategy_contract(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr(scanner, "get_connection", lambda: ConnWrapper(conn))

    response = scanner.list_scan_results(scan_date="2026-04-24", status="all", limit=50)

    pending = next(item for item in response["results"] if item["symbol"] == "sh600519")
    assert pending["strategy"] == "war2"
    assert pending["strategy_code"] == "war2"
    assert pending["strategy_id"] == "war2_trend_step"
    assert pending["strategy_version"] == "1.0.0"
    assert pending["strategy_name"] == "战法二：趋势台阶"
    assert pending["strategy_contract"]["status"] == "WATCHING"


def test_scan_status_counts_all_states(monkeypatch):
    conn = make_conn()
    reset_scan_job()
    monkeypatch.setattr(scanner, "get_connection", lambda: ConnWrapper(conn))

    response = scanner.scan_status(scan_date="2026-04-24")

    assert response["counts"] == {
        "pending": 1,
        "analyzing": 0,
        "ready": 1,
        "failed": 0,
    }
    assert response["total"] == 2
    assert response["job"]["last_status"] == "idle"


def test_run_scan_queues_background_job(monkeypatch):
    reset_scan_job()

    monkeypatch.setattr("server.workers.scanner.get_today", lambda: "2026-04-24")
    tasks = BackgroundTasks()

    response = scanner.run_scan(background_tasks=tasks, force=True)

    assert response == {
        "status": "queued",
        "scan_date": "2026-04-24",
        "candidate_count": 0,
    }
    assert scanner._scan_job_snapshot()["running"] is True
    assert len(tasks.tasks) == 1


def test_run_scan_returns_running_when_job_exists(monkeypatch):
    reset_scan_job()
    scanner._update_scan_job(
        running=True,
        last_status="running",
        last_scan_date="2026-04-24",
        last_candidate_count=3,
        last_error=None,
    )
    monkeypatch.setattr("server.workers.scanner.get_today", lambda: "2026-04-24")
    tasks = BackgroundTasks()

    response = scanner.run_scan(background_tasks=tasks, force=False)

    assert response == {
        "status": "running",
        "scan_date": "2026-04-24",
        "candidate_count": 3,
    }
    assert len(tasks.tasks) == 0


def test_scanner_admin_token_blocks_mutations_when_configured(monkeypatch):
    monkeypatch.setattr(scanner, "SCANNER_ADMIN_TOKEN", "secret")

    try:
        scanner.require_scanner_admin(x_scanner_admin_token="wrong")
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("expected HTTPException")

    assert scanner.require_scanner_admin(x_scanner_admin_token="secret") is None


def test_delete_scan_result_removes_row_and_404s(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr(scanner, "get_connection", lambda: ConnWrapper(conn))

    assert scanner.delete_scan_result(1) == {"status": "ok", "deleted_id": 1}
    response = scanner.list_scan_results(scan_date="2026-04-24", status="ready", limit=50)
    assert response["results"] == []
    event = conn.execute("SELECT * FROM coach_events WHERE symbol = 'sz000001'").fetchone()
    assert event["event_type"] == "USER_MARKED_ACTION"
    assert event["source"] == "scanner_api"

    try:
        scanner.delete_scan_result(1)
    except HTTPException as exc:
        assert exc.status_code == 404
    else:
        raise AssertionError("expected HTTPException")


def test_observe_scan_result_creates_group_adds_stock_and_deletes_candidate(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr(scanner, "get_connection", lambda: ConnWrapper(conn))

    response = scanner.observe_scan_result(1, group_name="观察", user_id=1)

    assert response == {
        "status": "ok",
        "symbol": "sz000001",
        "group_name": "观察",
        "deleted_id": 1,
    }
    group = conn.execute(
        "SELECT id, name FROM watchlist_groups WHERE user_id = ? AND name = ?",
        (1, "观察"),
    ).fetchone()
    assert group is not None
    item = conn.execute(
        "SELECT symbol, name FROM watchlist_items WHERE group_id = ?",
        (group["id"],),
    ).fetchone()
    assert dict(item) == {"symbol": "sz000001", "name": "sz000001"}
    assert conn.execute("SELECT id FROM scan_results WHERE id = 1").fetchone() is None
    event = conn.execute("SELECT * FROM coach_events WHERE symbol = 'sz000001'").fetchone()
    assert event["event_type"] == "USER_MARKED_ACTION"
    assert "SCAN_RESULT_OBSERVED" in event["evidence_json"]


def test_observe_scan_result_moves_existing_stock_from_other_group(monkeypatch):
    conn = make_conn()
    conn.execute(
        "INSERT INTO watchlist_groups (user_id, name, sort_order) VALUES (?, ?, ?)",
        (1, "短线", 0),
    )
    conn.execute(
        "INSERT INTO watchlist_groups (user_id, name, sort_order) VALUES (?, ?, ?)",
        (1, "观察", 1),
    )
    short_group = conn.execute(
        "SELECT id FROM watchlist_groups WHERE user_id = 1 AND name = '短线'"
    ).fetchone()
    observe_group = conn.execute(
        "SELECT id FROM watchlist_groups WHERE user_id = 1 AND name = '观察'"
    ).fetchone()
    conn.execute(
        "INSERT INTO watchlist_items (group_id, symbol, name, sort_order) VALUES (?, ?, ?, ?)",
        (short_group["id"], "sh600519", "贵州茅台", 1),
    )
    conn.commit()
    monkeypatch.setattr(scanner, "get_connection", lambda: ConnWrapper(conn))

    scanner.observe_scan_result(2, group_name="观察", user_id=1)

    old_item = conn.execute(
        "SELECT id FROM watchlist_items WHERE group_id = ? AND symbol = ?",
        (short_group["id"], "sh600519"),
    ).fetchone()
    new_item = conn.execute(
        "SELECT symbol FROM watchlist_items WHERE group_id = ? AND symbol = ?",
        (observe_group["id"], "sh600519"),
    ).fetchone()
    assert old_item is None
    assert new_item["symbol"] == "sh600519"
    assert conn.execute("SELECT id FROM scan_results WHERE id = 2").fetchone() is None
