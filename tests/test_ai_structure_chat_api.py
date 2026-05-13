from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt

from server.api import ai_structure
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


def ensure_user(user_id=1):
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, openid, nickname) VALUES (?, ?, ?)",
            (user_id, f"u{user_id}", f"U{user_id}"),
        )
        conn.commit()
    finally:
        conn.close()


def save_snapshot():
    return snapshot_service.save_snapshot(
        symbol="sh600519",
        level="5",
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-chat",
        data_as_of="2026-05-12",
        snapshot_payload={
            "level": "5",
            "price": 10.5,
            "klines": [{"time": "2026-05-12 10:00:00", "close": 10.5}],
            "active_zhongshu": {
                "zg": 11.0,
                "zd": 10.0,
                "begin_time": "2026-05-12 10:00:00",
                "end_time": "2026-05-12 11:00:00",
            },
        },
        raw_bi_context={"levels": {"5": {"last_close": 10.5}}},
        engine_version="test-czsc",
        adapter_version="test-adapter",
    )


def build_context(user_id=1):
    ensure_user(user_id)
    save_snapshot()
    context_service.prewarm_ai_structure_contexts(user_id=user_id, symbols=["sh600519"], levels=["5"])
    job = context_service.claim_next_context_job(worker_id=f"ctx-worker-{user_id}")
    result = context_service.run_context_job_sync(job)
    assert result["status"] == "success"
    return context_service.get_latest_ai_structure_context(user_id=user_id, symbol="sh600519")


def test_chat_answers_buy_window_with_evidence_and_disclaimer(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    context = build_context()
    called = {"snapshot": 0}

    def forbidden(*args, **kwargs):
        called["snapshot"] += 1
        raise AssertionError("chat must not compute CZSC")

    monkeypatch.setattr(snapshot_service.czsc_adapter, "analyze_czsc_structure_sync", forbidden)
    client = make_client()

    response = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "我现在能买吗？"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert called["snapshot"] == 0
    assert data["intent_type"] == "buy_window"
    assert data["context_id"] == context["context_id"]
    assert "不能直接回答" in data["coach_answer"]
    assert data["risk_disclaimer"] == "仅供参考，不构成投资建议"
    assert data["chart_focus"]["evidence_ids"]
    assert data["suggested_reminders"]

    chart = client.get(
        f"/api/ai-structure/chart-context/sh600519?"
        f"&context_id={data['context_id']}&level={data['chart_focus']['level']}"
        f"&evidence_ids={','.join(data['chart_focus']['evidence_ids'])}"
    )
    assert chart.status_code == 200
    overlays = chart.json()["data"]["overlays"]
    overlay_ids = set()
    if overlays["active_center"]:
        overlay_ids.add(overlays["active_center"]["evidence_id"])
    overlay_ids.update(line["evidence_id"] for line in overlays["lines"])
    assert set(data["chart_focus"]["evidence_ids"]) <= overlay_ids


def test_chat_answers_invalidation_question(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    client = make_client()

    response = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "跌破哪里就不看了？"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["intent_type"] == "invalidation"
    assert "10.00" in data["coach_answer"]
    assert "仅供参考，不构成投资建议" in data["coach_answer"]
    assert any(item["role"] == "invalidation" for item in data["referenced_boundaries"])


def test_chat_sessions_and_messages_are_user_scoped(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context(user_id=1)
    client = make_client()
    response = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "我现在能买吗？"},
    )
    session_id = response.json()["data"]["session_id"]

    sessions = client.get("/api/ai-structure/chat/sessions/sh600519")
    messages = client.get(f"/api/ai-structure/chat/messages?session_id={session_id}")
    other_messages = client.get(
        f"/api/ai-structure/chat/messages?session_id={session_id}",
        headers=auth_headers(2),
    )

    assert sessions.status_code == 200
    assert sessions.json()["data"]["sessions"][0]["session_id"] == session_id
    assert messages.status_code == 200
    assert messages.json()["data"]["messages"][0]["answer"]["intent_type"] == "buy_window"
    assert other_messages.status_code == 404


def test_chat_rejects_cross_user_session(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context(user_id=1)
    build_context(user_id=2)
    client = make_client()
    response = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "我现在能买吗？"},
    )
    session_id = response.json()["data"]["session_id"]

    other = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "我现在能买吗？", "session_id": session_id},
        headers=auth_headers(2),
    )

    assert other.status_code == 404


def test_chat_404_without_context(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    ensure_user()
    client = make_client()

    response = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "我现在能买吗？"},
    )

    assert response.status_code == 404
