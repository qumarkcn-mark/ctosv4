from datetime import datetime as RealDateTime

from server.api import data as data_api
from server.engines.ai_native import intraday_snapshot_hydrator as hydrator


class FixedDateTime(RealDateTime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 5, 28, 10, 0, 0, tzinfo=tz)


def test_m1_display_reads_intraday_bars(monkeypatch):
    monkeypatch.setattr(data_api, "datetime", FixedDateTime)

    def fake_query_intraday_bars(symbol, freq, start_time=None, limit=200, **_kwargs):
        assert symbol == "sh.600790"
        assert freq == "1"
        assert start_time == "2026-05-28"
        return [
            {
                "bar_time": "2026-05-28 09:31:00",
                "open": 10,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
                "volume": 100,
                "amount": 1000,
                "bar_status": "CLOSED",
                "source": "tdx_quote_aggregation",
                "sample_count": 3,
                "quality": "full",
            }
        ]

    monkeypatch.setattr(data_api, "query_intraday_bars", fake_query_intraday_bars)

    rows = data_api._query_intraday_today_1m_display_klines("sh.600790", 200)

    assert rows == [
        {
            "symbol": "sh600790",
            "freq": "1",
            "date": "2026-05-28 09:31:00",
            "open": 10,
            "high": 10.2,
            "low": 9.9,
            "close": 10.1,
            "volume": 100,
            "amount": 1000,
            "adjustflag": "3",
            "bar_status": "CLOSED",
            "source": "tdx_quote_aggregation",
            "sample_count": 3,
            "quality": "full",
            "gap_reason": "",
        }
    ]


def test_intraday_snapshot_prefers_intraday_bars_for_today(monkeypatch):
    monkeypatch.setattr(hydrator, "datetime", FixedDateTime)

    def fake_query_intraday_bars(symbol, freq, start_time=None, end_time=None, limit=360, **_kwargs):
        assert symbol == "sh.600790"
        assert freq == "1"
        return [
            {
                "bar_time": "2026-05-28 09:31:00",
                "open": 10,
                "high": 10.2,
                "low": 9.9,
                "close": 10.1,
                "volume": 100,
                "amount": 1000,
                "source": "tdx_quote_aggregation",
            },
            {
                "bar_time": "2026-05-28 09:32:00",
                "open": 10.1,
                "high": 10.3,
                "low": 10.0,
                "close": 10.2,
                "volume": 120,
                "amount": 1200,
                "source": "tdx_quote_aggregation",
            },
        ]

    monkeypatch.setattr(hydrator, "query_intraday_bars", fake_query_intraday_bars)
    monkeypatch.setattr(hydrator, "read_tdx_1m_klines", lambda *args, **kwargs: [])
    monkeypatch.setattr(hydrator, "query_klines", lambda *args, **kwargs: [])
    monkeypatch.setattr(hydrator, "read_tdx_day_klines", lambda *args, **kwargs: [])

    snapshot = hydrator.hydrate_intraday_snapshot("sh.600790", trade_date="2026-05-28")

    assert snapshot["available"] is True
    assert snapshot["source"] == "tdx_quote_aggregation"
    assert snapshot["recent_1m_bars"][-1]["t"] == "09:32"
    assert snapshot["recent_1m_bars"][-1]["c"] == 10.2
