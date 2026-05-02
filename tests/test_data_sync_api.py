"""Data sync API contract tests."""

import os
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import data as data_api
from server.workers import kline_sync_worker
from server.services import baostock_service


def test_sync_symbol_klines_only_syncs_requested_symbol(monkeypatch):
    calls = []

    async def inline_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def fake_fetch(symbol, freq):
        calls.append((symbol, freq))
        return 3

    monkeypatch.setattr(data_api, "run_in_threadpool", inline_threadpool)
    monkeypatch.setattr(kline_sync_worker, "ALL_FREQS", ["day", "30", "5"])
    monkeypatch.setattr(baostock_service, "fetch_klines_sync", fake_fetch)

    app = FastAPI()
    app.include_router(data_api.router)
    client = TestClient(app)

    response = client.post("/sync-klines/sh600549")
    payload = response.json()

    assert response.status_code == 200
    assert payload["status"] == "success"
    assert payload["symbol"] == "sh.600549"
    assert payload["total_written"] == 9
    assert payload["errors"] == 0
    assert calls == [
        ("sh.600549", "day"),
        ("sh.600549", "30"),
        ("sh.600549", "5"),
    ]
