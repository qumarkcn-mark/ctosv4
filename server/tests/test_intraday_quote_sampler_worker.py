import asyncio

from server.workers import intraday_quote_sampler_worker as worker_mod


def test_sampler_tick_reports_missing_bridge(monkeypatch):
    worker = worker_mod.IntradayQuoteSamplerWorker(interval_seconds=1, max_symbols=5)
    monkeypatch.setattr(worker_mod, "is_tdx_bridge_enabled", lambda: False)

    result = asyncio.run(worker.tick())

    assert result["error"] == "TDX_BRIDGE_URL_NOT_CONFIGURED"
    assert worker.status()["last_error"] == "TDX_BRIDGE_URL_NOT_CONFIGURED"
    assert worker.status()["bridge_enabled"] is False


def test_sampler_tick_ingests_quotes(monkeypatch):
    ingested = []
    worker = worker_mod.IntradayQuoteSamplerWorker(interval_seconds=1, max_symbols=5)
    monkeypatch.setattr(worker_mod, "is_tdx_bridge_enabled", lambda: True)
    monkeypatch.setattr(worker_mod, "load_watchboard_symbols", lambda _limit: ["sh.600118", "sz.300394"])

    async def fake_fetch(symbols):
        assert symbols == ["sh.600118", "sz.300394"]
        return {
            "sh600118": {"symbol": "sh600118", "price": 10.1, "time": "2026-06-02 10:01:01"},
            "sz300394": {"symbol": "sz300394", "price": 20.2, "time": "2026-06-02 10:01:01"},
        }

    monkeypatch.setattr(worker_mod, "fetch_tdx_quotes", fake_fetch)
    def fake_ingest(symbol, quote):
        ingested.append((symbol, quote))
        return True

    monkeypatch.setattr(worker_mod, "ingest_intraday_quote", fake_ingest)

    result = asyncio.run(worker.tick())

    assert result["symbols"] == 2
    assert result["quotes"] == 2
    assert result["ingested"] == 2
    assert result["error"] == ""
    assert [item[0] for item in ingested] == ["sh.600118", "sz.300394"]
    assert worker.status()["last_ingested"] == 2


def test_sampler_tick_reports_empty_quotes(monkeypatch):
    worker = worker_mod.IntradayQuoteSamplerWorker(interval_seconds=1, max_symbols=5)
    monkeypatch.setattr(worker_mod, "is_tdx_bridge_enabled", lambda: True)
    monkeypatch.setattr(worker_mod, "load_watchboard_symbols", lambda _limit: ["sh.600118"])

    async def fake_fetch(_symbols):
        return {}

    monkeypatch.setattr(worker_mod, "fetch_tdx_quotes", fake_fetch)

    result = asyncio.run(worker.tick())

    assert result["symbols"] == 1
    assert result["quotes"] == 0
    assert result["ingested"] == 0
    assert result["error"] == "NO_QUOTES_FROM_TDX_BRIDGE"
    assert worker.status()["last_error"] == "NO_QUOTES_FROM_TDX_BRIDGE"


def test_sampler_tick_reports_non_trading_quotes(monkeypatch):
    worker = worker_mod.IntradayQuoteSamplerWorker(interval_seconds=1, max_symbols=5)
    monkeypatch.setattr(worker_mod, "is_tdx_bridge_enabled", lambda: True)
    monkeypatch.setattr(worker_mod, "load_watchboard_symbols", lambda _limit: ["sh.600118"])

    async def fake_fetch(_symbols):
        return {"sh600118": {"symbol": "sh600118", "price": 10.1, "trade_datetime": "2026-06-02 21:57:20"}}

    monkeypatch.setattr(worker_mod, "fetch_tdx_quotes", fake_fetch)
    monkeypatch.setattr(worker_mod, "ingest_intraday_quote", lambda _symbol, _quote: False)

    result = asyncio.run(worker.tick())

    assert result["symbols"] == 1
    assert result["quotes"] == 1
    assert result["ingested"] == 0
    assert result["error"] == "NO_VALID_TRADING_MINUTE_QUOTES"
    assert worker.status()["last_error"] == "NO_VALID_TRADING_MINUTE_QUOTES"
