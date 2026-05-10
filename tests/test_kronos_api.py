from fastapi import FastAPI
from fastapi.testclient import TestClient

import server.api.kronos as kronos_api
from server.services.kronos_service import KronosUnavailable


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(kronos_api.router, prefix="/api/kronos")
    return TestClient(app)


def test_forecast_returns_404_when_data_is_missing(monkeypatch):
    async def _missing_forecast(*_args, **_kwargs):
        return None

    monkeypatch.setattr(kronos_api.kronos_service, "get_forecast", _missing_forecast)

    response = _client().get("/api/kronos/forecast/sh600519")

    assert response.status_code == 404
    assert "无法获取" in response.json()["detail"]


def test_forecast_returns_503_when_kronos_is_unavailable(monkeypatch):
    async def _unavailable_forecast(*_args, **_kwargs):
        raise KronosUnavailable("ModuleNotFoundError: torch")

    monkeypatch.setattr(kronos_api.kronos_service, "get_forecast", _unavailable_forecast)

    response = _client().get("/api/kronos/forecast/sh600519")

    assert response.status_code == 503
    assert "Kronos 模型暂不可用" in response.json()["detail"]


def test_status_uses_service_status(monkeypatch):
    monkeypatch.setattr(
        kronos_api.kronos_service,
        "status",
        lambda: {
            "status": "offline",
            "loaded": False,
            "available": False,
            "device": "unknown",
            "model_name": "test-model",
            "tokenizer_name": "test-tokenizer",
            "last_error": "not loaded",
            "last_error_at": "2026-05-10T00:00:00+00:00",
        },
    )

    response = _client().get("/api/kronos/status")

    assert response.status_code == 200
    assert response.json()["model_name"] == "test-model"
    assert response.json()["last_error"] == "not loaded"
