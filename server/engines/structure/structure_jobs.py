"""SQLite-backed structure compute job repository."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from server.config import STRUCTURE_JOB_TIMEOUT_SECONDS
from server.db.database import get_connection
from server.engines.structure.structure_key import StructureKey


SHANGHAI_TZ = timezone(timedelta(hours=8))
ACTIVE_STATUSES = {"PENDING", "RUNNING", "FAILED_RETRYABLE"}


def now_text() -> str:
    return datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds")


def enqueue_structure_job(
    structure_key: StructureKey,
    *,
    priority: int = 50,
    reason: str = "",
    requested_by_user_id: Optional[int] = None,
    retry_terminal: bool = False,
    recompute_completed: bool = False,
) -> dict[str, Any]:
    """Create or bump a compute job for a structure key."""
    conn = get_connection()
    try:
        now = now_text()
        key_hash = structure_key.hash
        existing = conn.execute(
            """
            SELECT *
              FROM structure_compute_jobs
             WHERE structure_key_hash = ?
             LIMIT 1
            """,
            (key_hash,),
        ).fetchone()
        if existing:
            row = dict(existing)
            if row["status"] in ACTIVE_STATUSES:
                conn.execute(
                    """
                    UPDATE structure_compute_jobs
                       SET priority = MAX(priority, ?),
                           reason = CASE WHEN ? != '' THEN ? ELSE reason END,
                           requested_by_user_id = COALESCE(?, requested_by_user_id),
                           updated_at = ?
                     WHERE id = ?
                    """,
                    (int(priority), reason, reason, requested_by_user_id, now, row["id"]),
                )
                conn.commit()
                bumped = conn.execute(
                    "SELECT * FROM structure_compute_jobs WHERE id = ?",
                    (row["id"],),
                ).fetchone()
                return _job_row(bumped, enqueued=False, bumped=True)
            if row["status"] in {"FAILED_FINAL", "CANCELLED"} and retry_terminal:
                new_job_id = uuid.uuid4().hex
                conn.execute(
                    """
                    UPDATE structure_compute_jobs
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
                           result_fingerprint = '',
                           error_code = '',
                           error_message = '',
                           updated_at = ?
                     WHERE id = ?
                    """,
                    (new_job_id, int(priority), reason, reason, requested_by_user_id, now, now, row["id"]),
                )
                conn.commit()
                retried = conn.execute(
                    "SELECT * FROM structure_compute_jobs WHERE id = ?",
                    (row["id"],),
                ).fetchone()
                return _job_row(retried, enqueued=True, bumped=False, retried=True)
            if row["status"] in {"SUCCESS", "SKIPPED"} and recompute_completed:
                new_job_id = uuid.uuid4().hex
                conn.execute(
                    """
                    UPDATE structure_compute_jobs
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
                           result_fingerprint = '',
                           error_code = '',
                           error_message = '',
                           updated_at = ?
                     WHERE id = ?
                    """,
                    (new_job_id, int(priority), reason, reason, requested_by_user_id, now, now, row["id"]),
                )
                conn.commit()
                requeued = conn.execute(
                    "SELECT * FROM structure_compute_jobs WHERE id = ?",
                    (row["id"],),
                ).fetchone()
                return _job_row(requeued, enqueued=True, bumped=False)
            if row["status"] in {"SUCCESS", "SKIPPED"}:
                return _job_row(existing, enqueued=False, bumped=False)
            return _job_row(existing, enqueued=False, bumped=False)

        job_id = uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO structure_compute_jobs (
                job_id, structure_key, structure_key_hash, symbol, freq,
                cchan_preset, compute_profile, kline_source, adjustflag,
                data_signature, source_role, priority, status, reason,
                requested_by_user_id, next_run_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?)
            ON CONFLICT(structure_key_hash) DO UPDATE SET
                priority = CASE
                    WHEN structure_compute_jobs.status IN ('PENDING', 'RUNNING', 'FAILED_RETRYABLE')
                    THEN MAX(structure_compute_jobs.priority, excluded.priority)
                    ELSE structure_compute_jobs.priority
                END,
                updated_at = excluded.updated_at
            """,
            (
                job_id,
                json.dumps(structure_key.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                key_hash,
                structure_key.symbol,
                structure_key.freq,
                structure_key.cchan_preset,
                structure_key.compute_profile,
                structure_key.source,
                structure_key.adjustflag,
                structure_key.data_signature,
                structure_key.source_role,
                int(priority),
                reason,
                requested_by_user_id,
                now,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM structure_compute_jobs WHERE structure_key_hash = ?",
            (key_hash,),
        ).fetchone()
        return _job_row(row, enqueued=True, bumped=False)
    finally:
        conn.close()


def sweep_stale_running_jobs(timeout_seconds: int = STRUCTURE_JOB_TIMEOUT_SECONDS) -> int:
    cutoff = (datetime.now(SHANGHAI_TZ) - timedelta(seconds=timeout_seconds)).isoformat(timespec="seconds")
    conn = get_connection()
    try:
        now = now_text()
        cur = conn.execute(
            """
            UPDATE structure_compute_jobs
               SET status = CASE
                    WHEN retry_count + 1 >= max_retries THEN 'FAILED_FINAL'
                    ELSE 'FAILED_RETRYABLE'
               END,
                   retry_count = retry_count + 1,
                   next_run_at = ?,
                   error_code = 'TIMEOUT',
                   error_message = 'Structure job timed out',
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


def claim_next_structure_job(*, worker_id: str, timeout_seconds: int = STRUCTURE_JOB_TIMEOUT_SECONDS) -> Optional[dict[str, Any]]:
    sweep_stale_running_jobs(timeout_seconds)
    conn = get_connection()
    try:
        now = now_text()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT *
              FROM structure_compute_jobs
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
        conn.execute(
            """
            UPDATE structure_compute_jobs
               SET status = 'RUNNING',
                   locked_by = ?,
                   locked_at = ?,
                   started_at = COALESCE(started_at, ?),
                   error_code = '',
                   error_message = '',
                   updated_at = ?
             WHERE id = ?
            """,
            (worker_id, now, now, now, row["id"]),
        )
        conn.commit()
        claimed = conn.execute(
            "SELECT * FROM structure_compute_jobs WHERE id = ?",
            (row["id"],),
        ).fetchone()
        return _job_row(claimed)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def complete_structure_job(job_id: str, *, structure_fingerprint: str) -> None:
    _update_job(
        job_id,
        status="SUCCESS",
        finished_at=now_text(),
        result_fingerprint=structure_fingerprint or "",
        error_code="",
        error_message="",
    )


def skip_structure_job(job_id: str, *, reason: str = "") -> None:
    _update_job(job_id, status="SKIPPED", finished_at=now_text(), error_code="", error_message=reason)


def cancel_structure_job(job_id: str, *, code: str = "JOB_SUPERSEDED", message: str = "") -> None:
    _update_job(job_id, status="CANCELLED", finished_at=now_text(), error_code=code, error_message=message)


def fail_structure_job(job_id: str, *, code: str, message: str, retryable: bool = True) -> None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT retry_count, max_retries FROM structure_compute_jobs WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            return
        retry_count = int(row["retry_count"] or 0) + 1
        max_retries = int(row["max_retries"] or 3)
        final = not retryable or retry_count >= max_retries
        delay_seconds = _backoff_seconds(retry_count)
        next_run = (datetime.now(SHANGHAI_TZ) + timedelta(seconds=delay_seconds)).isoformat(timespec="seconds")
        conn.execute(
            """
            UPDATE structure_compute_jobs
               SET status = ?,
                   retry_count = ?,
                   next_run_at = ?,
                   error_code = ?,
                   error_message = ?,
                   finished_at = CASE WHEN ? THEN ? ELSE finished_at END,
                   updated_at = ?
             WHERE job_id = ?
            """,
            (
                "FAILED_FINAL" if final else "FAILED_RETRYABLE",
                retry_count,
                next_run,
                code,
                message,
                1 if final else 0,
                now_text(),
                now_text(),
                job_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_structure_job_by_hash(structure_key_hash: str) -> Optional[dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM structure_compute_jobs WHERE structure_key_hash = ? ORDER BY id DESC LIMIT 1",
            (structure_key_hash,),
        ).fetchone()
        return _job_row(row) if row else None
    finally:
        conn.close()


def structure_job_stats(limit: int = 50) -> dict[str, Any]:
    conn = get_connection()
    try:
        counts = {
            row["status"].lower(): int(row["count"])
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM structure_compute_jobs GROUP BY status"
            ).fetchall()
        }
        rows = conn.execute(
            """
            SELECT *
              FROM structure_compute_jobs
             ORDER BY
                CASE status WHEN 'RUNNING' THEN 0 WHEN 'PENDING' THEN 1 WHEN 'FAILED_RETRYABLE' THEN 2 ELSE 3 END,
                priority DESC,
                updated_at DESC
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return {"queue": counts, "items": [_job_row(row) for row in rows]}
    finally:
        conn.close()


def _update_job(job_id: str, **fields: Any) -> None:
    fields["updated_at"] = now_text()
    names = list(fields.keys())
    sql = f"UPDATE structure_compute_jobs SET {', '.join(f'{name} = ?' for name in names)} WHERE job_id = ?"
    conn = get_connection()
    try:
        conn.execute(sql, [fields[name] for name in names] + [job_id])
        conn.commit()
    finally:
        conn.close()


def _backoff_seconds(retry_count: int) -> int:
    if retry_count <= 1:
        return 60
    if retry_count == 2:
        return 300
    return 1200


def _job_row(
    row: sqlite3.Row | dict,
    *,
    enqueued: Optional[bool] = None,
    bumped: Optional[bool] = None,
    retried: Optional[bool] = None,
) -> dict[str, Any]:
    data = dict(row)
    if enqueued is not None:
        data["enqueued"] = enqueued
    if bumped is not None:
        data["bumped"] = bumped
    if retried is not None:
        data["retried"] = retried
    return data
