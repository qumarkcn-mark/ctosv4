from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api import ai_structure
from server.db import database
from server.engines.ai_native import czsc_snapshot_service as snapshot_service
from server.engines.ai_native.momentum_context_service import get_momentum_context


def reset_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()


def make_client():
    app = FastAPI()
    app.include_router(ai_structure.router, prefix="/api/ai-structure")
    return TestClient(app)


def save_momentum_snapshot():
    klines = []
    prices = [
        10.0, 10.3, 10.8, 11.2, 11.6,
        11.1, 10.8, 10.6, 10.4,
        10.7, 11.0, 11.3, 11.5,
    ]
    for index, close in enumerate(prices, start=1):
        day = f"2026-05-{index:02d}"
        klines.append({
            "time": day,
            "open": close - 0.08,
            "high": close + 0.16,
            "low": close - 0.18,
            "close": close,
            "volume": 1000 + index * 100,
        })

    snapshot_service.save_snapshot(
        symbol="sh600519",
        level="day",
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-momentum-context",
        data_as_of="2026-05-13",
        snapshot_payload={
            "level": "day",
            "price": prices[-1],
            "klines": klines,
            "bis": [
                {
                    "direction": "up",
                    "is_up": True,
                    "is_sure": True,
                    "x0": "2026-05-01",
                    "x1": "2026-05-05",
                    "y0": 10.0,
                    "y1": 11.6,
                    "bar_count": 5,
                },
                {
                    "direction": "down",
                    "is_up": False,
                    "is_sure": True,
                    "x0": "2026-05-05",
                    "x1": "2026-05-09",
                    "y0": 11.6,
                    "y1": 10.4,
                    "bar_count": 5,
                },
                {
                    "direction": "up",
                    "is_up": True,
                    "is_sure": True,
                    "x0": "2026-05-09",
                    "x1": "2026-05-13",
                    "y0": 10.4,
                    "y1": 11.5,
                    "bar_count": 5,
                },
            ],
            "bi_zhongshus": [],
            "active_zhongshu": {},
        },
        raw_bi_context={"levels": {}},
        engine_version="test-czsc",
        adapter_version="test-adapter",
    )


def test_momentum_context_compares_latest_same_direction_leg(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    save_momentum_snapshot()

    context = get_momentum_context(symbol="sh600519", level="day")

    assert context["symbol"] == "sh.600519"
    assert context["version"] == "momentum_context.v1"
    assert context["direction"] == "up"
    assert context["current_leg"]["start_index"] == 8
    assert context["previous_leg"]["start_index"] == 0
    assert context["comparison"]["macd_area_ratio"] >= 0
    assert context["verdict"]["state"] in {"strengthening", "weakening", "neutral", "insufficient_data"}
    assert context["risk_boundary"] == "仅供结构观察，不构成投资建议"


def test_momentum_context_api_returns_public_snapshot_view(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    save_momentum_snapshot()

    response = make_client().get("/api/ai-structure/momentum-context/sh600519?level=day")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["snapshot_id"].startswith("czsc_snapshot_")
    assert data["current_leg"]["direction"] == "up"
    assert "买" not in data["verdict"]["label"]
    assert "卖" not in data["verdict"]["label"]


def test_momentum_context_api_404_when_missing(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)

    response = make_client().get("/api/ai-structure/momentum-context/sh600519?level=day")

    assert response.status_code == 404
