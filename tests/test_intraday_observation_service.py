import asyncio

import pytest

from server.services import intraday_observation_service as svc


@pytest.fixture(autouse=True)
def no_lake_1m(monkeypatch):
    monkeypatch.setattr(svc, "query_klines", lambda *args, **kwargs: [])


def test_intraday_observation_tracks_coverage_and_forming_macd(monkeypatch):
    svc.reset_intraday_observation_cache()

    async def fake_history(symbol, interval="m5", count=240, allow_short_fresh_cache=True):
        return [
            {
                "symbol": symbol,
                "freq": interval.replace("m", ""),
                "date": f"2026-05-21 14:{i:02d}:00",
                "open": 100 + i * 0.01,
                "high": 100.5 + i * 0.01,
                "low": 99.5 + i * 0.01,
                "close": 100.1 + i * 0.01,
                "volume": 1000 + i,
                "amount": 100000 + i,
                "bar_status": "CLOSED",
                "source": "history",
            }
            for i in range(60)
        ]

    monkeypatch.setattr(svc, "get_minute_klines", fake_history)

    first = {
        "symbol": "sz300394",
        "price": 101.0,
        "trade_datetime": "2026-05-22 13:20:12",
        "quote_time": "13:20:12",
        "source": "tdx_tq",
        "now_volume": 10,
    }
    second = {
        **first,
        "price": 101.5,
        "trade_datetime": "2026-05-22 13:21:03",
        "quote_time": "13:21:03",
        "now_volume": 20,
    }

    asyncio.run(svc.get_intraday_observation("sz.300394", quote=first))
    payload = asyncio.run(svc.get_intraday_observation("sz.300394", quote=second))

    assert payload["source"] == "tdx_quote_aggregation"
    assert payload["usage"] == "intraday_preview"
    assert payload["coverage"]["quality"] == "partial"
    assert payload["coverage"]["missing_open_session"] is True
    assert payload["levels"]["1m"]["intraday_bar_count"] == 2
    assert payload["levels"]["1m"]["last_bar_status"] == "FORMING"
    assert payload["levels"]["5m"]["intraday_bar_count"] >= 1
    assert payload["levels"]["30m"]["intraday_bar_count"] >= 1
    assert payload["levels"]["5m"]["macd_closed_only"]["basis"] == "closed_only"
    assert payload["levels"]["5m"]["macd_with_forming"]["basis"] == "with_forming"
    assert payload["levels"]["30m"]["macd_with_forming"]["basis"] == "with_forming"


def test_intraday_observation_without_quote_reports_none(monkeypatch):
    svc.reset_intraday_observation_cache()

    async def fake_fetch_quote(symbol):
        return None

    async def fake_history(symbol, interval="m5", count=240, allow_short_fresh_cache=True):
        return []

    monkeypatch.setattr(svc, "fetch_tdx_quote", fake_fetch_quote)
    monkeypatch.setattr(svc, "get_minute_klines", fake_history)

    payload = asyncio.run(svc.get_intraday_observation("sh.600790"))

    assert payload["coverage"]["quality"] == "none"
    assert payload["levels"]["1m"]["bar_count"] == 0


def test_intraday_observation_ignores_after_hours_quote_for_bar_aggregation(monkeypatch):
    svc.reset_intraday_observation_cache()

    async def fake_history(symbol, interval="m5", count=240, allow_short_fresh_cache=True):
        return []

    monkeypatch.setattr(svc, "get_minute_klines", fake_history)

    payload = asyncio.run(
        svc.get_intraday_observation(
            "sh.600790",
            quote={
                "symbol": "sh600790",
                "price": 4.23,
                "quote_time": "15:40:57",
                "trade_datetime": "2026-05-22 15:40:57",
                "source": "tdx_tq",
            },
        )
    )

    assert payload["quote"]["price"] == 4.23
    assert payload["coverage"]["quality"] == "none"
    assert payload["levels"]["1m"]["bar_count"] == 0
    assert payload["levels"]["5m"]["bar_count"] == 0
    assert payload["levels"]["5m"]["macd_with_forming"]["status"] == "insufficient_bars"


def test_intraday_observation_prefers_today_lake_1m_closed_bars(monkeypatch):
    svc.reset_intraday_observation_cache()

    def fake_lake(symbol, freq, **kwargs):
        assert symbol == "sh.600790"
        if freq != "1":
            return []
        assert kwargs["adjustflag"] == "3"
        if kwargs["source"] == "qmt":
            return []
        return [
            {
                "date": f"2026-05-22 09:{30 + i:02d}:00",
                "open": 4.0 + i * 0.01,
                "high": 4.02 + i * 0.01,
                "low": 3.99 + i * 0.01,
                "close": 4.01 + i * 0.01,
                "volume": 1000 + i,
                "amount": 4000 + i,
            }
            for i in range(4)
        ]

    async def fake_history(symbol, interval="m5", count=240, allow_short_fresh_cache=True):
        return []

    monkeypatch.setattr(svc, "query_klines", fake_lake)
    monkeypatch.setattr(svc, "get_minute_klines", fake_history)

    payload = asyncio.run(svc.get_intraday_observation("sh.600790", quote=None))

    assert payload["coverage"]["bar_count_1m"] == 4
    assert payload["coverage"]["missing_open_session"] is False
    assert payload["levels"]["1m"]["last_bar_status"] == "CLOSED"
    assert payload["levels"]["5m"]["intraday_bar_count"] == 2
    assert payload["levels"]["5m"]["last_bar_status"] == "CLOSED"


def test_intraday_observation_reads_minute_history_from_tdx(monkeypatch):
    calls = []

    def fake_lake(symbol, freq, **kwargs):
        calls.append((symbol, freq, kwargs))
        return []

    monkeypatch.setattr(svc, "query_klines", fake_lake)

    payload = svc.get_intraday_observation_snapshot("sh.600790", quote=None)

    assert payload["levels"]["5m"]["bar_count"] == 0
    assert any(freq == "5" and kwargs["source"] == "tdx" and kwargs["adjustflag"] == "2" for _, freq, kwargs in calls)
    assert any(freq == "30" and kwargs["source"] == "tdx" and kwargs["adjustflag"] == "2" for _, freq, kwargs in calls)
