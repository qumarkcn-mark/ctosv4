from server.db import database
from server.engines.ai_native import czsc_snapshot_service as service
from server.engines.ai_native import structure_context_service as context_service


def reset_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()


def signature(value):
    return {
        "source": "baostock",
        "row_count": 120,
        "first_date": "2026-01-01",
        "last_date": "2026-05-12",
        "signature": value,
    }


def test_status_reports_stale_when_data_signature_changes(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "get_kline_window_signature", lambda *args, **kwargs: signature("sig-old"))
    service.save_snapshot(
        symbol="sh600519",
        level="day",
        compute_profile=service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-old",
        data_as_of="2026-05-12",
        snapshot_payload={"level": "day", "price": 10.0},
        raw_bi_context={"levels": {}},
        engine_version="test",
        adapter_version="test-adapter",
    )

    monkeypatch.setattr(service, "get_kline_window_signature", lambda *args, **kwargs: signature("sig-new"))
    status = service.get_snapshot_status(symbol="sh.600519", level="day")

    assert status["status"] == "stale"
    assert status["snapshot"]["data_signature"] == "sig-old"
    assert status["freshness"]["data_signature"] == "sig-new"


def test_status_reports_pending_for_current_job_without_snapshot(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "get_kline_window_signature", lambda *args, **kwargs: signature("sig-pending"))

    service.prewarm_structure_snapshots(symbols=["sh600519"], levels=["day"])
    status = service.get_snapshot_status(symbol="sh600519", level="day")

    assert status["status"] == "pending"
    assert status["job"]["status"] == "PENDING"


def test_context_status_reports_snapshot_job_failure(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    conn = database.get_connection()
    try:
        conn.execute("INSERT INTO users (id, openid, nickname) VALUES (1, 'u1', 'U1')")
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(service, "get_kline_window_signature", lambda *args, **kwargs: signature("sig-failed"))

    service.prewarm_structure_snapshots(symbols=["sh600519"], levels=["5"], requested_by_user_id=1)
    job = service.claim_next_snapshot_job(worker_id="snap-worker")
    service._fail_job(job["job_id"], code="CZSC_UNAVAILABLE", message="CZSC dependency unavailable", retryable=False)

    status = context_service.get_ai_structure_context_status(user_id=1, symbol="sh600519", levels=["5"])

    assert status["status"] == "failed"
    assert status["stale_reason"] == "CZSC_UNAVAILABLE"
    assert status["job"]["status"] == "FAILED_FINAL"


def test_recover_failed_snapshot_jobs_requeues_infra_failures(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(service.czsc_adapter, "get_czsc_engine_version", lambda: "0.10.12")
    monkeypatch.setattr(service, "get_kline_window_signature", lambda *args, **kwargs: signature("sig-recover"))

    service.prewarm_structure_snapshots(symbols=["sh600519"], levels=["5"], requested_by_user_id=1)
    job = service.claim_next_snapshot_job(worker_id="snap-worker")
    service._fail_job(job["job_id"], code="CZSC_UNAVAILABLE", message="CZSC dependency unavailable", retryable=False)

    recovered = service.recover_failed_snapshot_jobs(reason="test_recover")
    next_job = service.claim_next_snapshot_job(worker_id="snap-worker")

    assert recovered["count"] == 1
    assert recovered["items"][0]["symbol"] == "sh.600519"
    assert recovered["items"][0]["previous_error_code"] == "CZSC_UNAVAILABLE"
    assert next_job["status"] == "RUNNING"
    assert next_job["symbol"] == "sh.600519"
    assert next_job["error_code"] == ""


def test_recover_failed_snapshot_jobs_skips_when_czsc_unavailable(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(service.czsc_adapter, "get_czsc_engine_version", lambda: "unavailable")
    monkeypatch.setattr(service, "get_kline_window_signature", lambda *args, **kwargs: signature("sig-still-missing"))

    service.prewarm_structure_snapshots(symbols=["sh600519"], levels=["5"], requested_by_user_id=1)
    job = service.claim_next_snapshot_job(worker_id="snap-worker")
    service._fail_job(job["job_id"], code="CZSC_UNAVAILABLE", message="CZSC dependency unavailable", retryable=False)

    recovered = service.recover_failed_snapshot_jobs(reason="test_recover")
    status = service.get_snapshot_status(symbol="sh600519", level="5")

    assert recovered == {"count": 0, "items": [], "skipped": True, "reason": "CZSC_UNAVAILABLE"}
    assert status["status"] == "failed"
    assert status["job"]["status"] == "FAILED_FINAL"
