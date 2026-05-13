from fastapi import FastAPI
from fastapi.testclient import TestClient
import jwt

from server.api import ai_structure
from server.api.auth import ALGORITHM, JWT_SECRET
from server.db import database
from server.engines.ai_native import czsc_snapshot_service as snapshot_service
from server.engines.ai_native import structure_context_service as context_service
from server.engines.ai_native.structure_evidence_service import get_chart_context


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


def build_context():
    ensure_user(1)
    snapshot_service.save_snapshot(
        symbol="sh600519",
        level="5",
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-chart",
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
    context_service.prewarm_ai_structure_contexts(user_id=1, symbols=["sh600519"], levels=["5"])
    job = context_service.claim_next_context_job(worker_id="ctx-worker")
    context_service.run_context_job_sync(job)
    return context_service.get_latest_ai_structure_context(user_id=1, symbol="sh600519")


def test_chart_context_renders_only_requested_evidence(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    context = build_context()
    evidence = context["boundary"]["levels"]["5"]["evidence"]

    chart = get_chart_context(
        user_id=1,
        symbol="sh600519",
        context_id=context["context_id"],
        level="5",
        evidence_ids=[evidence["invalidation_line"]],
    )

    assert chart["overlays"]["active_center"] is None
    assert len(chart["overlays"]["lines"]) == 1
    assert chart["overlays"]["lines"][0]["role"] == "invalidation"
    assert chart["overlays"]["lines"][0]["evidence_id"] == evidence["invalidation_line"]


def test_chart_context_is_user_scoped(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    context = build_context()
    client = make_client()

    own = client.get(f"/api/ai-structure/chart-context/sh600519?context_id={context['context_id']}&level=5")
    other = client.get(
        f"/api/ai-structure/chart-context/sh600519?context_id={context['context_id']}&level=5",
        headers=auth_headers(2),
    )

    assert own.status_code == 200
    assert other.status_code == 404
