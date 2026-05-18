import json

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api import ai_structure
from server.api.auth import ALGORITHM, JWT_SECRET
from server.db import database
from server.engines.ai_native.unified_reasoning_service import UNIFIED_FULL_TEXT_VERSION


def reset_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()


def make_client():
    app = FastAPI()
    app.include_router(ai_structure.router, prefix="/api/ai-structure")
    return TestClient(app)


def auth_headers(user_id: int) -> dict:
    return {"Authorization": f"Bearer {jwt.encode({'sub': str(user_id)}, JWT_SECRET, algorithm=ALGORITHM)}"}


def seed_user(user_id: int):
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, openid, nickname) VALUES (?, ?, ?)",
            (user_id, f"u{user_id}", f"U{user_id}"),
        )
        conn.commit()
    finally:
        conn.close()


def seed_reasoning(user_id: int, symbol: str):
    conn = database.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO ai_structure_reasoning_runs (
                run_id, user_id, symbol, prompt_version, status,
                full_reasoning_text, summary_json
            )
            VALUES (?, ?, ?, ?, 'SUCCESS', ?, ?)
            """,
            (
                f"run-{user_id}-{symbol}",
                user_id,
                symbol,
                UNIFIED_FULL_TEXT_VERSION,
                "完整推演：守住 4.37 看三买，跌破 4.13 止损。仅供参考，不构成投资建议。",
                json.dumps(
                    {
                        "coach_summary": "日线三买构建中，盯4.37承接",
                        "monitor_conditions": {
                            "triggers": [
                                {
                                    "id": "t1",
                                    "type": "price_below",
                                    "level": 4.37,
                                    "message_on_trigger": "到承接位",
                                    "action_on_trigger": "关注",
                                },
                                {
                                    "id": "t2",
                                    "type": "price_below",
                                    "level": 4.13,
                                    "message_on_trigger": "跌破4.13，结构失效",
                                    "action_on_trigger": "止损",
                                },
                                {
                                    "id": "t3",
                                    "type": "price_above",
                                    "level": 4.5,
                                    "message_on_trigger": "站回4.50，三买确认",
                                    "action_on_trigger": "加仓",
                                },
                                {
                                    "id": "t4",
                                    "type": "price_above",
                                    "level": 4.8,
                                    "message_on_trigger": "突破4.80，离开确认",
                                    "action_on_trigger": "关注",
                                }
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_watchboard_merges_positions_watchlist_reasoning_and_prices(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    seed_user(1)
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO positions (user_id, symbol, name, quantity, avg_cost, current_price) VALUES (?, ?, ?, ?, ?, ?)",
            (1, "sh.600790", "轻纺城", 20000, 4.22, 4.38),
        )
        conn.execute("INSERT INTO watchlist_groups (id, user_id, name, sort_order) VALUES (10, 1, '自选', 0)")
        conn.execute(
            "INSERT INTO watchlist_items (group_id, symbol, name, sort_order) VALUES (?, ?, ?, ?)",
            (10, "sh.600519", "贵州茅台", 0),
        )
        conn.commit()
    finally:
        conn.close()
    seed_reasoning(1, "sh.600790")

    async def fake_prices(symbols):
        assert symbols == ["sh.600790", "sh.600519"]
        return {
            "sh600790": {"price": 4.4, "change_pct": 1.15, "name": "轻纺城"},
            "sh600519": {"price": 1500, "change_pct": -0.5, "name": "贵州茅台"},
        }

    monkeypatch.setattr(ai_structure, "get_batch_prices", fake_prices)
    client = make_client()

    response = client.get("/api/ai-structure/watchboard", headers=auth_headers(1))

    assert response.status_code == 200
    groups = response.json()["data"]["groups"]
    assert [group["name"] for group in groups] == ["持仓", "自选", "备选"]
    position = groups[0]["items"][0]
    assert position["symbol"] == "sh.600790"
    assert position["price"] == 4.4
    assert position["position"]["pnl_pct"] == 4.27
    assert position["reasoning_summary"]["one_liner"] == "日线三买构建中，盯4.37承接"
    assert position["reasoning_summary"]["key_level_down"] == 4.37
    assert position["reasoning_summary"]["key_level_up"] == 4.5
    assert position["monitor_conditions"]["triggers"][0]["level"] == 4.37
    assert groups[1]["items"][0]["symbol"] == "sh.600519"


def test_watchboard_is_user_scoped(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    seed_user(1)
    seed_user(2)
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO positions (user_id, symbol, name, quantity, avg_cost) VALUES (?, ?, ?, ?, ?)",
            (2, "sh.600790", "轻纺城", 100, 4.22),
        )
        conn.commit()
    finally:
        conn.close()
    async def empty_prices(symbols):
        return {}

    monkeypatch.setattr(ai_structure, "get_batch_prices", empty_prices)
    client = make_client()

    response = client.get("/api/ai-structure/watchboard", headers=auth_headers(1))

    assert response.status_code == 200
    assert response.json()["data"]["groups"][0]["items"] == []


def test_watchboard_falls_back_to_legacy_reasoning_and_compacts_opening(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    seed_user(1)
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO positions (user_id, symbol, name, quantity, avg_cost) VALUES (?, ?, ?, ?, ?)",
            (1, "sz.002158", "汉钟精机", 100, 31.2),
        )
        conn.execute(
            """
            INSERT INTO ai_structure_reasoning_runs (
                run_id, user_id, symbol, prompt_version, status,
                full_reasoning_text, summary_json, updated_at
            )
            VALUES (?, ?, ?, ?, 'SUCCESS', ?, ?, ?)
            """,
            (
                "legacy-run",
                1,
                "sz.002158",
                "ai_structure_reasoning.e1_dynamic_growth.full_text",
                "好的，请坐。当前走势处于5分钟承接，30分钟蓄势，日线观望的关键窗口。仅供参考，不构成投资建议。",
                json.dumps(
                    {
                        "coach_summary": "好的，请坐。当前走势处于5分钟承接，30分钟蓄势。",
                        "front_panel_text": "日线回拉考验中枢上沿4.37，等待5分钟背驰确认三买。",
                        "key_boundaries": [
                            {"type": "trigger", "price": 4.37, "description": "日线中枢上沿，多空分界线"},
                            {"type": "invalidation", "price": 4.13, "description": "日线中枢下沿，多头防守线"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                "2026-05-17T10:00:00+08:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    async def empty_prices(symbols):
        return {}

    monkeypatch.setattr(ai_structure, "get_batch_prices", empty_prices)
    client = make_client()

    response = client.get("/api/ai-structure/watchboard", headers=auth_headers(1))

    assert response.status_code == 200
    item = response.json()["data"]["groups"][0]["items"][0]
    assert item["reasoning_source"] == "legacy"
    assert item["reasoning_summary"]["one_liner"] == "日线回拉考验中枢上沿4.37，等待5分钟背驰确认三买"
    assert item["monitor_conditions"]["triggers"][0]["level"] == 4.37
    assert item["monitor_conditions"]["triggers"][1]["action_on_trigger"] == "减仓"
    assert "好的" not in item["reasoning_summary"]["one_liner"]
