import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.api import structure as structure_api
from server.db import database
from server.engines.structure import snapshot_query
from server.engines.structure.chan_snapshot_cache import save_chan_snapshot
from server.engines.structure.structure_jobs import (
    claim_next_structure_job,
    complete_structure_job,
    enqueue_structure_job,
    fail_structure_job,
    sweep_stale_running_jobs,
)
from server.engines.structure.structure_key import build_structure_key
from server.services import chan_detail_service
from server.workers import structure_compute_worker


def reset_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()


def test_structure_key_is_stable_and_ignores_display_count():
    key1 = build_structure_key(
        symbol="sh600519",
        freq="m30",
        data_signature="sig-a",
        cchan_preset="live_tolerant",
        compute_profile="chart_standard_v1",
    )
    key2 = build_structure_key(
        symbol="sh.600519",
        freq="30",
        data_signature="sig-a",
        cchan_preset="live_tolerant",
        compute_profile="chart_standard_v1",
    )
    key3 = build_structure_key(
        symbol="sh.600519",
        freq="30",
        data_signature="sig-a",
        cchan_preset="live_tolerant",
        compute_profile="deep_audit_v1",
    )

    assert key1.hash == key2.hash
    assert key1.hash != key3.hash
    assert "display_count" not in key1.to_dict()


