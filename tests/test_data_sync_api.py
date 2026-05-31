"""Data sync API contract tests."""

import os
import sys
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import data as data_api
from server.workers import kline_sync_worker


def test_formal_sync_freqs_include_week_for_ai_context():
    assert kline_sync_worker.ALL_FREQS == ["week", "day", "60", "30", "15", "5", "1"]
    assert "1" not in kline_sync_worker.FREQ_TO_STRUCTURE_LEVEL


def test_sync_symbol_klines_only_syncs_requested_symbol(monkeypatch):
    fetch_calls = []
    upsert_calls = []

    async def inline_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    async def fake_fetch(symbol, period="1m", count=5000, refresh=False, **_kwargs):
        fetch_calls.append((symbol, period, count, refresh))
        return [
            {"date": "2026-05-22 15:00:00" if period.endswith("m") else "2026-05-22", "open": 1, "high": 2, "low": 1, "close": 2}
        ]

    def fake_upsert(symbol, freq, rows, adjustflag="2", update_meta=True, source="baostock", **_kwargs):
        upsert_calls.append((symbol, freq, len(rows), adjustflag, update_meta, source))
        return 3

    structure_changes = []

    def fake_enqueue_structure_jobs(changes, **kwargs):
        structure_changes.extend(changes)
        return {"count": len(changes), "items": []}

    monkeypatch.setattr(data_api, "run_in_threadpool", inline_threadpool)
    monkeypatch.setattr(kline_sync_worker, "ALL_FREQS", ["day", "30", "5"])
    monkeypatch.setattr(data_api, "fetch_tdx_klines", fake_fetch)
    monkeypatch.setattr(data_api, "upsert_klines", fake_upsert)
    monkeypatch.setattr(kline_sync_worker, "enqueue_structure_jobs_for_changes", fake_enqueue_structure_jobs)
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

    app = FastAPI()
    app.include_router(data_api.router)
    client = TestClient(app)

    response = client.post("/sync-klines/sh600549")
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["source"] == "tdx"
    assert payload["symbol"] == "sh.600549"
    assert payload["total_written"] == 9
    assert payload["errors"] == 0
    assert payload["structure_jobs"]["count"] == 3
    assert fetch_calls == [
        ("sh.600549", "1d", 5000, True),
        ("sh.600549", "30m", 5000, True),
        ("sh.600549", "5m", 5000, True),
    ]
    assert upsert_calls == [
        ("sh.600549", "day", 1, "2", True, "tdx"),
        ("sh.600549", "30", 1, "2", True, "tdx"),
        ("sh.600549", "5", 1, "2", True, "tdx"),
    ]
    assert structure_changes == [
        {"symbol": "sh.600549", "freq": "day", "written": 3},
        {"symbol": "sh.600549", "freq": "30", "written": 3},
        {"symbol": "sh.600549", "freq": "5", "written": 3},
    ]


def test_sync_symbol_klines_can_refresh_only_requested_interval(monkeypatch):
    fetch_calls = []

    async def inline_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    async def fake_fetch(symbol, period="1m", count=5000, refresh=False, **_kwargs):
        fetch_calls.append((symbol, period, count, refresh))
        return [{"date": "2026-05-22 15:00:00", "open": 1, "high": 2, "low": 1, "close": 2}]

    def fake_upsert(symbol, freq, rows, adjustflag="2", update_meta=True, source="baostock", **_kwargs):
        assert update_meta is True
        assert source == "tdx"
        return 2

    monkeypatch.setattr(data_api, "run_in_threadpool", inline_threadpool)
    monkeypatch.setattr(data_api, "fetch_tdx_klines", fake_fetch)
    monkeypatch.setattr(data_api, "upsert_klines", fake_upsert)
    monkeypatch.setattr(
        kline_sync_worker,
        "enqueue_structure_jobs_for_changes",
        lambda changes, **_kwargs: {"count": len(changes), "items": []},
    )
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

    app = FastAPI()
    app.include_router(data_api.router)
    client = TestClient(app)

    response = client.post("/sync-klines/sh600549?interval=m30")
    payload = response.json()

    assert response.status_code == 200
    assert payload["freqs"] == ["30"]
    assert payload["total_written"] == 4
    assert fetch_calls == [
        ("sh.600549", "1d", 5000, True),
        ("sh.600549", "30m", 5000, True),
    ]
    assert payload["results"][0]["freq"] == "day_factor"


def test_sync_all_klines_disables_legacy_baostock_force_sync():
    app = FastAPI()
    app.include_router(data_api.router)
    client = TestClient(app)

    response = client.post("/sync-klines")

    assert response.status_code == 200
    assert response.json()["status"] == "disabled"
    assert response.json()["source"] == "tdx"


