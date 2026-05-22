"""AI Native V5 CZSC-only snapshot jobs and queries."""

from __future__ import annotations

import hashlib
import json
import logging
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.concurrency import run_in_threadpool

from server.config import STRUCTURE_JOB_TIMEOUT_SECONDS
from server.db.database import get_connection
from server.domain.symbols import normalize_symbol
from server.engines.structure import czsc_adapter
from server.engines.structure.source_policy import resolve_structure_source_policy, structure_signature_for_policy
from server.engines.structure.structure_key import COMPUTE_PROFILES, FORMAL_ADJUSTFLAG, FORMAL_SOURCE, normalize_freq, resolve_compute_bars


SHANGHAI_TZ = timezone(timedelta(hours=8))
ENGINE = "czsc"
DEFAULT_LEVELS = ("week", "day", "30", "5")
DEFAULT_COMPUTE_PROFILE = "chart_standard_v1"
JOB_ACTIVE_STATUSES = {"PENDING", "RUNNING", "FAILED_RETRYABLE"}
RECOVERABLE_INFRA_ERROR_CODES = ("CZSC_UNAVAILABLE",)
logger = logging.getLogger(__name__)


def now_text() -> str:
    return datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds")


def snapshot_job_key(*, symbol: str, level: str, data_signature: str, compute_profile: str) -> str:
    payload = {
        "symbol": normalize_symbol(symbol),
        "level": normalize_freq(level),
        "engine": ENGINE,
        "data_signature": data_signature or "",
        "compute_profile": compute_profile,
    }
    return stable_hash(payload)


def stable_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def prewarm_structure_snapshots(
    *,
    symbols: list[str],
    levels: list[str] | None = None,
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
    priority: int = 80,
    reason: str = "manual_prewarm",
    requested_by_user_id: int | None = None,
    force_rebuild: bool = False,
) -> dict[str, Any]:
    if compute_profile not in COMPUTE_PROFILES:
        raise ValueError(f"unsupported compute profile: {compute_profile}")
    items = []
    for raw_symbol in symbols:
        symbol = normalize_symbol(raw_symbol)
        for raw_level in levels or list(DEFAULT_LEVELS):
            level = normalize_freq(raw_level)
            signature = _signature_for_level(symbol=symbol, level=level, compute_profile=compute_profile)
            if not signature.get("signature"):
                items.append({
                    "symbol": symbol,
                    "level": level,
                    "status": "skipped",
                    "reason": "NO_DATA",
                    "freshness": _freshness_from_signature(signature, stale_reason="NO_DATA"),
                })
                continue
            job = enqueue_snapshot_job(
                symbol=symbol,
                level=level,
                compute_profile=compute_profile,
                data_signature=signature["signature"],
                priority=priority,
                reason=reason,
                requested_by_user_id=requested_by_user_id,
                force_rebuild=force_rebuild,
            )
            job["freshness"] = _freshness_from_signature(signature)
            items.append(job)
    return {"count": len(items), "items": items}


