"""Watchlist 数据闭环测试。"""

import asyncio
import os
import sqlite3
import sys
from datetime import date, datetime
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


def test_add_watchlist_stock_queues_tdx_init_when_baostock_auto_sync_disabled(monkeypatch, tmp_path):
    db_path = tmp_path / "ctos.db"
    conn = _memory_connection(str(db_path))
    conn.close()
    queued = []

    def get_test_connection():
        test_conn = sqlite3.connect(db_path)
        test_conn.row_factory = sqlite3.Row
        return test_conn

    monkeypatch.setattr(watchlist_api, "get_connection", get_test_connection)
    monkeypatch.setattr(watchlist_api.config, "BAOSTOCK_AUTO_SYNC_ENABLED", False)
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
    assert payload["data_sync"]["source"] == "tdx"
    assert payload["data_sync"]["reason"] == "TDX_SINGLE_SYMBOL_INIT"
    assert payload["data_sync"]["quick_freqs"] == ["day", "5"]
    assert payload["data_sync"]["full_freqs"] == ["week", "day", "60", "30", "15", "5", "1"]
    assert queued == ["sh.600118"]


def test_watchlist_stock_init_status_reports_tdx_and_snapshot_readiness(monkeypatch):
    def fake_query_lake(symbol, freq, **kwargs):
        assert symbol == "sh.600118"
        assert kwargs["source"] == "tdx"
        assert kwargs["adjustflag"] in {"2", "3"}
        if freq in {"week", "day", "30", "5"}:
            return [{"date": "2026-05-22", "close": 10}]
        return []

    def fake_snapshot_status_batch(**kwargs):
        return {
            "sh.600118": {
                level: {
                    "status": "fresh",
                    "freshness": {"last_bar_at": "2026-05-22"},
                    "job": None,
                }
                for level in ("week", "day", "30", "5")
            }
        }

    monkeypatch.setattr(watchlist_api, "query_lake_klines", fake_query_lake)
    monkeypatch.setattr(watchlist_api, "get_snapshot_status_batch", fake_snapshot_status_batch)

    app = FastAPI()
    app.include_router(watchlist_api.router, prefix="/watchlist")
    client = TestClient(app)

    response = client.get("/watchlist/stocks/sh600118/init-status")
    payload = response.json()

    assert response.status_code == 200
    assert payload["symbol"] == "sh.600118"
    assert payload["stage"] == "ready_for_reasoning"
    assert payload["ready_for_reasoning"] is True
    assert payload["kline"]["ready"] is True
    assert payload["snapshots"]["ready"] is True


def test_watchlist_stock_init_status_falls_back_to_tdx_raw_lake(monkeypatch):
    calls = []

    def fake_query_lake(symbol, freq, **kwargs):
        calls.append((freq, kwargs["adjustflag"], kwargs["source"]))
        if kwargs["adjustflag"] == "2":
            return []
        if freq in {"week", "day", "30", "5"}:
            return [{"date": "2026-04-30", "close": 10}]
        return []

    monkeypatch.setattr(watchlist_api, "query_lake_klines", fake_query_lake)
    monkeypatch.setattr(watchlist_api, "get_snapshot_status_batch", lambda **_kwargs: {"sh.600036": {}})

    app = FastAPI()
    app.include_router(watchlist_api.router, prefix="/watchlist")
    client = TestClient(app)

    response = client.get("/watchlist/stocks/sh600036/init-status")
    payload = response.json()

    assert response.status_code == 200
    assert payload["kline"]["ready"] is True
    day_item = next(item for item in payload["kline"]["items"] if item["freq"] == "day")
    assert day_item["adjustflag"] == "3"
    assert ("day", "2", "tdx") in calls
    assert ("day", "3", "tdx") in calls


def test_watchlist_stock_init_status_marks_raw_when_qfq_is_stale(monkeypatch):
    def fake_query_lake(symbol, freq, **kwargs):
        if freq != "day":
            return [{"date": "2026-05-22", "close": 10}]
        if kwargs["adjustflag"] == "2":
            return [{"date": "2026-05-21", "close": 9}]
        return [{"date": "2026-05-22", "close": 10}]

    monkeypatch.setattr(watchlist_api, "query_lake_klines", fake_query_lake)
    monkeypatch.setattr(watchlist_api, "get_snapshot_status_batch", lambda **_kwargs: {"sh.600036": {}})

    app = FastAPI()
    app.include_router(watchlist_api.router, prefix="/watchlist")
    client = TestClient(app)

    response = client.get("/watchlist/stocks/sh600036/init-status")
    payload = response.json()

    assert response.status_code == 200
    day_item = next(item for item in payload["kline"]["items"] if item["freq"] == "day")
    assert day_item["latest"] == "2026-05-22"
    assert day_item["adjustflag"] == "3"


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


