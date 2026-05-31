import json

from server.engines.ai_native.structure_context_service import reasoning_availability
from server.db import database
from server.engines.ai_native.structure_context_service import (
    enqueue_context_job,
    get_latest_ai_structure_context,
    save_ai_structure_context,
    save_reasoning_run,
)
from server.engines.ai_native.structure_chat_service import answer_structure_question
from server.prompts.ai_structure_reasoning_prompt import normalize_reasoning_payload
from server.services.llm_service import _loads_lenient_json_object, _message_content_text, _message_reasoning_text


def test_reasoning_availability_only_ready_for_successful_llm():
    status = reasoning_availability({
        "reasoning": {
            "reasoning_meta": {
                "provider": "llm",
                "llm_status": "success",
            }
        }
    })

    assert status["ready"] is True
    assert status["status"] == "success"


def test_reasoning_availability_hides_local_fallback():
    status = reasoning_availability({
        "reasoning": {
            "reasoning_meta": {
                "provider": "local_fallback",
                "llm_status": "not_invoked",
            }
        }
    })

    assert status["ready"] is False
    assert status["status"] == "unavailable"
    assert "不展示本地算法边界" in status["message"]


def test_reasoning_availability_hides_failed_llm():
    status = reasoning_availability({
        "reasoning": {
            "reasoning_meta": {
                "provider": "local_fallback",
                "llm_status": "failed",
            }
        }
    })

    assert status["ready"] is False
    assert status["status"] == "failed"
    assert "不展示本地算法边界" in status["message"]


def test_reasoning_payload_level_fields_strip_price_conditions():
    reasoning_input = {
        "structure_facts": {
            "boundary": {
                "levels": {
                    "30": {"active_center": {"zg": 177.36, "zd": 165.42}},
                    "5": {"active_center": {"zg": 253.49, "zd": 243.0}},
                }
            }
        }
    }
    normalized = normalize_reasoning_payload(
        {
            "main_level": "30分钟",
            "trigger_level": "30分钟中枢上沿177.36元",
            "scenario_branches": [],
            "key_boundaries": [],
            "risk_notes": [],
        },
        symbol="sh.688008",
        reasoning_input=reasoning_input,
    )

    assert normalized["main_level"] == "30"
    assert normalized["trigger_level"] == "30"


def test_reasoning_payload_scrubs_unconfirmed_daily_down_bi():
    reasoning_input = {
        "structure_facts": {
            "snapshots": [
                {
                    "level": "day",
                    "raw_bi_context": {
                        "levels": {
                            "day": {
                                "bi_sequence": [
                                    {"direction": "UP", "is_sure": True},
                                ]
                            }
                        }
                    },
                }
            ],
            "boundary": {"levels": {"5": {"active_center": {"zg": 260.51, "zd": 253.49}}}},
        }
    }
    normalized = normalize_reasoning_payload(
        {
            "coach_summary": "日线回拉笔将延续。",
            "trend_growth": {
                "growth_path": "结束日线回拉。",
                "failure_path": "日线向下笔可能延续。",
            },
        },
        symbol="sh.688008",
        reasoning_input=reasoning_input,
    )

    text = json.dumps(normalized, ensure_ascii=False)
    assert "日线回拉笔" not in text
    assert "日线向下笔" not in text
    assert "日线顶分型后的待确认回落" in text


