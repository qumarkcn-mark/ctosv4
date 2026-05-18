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


def test_snapshot_status_batch_matches_single_status(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "get_kline_window_signature", lambda *args, **kwargs: signature("sig-batch"))
    service.save_snapshot(
        symbol="sh600519",
        level="5",
        compute_profile=service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-batch",
        data_as_of="2026-05-12",
        snapshot_payload={"level": "5", "price": 10.0},
        raw_bi_context={"levels": {}},
        engine_version="test",
        adapter_version="test-adapter",
    )

    single = service.get_snapshot_status(symbol="sh600519", level="5")
    batch = service.get_snapshot_status_batch(symbols=["sh600519"], levels=["5"])["sh.600519"]["5"]

    assert batch["status"] == single["status"] == "fresh"
    assert batch["snapshot"]["snapshot_id"] == single["snapshot"]["snapshot_id"]
    assert batch["freshness"]["data_signature"] == single["freshness"]["data_signature"]


def test_snapshot_status_batch_handles_mixed_symbols_and_missing_levels(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)

    def fake_signature(symbol, freq, *args, **kwargs):
        if symbol == "sh.600519" and freq == "5":
            return signature("sig-600519-5")
        if symbol == "sz.000988" and freq == "5":
            return signature("sig-000988-5-new")
        return {"source": "baostock", "row_count": 0, "first_date": "", "last_date": "", "signature": ""}

    monkeypatch.setattr(service, "get_kline_window_signature", fake_signature)
    service.save_snapshot(
        symbol="sh600519",
        level="5",
        compute_profile=service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-600519-5",
        data_as_of="2026-05-12",
        snapshot_payload={"level": "5", "price": 10.0},
        raw_bi_context={"levels": {}},
        engine_version="test",
        adapter_version="test-adapter",
    )
    service.save_snapshot(
        symbol="sz000988",
        level="5",
        compute_profile=service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-000988-5-old",
        data_as_of="2026-05-10",
        snapshot_payload={"level": "5", "price": 8.8},
        raw_bi_context={"levels": {}},
        engine_version="test",
        adapter_version="test-adapter",
    )

    batch = service.get_snapshot_status_batch(
        symbols=["sh600519", "sz000988", "sh600000"],
        levels=["5", "30"],
    )

    assert batch["sh.600519"]["5"]["status"] == "fresh"
    assert batch["sh.600519"]["30"]["status"] == "no_data"
    assert batch["sz.000988"]["5"]["status"] == "stale"
    assert batch["sz.000988"]["5"]["snapshot"]["data_signature"] == "sig-000988-5-old"
    assert batch["sh.600000"]["5"]["status"] == "no_data"
    assert batch["sh.600000"]["30"]["status"] == "no_data"


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


def test_context_status_reports_stale_when_kline_ahead_of_snapshot(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    conn = database.get_connection()
    try:
        conn.execute("INSERT INTO users (id, openid, nickname) VALUES (1, 'u1', 'U1')")
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(service, "get_kline_window_signature", lambda *args, **kwargs: signature("sig-old"))
    snap = service.save_snapshot(
        symbol="sh600519",
        level="5",
        compute_profile=service.DEFAULT_COMPUTE_PROFILE,
        data_signature="sig-old",
        data_as_of="2026-05-15 15:00:00",
        snapshot_payload={"level": "5", "price": 10.0},
        raw_bi_context={"levels": {}},
        engine_version="test",
        adapter_version="test-adapter",
    )
    context_service.prewarm_ai_structure_contexts(user_id=1, symbols=["sh600519"], levels=["5"])
    job = context_service.claim_next_context_job(worker_id="ctx-worker")
    context_service.run_context_job_sync(job)

    monkeypatch.setattr(service, "get_kline_window_signature", lambda *args, **kwargs: {
        **signature("sig-new"),
        "last_date": "2026-05-18 15:00:00",
    })
    status = context_service.get_ai_structure_context_status(user_id=1, symbol="sh600519", levels=["5"])

    assert status["context"]["source_snapshot_ids"] == [snap["snapshot_id"]]
    assert status["status"] == "stale"
    assert status["stale_reason"] == "KLINE_AHEAD_OF_SNAPSHOT"
    assert status["stale_levels"] == ["5"]
    assert status["level_freshness"][0]["data_as_of"] == "2026-05-15 15:00:00"
    assert status["level_freshness"][0]["kline_last_bar_at"] == "2026-05-18 15:00:00"


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