def test_scheduled_sync_scope_splits_daily_and_full_windows():
    assert kline_sync_worker._scheduled_sync_scope(datetime(2026, 5, 19, 16, 0)) is None
    assert kline_sync_worker._scheduled_sync_scope(datetime(2026, 5, 19, 17, 30)) == "daily"
    assert kline_sync_worker._scheduled_sync_scope(datetime(2026, 5, 19, 20, 30)) == "full"
    assert kline_sync_worker._freqs_for_sync_scope("daily") == ["day"]
    assert kline_sync_worker._freqs_for_sync_scope("full") == ["week", "day", "60", "30", "15", "5", "1"]


def test_daily_sync_does_not_block_later_minute_sync():
    worker = kline_sync_worker.KlineSyncWorker()
    today = date(2026, 5, 19)

    worker._mark_scope_synced("daily", today)

    assert worker._has_synced_scope_today("daily", today) is True
    assert worker._has_synced_scope_today("full", today) is False

    worker._mark_scope_synced("full", today)

    assert worker._has_synced_scope_today("daily", today) is True
    assert worker._has_synced_scope_today("full", today) is True


def test_kline_sync_loop_skips_startup_sync_by_default(monkeypatch):
    calls = []
    worker = kline_sync_worker.KlineSyncWorker()
    worker._running = True

    async def fake_do_sync(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(kline_sync_worker, "STARTUP_DELAY", 0)
    monkeypatch.setattr(kline_sync_worker, "CHECK_INTERVAL", 3600)
    monkeypatch.setattr(kline_sync_worker.config, "TDX_LOCAL_HISTORY_SYNC_ON_STARTUP_ENABLED", False, raising=False)
    monkeypatch.setattr(worker, "_do_sync", fake_do_sync)

    async def run_once():
        task = asyncio.create_task(worker._sync_loop())
        await asyncio.sleep(0.01)
        worker._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_once())

    assert calls == []


def test_kline_sync_loop_can_run_startup_sync_when_enabled(monkeypatch):
    calls = []
    worker = kline_sync_worker.KlineSyncWorker()
    worker._running = True

    async def fake_do_sync(*args, **kwargs):
        calls.append((args, kwargs))
        worker._running = False

    monkeypatch.setattr(kline_sync_worker, "STARTUP_DELAY", 0)
    monkeypatch.setattr(kline_sync_worker, "CHECK_INTERVAL", 3600)
    monkeypatch.setattr(kline_sync_worker.config, "TDX_LOCAL_HISTORY_SYNC_ON_STARTUP_ENABLED", True, raising=False)
    monkeypatch.setattr(kline_sync_worker, "_scheduled_sync_scope", lambda _now: None)
    monkeypatch.setattr(worker, "_do_sync", fake_do_sync)

    asyncio.run(worker._sync_loop())

    assert len(calls) == 1
    assert calls[0][0] == ("启动同步",)
    assert calls[0][1]["scope"] == "daily"
    assert calls[0][1]["mark_schedule"] is False


def test_sync_all_symbols_uses_tdx_local_history_when_baostock_disabled(monkeypatch):
    calls = []

    monkeypatch.setattr(kline_sync_worker.config, "BAOSTOCK_AUTO_SYNC_ENABLED", False)
    monkeypatch.setattr(kline_sync_worker.config, "TDX_LOCAL_HISTORY_SYNC_ENABLED", True)
    monkeypatch.setattr("server.db.kline_lake.query_klines", lambda *_args, **_kwargs: [])
    async def fake_fetch_empty(*_args, **_kwargs):
        return []

    monkeypatch.setattr("server.services.tdx_bridge_client.fetch_tdx_klines", fake_fetch_empty)
    monkeypatch.setattr(
        "server.services.tdx_daily_sync_service.read_tdx_day_klines",
        lambda symbol, limit=5000: [{"date": "2026-05-22", "open": 9, "high": 10, "low": 8, "close": 9.5}],
    )
    monkeypatch.setattr(
        "server.services.tdx_daily_sync_service.read_tdx_week_klines",
        lambda symbol, limit=1200: [{"date": "2026-05-22", "open": 8, "high": 10, "low": 7, "close": 9.5}],
    )
    monkeypatch.setattr(
        "server.services.tdx_minute_service.read_tdx_derived_minute_klines",
        lambda symbol, freq, limit=5000: [
            {"date": "2026-05-22 15:00:00", "open": 9, "high": 10, "low": 8, "close": 9.5}
        ],
    )
    monkeypatch.setattr("server.services.tdx_minute_service.derive_tdx_day_from_minutes", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "server.services.tdx_qfq_normalizer.rebuild_tdx_qfq_from_existing_factors",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="skipped",
            reason="test",
            day_factor_count=0,
            written={},
            missing_factor_dates={},
            total_written=0,
        ),
    )

    def fake_upsert(symbol, freq, rows, adjustflag="2", source="baostock"):
        calls.append((symbol, freq, len(rows), adjustflag, source))
        return len(rows)

    monkeypatch.setattr("server.db.kline_lake.upsert_klines", fake_upsert)

    result = kline_sync_worker._sync_all_symbols(["sz.301078"], ["week", "day", "1", "30"])

    assert result["total_written"] == 4
    assert ("sz.301078", "week", 1, "3", "tdx") in calls
    assert ("sz.301078", "day", 1, "3", "tdx") in calls
    assert ("sz.301078", "1", 1, "3", "tdx") in calls
    assert ("sz.301078", "30", 1, "3", "tdx") in calls
    assert result["changed"] == []


