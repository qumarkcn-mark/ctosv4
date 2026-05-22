from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api import intraday


def test_intraday_observation_api(monkeypatch):
    async def fake_observation(symbol):
        return {
            "source": "tdx_quote_aggregation",
            "usage": "intraday_preview",
            "symbol": symbol,
            "coverage": {"quality": "partial"},
            "levels": {"5m": {}, "30m": {}},
        }

    monkeypatch.setattr(intraday, "get_intraday_observation", fake_observation)
    app = FastAPI()
    app.include_router(intraday.router, prefix="/api/intraday")
    client = TestClient(app)

    response = client.get("/api/intraday/observation/sz300394")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["data"]["source"] == "tdx_quote_aggregation"
    assert payload["data"]["symbol"] == "sz.300394"
