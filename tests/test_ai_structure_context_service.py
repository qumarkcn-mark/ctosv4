from datetime import datetime, timedelta, timezone

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
    assert "仅供参考，不构成投资建议" in latest["summary_text"]
    assert latest["branches"]
    assert {branch["branch_type"] for branch in latest["branches"]} >= {
        "observe_breakout",
        "invalidation_watch",
        "holding_defense",
    }


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
