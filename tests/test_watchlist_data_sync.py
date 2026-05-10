"""Watchlist 数据闭环测试。"""

import os
import sqlite3
import sys
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import watchlist as watchlist_api
from server.workers import kline_sync_worker


def _memory_connection(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE watchlist_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0,
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
        INSERT INTO watchlist_groups (id, user_id, name, sort_order)
        VALUES (1, 1, '观察', 0);
        """
    )
    return conn


def test_add_watchlist_stock_queues_baostock_backfill(monkeypatch, tmp_path):
    db_path = tmp_path / "ctos.db"
    conn = _memory_connection(str(db_path))
    conn.close()
    queued = []

    def get_test_connection():
        test_conn = sqlite3.connect(db_path)
        test_conn.row_factory = sqlite3.Row
        return test_conn

    monkeypatch.setattr(watchlist_api, "get_connection", get_test_connection)
    monkeypatch.setattr(
        watchlist_api,
        "sync_new_watchlist_symbol",
        lambda symbol: queued.append(symbol),
    )

    app = FastAPI()
    app.include_router(watchlist_api.router, prefix="/watchlist")
    client = TestClient(app)

    response = client.post(
        "/watchlist/groups/观察/stocks",
        json={"symbol": "sh600118", "name": "中国卫星"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "ok"
    assert payload["symbol"] == "sh600118"
    assert payload["data_sync"]["status"] == "queued"
    assert payload["data_sync"]["quick_freqs"] == ["day", "5"]
    assert payload["data_sync"]["full_freqs"] == ["week", "day", "60", "30", "15", "5"]
    assert queued == ["sh.600118"]


def test_get_all_tracked_symbols_includes_watchlist(monkeypatch):
    app_conn = _memory_connection()
    app_conn.execute(
        "INSERT INTO watchlist_items (group_id, symbol, name, sort_order) VALUES (1, 'sz300866', '安克创新', 0)"
    )
    app_conn.execute(
        """
        CREATE TABLE positions (
            symbol TEXT NOT NULL,
            quantity INTEGER NOT NULL
        );
        """
    )
    app_conn.execute("INSERT INTO positions (symbol, quantity) VALUES ('sh600519', 100)")
    app_conn.commit()

    class FakeCursor:
        def __init__(self, rows):
            self._rows = rows

        def fetchall(self):
            return self._rows

    class LakeConn:
        def execute(self, _sql):
            return FakeCursor([{"symbol": "sh.600118"}])

    class NoCloseConnection:
        def __init__(self, conn):
            self._conn = conn

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def close(self):
            pass

    def fake_get_connection():
        return NoCloseConnection(app_conn)

    monkeypatch.setattr("server.db.kline_lake.get_lake_connection", lambda: LakeConn())
    monkeypatch.setattr("server.db.database.get_connection", fake_get_connection)

    symbols = kline_sync_worker._get_all_tracked_symbols()

    assert symbols == ["sh.600118", "sh.600519", "sz.300866"]


def test_sync_new_watchlist_symbol_runs_quick_then_full(monkeypatch):
    calls = []

    def fake_quick(symbol, freq):
        calls.append(("quick", symbol, freq))
        return 1

    def fake_full(symbol, freq):
        calls.append(("full", symbol, freq))
        return 2

    monkeypatch.setattr("server.services.baostock_service.fetch_klines_quick", fake_quick)
    monkeypatch.setattr("server.services.baostock_service.fetch_klines_sync", fake_full)
    monkeypatch.setattr(kline_sync_worker, "ALL_FREQS", ["week", "day", "60", "30", "15", "5"])

    result = kline_sync_worker.sync_new_watchlist_symbol("sz300866")

    assert result["symbol"] == "sz.300866"
    assert result["errors"] == []
    assert calls == [
        ("quick", "sz.300866", "day"),
        ("quick", "sz.300866", "5"),
        ("full", "sz.300866", "week"),
        ("full", "sz.300866", "day"),
        ("full", "sz.300866", "60"),
        ("full", "sz.300866", "30"),
        ("full", "sz.300866", "15"),
        ("full", "sz.300866", "5"),
    ]


def test_sync_new_watchlist_symbol_enqueues_all_changed_structure_levels(monkeypatch):
    enqueued_changes = []

    def fake_quick(_symbol, _freq):
        return 0

    def fake_full(_symbol, freq):
        return 1 if freq in {"week", "60", "15"} else 0

    def fake_enqueue(changes, **_kwargs):
        enqueued_changes.extend(changes)
        return {"count": len(changes), "items": []}

    monkeypatch.setattr("server.services.baostock_service.fetch_klines_quick", fake_quick)
    monkeypatch.setattr("server.services.baostock_service.fetch_klines_sync", fake_full)
    monkeypatch.setattr(kline_sync_worker, "ALL_FREQS", ["week", "day", "60", "30", "15", "5"])
    monkeypatch.setattr(kline_sync_worker, "enqueue_structure_jobs_for_changes", fake_enqueue)

    result = kline_sync_worker.sync_new_watchlist_symbol("sz300866")

    assert result["structure_jobs"]["count"] == 3
    assert enqueued_changes == [
        {"symbol": "sz.300866", "freq": "week", "written": 1},
        {"symbol": "sz.300866", "freq": "60", "written": 1},
        {"symbol": "sz.300866", "freq": "15", "written": 1},
    ]


def test_enqueue_structure_jobs_prioritizes_holdings(monkeypatch):
    enqueued = []

    def fake_build_formal_structure_key(symbol, freq, **kwargs):
        normalized = "sh.600519" if symbol in {"sh600519", "sh.600519"} else "sz.300866"
        return SimpleNamespace(
            symbol=normalized,
            freq=freq,
            hash=f"{normalized}:{freq}",
        ), {}

    def fake_enqueue_structure_job(structure_key, **kwargs):
        enqueued.append((structure_key.symbol, structure_key.freq, kwargs["priority"]))
        return {"status": "PENDING", "job_id": structure_key.hash, "enqueued": True, "bumped": False}

    monkeypatch.setattr(kline_sync_worker, "_get_holding_symbol_set", lambda: {"sh.600519"})
    monkeypatch.setattr("server.engines.structure.snapshot_query.build_formal_structure_key", fake_build_formal_structure_key)
    monkeypatch.setattr("server.engines.structure.structure_jobs.enqueue_structure_job", fake_enqueue_structure_job)

    result = kline_sync_worker.enqueue_structure_jobs_for_changes(
        [
            {"symbol": "sh600519", "freq": "day", "written": 10},
            {"symbol": "sz300866", "freq": "day", "written": 10},
        ],
        priority=80,
        holding_priority=95,
    )

    assert result["count"] == 2
    assert enqueued == [
        ("sh.600519", "day", 95),
        ("sz.300866", "day", 80),
    ]
    assert [item["priority"] for item in result["items"]] == [95, 80]
