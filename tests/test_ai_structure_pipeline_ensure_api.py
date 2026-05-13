from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api import ai_structure
from server.db import database
from server.engines.ai_native import pipeline_ensure_service as service


def reset_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()


def make_client():
    app = FastAPI()
    app.include_router(ai_structure.router, prefix="/api/ai-structure")
    return TestClient(app)


def test_pipeline_ensure_fetches_kline_then_enqueues_snapshot_and_context(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    counts = {}
    ensure_calls = []

    def fake_count(symbol, freq):
        return counts.get((symbol, freq), 0)

    async def fake_ensure(symbol, freq, min_count=200):
        ensure_calls.append({"symbol": symbol, "freq": freq, "min_count": min_count})
        counts[(symbol, freq)] = 8
        return True

    def fake_snapshot_prewarm(**kwargs):
        return {
            "count": 2,
            "items": [
                {"symbol": kwargs["symbols"][0], "level": level, "status": "PENDING"}
                for level in kwargs["levels"]
            ],
            "reason": kwargs["reason"],
        }

    def fake_context_prewarm(**kwargs):
        return {
            "count": 1,
            "items": [{"symbol": kwargs["symbols"][0], "status": "PENDING"}],
            "user_id": kwargs["user_id"],
            "reason": kwargs["reason"],
        }

    monkeypatch.setattr(service, "count_klines", fake_count)
    monkeypatch.setattr(service, "ensure_klines_cached", fake_ensure)
    monkeypatch.setattr(service, "prewarm_structure_snapshots", fake_snapshot_prewarm)
    monkeypatch.setattr(service, "prewarm_ai_structure_contexts", fake_context_prewarm)

    response = make_client().post(
        "/api/ai-structure/pipeline/ensure",
        json={
            "symbols": ["sh600519", "sh.600519"],
            "levels": ["day", "5"],
            "priority": 88,
            "reason": "test_pipeline",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["engine"] == "czsc"
    assert data["symbols"] == ["sh.600519"]
    assert data["levels"] == ["day", "5"]
    assert data["kline"]["ready"] is True
    assert data["kline"]["items"][0]["before"] == 0
    assert data["kline"]["items"][0]["after"] == 8
    assert ensure_calls == [
        {"symbol": "sh.600519", "freq": "day", "min_count": 1},
        {"symbol": "sh.600519", "freq": "5", "min_count": 1},
    ]
    assert data["snapshots"]["count"] == 2
    assert data["contexts"]["count"] == 1
    assert data["contexts"]["user_id"] == 1
