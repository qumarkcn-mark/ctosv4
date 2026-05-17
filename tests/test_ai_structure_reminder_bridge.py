from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt
import json

from server.api import ai_structure
from server.api.auth import ALGORITHM, JWT_SECRET
from server.db import database
from server.engines.ai_native import czsc_snapshot_service as snapshot_service
from server.engines.ai_native import structure_context_service as context_service
from server.engines.ai_native import structure_reminder_service as reminder_service


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


def build_context(user_id=1):
    ensure_user(user_id)
    snapshot_service.save_snapshot(
        symbol="sh600519",
        level="5",
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature=f"sig-reminder-{user_id}",
        data_as_of="2026-05-12",
        snapshot_payload={
            "level": "5",
            "price": 10.5,
            "klines": [{"time": "2026-05-12 10:00:00", "close": 10.5}],
            "active_zhongshu": {"zg": 11.0, "zd": 10.0},
        },
        raw_bi_context={"levels": {"5": {"last_close": 10.5}}},
        engine_version="test-czsc",
        adapter_version="test-adapter",
    )
    context_service.prewarm_ai_structure_contexts(user_id=user_id, symbols=["sh600519"], levels=["5"])
    job = context_service.claim_next_context_job(worker_id=f"ctx-worker-{user_id}")
    context_service.run_context_job_sync(job)
    mark_latest_context_llm_success(user_id=user_id)


def mark_latest_context_llm_success(user_id=1, symbol="sh600519"):
    latest = context_service.get_latest_ai_structure_context(user_id=user_id, symbol=symbol)
    assert latest
    reasoning = latest["reasoning"]
    meta = dict(reasoning.get("reasoning_meta") or {})
    meta.update({"provider": "llm", "llm_status": "success"})
    reasoning["reasoning_meta"] = meta
    conn = database.get_connection()
    try:
        conn.execute(
            "UPDATE ai_structure_contexts SET reasoning_json = ? WHERE context_id = ?",
            (json.dumps(reasoning, ensure_ascii=False), latest["context_id"]),
        )
        conn.commit()
    finally:
        conn.close()


def create_triggered_invalidation_reminder(client):
    chat = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "跌破哪里就不看了？"},
    ).json()["data"]
    candidate = chat["suggested_reminders"][0]
    reminder = client.post(
        "/api/ai-structure/reminders",
        json={
            "session_id": chat["session_id"],
            "message_id": chat["message_id"],
            "evidence_id": candidate["evidence_id"],
        },
    ).json()["data"]
    scan = reminder_service.scan_structure_reminders({
        "sh600519": {"price": float(candidate["trigger_price"]) - 0.1},
    })
    assert scan["count"] == 1
    return reminder, scan["items"][0]


def test_create_reminder_writes_alert_and_coach_event(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    client = make_client()
    chat = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "我现在能买吗？"},
    ).json()["data"]
    evidence_id = chat["suggested_reminders"][0]["evidence_id"]

    response = client.post(
        "/api/ai-structure/reminders",
        json={
            "session_id": chat["session_id"],
            "message_id": chat["message_id"],
            "evidence_id": evidence_id,
        },
    )

    assert response.status_code == 200
    reminder = response.json()["data"]
    assert reminder["alert_id"] > 0
    assert reminder["coach_event_id"]
    assert reminder["context_id"] == chat["context_id"]
    assert reminder["evidence_id"] == evidence_id

    conn = database.get_connection()
    try:
        alert = conn.execute("SELECT * FROM alerts WHERE id = ?", (reminder["alert_id"],)).fetchone()
        event = conn.execute("SELECT * FROM coach_events WHERE event_id = ?", (reminder["coach_event_id"],)).fetchone()
    finally:
        conn.close()
    assert alert["alert_type"] == "SIGNAL"
    assert alert["trigger_direction"] == reminder["direction"]
    assert "提醒不下单" in alert["message"]
    assert event["event_type"] == "AI_STRUCTURE_REMINDER_CREATED"


def test_duplicate_reminder_reuses_existing_alert(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    client = make_client()
    chat = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "跌破哪里就不看了？"},
    ).json()["data"]
    evidence_id = chat["suggested_reminders"][0]["evidence_id"]
    payload = {"session_id": chat["session_id"], "message_id": chat["message_id"], "evidence_id": evidence_id}

    first = client.post("/api/ai-structure/reminders", json=payload).json()["data"]
    second = client.post("/api/ai-structure/reminders", json=payload).json()["data"]

    assert second["duplicate"] is True
    assert second["alert_id"] == first["alert_id"]
    conn = database.get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM alerts").fetchone()["c"]
    finally:
        conn.close()
    assert count == 1


