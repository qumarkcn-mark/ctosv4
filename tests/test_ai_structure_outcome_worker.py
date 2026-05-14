import asyncio
import json

from server.db import database
from server.engines.ai_native import czsc_snapshot_service as snapshot_service
from server.engines.ai_native import outcome_settlement_service
from server.engines.ai_native import structure_context_service as context_service
from server.workers.ai_structure_outcome_worker import AIStructureOutcomeWorker


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


def build_branches(user_id=1):
    ensure_user(user_id)
    snapshot_service.save_snapshot(
        symbol="sh600519",
        level="5",
        compute_profile=snapshot_service.DEFAULT_COMPUTE_PROFILE,
        data_signature=f"sig-worker-{user_id}",
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


def set_branch_created_at(branch_id: str, value: str):
    conn = database.get_connection()
    try:
        conn.execute(
            "UPDATE scenario_branches SET created_at = ?, updated_at = ? WHERE branch_id = ?",
            (value, value, branch_id),
        )
        conn.commit()
    finally:
        conn.close()


def keep_only_branch(branch_id: str):
    conn = database.get_connection()
    try:
        conn.execute("UPDATE scenario_branches SET status = 'ARCHIVED' WHERE branch_id != ?", (branch_id,))
        conn.commit()
    finally:
        conn.close()


def test_settle_due_scenario_outcomes_writes_neutral_outcome_once(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    branches = build_branches()
    branch = next(item for item in branches if item["branch_type"] == "observe_breakout")
    keep_only_branch(branch["branch_id"])
    set_branch_created_at(branch["branch_id"], "2026-05-14T10:00:00+08:00")

    first = outcome_settlement_service.settle_due_scenario_outcomes(
        {"sh600519": {"price": 11.2}},
        windows=("same_day",),
        checked_at="2026-05-14T15:00:00+08:00",
    )
    second = outcome_settlement_service.settle_due_scenario_outcomes(
        {"sh600519": {"price": 11.2}},
        windows=("same_day",),
        checked_at="2026-05-14T15:30:00+08:00",
    )

    assert first["count"] == 1
    assert first["items"][0]["branch_id"] == branch["branch_id"]
    assert first["items"][0]["outcome"]["outcome"] == "triggered"
    assert first["items"][0]["outcome"]["user_followed_plan"] is None
    assert second["count"] == 0
    conn = database.get_connection()
    try:
        outcome_count = conn.execute("SELECT COUNT(*) AS c FROM scenario_outcomes").fetchone()["c"]
        memory = conn.execute(
            "SELECT * FROM ai_symbol_memory_profiles WHERE user_id = 1 AND symbol = 'sh.600519'",
        ).fetchone()
    finally:
        conn.close()
    assert outcome_count == 1
    assert json.loads(memory["profile_json"])["mistakes"] == []


def test_settle_due_scenario_outcomes_waits_for_window(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    branches = build_branches()
    branch = branches[0]
    set_branch_created_at(branch["branch_id"], "2026-05-12T10:00:00+08:00")

    early = outcome_settlement_service.settle_due_scenario_outcomes(
        {"sh600519": {"price": 10.5}},
        windows=("next_day",),
        checked_at="2026-05-12T15:00:00+08:00",
    )
    due = outcome_settlement_service.settle_due_scenario_outcomes(
        {"sh600519": {"price": 10.5}},
        windows=("next_day",),
        checked_at="2026-05-13T10:00:00+08:00",
    )

    assert early["count"] == 0
    assert due["count"] == 1
    assert due["items"][0]["settlement_window"] == "next_day"


def test_due_outcome_symbols_are_user_and_symbol_unique(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_branches(user_id=1)
    build_branches(user_id=2)

    symbols = outcome_settlement_service.list_due_outcome_symbols(
        windows=("same_day",),
        checked_at="2026-05-14T15:00:00+08:00",
    )

    assert symbols == ["sh.600519"]


def test_outcome_worker_tick_fetches_prices_and_settles(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    build_branches()

    async def fake_prices(symbols):
        assert symbols == ["sh.600519"]
        return {"sh.600519": {"price": 9.8}}

    monkeypatch.setattr("server.workers.ai_structure_outcome_worker.get_batch_prices", fake_prices)
    worker = AIStructureOutcomeWorker(interval_seconds=1)

    result = asyncio.run(worker.tick())

    assert result["count"] >= 1
    assert all(item["outcome"]["user_followed_plan"] is None for item in result["items"])
