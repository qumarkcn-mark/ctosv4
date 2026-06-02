from datetime import datetime as RealDateTime

from server.services import tdx_bridge_client as client


class AfterCloseDateTime(RealDateTime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 6, 2, 21, 58, 0, tzinfo=tz)


class TradingDateTime(RealDateTime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 6, 2, 10, 1, 0, tzinfo=tz)


def test_append_live_quote_1m_bar_skips_after_close(monkeypatch):
    monkeypatch.setattr(client, "datetime", AfterCloseDateTime)
    rows = [
        {
            "date": "2026-06-02 15:00:00",
            "open": 76.5,
            "high": 76.6,
            "low": 76.5,
            "close": 76.55,
            "volume": 100,
        }
    ]
    quote = {"price": 76.4, "quote_time": "15:00", "received_at": AfterCloseDateTime.now().timestamp()}

    result = client.append_live_quote_1m_bar(rows, quote, "sh.600118", 20)

    assert result == rows


def test_append_live_quote_1m_bar_allows_trading_minute(monkeypatch):
    monkeypatch.setattr(client, "datetime", TradingDateTime)
    rows = []
    quote = {"price": 76.4, "quote_time": "10:01", "received_at": TradingDateTime.now().timestamp()}

    result = client.append_live_quote_1m_bar(rows, quote, "sh.600118", 20)

    assert result[-1]["date"] == "2026-06-02 10:01:00"
    assert result[-1]["bar_status"] == "FORMING"
