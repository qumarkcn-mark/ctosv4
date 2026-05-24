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
    quick_calls = []

    def fake_count(symbol, freq):
        return counts.get((symbol, freq), 0)

    def fake_quick(symbol, freq):
        quick_calls.append({"symbol": symbol, "freq": freq})
        counts[(symbol, freq)] = 8
        return 8

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
            "allow_when_auto_disabled": kwargs.get("allow_when_auto_disabled"),
        }

    monkeypatch.setattr(service.config, "BAOSTOCK_AUTO_SYNC_ENABLED", True)
    monkeypatch.setattr(service, "count_klines", fake_count)
    monkeypatch.setattr(service, "fetch_klines_quick", fake_quick)
    monkeypatch.setattr(service, "prewarm_structure_snapshots", fake_snapshot_prewarm)
    monkeypatch.setattr(service, "prewarm_ai_structure_contexts", fake_context_prewarm)
    monkeypatch.setattr(service, "_schedule_backfill_rewarm", lambda **kwargs: None)

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
    assert quick_calls == [
        {"symbol": "sh.600519", "freq": "day"},
        {"symbol": "sh.600519", "freq": "5"},
    ]
    assert data["snapshots"]["count"] == 2
    assert data["contexts"]["count"] == 1
    assert data["contexts"]["user_id"] == 1
    assert data["contexts"].get("allow_when_auto_disabled") is False


def test_pipeline_ensure_skips_baostock_fetch_when_auto_sync_disabled(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    quick_calls = []
    backfill_calls = []

    monkeypatch.setattr(service.config, "BAOSTOCK_AUTO_SYNC_ENABLED", False)
    monkeypatch.setattr(service, "fetch_klines_quick", lambda *args, **kwargs: quick_calls.append(args))
    monkeypatch.setattr(service, "_schedule_backfill_rewarm", lambda **kwargs: backfill_calls.append(kwargs))
    monkeypatch.setattr(
        service,
        "prewarm_structure_snapshots",
        lambda **kwargs: {"count": len(kwargs["levels"]), "items": []},
    )
    monkeypatch.setattr(
        service,
        "prewarm_ai_structure_contexts",
        lambda **kwargs: {
            "count": len(kwargs["symbols"]),
            "items": [],
            "allow_when_auto_disabled": kwargs.get("allow_when_auto_disabled"),
        },
    )

    response = make_client().post(
        "/api/ai-structure/pipeline/ensure",
        json={"symbols": ["sh600519"], "levels": ["day"], "reason": "test_disabled"},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["kline"]["ready"] is True
    assert data["kline"]["items"][0]["status"] == "skipped"
    assert data["kline"]["items"][0]["reason"] == "BAOSTOCK_AUTO_SYNC_DISABLED"
    assert data["contexts"]["allow_when_auto_disabled"] is False
    assert quick_calls == []
    assert backfill_calls == []


def test_backfill_rewarms_changed_symbol(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    counts = {("sh.600519", "day"): 8}
    snapshot_calls = []
    context_calls = []

    def fake_count(symbol, freq):
        return counts.get((symbol, freq), 0)

    def fake_sync(symbol, freq):
        counts[(symbol, freq)] = 1200
        return 1192

    def fake_snapshot_prewarm(**kwargs):
        snapshot_calls.append(kwargs)
        return {"count": 1, "items": []}

    def fake_context_prewarm(**kwargs):
        context_calls.append(kwargs)
        return {"count": 1, "items": []}

    monkeypatch.setattr(service, "count_klines", fake_count)
    monkeypatch.setattr(service, "fetch_klines_sync", fake_sync)
    monkeypatch.setattr(service, "prewarm_structure_snapshots", fake_snapshot_prewarm)
    monkeypatch.setattr(service, "prewarm_ai_structure_contexts", fake_context_prewarm)

    import asyncio

    asyncio.run(service._backfill_and_rewarm(
        user_id=7,
        symbols=["sh.600519"],
        levels=["day"],
        compute_profile="chart_standard_v1",
        priority=88,
        reason="test_pipeline_backfill",
    ))

    assert snapshot_calls[0]["symbols"] == ["sh.600519"]
    assert snapshot_calls[0]["levels"] == ["day"]
    assert snapshot_calls[0]["requested_by_user_id"] == 7
    assert context_calls[0]["user_id"] == 7
    assert context_calls[0]["levels"] == ["day"]
    assert context_calls[0]["allow_when_auto_disabled"] is False