def test_enqueue_single_flight_priority_bump(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    key = build_structure_key(symbol="sh.600519", freq="day", data_signature="sig-a")

    first = enqueue_structure_job(key, priority=20, reason="prewarm")
    second = enqueue_structure_job(key, priority=90, reason="user_view")

    assert first["enqueued"] is True
    assert second["enqueued"] is False
    assert second["bumped"] is True
    assert second["job_id"] == first["job_id"]
    assert second["priority"] == 90


def test_formal_snapshot_rejects_fallback_payload(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    key = build_structure_key(symbol="sh.600519", freq="day", data_signature="sig-a")

    fingerprint = save_chan_snapshot(
        symbol=key.symbol,
        freq=key.freq,
        cchan_preset=key.cchan_preset,
        kline_source="tencent",
        adjustflag="3",
        end_date="",
        max_compute_bars=1200,
        data_signature=key.data_signature,
        last_kline_time="2026-05-08",
        kline_count=120,
        compute_bars=1200,
        result={
            "symbol": key.symbol,
            "freq": key.freq,
            "data_source": {"provider": "tencent"},
            "klines": [{"time": "2026-05-08", "close": 10}],
        },
        structure_key_hash=key.hash,
        compute_profile=key.compute_profile,
    )

    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM chan_structure_snapshots WHERE structure_key_hash = ?",
            (key.hash,),
        ).fetchone()
    finally:
        conn.close()

    assert fingerprint == ""
    assert row is None


def test_claim_sweeps_stale_running_job(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    key = build_structure_key(symbol="sh.600519", freq="day", data_signature="sig-a")
    job = enqueue_structure_job(key, priority=50)
    claimed = claim_next_structure_job(worker_id="worker-a")
    assert claimed["job_id"] == job["job_id"]

    old = (datetime.now(timezone(timedelta(hours=8))) - timedelta(seconds=120)).isoformat(timespec="seconds")
    conn = database.get_connection()
    try:
        conn.execute(
            "UPDATE structure_compute_jobs SET locked_at = ? WHERE job_id = ?",
            (old, job["job_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    swept = sweep_stale_running_jobs(timeout_seconds=1)
    assert swept == 1

    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT status, retry_count, error_code FROM structure_compute_jobs WHERE job_id = ?",
            (job["job_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "FAILED_RETRYABLE"
    assert row["retry_count"] == 1
    assert row["error_code"] == "TIMEOUT"


def test_snapshot_first_fresh_hit_does_not_compute(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)

    monkeypatch.setattr(
        snapshot_query,
        "get_kline_window_signature",
        lambda *args, **kwargs: {
            "source": "baostock",
            "row_count": 120,
            "first_date": "2026-01-01",
            "last_date": "2026-05-08",
            "signature": "sig-fresh",
        },
    )
    key, context = snapshot_query.build_formal_structure_key(
        symbol="sh.600519",
        freq="day",
        cchan_preset="live_tolerant",
        compute_profile="chart_standard_v1",
    )
    save_chan_snapshot(
        symbol=key.symbol,
        freq=key.freq,
        cchan_preset=key.cchan_preset,
        kline_source=key.source,
        adjustflag=key.adjustflag,
        end_date="",
        max_compute_bars=context["compute_bars"],
        data_signature=key.data_signature,
        last_kline_time="2026-05-08",
        kline_count=120,
        compute_bars=context["compute_bars"],
        result={
            "symbol": key.symbol,
            "freq": key.freq,
            "compute_bars": context["compute_bars"],
            "klines": [{"time": "2026-05-08", "close": 10}],
            "bis": [],
            "segs": [],
            "bi_zhongshus": [],
            "seg_zhongshus": [],
            "bsps": [],
            "stats": {"kline_count": 1},
        },
        structure_key_hash=key.hash,
        compute_profile=key.compute_profile,
    )

    async def fail_compute(**kwargs):
        raise AssertionError("fresh snapshot should not compute")

    monkeypatch.setattr(snapshot_query, "_compute_and_save_now", fail_compute)
    result = asyncio.run(
        snapshot_query.get_structure_snapshot_or_enqueue(
            symbol="sh.600519",
            freq="day",
            sync_if_missing=True,
        )
    )

    assert result["snapshot_status"] == "fresh"
    assert result["structure_key_hash"] == key.hash
    assert result["klines"][0]["time"] == "2026-05-08"


def test_snapshot_first_missing_enqueues_pending_job(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        snapshot_query,
        "get_kline_window_signature",
        lambda *args, **kwargs: {
            "source": "baostock",
            "row_count": 120,
            "first_date": "2026-01-01",
            "last_date": "2026-05-08",
            "signature": "sig-missing",
        },
    )

    result = asyncio.run(
        snapshot_query.get_structure_snapshot_or_enqueue(
            symbol="sh.600519",
            freq="day",
            sync_if_missing=False,
        )
    )

    assert result["snapshot_status"] == "pending"
    assert result["job"]["status"] == "PENDING"
    assert result["job"]["enqueued"] is True


def test_snapshot_compute_uses_profile_window_not_display_count(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    seen = {}
    monkeypatch.setattr(
        snapshot_query,
        "get_kline_window_signature",
        lambda *args, **kwargs: {
            "source": "baostock",
            "row_count": 1200,
            "first_date": "2026-01-01",
            "last_date": "2026-05-08",
            "signature": f"sig-{kwargs.get('limit')}",
        },
    )

    async def fake_get_chan_detail(*args, **kwargs):
        seen.update(kwargs)
        return {
            "symbol": args[0],
            "freq": kwargs["freq"],
            "compute_bars": kwargs["max_compute_bars"],
            "data_source": {"provider": "baostock"},
            "klines": [{"time": f"2026-05-{day:02d}", "close": day} for day in range(1, 9)],
            "bis": [],
            "segs": [],
            "bi_zhongshus": [],
            "seg_zhongshus": [],
            "bsps": [],
            "stats": {"kline_count": 8},
        }

    monkeypatch.setattr(chan_detail_service, "get_chan_detail", fake_get_chan_detail)

    result = asyncio.run(
        snapshot_query.get_structure_snapshot_or_enqueue(
            symbol="sh.600519",
            freq="5",
            display_count=4,
            compute_profile="chart_standard_v1",
            sync_if_missing=True,
        )
    )

    assert seen["count"] == 1200
    assert seen["max_compute_bars"] == 1200
    assert result["compute_profile"] == "chart_standard_v1"
    assert result["compute_bars"] == 1200
    assert [item["time"] for item in result["klines"]] == [
        "2026-05-05",
        "2026-05-06",
        "2026-05-07",
        "2026-05-08",
    ]


def test_snapshot_first_repeated_missing_uses_single_flight_job(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        snapshot_query,
        "get_kline_window_signature",
        lambda *args, **kwargs: {
            "source": "baostock",
            "row_count": 120,
            "first_date": "2026-01-01",
            "last_date": "2026-05-08",
            "signature": "sig-shared",
        },
    )

    first = asyncio.run(
        snapshot_query.get_structure_snapshot_or_enqueue(
            symbol="sh.600519",
            freq="day",
            sync_if_missing=False,
            priority=40,
            requested_by_user_id=1,
        )
    )
    second = asyncio.run(
        snapshot_query.get_structure_snapshot_or_enqueue(
            symbol="sh.600519",
            freq="day",
            sync_if_missing=False,
            priority=90,
            requested_by_user_id=2,
        )
    )

    conn = database.get_connection()
    try:
        rows = conn.execute("SELECT * FROM structure_compute_jobs").fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    assert second["job"]["job_id"] == first["job"]["job_id"]
    assert second["job"]["enqueued"] is False
    assert second["job"]["bumped"] is True
    assert second["job"]["priority"] == 90
    assert rows[0]["requested_by_user_id"] == 2


def test_snapshot_first_final_failed_returns_stable_error(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        snapshot_query,
        "get_kline_window_signature",
        lambda *args, **kwargs: {
            "source": "baostock",
            "row_count": 120,
            "first_date": "2026-01-01",
            "last_date": "2026-05-08",
            "signature": "sig-failed",
        },
    )
    key, _context = snapshot_query.build_formal_structure_key(
        symbol="sh.600519",
        freq="day",
        cchan_preset="live_tolerant",
        compute_profile="chart_standard_v1",
    )
    job = enqueue_structure_job(key, priority=50, reason="test")
    fail_structure_job(job["job_id"], code="ENGINE_ERROR", message="boom", retryable=False)

    result = asyncio.run(
        snapshot_query.get_structure_snapshot_or_enqueue(
            symbol="sh.600519",
            freq="day",
            sync_if_missing=False,
        )
    )

    assert result["snapshot_status"] == "failed"
    assert result["error"] == "ENGINE_ERROR"
    assert result["job"]["status"] == "FAILED_FINAL"
    assert result["job"]["enqueued"] is False


def test_snapshot_first_requeues_completed_job_when_snapshot_missing(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        snapshot_query,
        "get_kline_window_signature",
        lambda *args, **kwargs: {
            "source": "baostock",
            "row_count": 120,
            "first_date": "2026-01-01",
            "last_date": "2026-05-08",
            "signature": "sig-orphan-success",
        },
    )
    key, _context = snapshot_query.build_formal_structure_key(
        symbol="sh.600519",
        freq="day",
        cchan_preset="live_tolerant",
        compute_profile="chart_standard_v1",
    )
    job = enqueue_structure_job(key, priority=50, reason="test")
    complete_structure_job(job["job_id"], structure_fingerprint="fingerprint-without-snapshot")

    result = asyncio.run(
        snapshot_query.get_structure_snapshot_or_enqueue(
            symbol="sh.600519",
            freq="day",
            sync_if_missing=False,
            priority=90,
        )
    )

    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT job_id, status, retry_count, result_fingerprint, priority FROM structure_compute_jobs WHERE structure_key_hash = ?",
            (key.hash,),
        ).fetchone()
    finally:
        conn.close()

    assert result["snapshot_status"] == "pending"
    assert result["job"]["status"] == "PENDING"
    assert result["job"]["enqueued"] is True
    assert result["job"]["job_id"] != job["job_id"]
    assert row["status"] == "PENDING"
    assert row["retry_count"] == 0
    assert row["result_fingerprint"] == ""
    assert row["priority"] == 90


def test_terminal_failed_job_retries_only_when_explicit(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    key = build_structure_key(symbol="sh.600519", freq="day", data_signature="sig-retry")
    job = enqueue_structure_job(key, priority=50, reason="test")
    fail_structure_job(job["job_id"], code="ENGINE_ERROR", message="boom", retryable=False)

    passive = enqueue_structure_job(key, priority=90, reason="user_view")
    retry = enqueue_structure_job(key, priority=95, reason="manual_prewarm", retry_terminal=True)

    conn = database.get_connection()
    try:
        rows = conn.execute(
            "SELECT job_id, status, retry_count, error_code, priority FROM structure_compute_jobs WHERE structure_key_hash = ?",
            (key.hash,),
        ).fetchall()
    finally:
        conn.close()

    assert len(rows) == 1
    assert passive["status"] == "FAILED_FINAL"
    assert passive["job_id"] == job["job_id"]
    assert passive.get("enqueued") is False
    assert retry["status"] == "PENDING"
    assert retry["retried"] is True
    assert retry["job_id"] != job["job_id"]
    assert rows[0]["status"] == "PENDING"
    assert rows[0]["retry_count"] == 0
    assert rows[0]["error_code"] == ""
    assert rows[0]["priority"] == 95


def test_snapshot_first_stale_returns_old_snapshot_and_enqueues_refresh(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    signatures = {"value": "sig-old"}

    monkeypatch.setattr(
        snapshot_query,
        "get_kline_window_signature",
        lambda *args, **kwargs: {
            "source": "baostock",
            "row_count": 120,
            "first_date": "2026-01-01",
            "last_date": "2026-05-08",
            "signature": signatures["value"],
        },
    )
    old_key, context = snapshot_query.build_formal_structure_key(
        symbol="sh.600519",
        freq="day",
        cchan_preset="live_tolerant",
        compute_profile="chart_standard_v1",
    )
    save_chan_snapshot(
        symbol=old_key.symbol,
        freq=old_key.freq,
        cchan_preset=old_key.cchan_preset,
        kline_source=old_key.source,
        adjustflag=old_key.adjustflag,
        end_date="",
        max_compute_bars=context["compute_bars"],
        data_signature=old_key.data_signature,
        last_kline_time="2026-05-08",
        kline_count=120,
        compute_bars=context["compute_bars"],
        result={
            "symbol": old_key.symbol,
            "freq": old_key.freq,
            "compute_bars": context["compute_bars"],
            "klines": [{"time": "2026-05-08", "close": 10}],
            "bis": [],
            "segs": [],
            "bi_zhongshus": [],
            "seg_zhongshus": [],
            "bsps": [],
            "stats": {"kline_count": 1},
        },
        structure_key_hash=old_key.hash,
        compute_profile=old_key.compute_profile,
    )

    signatures["value"] = "sig-new"
    result = asyncio.run(
        snapshot_query.get_structure_snapshot_or_enqueue(
            symbol="sh.600519",
            freq="day",
            sync_if_missing=False,
        )
    )

    assert result["snapshot_status"] == "stale"
    assert result["job"]["status"] == "PENDING"
    assert result["structure_key_hash"] != old_key.hash
    assert result["klines"][0]["time"] == "2026-05-08"


def test_worker_supersedes_job_when_signature_changes(monkeypatch, tmp_path):
    reset_db(monkeypatch, tmp_path)
    old_key = build_structure_key(symbol="sh.600519", freq="day", data_signature="sig-old")
    new_key = build_structure_key(symbol="sh.600519", freq="day", data_signature="sig-new")
    original_job = enqueue_structure_job(old_key, priority=70)
    claimed = claim_next_structure_job(worker_id="worker-a")

    monkeypatch.setattr(
        structure_compute_worker,
        "build_formal_structure_key",
        lambda **kwargs: (new_key, {
            "compute_bars": 1200,
            "freshness": {"last_bar_at": "2026-05-08", "kline_count": 120},
        }),
    )

    async def fail_get_chan_detail(*args, **kwargs):
        raise AssertionError("superseded job should not compute")

    monkeypatch.setattr(structure_compute_worker, "get_chan_detail", fail_get_chan_detail)

    worker = structure_compute_worker.StructureComputeWorker(interval_seconds=0)
    asyncio.run(worker._run_job(claimed))

    conn = database.get_connection()
    try:
        old_row = conn.execute(
            "SELECT status, error_code FROM structure_compute_jobs WHERE job_id = ?",
            (original_job["job_id"],),
        ).fetchone()
        new_row = conn.execute(
            "SELECT status, structure_key_hash FROM structure_compute_jobs WHERE structure_key_hash = ?",
            (new_key.hash,),
        ).fetchone()
    finally:
        conn.close()

    assert old_row["status"] == "CANCELLED"
    assert old_row["error_code"] == "JOB_SUPERSEDED"
    assert new_row["status"] == "PENDING"
    assert new_row["structure_key_hash"] == new_key.hash


def test_structure_prewarm_skips_invalid_freq(monkeypatch):
    app = FastAPI()
    app.include_router(structure_api.router)
    client = TestClient(app)

    response = client.post(
        "/prewarm",
        json={"symbols": ["sh600519"], "freqs": ["bad"], "compute_profile": "chart_standard_v1"},
    )

    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    assert item["status"] == "skipped"
    assert item["reason"] == "INVALID_FREQ"
