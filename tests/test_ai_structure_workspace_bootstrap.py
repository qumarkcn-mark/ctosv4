import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api import ai_structure
from server.api.auth import ALGORITHM, JWT_SECRET
from server.db import database
from server.engines.ai_native import czsc_snapshot_service as snapshot_service
from server.engines.ai_native import structure_context_service as context_service
from server.engines.ai_native import workspace_bootstrap_service


def reset_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()


def make_client():
    app = FastAPI()
    app.include_router(ai_structure.router, prefix="/api/ai-structure")
    return TestClient(app)


def auth_headers(user_id: int) -> dict:
    return {"Authorization": f"Bearer {jwt.encode({'sub': str(user_id)}, JWT_SECRET, algorithm=ALGORITHM)}"}


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


def save_snapshot(symbol="sh600519", level="5", price=10.5, zg=11.0, zd=10.0):
    return snapshot_service.save_snapshot(
        symbol=symbol,
        level=level,
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature=f"sig-{symbol}-{level}",
        data_as_of="2026-05-12",
        snapshot_payload={
            "level": level,
            "price": price,
            "klines": [{"time": "2026-05-12 10:00:00", "close": price}],
            "active_zhongshu": {"zg": zg, "zd": zd},
        },
        raw_bi_context={"levels": {level: {"last_close": price}}},
        engine_version="test-czsc",
        adapter_version="test-adapter",
    )


def fresh_signature_for_saved_snapshot(symbol: str, freq: str, *args, **kwargs):
    compact = str(symbol).replace(".", "")
    return {
        "source": "baostock",
        "row_count": 120,
        "first_date": "2026-01-01",
        "last_date": "2026-05-12",
        "signature": f"sig-{compact}-{freq}",
    }


def build_context(user_id=1, symbol="sh600519"):
    ensure_user(user_id)
    context_service.prewarm_ai_structure_contexts(user_id=user_id, symbols=[symbol], levels=["5"])
    job = context_service.claim_next_context_job(worker_id=f"ctx-worker-{user_id}")
    assert context_service.run_context_job_sync(job)["status"] == "success"