def test_batch_prices_feed_intraday_observation_cache(monkeypatch):
    async def fake_prices(symbols):
        assert symbols == ["sz.002158"]
        return {
            "sz002158": {
                "symbol": "sz002158",
                "price": 32.6,
                "quote_time": "10:19:36",
                "trade_datetime": "2026-05-27 10:19:36",
                "source": "tencent_quote",
            }
        }

    ingest_calls = []
    monkeypatch.setattr(data_api, "get_batch_prices", fake_prices)
    monkeypatch.setattr(data_api, "ingest_intraday_quote", lambda symbol, quote: ingest_calls.append((symbol, quote)))

    app = FastAPI()
    app.include_router(data_api.router)
    client = TestClient(app)

    response = client.get("/prices?symbols=sz.002158")

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert len(ingest_calls) == 1
    assert ingest_calls[0][0] == "sz002158"
    assert ingest_calls[0][1]["trade_datetime"] == "2026-05-27 10:19:36"


def test_query_klines_reads_tdx_front_adjusted_lake(monkeypatch):
    query_calls = []

    async def inline_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def fake_query(symbol, freq, start_date=None, end_date=None, limit=2000, adjustflag="2", source=None):
        query_calls.append(
            {
                "symbol": symbol,
                "freq": freq,
                "start_date": start_date,
                "end_date": end_date,
                "limit": limit,
                "adjustflag": adjustflag,
                "source": source,
            }
        )
        return [{"date": "2026-05-22 15:00:00", "open": 1, "high": 2, "low": 1, "close": 2}]

    async def fake_fetch(*_args, **_kwargs):
        raise AssertionError("lake rows should be enough")

    monkeypatch.setattr(data_api, "run_in_threadpool", inline_threadpool)
    monkeypatch.setattr(data_api, "query_lake_klines", fake_query)
    monkeypatch.setattr(data_api, "fetch_tdx_klines", fake_fetch)

    app = FastAPI()
    app.include_router(data_api.router)
    client = TestClient(app)

    response = client.get("/klines/sh600790?interval=m30&count=120")

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert query_calls == [
        {
            "symbol": "sh.600790",
            "freq": "30",
            "start_date": None,
            "end_date": None,
            "limit": 120,
            "adjustflag": "2",
            "source": "tdx",
        },
        {
            "symbol": "sh.600790",
            "freq": "30",
            "start_date": None,
            "end_date": None,
            "limit": 120,
            "adjustflag": "3",
            "source": "tdx",
        },
    ]


def test_query_klines_falls_back_to_tdx_raw_lake(monkeypatch):
    query_calls = []

    async def inline_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def fake_query(symbol, freq, start_date=None, end_date=None, limit=2000, adjustflag="2", source=None):
        query_calls.append({"adjustflag": adjustflag, "source": source})
        if adjustflag == "2":
            return []
        return [{"date": "2026-04-30", "open": 1, "high": 2, "low": 1, "close": 2}]

    async def fake_fetch(*_args, **_kwargs):
        raise AssertionError("raw TDX lake rows should avoid bridge fallback")

    monkeypatch.setattr(data_api, "run_in_threadpool", inline_threadpool)
    monkeypatch.setattr(data_api, "query_lake_klines", fake_query)
    monkeypatch.setattr(data_api, "fetch_tdx_klines", fake_fetch)

    app = FastAPI()
    app.include_router(data_api.router)
    client = TestClient(app)

    response = client.get("/klines/sh600036?interval=day&count=120")

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert query_calls == [
        {"adjustflag": "2", "source": "tdx"},
        {"adjustflag": "3", "source": "tdx"},
    ]


def test_query_klines_uses_raw_when_qfq_lake_is_stale(monkeypatch):
    async def inline_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def fake_query(symbol, freq, start_date=None, end_date=None, limit=2000, adjustflag="2", source=None):
        if adjustflag == "2":
            return [{"date": "2026-05-21 15:00:00", "open": 1, "high": 2, "low": 1, "close": 2}]
        return [{"date": "2026-05-22 15:00:00", "open": 3, "high": 4, "low": 3, "close": 4}]

    async def fake_fetch(*_args, **_kwargs):
        raise AssertionError("fresher raw TDX lake rows should avoid bridge fallback")

    monkeypatch.setattr(data_api, "run_in_threadpool", inline_threadpool)
    monkeypatch.setattr(data_api, "query_lake_klines", fake_query)
    monkeypatch.setattr(data_api, "fetch_tdx_klines", fake_fetch)

    app = FastAPI()
    app.include_router(data_api.router)
    client = TestClient(app)

    response = client.get("/klines/sh600790?interval=m30&count=120")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["klines"][0]["date"] == "2026-05-22 15:00:00"