def test_sync_all_symbols_imports_tdx_front_day_before_qfq_rebuild(monkeypatch):
    from types import SimpleNamespace

    upserts = []

    async def fake_fetch(symbol, period="1m", count=5000, dividend_type="front", refresh=False):
        assert period == "1d"
        assert dividend_type == "front"
        assert refresh is True
        return [{"date": "2026-05-22", "open": 9, "high": 10, "low": 8, "close": 9.5}]

    def fake_upsert(symbol, freq, rows, adjustflag="2", source="baostock"):
        upserts.append((symbol, freq, len(rows), adjustflag, source))
        return len(rows)

    def fake_qfq(symbol, target_freqs=None, **_kwargs):
        assert symbol == "sz.301078"
        assert target_freqs == ["30"]
        return SimpleNamespace(
            total_written=1,
            written={"30": 1},
            status="ok",
            reason="",
            day_factor_count=1,
            missing_factor_dates={},
        )

    monkeypatch.setattr(kline_sync_worker.config, "BAOSTOCK_AUTO_SYNC_ENABLED", False)
    def fake_query(symbol, freq, limit=1, adjustflag="2", source="tdx"):
        if freq == "day" and adjustflag == "2":
            return [{"date": "2026-05-21"}]
        if freq == "day" and adjustflag == "3":
            return [{"date": "2026-05-22"}]
        if freq == "30" and adjustflag == "3":
            return [{"date": "2026-05-22 15:00:00"}]
        if freq == "30" and adjustflag == "2":
            return [{"date": "2026-05-21 15:00:00"}]
        return []

    monkeypatch.setattr("server.db.kline_lake.query_klines", fake_query)
    monkeypatch.setattr("server.services.tdx_bridge_client.fetch_tdx_klines", fake_fetch)
    monkeypatch.setattr("server.db.kline_lake.upsert_klines", fake_upsert)
    monkeypatch.setattr("server.services.tdx_qfq_normalizer.rebuild_tdx_qfq_from_existing_factors", fake_qfq)

    result = kline_sync_worker._sync_all_symbols(["sz.301078"], ["day", "30"])

    assert ("sz.301078", "day", 1, "2", "tdx") in upserts
    assert {"symbol": "sz.301078", "freq": "day", "written": 1, "source": "tdx_bridge_front"} in result["changed"]
    assert {"symbol": "sz.301078", "freq": "30", "written": 1, "source": "tdx_qfq"} in result["changed"]


def test_sync_new_watchlist_symbol_runs_quick_then_full(monkeypatch):
    calls = []

    def fake_quick(symbol, freq):
        calls.append(("quick", symbol, freq))
        return 1

    def fake_full(symbol, freq):
        calls.append(("full", symbol, freq))
        return 2

    monkeypatch.setattr(kline_sync_worker.config, "BAOSTOCK_AUTO_SYNC_ENABLED", True)
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


