"""Watchlist 数据闭环测试。"""

import asyncio
import os
import sqlite3
import sys

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


def test_enqueue_structure_jobs_for_changes_enqueues_czsc_v5_snapshots(monkeypatch):
    calls = []

    def fake_prewarm(**kwargs):
        calls.append(kwargs)
        return {
            "count": len(kwargs["levels"]),
            "items": [
                {"symbol": kwargs["symbols"][0], "level": level, "status": "PENDING", "engine": "czsc", "enqueued": True}
                for level in kwargs["levels"]
            ],
        }

    monkeypatch.setattr(
        "server.engines.ai_native.czsc_snapshot_service.prewarm_structure_snapshots",
        fake_prewarm,
    )
    monkeypatch.setattr(
        "server.engines.ai_native.universe_resolver.list_interested_user_ids_for_symbol",
        lambda _symbol: [1],
    )
    monkeypatch.setattr(
        "server.engines.ai_native.universe_resolver.has_active_position_for_symbol",
        lambda _symbol: True,
    )

    result = kline_sync_worker.enqueue_structure_jobs_for_changes(
        [
            {"symbol": "sh600519", "freq": "day", "written": 10},
            {"symbol": "sh600519", "freq": "15", "written": 10},
            {"symbol": "sz300866", "freq": "day", "written": 10},
        ],
        priority=80,
        holding_priority=95,
    )

    assert result["engine"] == "czsc"
    assert result["skipped"] is False
    assert result["count"] == 2
    assert [call["priority"] for call in calls] == [95, 95]
    assert calls[0]["levels"] == ["day"]
    assert calls[0]["requested_by_user_id"] == 1
    assert all(item["level"] != "15" for item in result["items"])


def test_enqueue_structure_jobs_for_changes_does_not_count_completed_jobs(monkeypatch):
    def fake_prewarm(**kwargs):
        return {
            "count": len(kwargs["levels"]),
            "items": [
                {"symbol": kwargs["symbols"][0], "level": "day", "status": "SUCCESS", "engine": "czsc", "enqueued": False}
            ],
        }

    monkeypatch.setattr(
        "server.engines.ai_native.czsc_snapshot_service.prewarm_structure_snapshots",
        fake_prewarm,
    )
    monkeypatch.setattr(
        "server.engines.ai_native.universe_resolver.list_interested_user_ids_for_symbol",
        lambda _symbol: [1],
    )
    monkeypatch.setattr(
        "server.engines.ai_native.universe_resolver.has_active_position_for_symbol",
        lambda _symbol: False,
    )

    result = kline_sync_worker.enqueue_structure_jobs_for_changes(
        [{"symbol": "sh600519", "freq": "day", "written": 10}],
        priority=80,
    )

    assert result["count"] == 0
    assert result["items"][0]["status"] == "SUCCESS"


def test_prewarm_ai_structure_universe_groups_jobs_by_symbol_priority(monkeypatch):
    snapshot_calls = []
    context_calls = []

    monkeypatch.setattr(
        "server.engines.ai_native.universe_resolver.list_ai_native_user_ids",
        lambda limit=None: [1],
    )
    monkeypatch.setattr(
        "server.engines.ai_native.universe_resolver.resolve_ai_native_universe",
        lambda user_id, sources: [
            {"symbol": "sh.600519", "priority": 100, "sources": ["positions"], "has_position": True},
            {"symbol": "sz.000001", "priority": 80, "sources": ["recent_chat"], "has_position": False},
            {"symbol": "sh.600000", "priority": 60, "sources": ["watchlist"], "has_position": False},
        ],
    )

    def fake_snapshot_prewarm(**kwargs):
        snapshot_calls.append(kwargs)
        return {
            "items": [
                {"symbol": symbol, "status": "PENDING", "enqueued": True}
                for symbol in kwargs["symbols"]
            ]
        }

    def fake_context_prewarm(**kwargs):
        context_calls.append(kwargs)
        return {
            "items": [
                {"symbol": symbol, "status": "PENDING", "enqueued": True}
                for symbol in kwargs["symbols"]
            ]
        }

    monkeypatch.setattr(
        "server.engines.ai_native.czsc_snapshot_service.prewarm_structure_snapshots",
        fake_snapshot_prewarm,
    )
    monkeypatch.setattr(
        "server.engines.ai_native.structure_context_service.prewarm_ai_structure_contexts",
        fake_context_prewarm,
    )

    result = kline_sync_worker.prewarm_ai_structure_universe_for_tracked_users(priority=70)

    assert [call["priority"] for call in snapshot_calls] == [100, 80, 60]
    assert [call["symbols"] for call in snapshot_calls] == [["sh.600519"], ["sz.000001"], ["sh.600000"]]
    assert [call["priority"] for call in context_calls] == [90, 70]
    assert [call["symbols"] for call in context_calls] == [["sh.600519"], ["sz.000001"]]
    assert result["count"] == 5
    assert result["users"][0]["snapshot_symbols"] == 3
    assert result["users"][0]["context_symbols"] == 2
    assert result["users"][0]["priority_buckets"] == [
        {"priority": 100, "symbols": ["sh.600519"]},
        {"priority": 80, "symbols": ["sz.000001"]},
        {"priority": 60, "symbols": ["sh.600000"]},
    ]


def test_refresh_unified_reasoning_after_kline_sync_uses_positions_and_watchlist(monkeypatch):
    calls = []

    monkeypatch.setattr(kline_sync_worker.config, "AI_UNIFIED_REASONING_AFTER_KLINE_SYNC_ENABLED", True)
    monkeypatch.setattr(kline_sync_worker, "list_ai_native_user_ids", lambda limit=None: [1])
    monkeypatch.setattr(
        kline_sync_worker,
        "resolve_ai_native_universe",
        lambda user_id, sources: [
            {"symbol": "sh.600519", "priority": 100, "sources": ["positions"]},
            {"symbol": "sz.000001", "priority": 60, "sources": ["watchlist"]},
            {"symbol": "sh.600000", "priority": 60, "sources": ["watchlist"]},
        ],
    )

    async def fake_trigger_unified_reasoning(**kwargs):
        calls.append(kwargs)
        return {"symbol": kwargs["symbol"]}

    monkeypatch.setattr(
        "server.engines.ai_native.unified_reasoning_service.trigger_unified_reasoning",
        fake_trigger_unified_reasoning,
    )

    result = asyncio.run(kline_sync_worker.refresh_unified_reasoning_for_tracked_users(max_symbols_per_user=2))

    assert result["generated"] == 2
    assert result["errors"] == []
    assert [call["symbol"] for call in calls] == ["sh.600519", "sz.000001"]
    assert all(call["user_id"] == 1 for call in calls)


def test_refresh_unified_reasoning_after_kline_sync_can_be_disabled(monkeypatch):
    monkeypatch.setattr(kline_sync_worker.config, "AI_UNIFIED_REASONING_AFTER_KLINE_SYNC_ENABLED", False)

    result = asyncio.run(kline_sync_worker.refresh_unified_reasoning_for_tracked_users())

    assert result == {"generated": 0, "errors": [], "skipped": True, "reason": "DISABLED"}