def enqueue_snapshot_job(
    *,
    symbol: str,
    level: str,
    compute_profile: str,
    data_signature: str,
    priority: int = 80,
    reason: str = "",
    requested_by_user_id: int | None = None,
    retry_terminal: bool = True,
    force_rebuild: bool = False,
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    normalized_level = normalize_freq(level)
    key = snapshot_job_key(
        symbol=canonical,
        level=normalized_level,
        data_signature=data_signature,
        compute_profile=compute_profile,
    )
    conn = get_connection()
    try:
        now = now_text()
        existing = conn.execute(
            "SELECT * FROM structure_snapshot_jobs WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        if existing:
            row = dict(existing)
            if row["status"] in JOB_ACTIVE_STATUSES:
                conn.execute(
                    """
                    UPDATE structure_snapshot_jobs
                       SET priority = MAX(priority, ?),
                           reason = CASE WHEN ? != '' THEN ? ELSE reason END,
                           requested_by_user_id = COALESCE(?, requested_by_user_id),
                           updated_at = ?
                     WHERE id = ?
                    """,
                    (int(priority), reason, reason, requested_by_user_id, now, row["id"]),
                )
                conn.commit()
                bumped = conn.execute("SELECT * FROM structure_snapshot_jobs WHERE id = ?", (row["id"],)).fetchone()
                return _job_row(bumped, enqueued=False, bumped=True)
            if row["status"] in {"FAILED_FINAL", "CANCELLED"} and retry_terminal:
                new_job_id = _new_id("v5snapjob")
                conn.execute(
                    """
                    UPDATE structure_snapshot_jobs
                       SET job_id = ?,
                           priority = ?,
                           status = 'PENDING',
                           reason = CASE WHEN ? != '' THEN ? ELSE reason END,
                           requested_by_user_id = COALESCE(?, requested_by_user_id),
                           retry_count = 0,
                           next_run_at = ?,
                           locked_by = '',
                           locked_at = NULL,
                           started_at = NULL,
                           finished_at = NULL,
                           result_snapshot_id = '',
                           error_code = '',
                           error_message = '',
                           updated_at = ?
                     WHERE id = ?
                    """,
                    (new_job_id, int(priority), reason, reason, requested_by_user_id, now, now, row["id"]),
                )
                conn.commit()
                retried = conn.execute("SELECT * FROM structure_snapshot_jobs WHERE id = ?", (row["id"],)).fetchone()
                return _job_row(retried, enqueued=True, bumped=False, retried=True)
            # force_rebuild: 把已终止但可复用幂等键的 job 重置为 PENDING，强制重跑 serializer
            if force_rebuild and row["status"] in {"SUCCESS", "SKIPPED"}:
                new_job_id = _new_id("v5snapjob")
                conn.execute(
                    """
                    UPDATE structure_snapshot_jobs
                       SET job_id = ?,
                           priority = ?,
                           status = 'PENDING',
                           reason = ?,
                           requested_by_user_id = COALESCE(?, requested_by_user_id),
                           retry_count = 0,
                           next_run_at = ?,
                           locked_by = '',
                           locked_at = NULL,
                           started_at = NULL,
                           finished_at = NULL,
                           result_snapshot_id = '',
                           error_code = '',
                           error_message = '',
                           updated_at = ?
                     WHERE id = ?
                    """,
                    (new_job_id, int(priority), reason or "force_rebuild", requested_by_user_id, now, now, row["id"]),
                )
                conn.commit()
                rebuilt = conn.execute("SELECT * FROM structure_snapshot_jobs WHERE id = ?", (row["id"],)).fetchone()
                return _job_row(rebuilt, enqueued=True, bumped=False, retried=False)
            return _job_row(existing, enqueued=False, bumped=False)

        job_id = _new_id("v5snapjob")
        conn.execute(
            """
            INSERT INTO structure_snapshot_jobs (
                job_id, idempotency_key, symbol, level, engine, compute_profile,
                data_signature, priority, status, reason, requested_by_user_id,
                next_run_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'czsc', ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                key,
                canonical,
                normalized_level,
                compute_profile,
                data_signature,
                int(priority),
                reason,
                requested_by_user_id,
                now,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM structure_snapshot_jobs WHERE idempotency_key = ?", (key,)).fetchone()
        return _job_row(row, enqueued=True, bumped=False)
    finally:
        conn.close()


def get_snapshot_status(
    *,
    symbol: str,
    level: str,
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    normalized_level = normalize_freq(level)
    signature = _signature_for_level(symbol=canonical, level=normalized_level, compute_profile=compute_profile)
    freshness = _freshness_from_signature(signature)
    if not signature.get("signature"):
        return {
            "symbol": canonical,
            "level": normalized_level,
            "engine": ENGINE,
            "compute_profile": compute_profile,
            "status": "no_data",
            "freshness": _freshness_from_signature(signature, stale_reason="NO_DATA"),
            "snapshot": None,
            "job": None,
        }
    conn = get_connection()
    try:
        fresh = conn.execute(
            """
            SELECT *
              FROM structure_snapshots
             WHERE symbol = ? AND level = ? AND engine = 'czsc'
               AND compute_profile = ? AND data_signature = ?
             ORDER BY updated_at DESC, id DESC
             LIMIT 1
            """,
            (canonical, normalized_level, compute_profile, signature["signature"]),
        ).fetchone()
        if fresh:
            return _status_payload("fresh", canonical, normalized_level, compute_profile, freshness, snapshot=fresh)

        key = snapshot_job_key(
            symbol=canonical,
            level=normalized_level,
            data_signature=signature["signature"],
            compute_profile=compute_profile,
        )
        job = conn.execute(
            "SELECT * FROM structure_snapshot_jobs WHERE idempotency_key = ? LIMIT 1",
            (key,),
        ).fetchone()
        latest = conn.execute(
            """
            SELECT *
              FROM structure_snapshots
             WHERE symbol = ? AND level = ? AND engine = 'czsc' AND compute_profile = ?
             ORDER BY
               CASE
                 WHEN json_extract(snapshot_json, '$.source.provider') = 'tdx' THEN 0
                 ELSE 1
               END,
               updated_at DESC,
               id DESC
             LIMIT 1
            """,
            (canonical, normalized_level, compute_profile),
        ).fetchone()
        if job and job["status"] in JOB_ACTIVE_STATUSES:
            status = "stale" if latest else "pending"
            return _status_payload(status, canonical, normalized_level, compute_profile, freshness, snapshot=latest, job=job)
        if job and job["status"] == "FAILED_FINAL":
            return _status_payload("failed", canonical, normalized_level, compute_profile, freshness, snapshot=latest, job=job)
        if latest:
            return _status_payload("stale", canonical, normalized_level, compute_profile, freshness, snapshot=latest, job=job)
        return _status_payload("pending", canonical, normalized_level, compute_profile, freshness, snapshot=None, job=job)
    finally:
        conn.close()


def get_snapshot_status_batch(
    *,
    symbols: list[str],
    levels: list[str],
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Return snapshot status for many symbol/level pairs with shared DB reads."""
    canonical_symbols = _unique_normalized_symbols(symbols)
    normalized_levels = _unique_normalized_levels(levels)
    if not canonical_symbols or not normalized_levels:
        return {}

    signatures: dict[tuple[str, str], dict[str, Any]] = {}
    signed_pairs: list[tuple[str, str]] = []
    for symbol in canonical_symbols:
        for level in normalized_levels:
            signature = _signature_for_level(symbol=symbol, level=level, compute_profile=compute_profile)
            signatures[(symbol, level)] = signature
            if signature.get("signature"):
                signed_pairs.append((symbol, level))

    latest_snapshots: dict[tuple[str, str], Any] = {}
    fresh_snapshots: dict[tuple[str, str], Any] = {}
    jobs_by_key: dict[str, Any] = {}
    conn = get_connection()
    try:
        if signed_pairs:
            symbol_placeholders = ",".join("?" for _ in canonical_symbols)
            level_placeholders = ",".join("?" for _ in normalized_levels)
            snapshot_rows = conn.execute(
                f"""
                SELECT *
                  FROM structure_snapshots
                 WHERE symbol IN ({symbol_placeholders})
                   AND level IN ({level_placeholders})
                   AND engine = 'czsc'
                   AND compute_profile = ?
                 ORDER BY updated_at DESC, id DESC
                """,
                (*canonical_symbols, *normalized_levels, compute_profile),
            ).fetchall()
            for row in snapshot_rows:
                key = (row["symbol"], row["level"])
                latest_snapshots.setdefault(key, row)
                signature = signatures.get(key) or {}
                if row["data_signature"] == signature.get("signature"):
                    fresh_snapshots.setdefault(key, row)

            job_keys = [
                snapshot_job_key(
                    symbol=symbol,
                    level=level,
                    data_signature=signatures[(symbol, level)]["signature"],
                    compute_profile=compute_profile,
                )
                for symbol, level in signed_pairs
            ]
            if job_keys:
                key_placeholders = ",".join("?" for _ in job_keys)
                job_rows = conn.execute(
                    f"SELECT * FROM structure_snapshot_jobs WHERE idempotency_key IN ({key_placeholders})",
                    job_keys,
                ).fetchall()
                jobs_by_key = {row["idempotency_key"]: row for row in job_rows}
    finally:
        conn.close()

    result: dict[str, dict[str, dict[str, Any]]] = {}
    for symbol in canonical_symbols:
        result[symbol] = {}
        for level in normalized_levels:
            result[symbol][level] = _snapshot_status_from_preloaded(
                symbol=symbol,
                level=level,
                compute_profile=compute_profile,
                signature=signatures[(symbol, level)],
                fresh=fresh_snapshots.get((symbol, level)),
                latest=latest_snapshots.get((symbol, level)),
                job=jobs_by_key.get(snapshot_job_key(
                    symbol=symbol,
                    level=level,
                    data_signature=signatures[(symbol, level)].get("signature") or "",
                    compute_profile=compute_profile,
                )) if signatures[(symbol, level)].get("signature") else None,
            )
    return result


def get_latest_snapshot(
    *,
    symbol: str,
    level: str,
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
) -> dict[str, Any] | None:
    canonical = normalize_symbol(symbol)
    normalized_level = normalize_freq(level)
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
              FROM structure_snapshots
             WHERE symbol = ? AND level = ? AND engine = 'czsc' AND compute_profile = ?
             ORDER BY updated_at DESC, id DESC
             LIMIT 1
            """,
            (canonical, normalized_level, compute_profile),
        ).fetchone()
        return _snapshot_row(row) if row else None
    finally:
        conn.close()


def recover_failed_snapshot_jobs(
    *,
    error_codes: tuple[str, ...] = RECOVERABLE_INFRA_ERROR_CODES,
    limit: int = 200,
    priority: int | None = None,
    reason: str = "recover_failed_snapshot_jobs",
) -> dict[str, Any]:
    """Requeue terminal snapshot jobs that failed for recoverable infrastructure reasons.

    This is intentionally narrow: data-quality failures such as NO_DATA stay
    terminal, while dependency outages like CZSC_UNAVAILABLE can recover after
    the runtime is fixed. The worker still performs the actual CZSC computation.
    """
    if not error_codes or limit <= 0:
        return {"count": 0, "items": [], "skipped": True, "reason": "NO_RECOVERABLE_CODES"}
    if czsc_adapter.get_czsc_engine_version() == "unavailable":
        return {"count": 0, "items": [], "skipped": True, "reason": "CZSC_UNAVAILABLE"}

    placeholders = ",".join("?" for _ in error_codes)
    conn = get_connection()
    try:
        now = now_text()
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            f"""
            SELECT *
              FROM structure_snapshot_jobs
             WHERE status = 'FAILED_FINAL'
               AND error_code IN ({placeholders})
             ORDER BY priority DESC, updated_at ASC, id ASC
             LIMIT ?
            """,
            (*error_codes, int(limit)),
        ).fetchall()
        if not rows:
            conn.commit()
            return {"count": 0, "items": [], "skipped": False, "reason": "NO_FAILED_JOBS"}

        items = []
        for row in rows:
            new_job_id = _new_id("v5snapjob")
            next_priority = int(priority) if priority is not None else int(row["priority"] or 80)
            conn.execute(
                """
                UPDATE structure_snapshot_jobs
                   SET job_id = ?,
                       priority = ?,
                       status = 'PENDING',
                       reason = ?,
                       retry_count = 0,
                       next_run_at = ?,
                       locked_by = '',
                       locked_at = NULL,
                       started_at = NULL,
                       finished_at = NULL,
                       result_snapshot_id = '',
                       error_code = '',
                       error_message = '',
                       updated_at = ?
                 WHERE id = ?
                """,
                (new_job_id, next_priority, reason, now, now, row["id"]),
            )
            items.append({
                "id": row["id"],
                "job_id": new_job_id,
                "symbol": row["symbol"],
                "level": row["level"],
                "status": "PENDING",
                "previous_error_code": row["error_code"],
                "recovered": True,
            })
        conn.commit()
        return {"count": len(items), "items": items, "skipped": False, "reason": reason}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def sweep_stale_snapshot_jobs(timeout_seconds: int = STRUCTURE_JOB_TIMEOUT_SECONDS) -> int:
    cutoff = (datetime.now(SHANGHAI_TZ) - timedelta(seconds=timeout_seconds)).isoformat(timespec="seconds")
    conn = get_connection()
    try:
        now = now_text()
        cur = conn.execute(
            """
            UPDATE structure_snapshot_jobs
               SET status = CASE
                    WHEN retry_count + 1 >= max_retries THEN 'FAILED_FINAL'
                    ELSE 'FAILED_RETRYABLE'
               END,
                   retry_count = retry_count + 1,
                   next_run_at = ?,
                   locked_by = '',
                   locked_at = NULL,
                   error_code = 'TIMEOUT',
                   error_message = 'AI structure snapshot job timed out',
                   updated_at = ?
             WHERE status = 'RUNNING'
               AND locked_at IS NOT NULL
               AND locked_at < ?
            """,
            (now, now, cutoff),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def claim_next_snapshot_job(
    *,
    worker_id: str | None = None,
    timeout_seconds: int = STRUCTURE_JOB_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    sweep_stale_snapshot_jobs(timeout_seconds)
    conn = get_connection()
    try:
        now = now_text()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT *
              FROM structure_snapshot_jobs
             WHERE status IN ('PENDING', 'FAILED_RETRYABLE')
               AND next_run_at <= ?
             ORDER BY priority DESC, created_at ASC
             LIMIT 1
            """,
            (now,),
        ).fetchone()
        if not row:
            conn.commit()
            return None
        worker = worker_id or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        conn.execute(
            """
            UPDATE structure_snapshot_jobs
               SET status = 'RUNNING',
                   locked_by = ?,
                   locked_at = ?,
                   started_at = COALESCE(started_at, ?),
                   error_code = '',
                   error_message = '',
                   updated_at = ?
             WHERE id = ?
            """,
            (worker, now, now, now, row["id"]),
        )
        conn.commit()
        claimed = conn.execute("SELECT * FROM structure_snapshot_jobs WHERE id = ?", (row["id"],)).fetchone()
        return _job_row(claimed)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def run_snapshot_job(job: dict[str, Any]) -> dict[str, Any]:
    return await run_in_threadpool(run_snapshot_job_sync, job)


def run_snapshot_job_sync(job: dict[str, Any]) -> dict[str, Any]:
    job_id = job["job_id"]
    try:
        current_signature = _signature_for_level(
            symbol=job["symbol"],
            level=job["level"],
            compute_profile=job["compute_profile"],
        )
        if not current_signature.get("signature"):
            _fail_job(job_id, code="NO_DATA", message="No CZSC input bars", retryable=False)
            return {"status": "failed", "error_code": "NO_DATA"}
        if current_signature["signature"] != job["data_signature"]:
            _skip_job(job_id, reason="STALE_INPUT")
            return {"status": "skipped", "reason": "STALE_INPUT"}

        result = czsc_adapter.analyze_czsc_structure_sync(
            job["symbol"],
            levels=[job["level"]],
            count=resolve_compute_bars(job["compute_profile"], job["level"]),
            compute_profile=job["compute_profile"],
        )
        if result.get("error") == "CZSC_UNAVAILABLE":
            _fail_job(job_id, code="CZSC_UNAVAILABLE", message="CZSC dependency unavailable", retryable=False)
            return {"status": "failed", "error_code": "CZSC_UNAVAILABLE"}
        level_payload = (result.get("levels") or {}).get(job["level"]) or {}
        if level_payload.get("error"):
            _fail_job(job_id, code=str(level_payload.get("error")), message=str(level_payload.get("message") or ""), retryable=True)
            return {"status": "failed", "error_code": level_payload.get("error")}

        raw_context = czsc_adapter.export_czsc_raw_bi_context_sync(
            job["symbol"],
            levels=[job["level"]],
            count=resolve_compute_bars(job["compute_profile"], job["level"]),
            compute_profile=job["compute_profile"],
            precomputed_result=result,
        )
        snapshot = save_snapshot(
            symbol=job["symbol"],
            level=job["level"],
            compute_profile=job["compute_profile"],
            data_signature=job["data_signature"],
            data_as_of=current_signature.get("last_date") or "",
            snapshot_payload=level_payload,
            raw_bi_context=raw_context,
            engine_version=czsc_adapter.get_czsc_engine_version(),
            adapter_version=czsc_adapter.ADAPTER_VERSION,
            status="fresh",
        )
        followup_context = _enqueue_followup_context_job(job)
        _complete_job(job_id, snapshot_id=snapshot["snapshot_id"])
        return {
            "status": "success",
            "snapshot_id": snapshot["snapshot_id"],
            "context_job": followup_context,
        }
    except Exception as exc:
        _fail_job(job_id, code="ENGINE_ERROR", message=str(exc)[:300], retryable=True)
        return {"status": "failed", "error_code": "ENGINE_ERROR", "error_message": str(exc)[:300]}


def save_snapshot(
    *,
    symbol: str,
    level: str,
    compute_profile: str,
    data_signature: str,
    data_as_of: str,
    snapshot_payload: dict[str, Any],
    raw_bi_context: dict[str, Any],
    engine_version: str,
    adapter_version: str,
    status: str = "fresh",
    error_code: str = "",
    error_message: str = "",
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    normalized_level = normalize_freq(level)
    payload_json = json.dumps(snapshot_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raw_json = json.dumps(raw_bi_context or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = stable_hash({
        "engine": ENGINE,
        "engine_version": engine_version,
        "adapter_version": adapter_version,
        "symbol": canonical,
        "level": normalized_level,
        "compute_profile": compute_profile,
        "data_signature": data_signature,
        "snapshot": snapshot_payload,
    })
    snapshot_id = f"czsc_snapshot_{fingerprint[:16]}"
    now = now_text()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO structure_snapshots (
                snapshot_id, symbol, level, engine, engine_version, adapter_version,
                compute_profile, data_signature, data_as_of, snapshot_json,
                raw_bi_context_json, structure_fingerprint, status, error_code,
                error_message, created_at, updated_at
            )
            VALUES (?, ?, ?, 'czsc', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, level, engine, compute_profile, data_signature)
            DO UPDATE SET
                snapshot_id = excluded.snapshot_id,
                engine_version = excluded.engine_version,
                adapter_version = excluded.adapter_version,
                data_as_of = excluded.data_as_of,
                snapshot_json = excluded.snapshot_json,
                raw_bi_context_json = excluded.raw_bi_context_json,
                structure_fingerprint = excluded.structure_fingerprint,
                status = excluded.status,
                error_code = excluded.error_code,
                error_message = excluded.error_message,
                updated_at = excluded.updated_at
            """,
            (
                snapshot_id,
                canonical,
                normalized_level,
                engine_version,
                adapter_version,
                compute_profile,
                data_signature,
                data_as_of or "",
                payload_json,
                raw_json,
                fingerprint,
                status,
                error_code,
                error_message,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM structure_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
        return _snapshot_row(row)
    finally:
        conn.close()


def _signature_for_level(*, symbol: str, level: str, compute_profile: str) -> dict[str, Any]:
    compute_bars = resolve_compute_bars(compute_profile, level)
    policy = resolve_structure_source_policy(
        symbol=symbol,
        level=level,
        limit=compute_bars,
    )
    signature = get_kline_window_signature(symbol, level, limit=compute_bars, policy=policy)
    signature["source_policy"] = policy
    return signature


def get_kline_window_signature(
    symbol: str,
    level: str,
    *,
    limit: int,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility seam for tests around the source-policy signature lookup."""
    return structure_signature_for_policy(symbol=symbol, level=level, limit=limit, policy=policy)


def _freshness_from_signature(signature: dict[str, Any], stale_reason: str = "") -> dict[str, Any]:
    return {
        "source": signature.get("source") or FORMAL_SOURCE,
        "source_policy": signature.get("source_policy") or {},
        "kline_count": int(signature.get("row_count") or 0),
        "first_bar_at": signature.get("first_date") or "",
        "last_bar_at": signature.get("last_date") or "",
        "data_signature": signature.get("signature") or "",
        "stale_reason": stale_reason,
    }


def _status_payload(
    status: str,
    symbol: str,
    level: str,
    compute_profile: str,
    freshness: dict[str, Any],
    *,
    snapshot=None,
    job=None,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "level": level,
        "engine": ENGINE,
        "compute_profile": compute_profile,
        "status": status,
        "freshness": freshness,
        "snapshot": _snapshot_row(snapshot) if snapshot else None,
        "job": _job_row(job) if job else None,
    }


def _snapshot_status_from_preloaded(
    *,
    symbol: str,
    level: str,
    compute_profile: str,
    signature: dict[str, Any],
    fresh=None,
    latest=None,
    job=None,
) -> dict[str, Any]:
    freshness = _freshness_from_signature(signature)
    if not signature.get("signature"):
        return {
            "symbol": symbol,
            "level": level,
            "engine": ENGINE,
            "compute_profile": compute_profile,
            "status": "no_data",
            "freshness": _freshness_from_signature(signature, stale_reason="NO_DATA"),
            "snapshot": None,
            "job": None,
        }
    if fresh:
        return _status_payload("fresh", symbol, level, compute_profile, freshness, snapshot=fresh)
    if job and job["status"] in JOB_ACTIVE_STATUSES:
        status = "stale" if latest else "pending"
        return _status_payload(status, symbol, level, compute_profile, freshness, snapshot=latest, job=job)
    if job and job["status"] == "FAILED_FINAL":
        return _status_payload("failed", symbol, level, compute_profile, freshness, snapshot=latest, job=job)
    if latest:
        return _status_payload("stale", symbol, level, compute_profile, freshness, snapshot=latest, job=job)
    return _status_payload("pending", symbol, level, compute_profile, freshness, snapshot=None, job=job)


def _job_row(row, **extra) -> dict[str, Any]:
    if row is None:
        return {}
    data = dict(row)
    data.update(extra)
    data["engine"] = ENGINE
    return data


def _snapshot_row(row) -> dict[str, Any]:
    data = dict(row)
    data["snapshot"] = json.loads(data.pop("snapshot_json") or "{}")
    data["raw_bi_context"] = json.loads(data.pop("raw_bi_context_json") or "{}")
    return data


def _unique_normalized_symbols(symbols: list[str]) -> list[str]:
    output = []
    seen = set()
    for raw_symbol in symbols:
        symbol = normalize_symbol(raw_symbol)
        if symbol in seen:
            continue
        seen.add(symbol)
        output.append(symbol)
    return output


def _unique_normalized_levels(levels: list[str]) -> list[str]:
    output = []
    seen = set()
    for raw_level in levels:
        level = normalize_freq(raw_level)
        if level in seen:
            continue
        seen.add(level)
        output.append(level)
    return output


def _complete_job(job_id: str, *, snapshot_id: str) -> None:
    _update_job(job_id, status="SUCCESS", finished_at=now_text(), result_snapshot_id=snapshot_id)


def _skip_job(job_id: str, *, reason: str) -> None:
    _update_job(job_id, status="SKIPPED", finished_at=now_text(), error_code=reason, error_message=reason)


def _fail_job(job_id: str, *, code: str, message: str, retryable: bool) -> None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT retry_count, max_retries FROM structure_snapshot_jobs WHERE job_id = ?", (job_id,)).fetchone()
        retry_count = int(row["retry_count"] or 0) + 1 if row else 1
        max_retries = int(row["max_retries"] or 3) if row else 3
        status = "FAILED_RETRYABLE" if retryable and retry_count < max_retries else "FAILED_FINAL"
        conn.execute(
            """
            UPDATE structure_snapshot_jobs
               SET status = ?,
                   retry_count = ?,
                   next_run_at = ?,
                   finished_at = ?,
                   error_code = ?,
                   error_message = ?,
                   updated_at = ?
             WHERE job_id = ?
            """,
            (status, retry_count, now_text(), now_text(), code, message, now_text(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def _update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = now_text()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values())
    conn = get_connection()
    try:
        conn.execute(f"UPDATE structure_snapshot_jobs SET {assignments} WHERE job_id = ?", [*values, job_id])
        conn.commit()
    finally:
        conn.close()


def _enqueue_followup_context_job(job: dict[str, Any]) -> dict[str, Any] | None:
    try:
        from server.engines.ai_native.structure_context_service import enqueue_context_job
        from server.engines.ai_native.universe_resolver import list_interested_user_ids_for_symbol

        snapshot_ids = _latest_snapshot_ids_for_context(
            symbol=job["symbol"],
            compute_profile=job["compute_profile"],
        )
        if not snapshot_ids:
            return None
        user_ids = _dedupe_user_ids([
            job.get("requested_by_user_id"),
            *list_interested_user_ids_for_symbol(job["symbol"]),
        ])
        if not user_ids:
            return None

        context_jobs = [
            enqueue_context_job(
                user_id=user_id,
                symbol=job["symbol"],
                compute_profile=job["compute_profile"],
                source_snapshot_ids=snapshot_ids,
                priority=max(1, int(job.get("priority") or 50) - 5),
                reason="snapshot_ready",
            )
            for user_id in user_ids
        ]
        if len(context_jobs) == 1:
            return context_jobs[0]
        return {
            "status": "PENDING" if any(item.get("status") == "PENDING" for item in context_jobs) else "queued",
            "count": len(context_jobs),
            "items": context_jobs,
            "source_snapshot_ids": snapshot_ids,
        }
    except Exception as exc:
        logger.warning("AI structure context follow-up enqueue failed for %s: %s", job.get("symbol"), exc)
        return None


def _dedupe_user_ids(raw_user_ids: list[Any]) -> list[int]:
    user_ids: list[int] = []
    seen: set[int] = set()
    for raw_user_id in raw_user_ids:
        if raw_user_id in (None, ""):
            continue
        user_id = int(raw_user_id)
        if user_id not in seen:
            seen.add(user_id)
            user_ids.append(user_id)
    return user_ids


def _latest_snapshot_ids_for_context(*, symbol: str, compute_profile: str) -> list[str]:
    ids = []
    for level in DEFAULT_LEVELS:
        snapshot = get_latest_snapshot(symbol=symbol, level=level, compute_profile=compute_profile)
        if snapshot:
            ids.append(snapshot["snapshot_id"])
    return ids


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"
