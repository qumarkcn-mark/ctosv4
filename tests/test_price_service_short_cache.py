import asyncio
from datetime import date

from server.services import price_service


def _fresh_row() -> dict:
    return {
        "date": date.today().isoformat(),
        "open": 10.0,
        "high": 10.2,
        "low": 9.8,
        "close": 10.1,
        "volume": 1000,
        "amount": 10000,
    }


def _stale_row() -> dict:
    return {
        "date": "2000-01-01",
        "open": 10.0,
        "high": 10.2,
        "low": 9.8,
        "close": 10.1,
        "volume": 1000,
        "amount": 10000,
    }


def test_daily_klines_return_fresh_short_cache_without_baostock(monkeypatch):
    monkeypatch.setattr(price_service, "query_klines", lambda *args, **kwargs: [_fresh_row()])
    monkeypatch.setattr(
        price_service,
        "fetch_klines_quick",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("BaoStock should not run for fresh short cache")),
    )

    rows = asyncio.run(
        price_service.get_daily_klines(
            "sz.301590",
            count=2000,
            allow_short_fresh_cache=True,
        )
    )

    assert len(rows) == 1


def test_daily_klines_return_stale_short_cache_without_blocking_baostock(monkeypatch):
    monkeypatch.setattr(price_service, "query_klines", lambda *args, **kwargs: [_stale_row()])
    monkeypatch.setattr(
        price_service,
        "fetch_klines_quick",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("BaoStock should not block chart display")),
    )

    rows = asyncio.run(
        price_service.get_daily_klines(
            "sz.301590",
            count=2000,
            allow_short_fresh_cache=True,
        )
    )

    assert rows == [_stale_row()]


def test_daily_klines_default_keeps_backfill_for_shared_callers(monkeypatch):
    calls = []
    monkeypatch.setattr(price_service, "query_klines", lambda *args, **kwargs: [_fresh_row()])
    monkeypatch.setattr(price_service, "fetch_klines_quick", lambda *args, **kwargs: calls.append(args))

    rows = asyncio.run(price_service.get_daily_klines("sz.301590", count=2000))

    assert len(rows) == 1
    assert calls == [("sz.301590", "day")]


def test_minute_klines_return_stale_short_cache_without_blocking_baostock(monkeypatch):
    row = {**_stale_row(), "date": "2000-01-01 15:00:00"}
    monkeypatch.setattr(price_service, "query_klines", lambda *args, **kwargs: [row])
    monkeypatch.setattr(
        price_service,
        "fetch_klines_quick",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("BaoStock should not block chart display")),
    )

    rows = asyncio.run(
        price_service.get_minute_klines(
            "sz.301590",
            interval="m30",
            count=2000,
            allow_short_fresh_cache=True,
        )
    )

    assert rows == [row]


def test_minute_klines_return_fresh_short_cache_without_baostock(monkeypatch):
    row = {**_fresh_row(), "date": f"{date.today().isoformat()} 15:00:00"}
    monkeypatch.setattr(price_service, "query_klines", lambda *args, **kwargs: [row])
    monkeypatch.setattr(
        price_service,
        "fetch_klines_quick",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("BaoStock should not run for fresh short cache")),
    )

    rows = asyncio.run(
        price_service.get_minute_klines(
            "sz.301590",
            interval="m30",
            count=2000,
            allow_short_fresh_cache=True,
        )
    )

    assert len(rows) == 1


def test_get_current_price_prefers_tdx_bridge(monkeypatch):
    tdx_quote = {"symbol": "sz301590", "price": 12.34, "source": "tdx_tq"}

    async def fake_tdx_quote(symbol):
        return tdx_quote

    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            raise AssertionError("Tencent fallback should not run when TDX has quote")

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr(price_service, "fetch_tdx_quote", fake_tdx_quote)
    monkeypatch.setattr(price_service.httpx, "AsyncClient", FailingClient)

    result = asyncio.run(price_service.get_current_price("sz.301590"))

    assert result == tdx_quote


def test_get_batch_prices_uses_tdx_and_falls_back_for_missing(monkeypatch):
    async def fake_tdx_quotes(symbols):
        return {"sz301590": {"symbol": "sz301590", "price": 12.34, "source": "tdx_tq"}}

    class FakeResponse:
        text = 'v_sh600519="1~贵州茅台~600519~1293.67~1311.00~1310.95~32214~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~0~20260522121438~0~0~1311.91~1291.11";'

        def raise_for_status(self):
            pass

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.requested_url = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            assert url.endswith("sh600519")
            return FakeResponse()

    monkeypatch.setattr(price_service, "fetch_tdx_quotes", fake_tdx_quotes)
    monkeypatch.setattr(price_service.httpx, "AsyncClient", FakeClient)

    result = asyncio.run(price_service.get_batch_prices(["sz.301590", "sh.600519"]))

    assert result["sz301590"]["source"] == "tdx_tq"
    assert result["sh600519"]["price"] == 1293.67
