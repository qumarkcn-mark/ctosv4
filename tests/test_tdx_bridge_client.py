import asyncio

from server.services import tdx_bridge_client


def test_fetch_tdx_klines_parses_columnar_kline_payload(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "Open": [{"time": "2026-05-22 13:19:00", "688008.SH": 266.6}],
                "High": [{"time": "2026-05-22 13:19:00", "688008.SH": 266.8}],
                "Low": [{"time": "2026-05-22 13:19:00", "688008.SH": 266.5}],
                "Close": [{"time": "2026-05-22 13:19:00", "688008.SH": 266.68}],
                "Volume": [{"time": "2026-05-22 13:19:00", "688008.SH": 1200}],
                "Amount": [{"time": "2026-05-22 13:19:00", "688008.SH": 320000}],
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None):
            assert url.endswith("/kline")
            assert params["symbol"] == "688008.SH"
            return FakeResponse()

    monkeypatch.setattr(tdx_bridge_client, "TDX_BRIDGE_URL", "http://tdx.local")
    monkeypatch.setattr(tdx_bridge_client.httpx, "AsyncClient", FakeClient)

    rows = asyncio.run(tdx_bridge_client.fetch_tdx_klines("sh688008", period="1m", count=5))

    assert rows == [
        {
            "symbol": "sh688008",
            "freq": "1",
            "date": "2026-05-22 13:19:00",
            "open": 266.6,
            "high": 266.8,
            "low": 266.5,
            "close": 266.68,
            "volume": 1200.0,
            "amount": 320000.0,
            "adjustflag": "2",
            "bar_status": "CLOSED",
            "source": "tdx_bridge",
        }
    ]


def test_tdx_period_to_freq_supports_native_daily_and_minutes():
    assert tdx_bridge_client._period_to_freq("1d") == "day"
    assert tdx_bridge_client._period_to_freq("1w") == "week"
    assert tdx_bridge_client._period_to_freq("m30") == "30"
    assert tdx_bridge_client._period_to_freq("60m") == "60"


def test_normalize_daily_kline_strips_midnight_time():
    row = tdx_bridge_client._normalize_kline(
        "sh600790",
        {"date": "20260522000000", "open": 4.0, "high": 4.2, "low": 3.9, "close": 4.1},
        "1d",
    )

    assert row["freq"] == "day"
    assert row["date"] == "2026-05-22"


def test_append_live_quote_1m_bar_adds_forming_bar():
    tdx_bridge_client._LIVE_1M_BARS.clear()
    rows = [
        {
            "symbol": "sh688008",
            "freq": "1",
            "date": "2026-05-11 15:00:00",
            "open": 249.22,
            "high": 249.22,
            "low": 249.22,
            "close": 249.22,
            "volume": 957600.0,
            "amount": 238659552.0,
            "adjustflag": "2",
            "bar_status": "CLOSED",
            "source": "tdx_local_1m",
        }
    ]
    quote = {
        "symbol": "sh688008",
        "price": 266.68,
        "trade_datetime": "2026-05-22 13:20:01",
        "now_volume": 25,
        "source": "tdx_tq",
    }

    merged = tdx_bridge_client.append_live_quote_1m_bar(rows, quote, "sh688008", count=10)

    assert merged[-1]["date"] == "2026-05-22 13:20:00"
    assert merged[-1]["close"] == 266.68
    assert merged[-1]["bar_status"] == "FORMING"
    assert merged[-1]["source"] == "tdx_live_quote_1m"


def test_append_live_quote_1m_bar_ignores_after_hours_quote():
    tdx_bridge_client._LIVE_1M_BARS.clear()
    rows = [
        {
            "symbol": "sz301076",
            "freq": "1",
            "date": "2026-05-25 15:00:00",
            "open": 31.94,
            "high": 31.94,
            "low": 31.94,
            "close": 31.94,
            "volume": 37600.0,
            "amount": 1200624.62,
            "adjustflag": "2",
            "bar_status": "CLOSED",
            "source": "tdx_local_1m",
        }
    ]
    quote = {
        "symbol": "sz301076",
        "price": 31.94,
        "trade_datetime": "2026-05-25 16:14:00",
        "now_volume": 0,
        "source": "tencent_quote",
    }

    merged = tdx_bridge_client.append_live_quote_1m_bar(rows, quote, "sz.301076", count=10)

    assert merged == rows


def test_append_live_quote_1m_bar_updates_same_minute():
    tdx_bridge_client._LIVE_1M_BARS.clear()
    rows = [
        {
            "symbol": "sh688008",
            "freq": "1",
            "date": "2026-05-22 13:20:00",
            "open": 266.68,
            "high": 266.7,
            "low": 266.5,
            "close": 266.68,
            "volume": 25.0,
            "amount": 0.0,
            "adjustflag": "2",
            "bar_status": "FORMING",
            "source": "tdx_live_quote_1m",
        }
    ]
    quote = {
        "symbol": "sh688008",
        "price": 266.88,
        "trade_datetime": "2026-05-22 13:20:45",
        "now_volume": 40,
        "source": "tdx_tq",
    }

    merged = tdx_bridge_client.append_live_quote_1m_bar(rows, quote, "sh688008", count=10)

    assert len(merged) == 1
    assert merged[0]["open"] == 266.68
    assert merged[0]["high"] == 266.88
    assert merged[0]["low"] == 266.5
    assert merged[0]["close"] == 266.88
    assert merged[0]["volume"] == 40.0


def test_append_live_quote_1m_bar_accumulates_within_same_minute():
    tdx_bridge_client._LIVE_1M_BARS.clear()
    rows = []
    first = {
        "symbol": "sh688008",
        "price": 266.68,
        "trade_datetime": "2026-05-22 13:20:01",
        "now_volume": 25,
    }
    second = {
        "symbol": "sh688008",
        "price": 266.4,
        "trade_datetime": "2026-05-22 13:20:40",
        "now_volume": 40,
    }

    tdx_bridge_client.append_live_quote_1m_bar(rows, first, "sh688008", count=10)
    merged = tdx_bridge_client.append_live_quote_1m_bar(rows, second, "sh688008", count=10)

    assert merged[-1]["open"] == 266.68
    assert merged[-1]["high"] == 266.68
    assert merged[-1]["low"] == 266.4
    assert merged[-1]["close"] == 266.4
    assert merged[-1]["volume"] == 40.0
