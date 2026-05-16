from datetime import datetime, timedelta, timezone
import asyncio
import json

from server.db import database
from server.engines.ai_native import structure_context_service as context_service
from server.engines.ai_native import czsc_snapshot_service as snapshot_service


def reset_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()


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


def save_snapshot(symbol="sh600519", level="5", signature="sig-5", price=10.5, zg=11.0, zd=10.0):
    return snapshot_service.save_snapshot(
        symbol=symbol,
        level=level,
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature=signature,
        data_as_of="2026-05-12",
        snapshot_payload={
            "level": level,
            "price": price,
            "active_zhongshu": {
                "zg": zg,
                "zd": zd,
                "begin_time": "2026-05-12 10:00:00",
                "end_time": "2026-05-12 11:00:00",
            },
        },
        raw_bi_context={"levels": {level: {"last_close": price}}},
        engine_version="test-czsc",
        adapter_version="test-adapter",
    )


def test_context_prewarm_enqueues_without_structure_compute(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    ensure_user()
    snap = save_snapshot()
    called = {"snapshot": 0}

    def forbidden(*args, **kwargs):
        called["snapshot"] += 1
        raise AssertionError("context prewarm must not compute CZSC")

    monkeypatch.setattr(snapshot_service.czsc_adapter, "analyze_czsc_structure_sync", forbidden)

    result = context_service.prewarm_ai_structure_contexts(
        user_id=1,
        symbols=["sh600519"],
        levels=["5"],
    )

    assert called["snapshot"] == 0
    assert result["count"] == 1
    assert result["items"][0]["status"] == "PENDING"
    assert result["items"][0]["source_snapshot_ids"] == [snap["snapshot_id"]]


def test_context_worker_creates_user_context_and_branches(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    snap = save_snapshot()
    ensure_user()
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT INTO positions (user_id, symbol, name, quantity, avg_cost, current_price) VALUES (?, ?, ?, ?, ?, ?)",
            (1, "sh.600519", "贵州茅台", 100, 9.8, 10.5),
        )
        conn.commit()
    finally:
        conn.close()

    context_service.prewarm_ai_structure_contexts(user_id=1, symbols=["sh600519"], levels=["5"])
    job = context_service.claim_next_context_job(worker_id="ctx-worker")
    result = context_service.run_context_job_sync(job)

    assert result["status"] == "success"
    latest = context_service.get_latest_ai_structure_context(user_id=1, symbol="sh600519")
    assert latest["user_id"] == 1
    assert latest["symbol"] == "sh.600519"
    assert latest["source_snapshot_ids"] == [snap["snapshot_id"]]
    assert latest["raw_context"]["position_context"]["has_position"] is True
    assert latest["background"]["rules"]["structure_source"] == "czsc_snapshot_only"
    assert latest["prompt_version"] == "ai_structure_reasoning.e1_dynamic_growth"
    assert latest["reasoning"]["version"] == "ai_structure_reasoning.e1_dynamic_growth"
    assert latest["reasoning"]["trend_growth"]["growth_path"]
    assert latest["reasoning"]["scenario_branches"]
    assert latest["main_level"] == "5"
    assert latest["trigger_level"] == "5"
    assert latest["coach_summary"]
    assert "仅供参考，不构成投资建议" in latest["summary_text"]
    assert latest["branches"]
    assert {branch["branch_type"] for branch in latest["branches"]} >= {
        "observe_breakout",
        "invalidation_watch",
        "holding_defense",
    }


def test_async_context_worker_uses_llm_reasoning_when_key_configured(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    save_snapshot()
    ensure_user()
    conn = database.get_connection()
    try:
        conn.execute(
            "UPDATE users SET settings_json = ? WHERE id = 1",
            (json.dumps({"deepseek_api_key": "sk-test", "ai_native_thinking_enabled": False}),),
        )
        conn.commit()
    finally:
        conn.close()

    async def fake_markdown(self, system_prompt, context_json, *, user_id=1, model_route=None):
        assert "缠论结构推演层" in system_prompt
        assert model_route.thinking_enabled is True
        payload = json.loads(context_json)
        assert payload["structure_facts"]["boundary"]["primary_level"] == "5"
        return "完整 Think 推演：日线中枢边界内，5分钟触发观察。仅供参考，不构成投资建议"

    async def fake_infer(self, system_prompt, context_json, *, user_id=1, model_route=None):
        assert "前端摘要层" in system_prompt
        assert "不要写成逐级别笔走势清单" in system_prompt
        assert "不得提前写成“日线向下笔已形成”" in system_prompt
        assert model_route.thinking_enabled is False
        payload = json.loads(context_json)
        assert payload["structure_context"]["structure_facts"]["boundary"]["primary_level"] == "5"
        assert "完整 Think 推演" in payload["full_reasoning_text"]
        return {
            "version": "ai_structure_reasoning.e1_dynamic_growth",
            "symbol": "sh.600519",
            "main_level": "day",
            "trigger_level": "5",
            "structure_summary": "日线中枢边界内，5分钟触发观察。",
            "trend_growth": {
                "current_state": "test_llm",
                "growth_path": "日线等待，5分钟承接后再看离开。",
                "next_confirmation": "5分钟回踩不破。",
                "failure_path": "跌破5分钟下沿。",
            },
            "divergence_view": {"status": "potential", "level": "5", "evidence": "离开段力度待比较", "risk_note": "关注潜在背驰"},
            "resonance_view": {"higher_level_context": "日线中枢", "lower_level_trigger": "5分钟承接", "resonance_type": "A+小b"},
            "scenario_branches": [
                {
                    "branch_type": "llm_a_plus_b",
                    "main_level": "day",
                    "trigger_level": "5",
                    "trigger_condition": {"type": "price_above", "price": 11.0, "level": "5", "label": "5分钟站上上沿"},
                    "invalidate_condition": {"type": "price_below", "price": 10.0, "level": "5", "label": "跌破5分钟下沿"},
                    "chart_focus": [],
                }
            ],
            "key_boundaries": [],
            "coach_summary": "LLM 推演摘要。仅供参考，不构成投资建议",
            "risk_notes": ["仅供参考，不构成投资建议"],
        }

    monkeypatch.setattr("server.services.llm_service.LLMService.infer_ai_native_markdown", fake_markdown)
    monkeypatch.setattr("server.services.llm_service.LLMService.infer_ai_native_json", fake_infer)

    context_service.prewarm_ai_structure_contexts(user_id=1, symbols=["sh600519"], levels=["5"])
    job = context_service.claim_next_context_job(worker_id="ctx-worker")
    result = asyncio.run(context_service.run_context_job(job))

    assert result["status"] == "success"
    latest = context_service.get_latest_ai_structure_context(user_id=1, symbol="sh600519")
    assert latest["reasoning"]["reasoning_meta"]["provider"] == "llm"
    assert latest["reasoning"]["reasoning_meta"]["pipeline"] == "think_full_text_then_flash_summary"
    assert latest["reasoning"]["reasoning_meta"]["full_reasoning_available"] is True
    assert latest["reasoning"]["trend_growth"]["current_state"] == "test_llm"
    assert latest["main_level"] == "day"
    assert latest["trigger_level"] == "5"
    assert latest["branches"][0]["branch_type"] == "llm_a_plus_b"
    conn = database.get_connection()
    try:
        run = conn.execute("SELECT * FROM ai_structure_reasoning_runs WHERE context_id = ?", (latest["context_id"],)).fetchone()
    finally:
        conn.close()
    assert run is not None
    assert run["status"] == "SUCCESS"
    assert "完整 Think 推演" in run["full_reasoning_text"]


def test_context_background_contract_keeps_fundamental_context_only(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    snap = save_snapshot()
    ensure_user()
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
                "品牌和现金流背景较强，但短线仍需结构确认",
                json.dumps(["盈利质量高"], ensure_ascii=False),
                json.dumps(["估值不低"], ensure_ascii=False),
                json.dumps([], ensure_ascii=False),
                "2026-05-12T12:00:00+08:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    context = context_service.build_ai_structure_context(user_id=1, symbol="sh600519", snapshots=[snap])

    background = context["background"]
    assert background["rules"]["background_role"] == "context_only"
    assert background["rules"]["structure_role"] == "decision_boundary"
    assert background["rules"]["conflict_policy"] == "structure_discipline_first"
    assert background["fundamental"]["status"] == "available"
    assert background["fundamental"]["verdict"] == "支持"
    assert "结构确认" in background["fundamental"]["summary"]
    assert context["raw_context"]["background_context"]["fundamental"]["role"] == "context_only"
    assert context["reasoning"]["resonance_view"]["conflict_note"]


def test_context_status_turns_stale_when_new_snapshot_exists(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    ensure_user()
    save_snapshot(signature="sig-old", price=10.5)
    context_service.prewarm_ai_structure_contexts(user_id=1, symbols=["sh600519"], levels=["5"])
    job = context_service.claim_next_context_job(worker_id="ctx-worker")
    context_service.run_context_job_sync(job)

    status = context_service.get_ai_structure_context_status(user_id=1, symbol="sh600519", levels=["5"])
    assert status["status"] == "fresh"

    save_snapshot(signature="sig-new", price=11.2, zg=11.5, zd=10.8)
    stale = context_service.get_ai_structure_context_status(user_id=1, symbol="sh600519", levels=["5"])

    assert stale["status"] == "stale"
    assert stale["stale_reason"] == "SOURCE_SNAPSHOT_CHANGED"
    assert stale["context"] is not None


def test_stale_running_context_job_returns_to_retryable(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    ensure_user()
    save_snapshot()
    context_service.prewarm_ai_structure_contexts(user_id=1, symbols=["sh600519"], levels=["5"])
    job = context_service.claim_next_context_job(worker_id="ctx-worker")
    old = (datetime.now(timezone(timedelta(hours=8))) - timedelta(seconds=120)).isoformat(timespec="seconds")

    conn = database.get_connection()
    try:
        conn.execute(
            "UPDATE ai_structure_context_jobs SET locked_at = ? WHERE job_id = ?",
            (old, job["job_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    swept = context_service.sweep_stale_context_jobs(timeout_seconds=1)
    assert swept == 1

    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT status, retry_count, locked_by, locked_at, error_code FROM ai_structure_context_jobs WHERE job_id = ?",
            (job["job_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "FAILED_RETRYABLE"
    assert row["retry_count"] == 1
    assert row["locked_by"] == ""
    assert row["locked_at"] is None
    assert row["error_code"] == "TIMEOUT"
