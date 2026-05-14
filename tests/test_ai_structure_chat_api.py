from fastapi import FastAPI
from fastapi.testclient import TestClient
import json
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


def save_snapshot(signature="sig-chat", price=10.5, zg=11.0, zd=10.0):
    return snapshot_service.save_snapshot(
        symbol="sh600519",
        level="5",
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature=signature,
        data_as_of="2026-05-12",
        snapshot_payload={
            "level": "5",
            "price": price,
            "klines": [{"time": "2026-05-12 10:00:00", "close": price}],
            "active_zhongshu": {
                "zg": zg,
                "zd": zd,
                "begin_time": "2026-05-12 10:00:00",
                "end_time": "2026-05-12 11:00:00",
            },
        },
        raw_bi_context={"levels": {"5": {"last_close": price}}},
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


def seed_fundamental_background():
    conn = database.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO scan_results (
                scan_date, symbol, strategy, status, llm_verdict, llm_summary,
                llm_pros, llm_cons, llm_red_flags, fundamental_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-05-12",
                "sh.600519",
                "war1",
                "ready",
                "支持",
                "基本面长期背景强，但短线需要结构触发确认",
                json.dumps(["品牌壁垒"], ensure_ascii=False),
                json.dumps(["估值偏高"], ensure_ascii=False),
                json.dumps([], ensure_ascii=False),
                "2026-05-12T12:00:00+08:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()


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
    assert data["data_status"]["status"] == "fresh"
    assert set(data["data_status"]["missing_levels"]) == {"week", "day", "30"}
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


def test_chat_uses_fundamental_background_without_trade_instruction(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    ensure_user()
    seed_fundamental_background()
    save_snapshot()
    context_service.prewarm_ai_structure_contexts(user_id=1, symbols=["sh600519"], levels=["5"])
    job = context_service.claim_next_context_job(worker_id="ctx-worker")
    assert context_service.run_context_job_sync(job)["status"] == "success"
    client = make_client()

    response = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "我现在能买吗？"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    answer = data["coach_answer"]
    assert "背景层只作观察背景" in answer
    assert "不能替代 CZSC 触发线和失败线" in answer
    assert "不能直接回答" in answer
    assert "可以买" not in answer
    assert answer.endswith("仅供参考，不构成投资建议")


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


def test_chat_marks_stale_context_without_recomputing(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    save_snapshot(signature="sig-new-chat", price=11.2, zg=11.6, zd=10.9)
    called = {"snapshot": 0}

    def forbidden(*args, **kwargs):
        called["snapshot"] += 1
        raise AssertionError("chat must not refresh CZSC inline")

    monkeypatch.setattr(snapshot_service.czsc_adapter, "analyze_czsc_structure_sync", forbidden)
    client = make_client()

    response = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "我现在能买吗？"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert called["snapshot"] == 0
    assert data["data_status"]["status"] == "stale"
    assert data["data_status"]["stale_reason"] == "SOURCE_SNAPSHOT_CHANGED"
    assert "结构快照待刷新，当前基于上一版数据" in data["coach_answer"]
    assert "不能直接回答" in data["coach_answer"]
    assert data["coach_answer"].endswith("仅供参考，不构成投资建议")


def test_chat_guardrails_out_of_scope_fundamental_trade_question(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    client = make_client()

    response = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "基本面这么好是不是可以买，目标价多少？"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["intent_type"] == "out_of_scope"
    assert "超出当前结构教练边界" in data["coach_answer"]
    assert "不能给目标价、荐股、基本面买卖结论或收益预测" in data["coach_answer"]
    assert "站上 11.00" in data["coach_answer"]
    assert "跌破 10.00" in data["coach_answer"]
    assert data["suggested_reminders"] == []
    assert data["coach_answer"].endswith("仅供参考，不构成投资建议")


def test_chat_guardrails_out_of_scope_even_when_boundary_missing(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    ensure_user()
    save_snapshot(zg=0, zd=0)
    context_service.prewarm_ai_structure_contexts(user_id=1, symbols=["sh600519"], levels=["5"])
    job = context_service.claim_next_context_job(worker_id="ctx-worker")
    assert context_service.run_context_job_sync(job)["status"] == "success"
    client = make_client()

    response = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "推荐一只类似的票，目标价多少？"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["intent_type"] == "out_of_scope"
    assert "不能给目标价、荐股、基本面买卖结论或收益预测" in data["coach_answer"]
    assert "CZSC 边界不足" in data["coach_answer"]
    assert data["referenced_boundaries"] == []
    assert data["suggested_reminders"] == []
    assert data["coach_answer"].endswith("仅供参考，不构成投资建议")


def test_chat_includes_only_mistake_memory_warning(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    client = make_client()
    conn = database.get_connection()
    try:
        branch = conn.execute(
            "SELECT * FROM scenario_branches WHERE user_id = 1 AND symbol = 'sh.600519' AND branch_type = 'observe_breakout' LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
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

    response = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "我现在能买吗？"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert "历史纪律提示" in data["coach_answer"]
    assert data["memory_context"]["mistakes"][0]["type"] == "ignored_invalidation"
    assert len(data["memory_context"]["active_warnings"]) == 1


def test_chat_answers_review_question_from_outcomes(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    client = make_client()
    conn = database.get_connection()
    try:
        branch = conn.execute(
            "SELECT * FROM scenario_branches WHERE user_id = 1 AND symbol = 'sh.600519' AND branch_type = 'observe_breakout' LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
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

    response = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "我上次错在哪里？"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["intent_type"] == "review"
    assert "失效后没有按计划处理" in data["coach_answer"]
    assert "这不是交易指令" in data["coach_answer"]
    assert data["coach_answer"].endswith("仅供参考，不构成投资建议")
    assert data["review_context"]["items"][0]["is_mistake"] is True
    assert data["suggested_reminders"] == []


def test_chat_answers_review_question_without_outcomes(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    client = make_client()

    response = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "最近有没有纪律问题？"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["intent_type"] == "review"
    assert "还没有可复盘的结构分支结果" in data["coach_answer"]
    assert data["review_context"]["count"] == 0


def test_chat_review_answer_discloses_stale_context(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context()
    save_snapshot(signature="sig-review-stale", price=11.2, zg=11.6, zd=10.9)
    client = make_client()

    response = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "最近有没有纪律问题？"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["intent_type"] == "review"
    assert data["data_status"]["status"] == "stale"
    assert "结构快照待刷新，当前基于上一版数据" in data["coach_answer"]
    assert data["coach_answer"].endswith("仅供参考，不构成投资建议")


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


def test_chat_followup_uses_session_context_for_ellipsis(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context(user_id=1)
    client = make_client()
    first = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "我现在能买吗？"},
    )
    session_id = first.json()["data"]["session_id"]

    followup = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "那跌破呢？", "session_id": session_id},
    )

    assert followup.status_code == 200
    data = followup.json()["data"]
    assert data["session_id"] == session_id
    assert data["intent_type"] == "invalidation"
    assert data["conversation_context"]["turn_count"] == 1
    assert data["conversation_context"]["last_intent_type"] == "buy_window"
    assert "10.00" in data["coach_answer"]
    assert "仅供参考，不构成投资建议" in data["coach_answer"]


def test_chat_followup_reuses_last_intent_when_question_is_implicit(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context(user_id=1)
    client = make_client()
    first = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "跌破哪里就不看了？"},
    )
    session_id = first.json()["data"]["session_id"]

    followup = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "那继续呢？", "session_id": session_id},
    )

    assert followup.status_code == 200
    data = followup.json()["data"]
    assert data["intent_type"] == "invalidation"
    assert data["conversation_context"]["last_intent_type"] == "invalidation"
    assert "当前观察分支就要降级" in data["coach_answer"]


def test_chat_followup_can_request_reminder_from_prior_answer(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_context(user_id=1)
    client = make_client()
    first = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "跌破哪里就不看了？"},
    )
    session_id = first.json()["data"]["session_id"]

    followup = client.post(
        "/api/ai-structure/chat",
        json={"symbol": "sh600519", "question": "那帮我盯一下", "session_id": session_id},
    )

    assert followup.status_code == 200
    data = followup.json()["data"]
    assert data["intent_type"] == "reminder"
    assert data["conversation_context"]["last_intent_type"] == "invalidation"
    assert len(data["suggested_reminders"]) == 2
    assert "提醒只帮助你复核，不代表交易指令" in data["coach_answer"]


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