def test_sync_new_watchlist_symbol_runs_tdx_init_when_baostock_disabled(monkeypatch):
    from types import SimpleNamespace

    fetched = []
    upserts = []
    enqueued_changes = []

    async def fake_fetch(symbol, period, count=5000, refresh=True):
        fetched.append((symbol, period, count, refresh))
        if period == "1h":
            return []
        return [
            {
                "date": "2026-05-22 15:00:00" if period.endswith("m") else "2026-05-22",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 1000,
                "amount": 10000,
            }
        ]

    def fake_upsert(symbol, freq, rows, adjustflag="2", source="baostock"):
        upserts.append((symbol, freq, len(rows), adjustflag, source))
        return len(rows)

    def fake_enqueue(changes, **_kwargs):
        enqueued_changes.extend(changes)
        return {"count": len(changes), "items": []}

    def fake_prewarm(**kwargs):
        return {"count": len(kwargs["levels"]), "items": []}

    monkeypatch.setattr(kline_sync_worker.config, "BAOSTOCK_AUTO_SYNC_ENABLED", False)
    monkeypatch.setattr("server.services.tdx_bridge_client.fetch_tdx_klines", fake_fetch)
    monkeypatch.setattr("server.services.tdx_minute_service.read_tdx_derived_minute_klines", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("server.db.kline_lake.upsert_klines", fake_upsert)
    monkeypatch.setattr(
        "server.services.tdx_qfq_normalizer.rebuild_tdx_qfq_from_existing_factors",
        lambda *_args, **_kwargs: SimpleNamespace(
            status="skipped",
            reason="test",
            day_factor_count=0,
            written={},
            missing_factor_dates={},
            total_written=0,
        ),
    )
    monkeypatch.setattr(kline_sync_worker, "enqueue_structure_jobs_for_changes", fake_enqueue)
    monkeypatch.setattr(
        "server.engines.ai_native.czsc_snapshot_service.prewarm_structure_snapshots",
        fake_prewarm,
    )

    result = kline_sync_worker.sync_new_watchlist_symbol("sh600118")

    assert result["source"] == "tdx"
    assert result["errors"] == []
    assert ("sh.600118", "1d", 5000, True) in fetched
    assert ("sh.600118", "day", 1, "2", "tdx") in upserts
    assert any(item["freq"] == "60" and item["period"] == "1h" and item["status"] == "no_data" for item in result["full"])
    assert all(change["freq"] != "60" for change in enqueued_changes)
    assert result["snapshot_prewarm"]["count"] == 4


def test_sync_new_watchlist_symbol_enqueues_all_changed_structure_levels(monkeypatch):
    enqueued_changes = []

    def fake_quick(_symbol, _freq):
        return 0

    def fake_full(_symbol, freq):
        return 1 if freq in {"week", "60", "15"} else 0

    def fake_enqueue(changes, **_kwargs):
        enqueued_changes.extend(changes)
        return {"count": len(changes), "items": []}

    monkeypatch.setattr(kline_sync_worker.config, "BAOSTOCK_AUTO_SYNC_ENABLED", True)
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
    assert [call["allow_when_auto_disabled"] for call in context_calls] == [False, False]
    assert result["count"] == 5
    assert result["users"][0]["snapshot_symbols"] == 3
    assert result["users"][0]["context_symbols"] == 2
    assert result["users"][0]["priority_buckets"] == [
        {"priority": 100, "symbols": ["sh.600519"]},
        {"priority": 80, "symbols": ["sz.000001"]},
        {"priority": 60, "symbols": ["sh.600000"]},
    ]


def test_refresh_unified_reasoning_after_kline_sync_uses_watchboard_universe(monkeypatch):
    calls = []

    monkeypatch.setattr(kline_sync_worker.config, "AI_UNIFIED_REASONING_AFTER_KLINE_SYNC_ENABLED", True)
    monkeypatch.setattr(
        "server.engines.ai_native.universe_resolver.list_watchboard_user_ids",
        lambda limit=None: [1],
    )
    monkeypatch.setattr(
        "server.engines.ai_native.universe_resolver.resolve_watchboard_universe",
        lambda user_id: [
            {"symbol": "sh.600519", "priority": 100, "sources": ["positions"]},
            {"symbol": "sz.000001", "priority": 60, "sources": ["watchboard"]},
            {"symbol": "sh.600000", "priority": 60, "sources": ["watchboard"]},
        ],
    )

    async def fake_request_ai_reasoning(**kwargs):
        calls.append(kwargs)
        return {"symbol": kwargs["symbol"], "trigger": {"decision": "generated"}}

    monkeypatch.setattr(
        "server.engines.ai_native.ai_trigger_service.request_ai_reasoning",
        fake_request_ai_reasoning,
    )

    result = asyncio.run(kline_sync_worker.refresh_unified_reasoning_for_tracked_users(max_symbols_per_user=2))

    assert result["generated"] == 2
    assert result["errors"] == []
    assert [call["symbol"] for call in calls] == ["sh.600519", "sz.000001"]
    assert all(call["user_id"] == 1 for call in calls)
    assert {call["trigger_reason"] for call in calls} == {"post_tdx_refresh"}


def test_refresh_unified_reasoning_after_kline_sync_can_be_disabled(monkeypatch):
    monkeypatch.setattr(kline_sync_worker.config, "AI_UNIFIED_REASONING_AFTER_KLINE_SYNC_ENABLED", False)

    result = asyncio.run(kline_sync_worker.refresh_unified_reasoning_for_tracked_users())

    assert result == {"generated": 0, "errors": [], "skipped": True, "reason": "DISABLED"}
