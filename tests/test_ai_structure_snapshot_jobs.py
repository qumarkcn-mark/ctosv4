from datetime import datetime, timedelta, timezone

from server.db import database
from server.engines.ai_native import czsc_snapshot_service as service


def reset_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    conn = database.get_connection()
    try:
        conn.execute("INSERT INTO users (id, openid, nickname) VALUES (1, 'u1', 'U1')")
        conn.commit()
    finally:
        conn.close()


def fake_signature(signature="sig-a", last_date="2026-05-12", row_count=120):
    return {
        "source": "baostock",
        "row_count": row_count,
        "first_date": "2026-01-01",
        "last_date": last_date,
        "signature": signature,
    }


def test_prewarm_enqueues_without_computing(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    called = {"analyze": 0}
    monkeypatch.setattr(service, "get_kline_window_signature", lambda *args, **kwargs: fake_signature())
    monkeypatch.setattr(service.czsc_adapter, "analyze_czsc_structure_sync", lambda *args, **kwargs: called.__setitem__("analyze", 1))

    result = service.prewarm_structure_snapshots(
        symbols=["sh600519"],
        levels=["day"],
        requested_by_user_id=1,
    )

    assert called["analyze"] == 0
    assert result["count"] == 1
    item = result["items"][0]
    assert item["engine"] == "czsc"
    assert item["status"] == "PENDING"
    assert item["enqueued"] is True


def test_snapshot_force_rebuild_requeues_existing_skipped(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "_signature_for_level", lambda *args, **kwargs: fake_signature())

    first = service.prewarm_structure_snapshots(symbols=["sh600519"], levels=["day"], requested_by_user_id=1)
    first_job = first["items"][0]
    conn = database.get_connection()
    try:
        conn.execute(
            "UPDATE structure_snapshot_jobs SET status = 'SKIPPED', error_code = 'STALE_INPUT' WHERE job_id = ?",
            (first_job["job_id"],),
        )
        conn.commit()
    finally:
        conn.close()

    rebuilt = service.prewarm_structure_snapshots(
        symbols=["sh600519"],
        levels=["day"],
        requested_by_user_id=1,
        force_rebuild=True,
    )

    item = rebuilt["items"][0]
    assert item["status"] == "PENDING"
    assert item["enqueued"] is True
    assert item["job_id"] != first_job["job_id"]


def test_snapshot_worker_creates_czsc_snapshot(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "get_kline_window_signature", lambda *args, **kwargs: fake_signature())

    def fake_analyze(symbol, levels, count, compute_profile):
        return {
            "engine": "czsc",
            "error": "",
            "levels": {
                "day": {
                    "level": "day",
                    "klines": [{"time": "2026-05-12", "close": 10.5}],
                    "bis": [],
                    "zhongshus": [],
                    "active_zhongshu": {},
                    "price": 10.5,
                }
            },
        }

    def fake_raw(symbol, levels, count, compute_profile, precomputed_result=None):
        assert precomputed_result is not None
        return {"symbol": "sh.600519", "version": "czsc_raw_bi_context.v1", "levels": {"day": {"last_close": 10.5}}}

    monkeypatch.setattr(service.czsc_adapter, "analyze_czsc_structure_sync", fake_analyze)
    monkeypatch.setattr(service.czsc_adapter, "export_czsc_raw_bi_context_sync", fake_raw)
    monkeypatch.setattr(service.czsc_adapter, "get_czsc_engine_version", lambda: "test-czsc")

    service.prewarm_structure_snapshots(symbols=["sh600519"], levels=["day"], requested_by_user_id=1)
    job = service.claim_next_snapshot_job(worker_id="test-worker")
    result = service.run_snapshot_job_sync(job)

    assert result["status"] == "success"
    assert result["context_job"]["status"] == "PENDING"
    assert result["context_job"]["source_snapshot_ids"] == [result["snapshot_id"]]
    latest = service.get_latest_snapshot(symbol="sh.600519", level="day")
    assert latest["engine"] == "czsc"
    assert latest["engine_version"] == "test-czsc"
    assert latest["snapshot"]["price"] == 10.5
    status = service.get_snapshot_status(symbol="sh.600519", level="day")
    assert status["status"] == "fresh"
    assert status["snapshot"]["snapshot_id"] == latest["snapshot_id"]


def test_snapshot_followup_context_uses_latest_snapshot_set(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    signatures = {
        "day": fake_signature(signature="sig-day"),
        "5": fake_signature(signature="sig-5"),
    }
    monkeypatch.setattr(
        service,
        "get_kline_window_signature",
        lambda symbol, level, **kwargs: signatures.get(level, fake_signature(signature=f"sig-{level}")),
    )

    def fake_analyze(symbol, levels, count, compute_profile):
        level = levels[0]
        return {
            "engine": "czsc",
            "error": "",
            "levels": {
                level: {
                    "level": level,
                    "klines": [{"time": "2026-05-12", "close": 10.5}],
                    "bis": [],
                    "zhongshus": [],
                    "active_zhongshu": {},
                    "price": 10.5,
                }
            },
        }

    def fake_raw(symbol, levels, count, compute_profile, precomputed_result=None):
        assert precomputed_result is not None
        level = levels[0]
        return {"symbol": "sh.600519", "version": "czsc_raw_bi_context.v1", "levels": {level: {"last_close": 10.5}}}

    monkeypatch.setattr(service.czsc_adapter, "analyze_czsc_structure_sync", fake_analyze)
    monkeypatch.setattr(service.czsc_adapter, "export_czsc_raw_bi_context_sync", fake_raw)
    monkeypatch.setattr(service.czsc_adapter, "get_czsc_engine_version", lambda: "test-czsc")

    service.prewarm_structure_snapshots(symbols=["sh600519"], levels=["day", "5"], requested_by_user_id=1)
    first_job = service.claim_next_snapshot_job(worker_id="test-worker")
    first_result = service.run_snapshot_job_sync(first_job)
    second_job = service.claim_next_snapshot_job(worker_id="test-worker")
    second_result = service.run_snapshot_job_sync(second_job)

    assert first_result["status"] == "success"
    assert second_result["status"] == "success"
    assert set(second_result["context_job"]["source_snapshot_ids"]) == {
        first_result["snapshot_id"],
        second_result["snapshot_id"],
    }


def test_snapshot_followup_context_enqueues_all_interested_users(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    conn = database.get_connection()
    try:
        conn.execute("INSERT INTO users (id, openid, nickname) VALUES (2, 'u2', 'U2')")
        conn.execute(
            "INSERT INTO watchlist_groups (id, user_id, name, sort_order) VALUES (10, 1, '观察', 0), (20, 2, '观察', 0)"
        )
        conn.execute(
            """
            INSERT INTO watchlist_items (group_id, symbol, name, sort_order)
            VALUES (10, 'sh600519', '贵州茅台', 0),
                   (20, 'sh.600519', '贵州茅台', 0)
            """
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(service, "get_kline_window_signature", lambda *args, **kwargs: fake_signature())

    def fake_analyze(symbol, levels, count, compute_profile):
        return {
            "engine": "czsc",
            "error": "",
            "levels": {
                "day": {
                    "level": "day",
                    "klines": [{"time": "2026-05-12", "close": 10.5}],
                    "bis": [],
                    "zhongshus": [],
                    "active_zhongshu": {},
                    "price": 10.5,
                }
            },
        }

    def fake_raw(symbol, levels, count, compute_profile, precomputed_result=None):
        assert precomputed_result is not None
        return {"symbol": "sh.600519", "version": "czsc_raw_bi_context.v1", "levels": {"day": {"last_close": 10.5}}}

    monkeypatch.setattr(service.czsc_adapter, "analyze_czsc_structure_sync", fake_analyze)
    monkeypatch.setattr(service.czsc_adapter, "export_czsc_raw_bi_context_sync", fake_raw)
    monkeypatch.setattr(service.czsc_adapter, "get_czsc_engine_version", lambda: "test-czsc")

    service.prewarm_structure_snapshots(symbols=["sh600519"], levels=["day"])
    job = service.claim_next_snapshot_job(worker_id="test-worker")
    result = service.run_snapshot_job_sync(job)

    assert result["status"] == "success"
    assert result["context_job"]["count"] == 2
    assert {item["user_id"] for item in result["context_job"]["items"]} == {1, 2}

    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT user_id FROM ai_structure_context_jobs ORDER BY user_id"
        ).fetchall()
    finally:
        conn.close()
    assert [row["user_id"] for row in rows] == [1, 2]


def test_no_data_prewarm_skips_without_job(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        service,
        "get_kline_window_signature",
        lambda *args, **kwargs: fake_signature(signature="", last_date="", row_count=0),
    )

    result = service.prewarm_structure_snapshots(symbols=["sh600519"], levels=["day"])

    assert result["items"][0]["status"] == "skipped"
    assert result["items"][0]["reason"] == "NO_DATA"
    conn = database.get_connection()
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM structure_snapshot_jobs").fetchone()["c"]
    finally:
        conn.close()
    assert count == 0


def test_stale_running_snapshot_job_returns_to_retryable(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "get_kline_window_signature", lambda *args, **kwargs: fake_signature())

    service.prewarm_structure_snapshots(symbols=["sh600519"], levels=["day"], requested_by_user_id=1)
    job = service.claim_next_snapshot_job(worker_id="test-worker")
    old = (datetime.now(timezone(timedelta(hours=8))) - timedelta(seconds=120)).isoformat(timespec="seconds")

    conn = database.get_connection()
    try:
        conn.execute(
            "UPDATE structure_snapshot_jobs SET locked_at = ? WHERE job_id = ?",
            (old, job["job_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    swept = service.sweep_stale_snapshot_jobs(timeout_seconds=1)
    assert swept == 1

    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT status, retry_count, locked_by, locked_at, error_code FROM structure_snapshot_jobs WHERE job_id = ?",
            (job["job_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "FAILED_RETRYABLE"
    assert row["retry_count"] == 1
    assert row["locked_by"] == ""
    assert row["locked_at"] is None
    assert row["error_code"] == "TIMEOUT"

    reclaimed = service.claim_next_snapshot_job(worker_id="test-worker-2")
    assert reclaimed["job_id"] == job["job_id"]
    assert reclaimed["status"] == "RUNNING"