def seed_universe():
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO users (id, openid, nickname) VALUES (1, 'u1', 'U1'), (2, 'u2', 'U2')"
        )
        conn.execute(
            """
            INSERT INTO positions (user_id, symbol, name, quantity, avg_cost)
            VALUES (1, 'sh600519', '贵州茅台', 100, 100.0),
                   (2, 'sz000001', '平安银行', 100, 10.0)
            """
        )
        conn.execute(
            "INSERT INTO watchlist_groups (id, user_id, name, sort_order) VALUES (10, 1, '观察', 0)"
        )
        conn.execute(
            """
            INSERT INTO watchlist_items (group_id, symbol, name, sort_order)
            VALUES (10, 'sz000988', '华工科技', 0)
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_workspace_bootstrap_returns_user_scoped_workspace_state(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(snapshot_service, "get_kline_window_signature", fresh_signature_for_saved_snapshot)
    seed_universe()
    save_snapshot()
    build_context(user_id=1)
    save_snapshot(symbol="sz000988")
    context_service.prewarm_ai_structure_contexts(user_id=1, symbols=["sz000988"], levels=["5"])
    client = make_client()

    response = client.post(
        "/api/ai-structure/workspace/bootstrap",
        json={
            "sources": ["positions", "watchlist"],
            "levels": ["5"],
            "ensure_pipeline": False,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["user_id"] == 1
    assert data["levels"] == ["5"]
    assert [item["symbol"] for item in data["universe"]] == ["sh.600519", "sz.000988"]
    assert data["ensure_pipeline"] is None

    held = data["symbols"][0]
    assert held["symbol"] == "sh.600519"
    assert held["has_position"] is True
    assert held["context_status"]["status"] == "fresh"
    assert "context" not in held["context_status"]
    assert held["latest_context"]["context_id"]
    assert held["latest_context"]["main_level"] == "5"
    assert held["branches"]["count"] > 0
    assert held["reminders"]["count"] == 0
    assert held["outcomes"]["memory"]["stats"]["total_outcomes"] == 0

    watch_only = data["symbols"][1]
    assert watch_only["symbol"] == "sz.000988"
    assert watch_only["latest_context"] is None
    assert watch_only["context_status"]["status"] == "pending"
    assert set(watch_only["context_status"]["job"]) == {
        "job_id",
        "status",
        "reason",
        "error_code",
        "next_run_at",
        "updated_at",
    }
    assert watch_only["context_status"]["job"]["status"] == "PENDING"

    other = client.post(
        "/api/ai-structure/workspace/bootstrap",
        json={"sources": ["positions", "watchlist"], "levels": ["5"]},
        headers=auth_headers(2),
    )
    assert other.status_code == 200
    assert [item["symbol"] for item in other.json()["data"]["universe"]] == ["sz.000001"]


def test_workspace_bootstrap_miniprogram_profile_is_compact(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(snapshot_service, "get_kline_window_signature", fresh_signature_for_saved_snapshot)
    seed_universe()
    save_snapshot()
    build_context(user_id=1)
    client = make_client()

    response = client.post(
        "/api/ai-structure/workspace/bootstrap",
        json={
            "sources": ["positions"],
            "levels": ["5"],
            "client": "miniprogram",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["client"] == "miniprogram"
    assert data["include"] == ["context_status", "reminders", "outcomes"]
    held = data["symbols"][0]
    assert held["context_status"]["status"] == "fresh"
    assert held["reminders"]["count"] == 0
    assert held["outcomes"]["memory"]["stats"]["total_outcomes"] == 0
    assert "latest_context" not in held
    assert "branches" not in held


def test_workspace_bootstrap_include_can_target_worker_contract(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    seed_universe()
    save_snapshot()
    build_context(user_id=1)
    client = make_client()

    response = client.post(
        "/api/ai-structure/workspace/bootstrap",
        json={
            "sources": ["positions"],
            "levels": ["5"],
            "client": "worker",
            "include": ["context_status"],
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["client"] == "worker"
    assert data["include"] == ["context_status"]
    held = data["symbols"][0]
    assert set(held) == {"symbol", "name", "sources", "priority", "has_position", "context_status"}


def test_workspace_bootstrap_focus_symbol_is_included_before_limit(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    seed_universe()
    conn = database.get_connection()
    try:
        conn.executemany(
            "INSERT INTO watchlist_items (group_id, symbol, name, sort_order) VALUES (10, ?, ?, ?)",
            [(f"sh6000{i:02d}", f"样本{i}", i) for i in range(30)],
        )
        conn.commit()
    finally:
        conn.close()
    client = make_client()

    response = client.post(
        "/api/ai-structure/workspace/bootstrap",
        json={
            "sources": ["positions", "watchlist"],
            "focus_symbols": ["sh600029"],
            "levels": ["5"],
            "client": "miniprogram",
            "limit": 3,
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    symbols = [item["symbol"] for item in data["symbols"]]
    assert symbols[0] == "sh.600029"
    assert "sh.600029" in symbols
    assert len(symbols) == 3
    focused = data["symbols"][0]
    assert "focus" in focused["sources"]
    assert focused["priority"] == 120


def test_workspace_bootstrap_rejects_unknown_include_section(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    seed_universe()
    client = make_client()

    response = client.post(
        "/api/ai-structure/workspace/bootstrap",
        json={
            "sources": ["positions"],
            "levels": ["5"],
            "include": ["context_status", "raw_context"],
        },
    )

    assert response.status_code == 400
    assert "unsupported workspace include section" in response.json()["detail"]


def test_workspace_bootstrap_can_enqueue_pipeline_without_inline_structure(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    seed_universe()
    conn = database.get_connection()
    try:
        conn.executemany(
            "INSERT INTO watchlist_items (group_id, symbol, name, sort_order) VALUES (10, ?, ?, ?)",
            [
                ("sh600000", "浦发银行", 1),
                ("sh600004", "白云机场", 2),
                ("sh600006", "东风汽车", 3),
                ("sh600007", "中国国贸", 4),
                ("sh600008", "首创环保", 5),
                ("sh600009", "上海机场", 6),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    calls = []

    async def fake_ensure(**kwargs):
        calls.append(kwargs)
        return {
            "symbols": kwargs["symbols"],
            "levels": kwargs["levels"],
            "engine": "czsc",
            "reason": kwargs["reason"],
            "kline": {
                "ready": True,
                "items": [{"symbol": symbol, "level": "5", "ready": True, "status": "ready"} for symbol in kwargs["symbols"]],
                "errors": [],
            },
            "snapshots": {
                "count": len(kwargs["symbols"]),
                "items": [{"job_id": f"snapshot-{symbol}", "status": "PENDING"} for symbol in kwargs["symbols"]],
            },
            "contexts": {
                "count": len(kwargs["symbols"]),
                "items": [{"job_id": f"context-{symbol}", "status": "PENDING"} for symbol in kwargs["symbols"]],
            },
        }

    monkeypatch.setattr(workspace_bootstrap_service, "ensure_ai_structure_pipeline", fake_ensure)
    client = make_client()

    response = client.post(
        "/api/ai-structure/workspace/bootstrap",
        json={
            "sources": ["positions", "watchlist"],
            "levels": ["5"],
            "ensure_pipeline": True,
            "priority": 91,
            "reason": "test_bootstrap",
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert calls == [
        {
            "user_id": 1,
            "symbols": ["sh.600519", "sh.600000", "sh.600004", "sh.600006", "sh.600007"],
            "levels": ["5"],
            "compute_profile": "chart_standard_v1",
            "priority": 91,
            "reason": "test_bootstrap",
        }
    ]
    assert data["ensure_pipeline"]["engine"] == "czsc"
    assert data["ensure_pipeline"]["snapshots"]["count"] == 5
    assert data["ensure_pipeline"]["snapshots"]["status_counts"] == {"PENDING": 5}
    assert "items" not in data["ensure_pipeline"]["snapshots"]
    assert "items" not in data["ensure_pipeline"]["contexts"]
    assert "items" not in data["ensure_pipeline"]["kline"]
    assert data["ensure_pipeline"]["scope"]["requested_symbol_count"] == 8
    assert data["ensure_pipeline"]["scope"]["ensured_symbol_count"] == 5
    assert data["ensure_pipeline"]["scope"]["skipped_symbols"] == ["sh.600008", "sh.600009", "sz.000988"]