def test_list_reminders_returns_user_symbol_scope(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    client = make_client()
    chat = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "我现在能买吗？"},
    ).json()["data"]
    evidence_id = chat["suggested_reminders"][0]["evidence_id"]
    client.post(
        "/api/ai-structure/reminders",
        json={"session_id": chat["session_id"], "message_id": chat["message_id"], "evidence_id": evidence_id},
    )

    response = client.get("/api/ai-structure/reminders/sh600519")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["count"] == 1
    assert data["items"][0]["symbol"] == "sh.600519"
    assert data["items"][0]["status"] == "ACTIVE"
    assert data["items"][0]["triggered"] is False
    assert data["items"][0]["message"]


def test_scan_structure_reminders_triggers_active_link(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    client = make_client()
    chat = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "跌破哪里就不看了？"},
    ).json()["data"]
    candidate = chat["suggested_reminders"][0]
    reminder = client.post(
        "/api/ai-structure/reminders",
        json={
            "session_id": chat["session_id"],
            "message_id": chat["message_id"],
            "evidence_id": candidate["evidence_id"],
        },
    ).json()["data"]

    result = reminder_service.scan_structure_reminders({
        "sh600519": {"price": float(candidate["trigger_price"]) - 0.1},
    })

    assert result["count"] == 1
    assert result["items"][0]["alert_id"] == reminder["alert_id"]
    assert result["items"][0]["settled_outcome"]["outcome"] == "invalidated"
    assert result["items"][0]["settled_outcome"]["user_followed_plan"] is None
    assert result["items"][0]["message"]
    conn = database.get_connection()
    try:
        alert = conn.execute("SELECT * FROM alerts WHERE id = ?", (reminder["alert_id"],)).fetchone()
        link = conn.execute(
            "SELECT * FROM ai_structure_reminder_links WHERE alert_id = ?",
            (reminder["alert_id"],),
        ).fetchone()
        event = conn.execute(
            "SELECT * FROM coach_events WHERE event_type = 'AI_STRUCTURE_REMINDER_TRIGGERED'",
        ).fetchone()
        outcome = conn.execute(
            "SELECT * FROM scenario_outcomes WHERE user_id = 1 AND symbol = 'sh.600519'",
        ).fetchone()
        memory = conn.execute(
            "SELECT * FROM ai_symbol_memory_profiles WHERE user_id = 1 AND symbol = 'sh.600519'",
        ).fetchone()
    finally:
        conn.close()
    assert alert["is_triggered"] == 1
    assert alert["triggered_at"]
    assert link["status"] == "TRIGGERED"
    assert event["symbol"] == "sh.600519"
    assert outcome["outcome"] == "invalidated"
    assert outcome["user_followed_plan"] is None
    assert json.loads(memory["profile_json"])["mistakes"] == []


