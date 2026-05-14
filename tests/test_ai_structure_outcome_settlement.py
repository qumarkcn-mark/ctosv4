from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt

from server.api import ai_structure
from server.api.auth import ALGORITHM, JWT_SECRET
from server.db import database
from server.engines.ai_native import czsc_snapshot_service as snapshot_service
from server.engines.ai_native import structure_context_service as context_service
from server.engines.ai_native import scenario_outcome_service as outcome_service


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


def build_branches(user_id=1):
    ensure_user(user_id)
    snapshot_service.save_snapshot(
        symbol="sh600519",
        level="5",
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature=f"sig-outcome-{user_id}",
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
    context_service.prewarm_ai_structure_contexts(user_id=user_id, symbols=["sh600519"], levels=["5"])
    job = context_service.claim_next_context_job(worker_id=f"ctx-worker-{user_id}")
    context_service.run_context_job_sync(job)
    latest = context_service.get_latest_ai_structure_context(user_id=user_id, symbol="sh600519")
    return latest["branches"]


def test_settle_branch_updates_memory(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    branches = build_branches()
    branch = next(item for item in branches if item["branch_type"] == "observe_breakout")
    client = make_client()

    response = client.post(
        "/api/ai-structure/branches/settle",
        json={
            "branch_id": branch["branch_id"],
            "current_price": 11.2,
            "settlement_window": "same_day",
            "checked_at": "2026-05-12T15:00:00+08:00",
            "user_followed_plan": True,
        },
    )

    assert response.status_code == 200
    outcome = response.json()["data"]
    assert outcome["outcome"] == "triggered"
    assert outcome["triggered_price"] == 11.2

    memory = client.get("/api/ai-structure/memory/sh600519")
    assert memory.status_code == 200
    assert memory.json()["data"]["stats"]["total_outcomes"] == 1
    assert memory.json()["data"]["stats"]["triggered"] == 1
    assert memory.json()["data"]["profile"]["mistakes"] == []
    assert memory.json()["data"]["profile"]["active_warnings"] == []


def test_invalidated_unfollowed_plan_enters_mistake_memory(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    branches = build_branches()
    branch = next(item for item in branches if item["branch_type"] == "observe_breakout")
    client = make_client()

    response = client.post(
        "/api/ai-structure/branches/settle",
        json={
            "branch_id": branch["branch_id"],
            "current_price": 9.8,
            "settlement_window": "same_day",
            "checked_at": "2026-05-12T15:00:00+08:00",
            "user_followed_plan": False,
        },
    )

    assert response.status_code == 200
    outcome = response.json()["data"]
    assert outcome["outcome"] == "invalidated"
    memory = client.get("/api/ai-structure/memory/sh600519").json()["data"]
    assert memory["stats"]["ignored_invalidation_count_30d"] == 1
    assert memory["stats"]["mistake_count_30d"] == 1
    assert memory["profile"]["mistakes"][0]["type"] == "ignored_invalidation"
    assert memory["profile"]["mistakes"][0]["count_30d"] == 1
    assert "没有按计划处理" in memory["profile"]["active_warnings"][0]["text"]


def test_memory_context_for_chat_is_tiny(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    branches = build_branches()
    branch = next(item for item in branches if item["branch_type"] == "observe_breakout")
    client = make_client()
    client.post(
        "/api/ai-structure/branches/settle",
        json={
            "branch_id": branch["branch_id"],
            "current_price": 9.8,
            "settlement_window": "same_day",
            "checked_at": "2026-05-12T15:00:00+08:00",
            "user_followed_plan": False,
        },
    )

    memory = outcome_service.get_memory_context_for_chat(user_id=1, symbol="sh600519")

    assert list(memory.keys()) == ["memory_version", "mistakes", "active_warnings"]
    assert len(memory["mistakes"]) == 1
    assert len(memory["active_warnings"]) == 1


def test_settle_branch_is_user_scoped(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    branches = build_branches(user_id=1)
    ensure_user(2)
    client = make_client()

    response = client.post(
        "/api/ai-structure/branches/settle",
        json={"branch_id": branches[0]["branch_id"], "current_price": 11.2},
        headers=auth_headers(2),
    )

    assert response.status_code == 404


def test_expired_branch_settlement(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    branches = build_branches()
    branch = branches[0]
    client = make_client()

    response = client.post(
        "/api/ai-structure/branches/settle",
        json={"branch_id": branch["branch_id"], "expired": True, "settlement_window": "3d"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["outcome"] == "expired"
