from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api import ai_structure
from server.db import database
from server.engines.ai_native import czsc_snapshot_service as service


def reset_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()


def make_client():
    app = FastAPI()
    app.include_router(ai_structure.router, prefix="/api/ai-structure")
    return TestClient(app)


def fake_signature(signature="sig-api", last_date="2026-05-12", row_count=120):
    return {
        "source": "baostock",
        "row_count": row_count,
        "first_date": "2026-01-01",
        "last_date": last_date,
        "signature": signature,
    }


def test_universe_api_returns_user_scoped_symbols(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    conn = database.get_connection()
    try:
        conn.execute("INSERT INTO users (id, openid, nickname) VALUES (1, 'u1', 'U1')")
        conn.execute(
            "INSERT INTO positions (user_id, symbol, name, quantity, avg_cost) VALUES (1, 'sh600519', '贵州茅台', 10, 100)"
        )
        conn.commit()
    finally:
        conn.close()

    response = make_client().get("/api/ai-structure/universe")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["symbols"][0]["symbol"] == "sh.600519"
    assert data["symbols"][0]["sources"] == ["positions"]


def test_snapshot_prewarm_api_enqueues_and_status_reads_pending(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "get_kline_window_signature", lambda *args, **kwargs: fake_signature())
    client = make_client()

    response = client.post(
        "/api/ai-structure/snapshots/prewarm",
        json={"symbols": ["sh600519"], "levels": ["day"], "priority": 90},
    )

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["engine"] == "czsc"
    assert item["status"] == "PENDING"
    assert item["requested_by_user_id"] == 1

    status_response = client.get("/api/ai-structure/snapshots/status/sh600519?level=day")
    assert status_response.status_code == 200
    status = status_response.json()["data"]
    assert status["status"] == "pending"
    assert status["job"]["status"] == "PENDING"


def test_latest_snapshot_api_returns_saved_snapshot(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    service.save_snapshot(
        symbol="sh600519",
        level="day",
        compute_profile=service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-latest",
        data_as_of="2026-05-12",
        snapshot_payload={"level": "day", "price": 10.5},
        raw_bi_context={"levels": {}},
        engine_version="test-engine",
        adapter_version="test-adapter",
    )

    response = make_client().get("/api/ai-structure/snapshots/latest/sh600519?level=day")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["engine"] == "czsc"
    assert data["snapshot"]["price"] == 10.5


def test_latest_snapshot_api_404_when_missing(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)

    response = make_client().get("/api/ai-structure/snapshots/latest/sh600519?level=day")

    assert response.status_code == 404
