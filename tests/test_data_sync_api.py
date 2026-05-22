"""Data sync API contract tests."""

import os
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import data as data_api
from server.workers import kline_sync_worker


def test_formal_sync_freqs_include_week_for_ai_context():
    assert kline_sync_worker.ALL_FREQS == ["week", "day", "60", "30", "15", "5"]


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

    app = FastAPI()
    app.include_router(data_api.router)
    client = TestClient(app)

    response = client.post("/sync-klines/sh600549?interval=m30")
    payload = response.json()

    assert response.status_code == 200
    assert payload["freqs"] == ["30"]
    assert payload["total_written"] == 2
    assert fetch_calls == [("sh.600549", "30m", 5000, True)]


def test_sync_all_klines_disables_legacy_baostock_force_sync():
    app = FastAPI()
    app.include_router(data_api.router)
    client = TestClient(app)

    response = client.post("/sync-klines")

    assert response.status_code == 200
    assert response.json()["status"] == "disabled"
    assert response.json()["source"] == "tdx"


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
        }
    ]


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