def test_query_m1_klines_appends_current_price_quote(monkeypatch):
    async def inline_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    async def fake_fetch(symbol, period="1m", count=5000, refresh=False, **_kwargs):
        assert period == "1m"
        return [
            {
                "symbol": "sh688008",
                "freq": "1",
                "date": "2026-05-22 15:00:00",
                "open": 260,
                "high": 261,
                "low": 259,
                "close": 260.5,
                "volume": 1000,
                "amount": 260500,
                "adjustflag": "2",
                "bar_status": "CLOSED",
                "source": "tdx_bridge",
            }
        ]

    async def fake_current_price(symbol):
        assert symbol == "sh.688008"
        return {
            "symbol": "sh688008",
            "price": 270.98,
            "trade_datetime": "2026-05-25 10:27:41",
            "quote_time": "10:27:41",
            "source": "tencent_quote",
        }

    monkeypatch.setattr(data_api, "run_in_threadpool", inline_threadpool)
    monkeypatch.setattr(data_api, "fetch_tdx_klines", fake_fetch)
    monkeypatch.setattr(data_api, "get_current_price", fake_current_price)

    app = FastAPI()
    app.include_router(data_api.router)
    client = TestClient(app)

    response = client.get("/klines/sh688008?interval=m1&count=120")

    assert response.status_code == 200
    payload = response.json()
    assert payload["interval"] == "m1"
    assert payload["klines"][-1]["date"] == "2026-05-25 10:27:00"
    assert payload["klines"][-1]["close"] == 270.98
    assert payload["klines"][-1]["bar_status"] == "FORMING"


def test_postmarket_sync_skips_when_tdx_stale(monkeypatch):
    data_api._POSTMARKET_SYNC_JOBS.clear()

    async def inline_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def fake_readiness(vipdoc=None):
        return {
            "status": "stale",
            "message": "TDX 本地数据还没到 2026-05-25",
            "latest": {"day": "2026-05-24", "m1": "", "m5": ""},
        }

    def fake_run(*_args, **_kwargs):
        raise AssertionError("stale TDX should not run postmarket sync")

    monkeypatch.setattr(data_api, "run_in_threadpool", inline_threadpool)
    monkeypatch.setattr(data_api, "_tdx_postmarket_readiness", fake_readiness)
    monkeypatch.setattr(data_api, "_run_tdx_postmarket_sync", fake_run)

    app = FastAPI()
    app.include_router(data_api.router)
    client = TestClient(app)

    response = client.post("/tdx/sync/postmarket")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "stale"
    assert payload["skipped"] is True
    assert payload["sync_result"] is None


def test_postmarket_sync_runs_when_tdx_ready(monkeypatch):
    data_api._POSTMARKET_SYNC_JOBS.clear()

    async def inline_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def fake_readiness(vipdoc=None):
        return {
            "status": "ready",
            "message": "ready",
            "latest": {"day": "2026-05-25", "m1": "2026-05-25 15:00:00", "m5": "2026-05-25 15:00:00"},
        }

    def fake_run(vipdoc=None, mode="incremental"):
        assert mode == "incremental"
        return {
            "status": "success",
            "tracked": {"updated_symbols": 3, "total_symbols": 5, "errors": 0},
            "snapshot_prewarm": {"count": 12},
        }

    monkeypatch.setattr(data_api, "run_in_threadpool", inline_threadpool)
    monkeypatch.setattr(data_api, "_tdx_postmarket_readiness", fake_readiness)
    monkeypatch.setattr(data_api, "_run_tdx_postmarket_sync", fake_run)

    app = FastAPI()
    app.include_router(data_api.router)
    client = TestClient(app)

    response = client.post("/tdx/sync/postmarket")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["skipped"] is False
    assert payload["job_id"]

    latest = client.get("/tdx/sync/postmarket/latest").json()
    assert latest["status"] == "success"
    assert "更新 3/5 只" in latest["message"]


def test_lake_status_api_is_readonly_contract(monkeypatch):
    async def inline_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr(data_api, "run_in_threadpool", inline_threadpool)
    monkeypatch.setattr(
        data_api,
        "lake_status",
        lambda: {
            "status": "ok",
            "sources": [{"source": "tdx", "health": "ok"}],
            "legacy": {"active": False},
        },
    )

    app = FastAPI()
    app.include_router(data_api.router)
    client = TestClient(app)

    response = client.get("/lake/status")

    assert response.status_code == 200
    assert response.json()["sources"][0]["source"] == "tdx"
