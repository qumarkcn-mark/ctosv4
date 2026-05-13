from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt

from server.api import ai_structure
from server.api import auth
from server.api.auth import ALGORITHM, JWT_SECRET
from server.db import database
from server.engines.ai_native import czsc_snapshot_service as snapshot_service
from server.engines.ai_native import structure_context_service as context_service


def reset_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()


def make_client():
    app = FastAPI()
    app.include_router(ai_structure.router, prefix="/api/ai-structure")
    return TestClient(app)


def auth_headers(user_id: int) -> dict:
    return {"Authorization": f"Bearer {jwt.encode({'sub': str(user_id)}, JWT_SECRET, algorithm=ALGORITHM)}"}


def save_snapshot():
    return snapshot_service.save_snapshot(
        symbol="sh600519",
        level="5",
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-user",
        data_as_of="2026-05-12",
        snapshot_payload={
            "level": "5",
            "price": 10.5,
            "active_zhongshu": {"zg": 11.0, "zd": 10.0},
        },
        raw_bi_context={"levels": {"5": {"last_close": 10.5}}},
        engine_version="test-czsc",
        adapter_version="test-adapter",
    )


def ensure_user(user_id: int):
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, openid, nickname) VALUES (?, ?, ?)",
            (user_id, f"u{user_id}", f"U{user_id}"),
        )
        conn.commit()
    finally:
        conn.close()


def build_user_context(user_id: int):
    ensure_user(user_id)
    context_service.prewarm_ai_structure_contexts(user_id=user_id, symbols=["sh600519"], levels=["5"])
    job = context_service.claim_next_context_job(worker_id=f"ctx-worker-{user_id}")
    result = context_service.run_context_job_sync(job)
    assert result["status"] == "success"
    return context_service.get_latest_ai_structure_context(user_id=user_id, symbol="sh600519")


def test_latest_context_is_user_scoped(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    save_snapshot()
    ensure_user(1)
    user1_context = build_user_context(1)
    client = make_client()

    own = client.get("/api/ai-structure/contexts/latest/sh600519")
    other = client.get("/api/ai-structure/contexts/latest/sh600519", headers=auth_headers(2))
    spoofed = client.get("/api/ai-structure/contexts/latest/sh600519?user_id=2")

    assert own.status_code == 200
    assert own.json()["data"]["context_id"] == user1_context["context_id"]
    assert other.status_code == 404
    assert spoofed.status_code == 200
    assert spoofed.json()["data"]["context_id"] == user1_context["context_id"]


def test_branches_do_not_leak_across_users(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    save_snapshot()
    ensure_user(1)
    user1_context = build_user_context(1)
    client = make_client()

    own = client.get(f"/api/ai-structure/branches/sh600519?context_id={user1_context['context_id']}")
    other = client.get(
        f"/api/ai-structure/branches/sh600519?context_id={user1_context['context_id']}",
        headers=auth_headers(2),
    )

    assert own.status_code == 200
    assert own.json()["data"]["branches"]
    assert other.status_code == 404


def test_context_api_prewarm_status_and_latest(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    save_snapshot()
    ensure_user(1)
    client = make_client()

    prewarm = client.post(
        "/api/ai-structure/contexts/prewarm",
        json={"symbols": ["sh600519"], "levels": ["5"], "priority": 75},
    )
    assert prewarm.status_code == 200
    assert prewarm.json()["data"]["items"][0]["status"] == "PENDING"

    status = client.get("/api/ai-structure/contexts/status/sh600519?levels=5")
    assert status.status_code == 200
    assert status.json()["data"]["status"] == "pending"

    job = context_service.claim_next_context_job(worker_id="ctx-worker")
    context_service.run_context_job_sync(job)

    latest = client.get("/api/ai-structure/contexts/latest/sh600519")
    assert latest.status_code == 200
    assert latest.json()["data"]["branches"]


def test_ai_structure_routes_require_auth_without_dev_fallback(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(auth.config, "DEBUG", False)
    monkeypatch.setattr(auth.config, "DEV_AUTH_FALLBACK", False)
    client = make_client()

    checks = [
        client.get("/api/ai-structure/universe"),
        client.get("/api/ai-structure/contexts/latest/sh600519"),
        client.post(
            "/api/ai-structure/chat",
            json={"symbol": "sh600519", "question": "我现在能买吗？"},
        ),
        client.post(
            "/api/ai-structure/reminders",
            json={
                "session_id": "session_test",
                "message_id": "message_test",
                "evidence_id": "evidence_test",
            },
        ),
        client.post("/api/ai-structure/branches/settle", json={"branch_id": "branch_test"}),
    ]

    assert [response.status_code for response in checks] == [401, 401, 401, 401, 401]
