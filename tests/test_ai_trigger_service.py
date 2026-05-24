import asyncio

from server.db import database
from server.engines.ai_native import ai_trigger_service as service


def reset_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    conn = database.get_connection()
    try:
        conn.execute("INSERT INTO users (id, openid, nickname) VALUES (1, 'u1', 'U1')")
        conn.commit()
    finally:
        conn.close()


def test_auto_trigger_skips_non_watchboard_symbol(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(service.config, "AI_AUTO_FULL_REASONING_ENABLED", True)
    monkeypatch.setattr(service.config, "AI_TRIGGER_COOLDOWN_SECONDS", 0)

    result = asyncio.run(service.request_ai_reasoning(
        user_id=1,
        symbol="sh600519",
        trigger_reason=service.TRIGGER_POST_TDX_REFRESH,
    ))

    assert result["decision"] == "skipped"
    assert result["skip_reason"] == "NOT_WATCHBOARD_SYMBOL"
    conn = database.get_connection()
    try:
        row = conn.execute("SELECT decision, skip_reason FROM ai_trigger_logs").fetchone()
    finally:
        conn.close()
    assert dict(row) == {"decision": "skipped", "skip_reason": "NOT_WATCHBOARD_SYMBOL"}


def test_auto_trigger_generates_for_watchboard_symbol(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    conn = database.get_connection()
    try:
        conn.execute("INSERT INTO watchlist_groups (id, user_id, name, sort_order) VALUES (10, 1, '自选', 0)")
        conn.execute(
            "INSERT INTO watchlist_items (group_id, symbol, name, sort_order) VALUES (10, 'sh600519', '贵州茅台', 0)"
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(service.config, "AI_AUTO_FULL_REASONING_ENABLED", True)
    monkeypatch.setattr(service.config, "AI_TRIGGER_COOLDOWN_SECONDS", 0)

    async def fake_trigger(**kwargs):
        return {
            "symbol": kwargs["symbol"],
            "run_id": "run_1",
            "context_id": "ctx_1",
            "data_as_of": "2026-05-23",
        }

    monkeypatch.setattr(service, "trigger_unified_reasoning", fake_trigger)

    result = asyncio.run(service.request_ai_reasoning(
        user_id=1,
        symbol="sh600519",
        trigger_reason=service.TRIGGER_POST_TDX_REFRESH,
    ))

    assert result["trigger"]["decision"] == "generated"
    assert result["trigger"]["run_id"] == "run_1"
    conn = database.get_connection()
    try:
        row = conn.execute("SELECT decision, run_id, context_id FROM ai_trigger_logs").fetchone()
    finally:
        conn.close()
    assert dict(row) == {"decision": "generated", "run_id": "run_1", "context_id": "ctx_1"}


def test_manual_trigger_bypasses_watchboard_check(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(service.config, "AI_MANUAL_FULL_REASONING_ENABLED", True)
    monkeypatch.setattr(service.config, "AI_TRIGGER_COOLDOWN_SECONDS", 0)

    async def fake_trigger(**kwargs):
        return {"symbol": kwargs["symbol"], "run_id": "run_2", "context_id": "ctx_2"}

    monkeypatch.setattr(service, "trigger_unified_reasoning", fake_trigger)

    result = asyncio.run(service.request_ai_reasoning(
        user_id=1,
        symbol="sh600519",
        trigger_reason=service.TRIGGER_MANUAL_FULL_REASONING,
    ))

    assert result["trigger"]["decision"] == "generated"
    assert result["trigger"]["trigger_reason"] == service.TRIGGER_MANUAL_FULL_REASONING


def test_cooldown_skips_repeated_non_force_trigger(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(service.config, "AI_MANUAL_FULL_REASONING_ENABLED", True)
    monkeypatch.setattr(service.config, "AI_TRIGGER_COOLDOWN_SECONDS", 1800)

    async def fake_trigger(**kwargs):
        return {"symbol": kwargs["symbol"], "run_id": "run_3", "context_id": "ctx_3"}

    monkeypatch.setattr(service, "trigger_unified_reasoning", fake_trigger)

    first = asyncio.run(service.request_ai_reasoning(
        user_id=1,
        symbol="sh600519",
        trigger_reason=service.TRIGGER_MANUAL_FULL_REASONING,
    ))
    second = asyncio.run(service.request_ai_reasoning(
        user_id=1,
        symbol="sh600519",
        trigger_reason=service.TRIGGER_MANUAL_FULL_REASONING,
    ))

    assert first["trigger"]["decision"] == "generated"
    assert second["decision"] == "skipped"
    assert second["skip_reason"] == "COOLDOWN"
