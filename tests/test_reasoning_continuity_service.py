import json

from server.db import database
from server.engines.ai_native.reasoning_continuity_service import build_reasoning_continuity_context


def setup_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    conn = database.get_connection()
    try:
        conn.execute("INSERT INTO users (id, openid, nickname) VALUES (1, 'u1', 'U1')")
        conn.execute(
            """
            INSERT INTO ai_structure_reasoning_runs (
                run_id, user_id, symbol, context_id, source_snapshot_ids_json,
                prompt_version, status, full_reasoning_text, summary_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-prev",
                1,
                "sz.300394",
                "ctx-prev",
                json.dumps(["snap-a"]),
                "unified_reasoning.v2.full_text",
                "SUCCESS",
                "previous full reasoning",
                json.dumps(
                    {
                        "generated_at": "2026-05-22T10:00:00+08:00",
                        "data_as_of": "2026-05-22 09:30:00",
                        "card_summary": "关注365压力",
                        "card_action": "持仓观察",
                        "monitor_conditions": {
                            "triggers": [
                                {
                                    "type": "price_above",
                                    "level": 365.05,
                                    "message_on_trigger": "站上压力",
                                    "action_on_trigger": "关注",
                                },
                                {
                                    "type": "price_below",
                                    "level": 334.27,
                                    "message_on_trigger": "跌破支撑",
                                    "action_on_trigger": "考虑减仓",
                                },
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
                "2026-05-22T10:00:00+08:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO ai_structure_chat_messages (
                message_id, session_id, user_id, symbol, context_id, role,
                question_text, intent_type, answer_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, 'assistant', ?, ?, ?, ?)
            """,
            (
                "msg-1",
                "sess-1",
                1,
                "sz.300394",
                "ctx-prev",
                "30分钟MACD还没回零轴",
                "structure_followup",
                "{}",
                "2026-05-22T10:30:00+08:00",
            ),
        )
        conn.execute(
            """
            INSERT INTO scenario_outcomes (
                outcome_id, user_id, branch_id, symbol, checked_at, outcome,
                outcome_score, settlement_window, trigger_price, invalidated_price,
                user_followed_plan
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "out-1",
                1,
                "branch-1",
                "sz.300394",
                "2026-05-21T15:00:00+08:00",
                "invalidated",
                -1,
                "manual",
                334.27,
                334.0,
                0,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_build_reasoning_continuity_context(monkeypatch, tmp_path):
    setup_db(monkeypatch, tmp_path)

    context = build_reasoning_continuity_context(
        user_id=1,
        symbol="sz300394",
        current_price=372.7,
        intraday_observation={
            "as_of": "2026-05-22 14:23:23",
            "coverage": {"quality": "partial"},
            "quote": {"price": 372.7},
        },
        prompt_versions={"unified_reasoning.v2.full_text"},
    )

    assert context["version"] == "reasoning_continuity.v1"
    assert context["previous_reasoning"]["run_id"] == "run-prev"
    assert context["previous_reasoning"]["card_summary"] == "关注365压力"
    assert context["trigger_status_since_last_run"][0]["status"] == "crossed"
    assert context["trigger_status_since_last_run"][1]["status"] == "not_touched"
    assert context["recent_user_observations"][0]["question_text"] == "30分钟MACD还没回零轴"
    assert context["recent_outcomes"][0]["outcome"] == "invalidated"
    assert context["recent_outcomes"][0]["user_followed_plan"] is False
    assert context["intraday_reference"]["coverage"]["quality"] == "partial"
