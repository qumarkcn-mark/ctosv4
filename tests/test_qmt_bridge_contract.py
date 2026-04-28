import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qmt_bridge.app import create_app
from qmt_bridge.provider import FakeMarketDataProvider
from qmt_bridge.symbols import from_qmt_symbol, normalize_period, to_ctos_freq, to_qmt_symbol
from server.db.kline_lake import get_lake_path
from server.services import qmt_bridge_client


def test_qmt_symbol_mapping_round_trips_ctos_canonical_symbol():
    assert to_qmt_symbol("sh.688008") == "688008.SH"
    assert to_qmt_symbol("sz300124") == "300124.SZ"
    assert from_qmt_symbol("688008.SH") == "sh.688008"
    assert from_qmt_symbol("300124.SZ") == "sz.300124"


def test_period_mapping_keeps_ctos_freq_explicit():
    assert normalize_period("5") == "5m"
    assert normalize_period("m30") == "30m"
    assert normalize_period("60") == "1h"
    assert normalize_period("day") == "1d"
    assert to_ctos_freq("5m") == "5"
    assert to_ctos_freq("1d") == "day"


def test_bridge_health_is_read_only():
    client = TestClient(create_app(FakeMarketDataProvider(price=176.5)))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["read_only"] is True


def test_bridge_quotes_returns_canonical_symbols():
    client = TestClient(create_app(FakeMarketDataProvider(price=176.5)))

    response = client.get("/quotes", params={"symbols": "sh.688008,sz.300124"})

    assert response.status_code == 200
    quotes = response.json()["quotes"]
    assert [row["symbol"] for row in quotes] == ["sh.688008", "sz.300124"]
    assert quotes[0]["source"] == "qmt_fake"


def test_bridge_klines_marks_closed_bars_and_real_price_adjustment():
    client = TestClient(create_app(FakeMarketDataProvider(price=80.0)))

    response = client.get("/klines", params={"symbol": "sz002460", "period": "5m", "limit": 2})

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert payload["klines"][0]["symbol"] == "sz.002460"
    assert payload["klines"][0]["freq"] == "5"
    assert payload["klines"][0]["adjustflag"] == "3"
    assert payload["klines"][0]["bar_status"] == "CLOSED"


def test_kline_lake_supports_qmt_source_path():
    assert get_lake_path("qmt").endswith("qmt_lake.db")


class FakeClient:
    async def health(self):
        return {"status": "ok", "provider": "fake"}

    async def get_klines(self, symbol: str, period: str = "5m", limit: int = 240):
        return [
            {
                "symbol": "sh.688008",
                "freq": "5",
                "date": "2026-04-28 10:35:00",
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100.5,
                "volume": 1,
                "amount": 100.5,
                "adjustflag": "3",
                "bar_status": "CLOSED",
            },
            {
                "symbol": "sh.688008",
                "freq": "5",
                "date": "2026-04-28 10:40:00",
                "open": 100.5,
                "high": 102,
                "low": 100,
                "close": 101.5,
                "volume": 1,
                "amount": 101.5,
                "adjustflag": "3",
                "bar_status": "FORMING",
            },
        ]


def test_qmt_health_returns_available_shape():
    import asyncio

    payload = asyncio.run(qmt_bridge_client.qmt_health(FakeClient()))

    assert payload["available"] is True
    assert payload["status"] == "ok"


class FakeSseHealthClient:
    async def health(self):
        return {
            "ok": True,
            "host": "MARK",
            "qmt": "localhost:58600",
            "subscriptions": {"000001.SZ:tick": -2},
            "last_count": 0,
        }


def test_qmt_health_accepts_windows_sse_gateway_shape():
    import asyncio

    payload = asyncio.run(qmt_bridge_client.qmt_health(FakeSseHealthClient()))

    assert payload["available"] is True
    assert payload["status"] == "ok"
    assert payload["provider"] == "qmt_sse_gateway"
    assert payload["qmt"] == "localhost:58600"


def test_fetch_qmt_klines_caches_only_closed_rows(monkeypatch):
    import asyncio

    writes = []

    def fake_upsert(symbol, freq, rows, adjustflag="2", update_meta=True, source="baostock"):
        writes.append((symbol, freq, rows, adjustflag, source))
        return len(rows)

    monkeypatch.setattr(qmt_bridge_client, "upsert_klines", fake_upsert)

    payload = asyncio.run(
        qmt_bridge_client.fetch_qmt_klines("sh688008", period="5m", client=FakeClient())
    )

    assert payload["count"] == 2
    assert payload["closed_count"] == 1
    assert payload["cached_count"] == 1
    assert writes[0][0] == "sh.688008"
    assert writes[0][1] == "5"
    assert writes[0][3] == "3"
    assert writes[0][4] == "qmt"
    assert writes[0][2][0]["bar_status"] == "CLOSED"