def test_scan_structure_reminders_ignores_non_triggered_price(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    client = make_client()
    chat = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "跌破哪里就不看了？"},
    ).json()["data"]
    candidate = chat["suggested_reminders"][0]
    reminder = client.post(
        "/api/ai-structure/reminders",
        json={
            "session_id": chat["session_id"],
            "message_id": chat["message_id"],
            "evidence_id": candidate["evidence_id"],
        },
    ).json()["data"]

    result = reminder_service.scan_structure_reminders({
        "sh600519": {"price": float(candidate["trigger_price"]) + 0.1},
    })

    assert result["count"] == 0
    conn = database.get_connection()
    try:
        alert = conn.execute("SELECT * FROM alerts WHERE id = ?", (reminder["alert_id"],)).fetchone()
        link = conn.execute(
            "SELECT * FROM ai_structure_reminder_links WHERE alert_id = ?",
            (reminder["alert_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert alert["is_triggered"] == 0
    assert link["status"] == "ACTIVE"


def test_reminder_is_user_scoped(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context(user_id=1)
    ensure_user(2)
    client = make_client()
    chat = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "我现在能买吗？"},
    ).json()["data"]

    response = client.post(
        "/api/ai-structure/reminders",
        json={
            "session_id": chat["session_id"],
            "message_id": chat["message_id"],
            "evidence_id": chat["suggested_reminders"][0]["evidence_id"],
        },
        headers=auth_headers(2),
    )

    assert response.status_code == 404


def test_ack_handled_marks_outcome_followed_without_mistake(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    client = make_client()
    reminder, _ = create_triggered_invalidation_reminder(client)

    response = client.post(
        f"/api/ai-structure/reminders/{reminder['id']}/ack",
        json={"action": "handled"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "ACKED_HANDLED"
    assert data["ack_action"] == "handled"
    assert data["outcome"]["outcome"] == "invalidated"
    assert data["outcome"]["user_followed_plan"] == 1
    conn = database.get_connection()
    try:
        outcome = conn.execute("SELECT * FROM scenario_outcomes WHERE user_id = 1").fetchone()
        memory = conn.execute(
            "SELECT * FROM ai_symbol_memory_profiles WHERE user_id = 1 AND symbol = 'sh.600519'",
        ).fetchone()
        event = conn.execute(
            "SELECT * FROM coach_events WHERE event_type = 'AI_STRUCTURE_REMINDER_ACKED'",
        ).fetchone()
    finally:
        conn.close()
    assert outcome["user_followed_plan"] == 1
    assert json.loads(memory["profile_json"])["mistakes"] == []
    assert event["severity"] == "INFO"


def test_ack_ignored_marks_outcome_unfollowed_and_enters_mistake_memory(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    client = make_client()
    reminder, _ = create_triggered_invalidation_reminder(client)

    response = client.post(
        f"/api/ai-structure/reminders/{reminder['id']}/ack",
        json={"action": "ignored"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "ACKED_IGNORED"
    assert data["outcome"]["user_followed_plan"] == 0
    conn = database.get_connection()
    try:
        memory = conn.execute(
            "SELECT * FROM ai_symbol_memory_profiles WHERE user_id = 1 AND symbol = 'sh.600519'",
        ).fetchone()
        event = conn.execute(
            "SELECT * FROM coach_events WHERE event_type = 'AI_STRUCTURE_REMINDER_ACKED'",
        ).fetchone()
    finally:
        conn.close()
    mistakes = json.loads(memory["profile_json"])["mistakes"]
    assert mistakes[0]["type"] == "ignored_invalidation"
    assert event["severity"] == "WARNING"


def test_ack_ignored_creates_outcome_when_auto_settlement_was_missing(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    client = make_client()
    reminder, _ = create_triggered_invalidation_reminder(client)
    conn = database.get_connection()
    try:
        conn.execute("DELETE FROM scenario_outcomes WHERE user_id = 1")
        conn.execute("DELETE FROM ai_symbol_memory_profiles WHERE user_id = 1")
        conn.commit()
    finally:
        conn.close()

    response = client.post(
        f"/api/ai-structure/reminders/{reminder['id']}/ack",
        json={"action": "ignored"},
    )

    assert response.status_code == 200
    conn = database.get_connection()
    try:
        outcome = conn.execute("SELECT * FROM scenario_outcomes WHERE user_id = 1").fetchone()
        memory = conn.execute(
            "SELECT * FROM ai_symbol_memory_profiles WHERE user_id = 1 AND symbol = 'sh.600519'",
        ).fetchone()
    finally:
        conn.close()
    assert outcome["settlement_window"] == "ai_reminder_trigger"
    assert outcome["outcome"] == "invalidated"
    assert outcome["user_followed_plan"] == 0
    assert json.loads(memory["profile_json"])["mistakes"][0]["type"] == "ignored_invalidation"


def test_ack_continue_watch_records_event_without_mistake(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    client = make_client()
    reminder, _ = create_triggered_invalidation_reminder(client)

    response = client.post(
        f"/api/ai-structure/reminders/{reminder['id']}/ack",
        json={"action": "continue_watch"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "ACKED_CONTINUE_WATCH"
    assert data["outcome"] is None
    conn = database.get_connection()
    try:
        outcome = conn.execute("SELECT * FROM scenario_outcomes WHERE user_id = 1").fetchone()
        memory = conn.execute(
            "SELECT * FROM ai_symbol_memory_profiles WHERE user_id = 1 AND symbol = 'sh.600519'",
        ).fetchone()
    finally:
        conn.close()
    assert outcome["user_followed_plan"] is None
    assert json.loads(memory["profile_json"])["mistakes"] == []


def test_ack_reminder_is_user_scoped(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    ensure_user(2)
    client = make_client()
    reminder, _ = create_triggered_invalidation_reminder(client)

    response = client.post(
        f"/api/ai-structure/reminders/{reminder['id']}/ack",
        json={"action": "ignored"},
        headers=auth_headers(2),
    )

    assert response.status_code == 404


def test_ack_reminder_cannot_be_applied_twice(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    client = make_client()
    reminder, _ = create_triggered_invalidation_reminder(client)

    first = client.post(
        f"/api/ai-structure/reminders/{reminder['id']}/ack",
        json={"action": "handled"},
    )
    second = client.post(
        f"/api/ai-structure/reminders/{reminder['id']}/ack",
        json={"action": "ignored"},
    )

    assert first.status_code == 200
    assert second.status_code == 400
    conn = database.get_connection()
    try:
        outcome = conn.execute("SELECT * FROM scenario_outcomes WHERE user_id = 1").fetchone()
    finally:
        conn.close()
    assert outcome["user_followed_plan"] == 1