def test_context_job_force_rebuild_requeues_existing_success(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    conn = database.get_connection()
    try:
        conn.execute("INSERT INTO users (id, openid, nickname) VALUES (1, 'u1', 'U1')")
        conn.commit()
    finally:
        conn.close()

    first = enqueue_context_job(
        user_id=1,
        symbol="sh.688008",
        compute_profile="chart_standard_v1",
        source_snapshot_ids=["snap-a", "snap-b"],
        reason="first",
    )
    conn = database.get_connection()
    try:
        conn.execute(
            """
            UPDATE ai_structure_context_jobs
               SET status = 'SUCCESS',
                   result_context_id = 'ctx-old',
                   finished_at = updated_at
             WHERE job_id = ?
            """,
            (first["job_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    rebuilt = enqueue_context_job(
        user_id=1,
        symbol="sh.688008",
        compute_profile="chart_standard_v1",
        source_snapshot_ids=["snap-a", "snap-b"],
        reason="retry",
        force_rebuild=True,
    )

    assert rebuilt["status"] == "PENDING"
    assert rebuilt["forced"] is True
    assert rebuilt["job_id"] != first["job_id"]
    assert rebuilt["result_context_id"] == ""


def test_lenient_json_repairs_code_fence_and_trailing_comma():
    parsed = _loads_lenient_json_object('```json\\n{"a": 1, "b": "2%",}\\n```')

    assert parsed == {"a": 1, "b": "2%"}


def test_reasoning_content_is_not_treated_as_json_content():
    class Message:
        content = ""
        reasoning_content = '{"should_not": "parse_as_final"}'

    assert _message_content_text(Message()) == ""
    assert _message_reasoning_text(Message()) == '{"should_not": "parse_as_final"}'


def test_latest_context_prefers_previous_successful_reasoning(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    conn = database.get_connection()
    try:
        conn.execute("INSERT INTO users (id, openid, nickname) VALUES (1, 'u1', 'U1')")
        conn.commit()
    finally:
        conn.close()

    common = {
        "user_id": 1,
        "symbol": "sh.688008",
        "prompt_version": "ai_structure_reasoning.e1_dynamic_growth",
        "source_snapshot_ids": ["snap-a"],
        "raw_context": {},
        "background": {},
        "boundary": {},
        "summary_text": "",
    }
    success = save_ai_structure_context(
        **common,
        context_fingerprint="a" * 64,
        reasoning={"reasoning_meta": {"provider": "llm", "llm_status": "success"}},
    )
    save_ai_structure_context(
        **common,
        context_fingerprint="b" * 64,
        reasoning={"reasoning_meta": {"provider": "local_fallback", "llm_status": "failed"}},
    )

    latest = get_latest_ai_structure_context(user_id=1, symbol="sh.688008")

    assert latest["context_id"] == success["context_id"]
    assert reasoning_availability(latest)["ready"] is True


def test_chat_answers_from_saved_full_reasoning(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO users (id, openid, nickname, settings_json) VALUES (1, 'u1', 'U1', ?)",
            (json.dumps({"deepseek_api_key": "sk-test"}),),
        )
        conn.commit()
    finally:
        conn.close()

    context = save_ai_structure_context(
        user_id=1,
        symbol="sh.688008",
        prompt_version="ai_structure_reasoning.e1_dynamic_growth",
        context_fingerprint="c" * 64,
        source_snapshot_ids=["snap-a"],
        raw_context={"position_context": {"has_position": True, "quantity": 2000, "avg_cost": 135, "current_price": 247.98}},
        reasoning={
            "reasoning_meta": {"provider": "llm", "llm_status": "success"},
            "structure_summary": "周线向上离开，5分钟跌破小中枢。",
            "scenario_branches": [],
        },
        background={},
        boundary={
            "levels": {
                "5": {
                    "snapshot_id": "snap-a",
                    "active_center": {"zg": 253.49, "zd": 243.0},
                    "evidence": {"trigger_line": "snap-a:5:line:trigger", "invalidation_line": "snap-a:5:line:invalidation"},
                }
            }
        },
        summary_text="周线强，小级别破坏。",
        coach_summary="周线强，小级别破坏。",
        main_level="day",
        trigger_level="5",
    )
    save_reasoning_run(
        user_id=1,
        symbol="sh.688008",
        source_snapshot_ids=["snap-a"],
        prompt_version="ai_structure_reasoning.e1_dynamic_growth.full_text",
        status="SUCCESS",
        full_reasoning_text="Think全文：当前不适合加仓，5分钟需站回253.49，跌破243要防守。仅供参考，不构成投资建议",
        summary={},
        context_id=context["context_id"],
    )

    async def fake_markdown(self, system_prompt, context_json, *, user_id=1, model_route=None):
        payload = json.loads(context_json)
        assert payload["version"] == "ai_structure_chat_from_saved_reasoning.v2"
        assert payload["chat_style"] == "intraday_companion"
        assert "Think全文" in payload["full_reasoning_excerpt"]
        assert "full_reasoning_text" not in payload
        assert payload["chat_context"]["version"] == "ai_structure_chat_context.v1"
        assert payload["answer_contract"]["mode"] == "concise"
        assert payload["question"] == "我先持仓2000股，成本135，要不要加仓？"
        assert payload["runtime_context"]["current_price"] == 247.98
        assert payload["runtime_context"]["think"]["ready"] is True
        assert payload["runtime_context"]["think"]["llm_status"] == "success"
        assert model_route.model_name == "deepseek-v4-flash"
        assert model_route.thinking_enabled is False
        assert model_route.reasoning_effort == "high"
        assert "盘中盯盘搭档" in system_prompt
        assert "缠中说缠原文" in system_prompt
        return "已有盈利仓先保护利润，现在不适合加仓，只有5分钟站回253.49后才进入观察；跌破243要复核防守。仅供参考，不构成投资建议"

    monkeypatch.setattr("server.services.llm_service.LLMService.infer_ai_native_markdown", fake_markdown)

    answer = answer_structure_question(
        user_id=1,
        symbol="sh.688008",
        question="我先持仓2000股，成本135，要不要加仓？",
    )

    assert answer["context_id"] == context["context_id"]
    assert answer["runtime_context"]["current_price"] == 247.98
    assert answer["runtime_context"]["think"]["ready"] is True
    assert "不适合加仓" in answer["coach_answer"]
    assert "253.49" in answer["coach_answer"]
    assert answer["coach_answer"].endswith("仅供参考，不构成投资建议")
    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT mode, trigger_reason, decision, context_id FROM ai_trigger_logs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert dict(row) == {
        "mode": "short_answer",
        "trigger_reason": "user_question",
        "decision": "generated",
        "context_id": context["context_id"],
    }


def test_unified_chat_receives_reasoning_continuity_context(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO users (id, openid, nickname, settings_json) VALUES (1, 'u1', 'U1', ?)",
            (json.dumps({"deepseek_api_key": "sk-test"}),),
        )
        conn.commit()
    finally:
        conn.close()

    context = save_ai_structure_context(
        user_id=1,
        symbol="sh.600790",
        prompt_version="unified_reasoning.v2",
        context_fingerprint="e" * 64,
        source_snapshot_ids=["snap-u"],
        raw_context={"position_context": {"holding": True, "shares": 40000, "cost": 4.39, "current_price": 4.01}},
        reasoning={
            "reasoning_meta": {"provider": "llm", "llm_status": "success"},
            "structure_summary": "日线中枢下沿观察。",
            "scenario_branches": [],
        },
        background={},
        boundary={
            "levels": {
                "day": {
                    "snapshot_id": "snap-u",
                    "active_center": {"zg": 4.09, "zd": 3.79},
                    "evidence": {"trigger_line": "snap-u:day:line:trigger"},
                }
            }
        },
        summary_text="日线中枢下沿观察。",
        coach_summary="日线中枢下沿观察。",
        main_level="day",
        trigger_level="5",
    )
    save_reasoning_run(
        user_id=1,
        symbol="sh.600790",
        source_snapshot_ids=["snap-u"],
        prompt_version="unified_reasoning.v2.full_text",
        status="SUCCESS",
        full_reasoning_text=(
            "统一推演全文：轻纺城围绕4.09和3.79观察。"
            + "盘中背景。" * 500
            + "最后两行卡片：现在验证4.09上方能否站稳；成立看5分钟二买，失败回中枢。仅供参考，不构成投资建议"
        ),
        summary={
            "card_summary": "测试4.09压力",
            "card_secondary": "站稳看5分钟二买，跌回中枢看失败",
            "card_action": "持仓观察",
            "watch_state_machine": {
                "current_state": {"label": "压力测试"},
                "transitions": [{"then": "站稳看5分钟二买"}],
            },
            "monitor_conditions": {
                "triggers": [
                    {"type": "price_above", "level": 4.09, "message_on_trigger": "站回上沿", "action_on_trigger": "关注"},
                    {"type": "price_below", "level": 3.79, "message_on_trigger": "跌破下沿", "action_on_trigger": "关注"},
                ]
            },
        },
        context_id=context["context_id"],
    )

    monkeypatch.setattr(
        "server.engines.ai_native.structure_chat_service._chat_intraday_observation",
        lambda symbol, quote=None: {
            "as_of": "2026-05-22 14:30:00",
            "source": "tdx_quote_aggregation",
            "usage": "intraday_preview",
            "coverage": {"quality": "partial"},
            "quote": {"price": 4.12, "change_pct": 1.2},
            "levels": {
                "5m": {
                    "last_bar_at": "2026-05-22 14:30:00",
                    "last_bar_status": "FORMING",
                    "last_close": 4.12,
                    "intraday_bar_count": 48,
                    "macd_closed_only": {
                        "basis": "closed_only",
                        "macd_state": "below_zero",
                        "macd_momentum": "improving",
                        "volume_state": "low_volume",
                    },
                    "macd_with_forming": {
                        "basis": "with_forming",
                        "macd_state": "near_zero",
                        "macd_momentum": "strengthening",
                        "volume_state": "expanding",
                    },
                }
            },
        },
    )

    async def fake_markdown(self, system_prompt, context_json, *, user_id=1, model_route=None):
        payload = json.loads(context_json)
        continuity = payload["reasoning_continuity_context"]
        assert payload["version"] == "unified_reasoning_chat.v1"
        assert payload["chat_style"] == "intraday_companion"
        assert payload["chat_context"]["version"] == "ai_structure_chat_context.v1"
        assert payload["chat_context"]["live_tape"]["price"] == 4.12
        assert payload["chat_context"]["intraday_live_snapshot"]["price"] == 4.12
        assert "postmarket_1m_snapshot" in payload["chat_context"]
        assert "today_1m_intraday_snapshot" not in payload["chat_context"]
        assert payload["intraday_live_snapshot"]["quote"]["price"] == 4.12
        assert "postmarket_1m_snapshot" in payload
        assert "today_1m_intraday_snapshot" not in payload
        assert payload["chat_context"]["live_tape"]["price_source"] == "intraday_quote"
        assert payload["chat_context"]["live_tape"]["levels"]["5m"]["with_forming"]["macd_momentum"] == "strengthening"
        assert payload["chat_context"]["trigger_state"]["crossed"][0]["level"] == 4.09
        assert "full_reasoning_text" not in payload
        assert payload["full_reasoning_excerpt"].startswith("统一推演全文")
        assert payload["full_reasoning_tail_excerpt"].startswith("[前文已截断")
        assert "成立看5分钟二买，失败回中枢" in payload["full_reasoning_tail_excerpt"]
        assert payload["watch_card_context"]["card_summary"] == "测试4.09压力"
        assert payload["watch_card_context"]["card_secondary"] == "站稳看5分钟二买，跌回中枢看失败"
        assert payload["watch_card_context"]["watch_state_machine"]["current_state"]["label"] == "压力测试"
        assert payload["answer_contract"]["mode"] == "concise"
        assert "缠中说缠原文" in system_prompt
        assert "买卖点转化" in system_prompt
        assert "像真人一样回答" in system_prompt
        assert continuity["previous_reasoning"]["card_summary"] == "测试4.09压力"
        assert continuity["trigger_status_since_last_run"][0]["status"] == "crossed"
        assert continuity["trigger_status_since_last_run"][0]["current_price"] == 4.12
        assert continuity["intraday_reference"]["coverage"]["quality"] == "partial"
        return "盘中已经站到4.09上方，这比上一轮更强；但5分钟还是FORMING，先看能不能守住4.09。仅供参考，不构成投资建议"

    monkeypatch.setattr("server.services.llm_service.LLMService.infer_ai_native_markdown", fake_markdown)

    answer = answer_structure_question(
        user_id=1,
        symbol="sh.600790",
        question="现在盘中怎么看？",
    )

    assert answer["reasoning_continuity_context"]["previous_reasoning"]["card_summary"] == "测试4.09压力"
    assert answer["reasoning_continuity_context"]["trigger_status_since_last_run"][0]["status"] == "crossed"
    assert "4.09" in answer["coach_answer"]


def test_chat_does_not_use_full_reasoning_from_different_snapshot_set(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO users (id, openid, nickname, settings_json) VALUES (1, 'u1', 'U1', ?)",
            (json.dumps({"deepseek_api_key": "sk-test"}),),
        )
        conn.commit()
    finally:
        conn.close()

    context = save_ai_structure_context(
        user_id=1,
        symbol="sh.688008",
        prompt_version="ai_structure_reasoning.e1_dynamic_growth",
        context_fingerprint="d" * 64,
        source_snapshot_ids=["snap-new"],
        raw_context={},
        reasoning={
            "reasoning_meta": {"provider": "llm", "llm_status": "success"},
            "structure_summary": "新结构摘要。",
            "scenario_branches": [],
        },
        background={},
        boundary={
            "levels": {
                "5": {
                    "snapshot_id": "snap-new",
                    "active_center": {"zg": 253.49, "zd": 243.0},
                    "evidence": {"trigger_line": "snap-new:5:line:trigger", "invalidation_line": "snap-new:5:line:invalidation"},
                }
            }
        },
        summary_text="新结构摘要。",
    )
    save_reasoning_run(
        user_id=1,
        symbol="sh.688008",
        source_snapshot_ids=["snap-old"],
        prompt_version="ai_structure_reasoning.e1_dynamic_growth.full_text",
        status="SUCCESS",
        full_reasoning_text="旧 Think 全文，不应该被新 context 使用。",
        summary={},
        context_id="old-context",
    )

    async def forbidden_markdown(*args, **kwargs):
        raise AssertionError("chat must not use reasoning text from another snapshot set")

    monkeypatch.setattr("server.services.llm_service.LLMService.infer_ai_native_markdown", forbidden_markdown)

    answer = answer_structure_question(
        user_id=1,
        symbol="sh.688008",
        question="我现在能买吗？",
    )

    assert answer["context_id"] == context["context_id"]
    assert "不能直接回答" in answer["coach_answer"]
    assert "旧 Think" not in answer["coach_answer"]
