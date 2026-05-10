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


def test_daily_klines_default_keeps_backfill_for_shared_callers(monkeypatch):
    calls = []
    monkeypatch.setattr(price_service, "query_klines", lambda *args, **kwargs: [_fresh_row()])
    monkeypatch.setattr(price_service, "fetch_klines_quick", lambda *args, **kwargs: calls.append(args))

    rows = asyncio.run(price_service.get_daily_klines("sz.301590", count=2000))

    assert len(rows) == 1
    assert calls == [("sz.301590", "day")]


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
