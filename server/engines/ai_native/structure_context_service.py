"""AI Native V5 user-scoped structure context jobs.

This layer is a pure downstream consumer of CZSC snapshots. It never calls
heavy structure computation and never reads legacy radar outputs.
"""

from __future__ import annotations

import json
import socket
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi.concurrency import run_in_threadpool

from server.config import STRUCTURE_JOB_TIMEOUT_SECONDS
from server.db.database import get_connection
from server.domain.symbols import normalize_symbol
from server.engines.ai_native.czsc_snapshot_service import (
    DEFAULT_COMPUTE_PROFILE,
    DEFAULT_LEVELS,
    JOB_ACTIVE_STATUSES,
    get_latest_snapshot,
    get_snapshot_status,
    now_text,
    stable_hash,
)
from server.engines.ai_native.scenario_branch_service import (
    list_scenario_branches,
    upsert_scenario_branches_for_context,
)


PROMPT_VERSION = "ai_structure_context.v1"


def context_job_key(
    *,
    user_id: int,
    symbol: str,
    source_snapshot_ids: list[str],
    prompt_version: str = PROMPT_VERSION,
) -> str:
    return stable_hash({
        "user_id": int(user_id),
        "symbol": normalize_symbol(symbol),
        "source_snapshot_ids": sorted(source_snapshot_ids),
        "prompt_version": prompt_version,
    })


def prewarm_ai_structure_contexts(
    *,
    user_id: int,
    symbols: list[str],
    levels: list[str] | None = None,
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
    priority: int = 70,
    reason: str = "manual_context_prewarm",
) -> dict[str, Any]:
    items = []
    for raw_symbol in symbols:
        symbol = normalize_symbol(raw_symbol)
        snapshot_set = _latest_snapshot_set(symbol=symbol, levels=levels or list(DEFAULT_LEVELS), compute_profile=compute_profile)
        if not snapshot_set["snapshot_ids"]:
            items.append({
                "symbol": symbol,
                "status": "skipped",
                "reason": "NO_SNAPSHOT",
                "missing_levels": snapshot_set["missing_levels"],
            })
            continue
        job = enqueue_context_job(
            user_id=user_id,
            symbol=symbol,
            compute_profile=compute_profile,
            source_snapshot_ids=snapshot_set["snapshot_ids"],
            priority=priority,
            reason=reason,
        )
        job["missing_levels"] = snapshot_set["missing_levels"]
        items.append(job)
    return {"count": len(items), "items": items}


def enqueue_context_job(
    *,
    user_id: int,
    symbol: str,
    compute_profile: str,
    source_snapshot_ids: list[str],
    priority: int = 70,
    reason: str = "",
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    snapshot_ids = sorted({item for item in source_snapshot_ids if item})
    if not snapshot_ids:
        raise ValueError("source_snapshot_ids required")
    key = context_job_key(
        user_id=user_id,
        symbol=canonical,
        source_snapshot_ids=snapshot_ids,
    )
    conn = get_connection()
    try:
        now = now_text()
        existing = conn.execute("SELECT * FROM ai_structure_context_jobs WHERE idempotency_key = ?", (key,)).fetchone()
        if existing:
            row = dict(existing)
            if row["status"] in JOB_ACTIVE_STATUSES:
                conn.execute(
                    """
                    UPDATE ai_structure_context_jobs
                       SET priority = MAX(priority, ?),
                           reason = CASE WHEN ? != '' THEN ? ELSE reason END,
                           updated_at = ?
                     WHERE id = ?
                    """,
                    (int(priority), reason, reason, now, row["id"]),
                )
                conn.commit()
                bumped = conn.execute("SELECT * FROM ai_structure_context_jobs WHERE id = ?", (row["id"],)).fetchone()
                return _job_row(bumped, enqueued=False, bumped=True)
            if row["status"] in {"FAILED_FINAL", "CANCELLED"}:
                new_job_id = _new_id("v5ctxjob")
                conn.execute(
                    """
                    UPDATE ai_structure_context_jobs
                       SET job_id = ?,
                           priority = ?,
                           status = 'PENDING',
                           reason = CASE WHEN ? != '' THEN ? ELSE reason END,
                           retry_count = 0,
                           next_run_at = ?,
                           locked_by = '',
                           locked_at = NULL,
                           started_at = NULL,
                           finished_at = NULL,
                           result_context_id = '',
                           error_code = '',
                           error_message = '',
                           updated_at = ?
                     WHERE id = ?
                    """,
                    (new_job_id, int(priority), reason, reason, now, now, row["id"]),
                )
                conn.commit()
                retried = conn.execute("SELECT * FROM ai_structure_context_jobs WHERE id = ?", (row["id"],)).fetchone()
                return _job_row(retried, enqueued=True, bumped=False, retried=True)
            return _job_row(existing, enqueued=False, bumped=False)

        job_id = _new_id("v5ctxjob")
        conn.execute(
            """
            INSERT INTO ai_structure_context_jobs (
                job_id, idempotency_key, user_id, symbol, compute_profile,
                prompt_version, source_snapshot_ids_json, priority, status,
                reason, next_run_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?)
            """,
            (
                job_id,
                key,
                int(user_id),
                canonical,
                compute_profile,
                PROMPT_VERSION,
                _json(snapshot_ids),
                int(priority),
                reason,
                now,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM ai_structure_context_jobs WHERE idempotency_key = ?", (key,)).fetchone()
        return _job_row(row, enqueued=True, bumped=False)
    finally:
        conn.close()


def get_latest_ai_structure_context(*, user_id: int, symbol: str) -> dict[str, Any] | None:
    canonical = normalize_symbol(symbol)
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
              FROM ai_structure_contexts
             WHERE user_id = ? AND symbol = ?
             ORDER BY updated_at DESC, id DESC
             LIMIT 1
            """,
            (int(user_id), canonical),
        ).fetchone()
        if not row:
            return None
        context = _context_row(row)
        context["branches"] = list_scenario_branches(user_id=int(user_id), symbol=canonical, context_id=context["context_id"])
        return context
    finally:
        conn.close()


def get_ai_structure_context_status(
    *,
    user_id: int,
    symbol: str,
    levels: list[str] | None = None,
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    requested_levels = levels or list(DEFAULT_LEVELS)
    latest = get_latest_ai_structure_context(user_id=user_id, symbol=canonical)
    snapshot_set = _latest_snapshot_set(symbol=canonical, levels=requested_levels, compute_profile=compute_profile)
    latest_ids = set(snapshot_set["snapshot_ids"])
    conn = get_connection()
    try:
        job = None
        if latest_ids:
            key = context_job_key(user_id=user_id, symbol=canonical, source_snapshot_ids=list(latest_ids))
            job = conn.execute("SELECT * FROM ai_structure_context_jobs WHERE idempotency_key = ?", (key,)).fetchone()
        if latest:
            source_ids = set(latest.get("source_snapshot_ids") or [])
            status = "fresh" if latest_ids and source_ids == latest_ids else "stale"
            stale_reason = "" if status == "fresh" else "SOURCE_SNAPSHOT_CHANGED"
            return {
                "symbol": canonical,
                "user_id": int(user_id),
                "status": status,
                "stale_reason": stale_reason,
                "context": latest,
                "job": _job_row(job) if job else None,
                "missing_levels": snapshot_set["missing_levels"],
            }
        snapshot_gate = _snapshot_gate_status(symbol=canonical, levels=requested_levels, compute_profile=compute_profile)
        if job and job["status"] in JOB_ACTIVE_STATUSES:
            return {
                "symbol": canonical,
                "user_id": int(user_id),
                "status": "pending",
                "stale_reason": "",
                "context": None,
                "job": _job_row(job),
                "missing_levels": snapshot_set["missing_levels"],
            }
        if snapshot_gate:
            return {
                "symbol": canonical,
                "user_id": int(user_id),
                "status": snapshot_gate["status"],
                "stale_reason": snapshot_gate["stale_reason"],
                "context": None,
                "job": snapshot_gate["job"],
                "missing_levels": snapshot_set["missing_levels"],
            }
        return {
            "symbol": canonical,
            "user_id": int(user_id),
            "status": "no_snapshot" if not latest_ids else "pending",
            "stale_reason": "NO_SNAPSHOT" if not latest_ids else "",
            "context": None,
            "job": _job_row(job) if job else None,
            "missing_levels": snapshot_set["missing_levels"],
        }
    finally:
        conn.close()


def _snapshot_gate_status(*, symbol: str, levels: list[str], compute_profile: str) -> dict[str, Any] | None:
    pending = None
    for level in levels:
        status = get_snapshot_status(symbol=symbol, level=level, compute_profile=compute_profile)
        if status.get("status") == "failed":
            job = status.get("job") or {}
            return {
                "status": "failed",
                "stale_reason": job.get("error_code") or "SNAPSHOT_FAILED",
                "job": job,
            }
        if status.get("status") in {"pending", "stale"} and status.get("job"):
            pending = {
                "status": "pending",
                "stale_reason": "",
                "job": status.get("job"),
            }
    return pending


def sweep_stale_context_jobs(timeout_seconds: int = STRUCTURE_JOB_TIMEOUT_SECONDS) -> int:
    cutoff = (datetime.now().astimezone() - timedelta(seconds=timeout_seconds)).isoformat(timespec="seconds")
    conn = get_connection()
    try:
        now = now_text()
        cur = conn.execute(
            """
            UPDATE ai_structure_context_jobs
               SET status = CASE
                    WHEN retry_count + 1 >= max_retries THEN 'FAILED_FINAL'
                    ELSE 'FAILED_RETRYABLE'
               END,
                   retry_count = retry_count + 1,
                   next_run_at = ?,
                   locked_by = '',
                   locked_at = NULL,
                   error_code = 'TIMEOUT',
                   error_message = 'AI structure context job timed out',
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


def claim_next_context_job(
    *,
    worker_id: str | None = None,
    timeout_seconds: int = STRUCTURE_JOB_TIMEOUT_SECONDS,
) -> dict[str, Any] | None:
    sweep_stale_context_jobs(timeout_seconds)
    conn = get_connection()
    try:
        now = now_text()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT *
              FROM ai_structure_context_jobs
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
            UPDATE ai_structure_context_jobs
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
        claimed = conn.execute("SELECT * FROM ai_structure_context_jobs WHERE id = ?", (row["id"],)).fetchone()
        return _job_row(claimed)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


async def run_context_job(job: dict[str, Any]) -> dict[str, Any]:
    return await run_in_threadpool(run_context_job_sync, job)


def run_context_job_sync(job: dict[str, Any]) -> dict[str, Any]:
    job_id = job["job_id"]
    try:
        source_snapshot_ids = json.loads(job.get("source_snapshot_ids_json") or "[]")
        snapshots = _load_snapshots_by_ids(source_snapshot_ids)
        if not snapshots:
            _fail_job(job_id, code="NO_SNAPSHOT", message="No CZSC snapshots available", retryable=False)
            return {"status": "failed", "error_code": "NO_SNAPSHOT"}
        source_levels = [item["level"] for item in snapshots]
        current_set = _latest_snapshot_set(symbol=job["symbol"], levels=source_levels, compute_profile=job["compute_profile"])
        if set(source_snapshot_ids) != set(current_set["snapshot_ids"]):
            _skip_job(job_id, reason="STALE_INPUT")
            return {"status": "skipped", "reason": "STALE_INPUT"}

        context = build_ai_structure_context(
            user_id=int(job["user_id"]),
            symbol=job["symbol"],
            snapshots=snapshots,
            prompt_version=job["prompt_version"],
        )
        saved = save_ai_structure_context(**context)
        branches = upsert_scenario_branches_for_context(saved)
        _complete_job(job_id, context_id=saved["context_id"])
        return {"status": "success", "context_id": saved["context_id"], "branch_count": len(branches)}
    except Exception as exc:
        _fail_job(job_id, code="CONTEXT_ERROR", message=str(exc)[:300], retryable=True)
        return {"status": "failed", "error_code": "CONTEXT_ERROR", "error_message": str(exc)[:300]}


def build_ai_structure_context(
    *,
    user_id: int,
    symbol: str,
    snapshots: list[dict[str, Any]],
    prompt_version: str = PROMPT_VERSION,
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    ordered = sorted(snapshots, key=lambda item: _level_rank(item.get("level")))
    source_snapshot_ids = [item["snapshot_id"] for item in ordered]
    position = _position_context(user_id=user_id, symbol=canonical)
    boundary = _boundary_payload(ordered)
    background = _background_context(symbol=canonical, boundary=boundary, position=position, snapshots=ordered)
    raw_context = {
        "version": prompt_version,
        "symbol": canonical,
        "source": "czsc_snapshot",
        "source_snapshot_ids": source_snapshot_ids,
        "position_context": position,
        "background_context": background,
        "snapshots": [_compact_snapshot(item) for item in ordered],
    }
    summary_text = _summary_text(canonical, boundary, position)
    fingerprint = stable_hash({
        "user_id": int(user_id),
        "symbol": canonical,
        "prompt_version": prompt_version,
        "source_snapshot_ids": source_snapshot_ids,
        "boundary": boundary,
        "position": position,
        "background": background,
    })
    return {
        "user_id": int(user_id),
        "symbol": canonical,
        "prompt_version": prompt_version,
        "context_fingerprint": fingerprint,
        "source_snapshot_ids": source_snapshot_ids,
        "raw_context": raw_context,
        "background": background,
        "boundary": boundary,
        "summary_text": summary_text,
        "status": "fresh",
        "stale_reason": "",
    }


def save_ai_structure_context(
    *,
    user_id: int,
    symbol: str,
    prompt_version: str,
    context_fingerprint: str,
    source_snapshot_ids: list[str],
    raw_context: dict[str, Any],
    background: dict[str, Any],
    boundary: dict[str, Any],
    summary_text: str,
    status: str = "fresh",
    stale_reason: str = "",
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    context_id = f"v5ctx_{context_fingerprint[:16]}"
    now = now_text()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO ai_structure_contexts (
                context_id, user_id, symbol, prompt_version, context_fingerprint,
                source_snapshot_ids_json, raw_context_json, background_json,
                boundary_json, summary_text, status, stale_reason, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, symbol, context_fingerprint)
            DO UPDATE SET
                source_snapshot_ids_json = excluded.source_snapshot_ids_json,
                raw_context_json = excluded.raw_context_json,
                background_json = excluded.background_json,
                boundary_json = excluded.boundary_json,
                summary_text = excluded.summary_text,
                status = excluded.status,
                stale_reason = excluded.stale_reason,
                updated_at = excluded.updated_at
            """,
            (
                context_id,
                int(user_id),
                canonical,
                prompt_version,
                context_fingerprint,
                _json(source_snapshot_ids),
                _json(raw_context),
                _json(background),
                _json(boundary),
                summary_text,
                status,
                stale_reason,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM ai_structure_contexts WHERE context_id = ?", (context_id,)).fetchone()
        return _context_row(row)
    finally:
        conn.close()


def _latest_snapshot_set(*, symbol: str, levels: list[str], compute_profile: str) -> dict[str, Any]:
    snapshots = []
    missing = []
    for level in levels:
        snapshot = get_latest_snapshot(symbol=symbol, level=level, compute_profile=compute_profile)
        if snapshot:
            snapshots.append(snapshot)
        else:
            missing.append(level)
    return {
        "snapshots": snapshots,
        "snapshot_ids": [item["snapshot_id"] for item in snapshots],
        "missing_levels": missing,
    }


def _load_snapshots_by_ids(snapshot_ids: list[str]) -> list[dict[str, Any]]:
    if not snapshot_ids:
        return []
    placeholders = ",".join("?" for _ in snapshot_ids)
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT * FROM structure_snapshots WHERE snapshot_id IN ({placeholders})",
            snapshot_ids,
        ).fetchall()
        loaded = [_snapshot_row(row) for row in rows]
        return sorted(loaded, key=lambda item: _level_rank(item.get("level")))
    finally:
        conn.close()


def _position_context(*, user_id: int, symbol: str) -> dict[str, Any]:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT symbol, name, quantity, avg_cost, current_price, stop_loss_price, trailing_stop_price
              FROM positions
             WHERE user_id = ? AND symbol = ? AND quantity > 0
             LIMIT 1
            """,
            (int(user_id), normalize_symbol(symbol)),
        ).fetchone()
        if not row:
            return {"has_position": False, "symbol": normalize_symbol(symbol)}
        data = dict(row)
        current_price = _num(data.get("current_price")) or _num(data.get("avg_cost"))
        avg_cost = _num(data.get("avg_cost"))
        data["has_position"] = True
        data["current_price"] = current_price
        data["pnl_pct"] = round((current_price - avg_cost) / avg_cost * 100, 2) if avg_cost > 0 and current_price > 0 else None
        return data
    finally:
        conn.close()


def _background_context(
    *,
    symbol: str,
    boundary: dict[str, Any],
    position: dict[str, Any],
    snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    fundamental = _latest_fundamental_background(symbol)
    return {
        "version": "ai_structure_background.v1",
        "symbol": normalize_symbol(symbol),
        "available_levels": [item.get("level") for item in snapshots],
        "primary_level": _primary_level(boundary),
        "has_position": position.get("has_position", False),
        "fundamental": fundamental,
        "market": {
            "fund_flow": {},
            "sector_context": {},
            "index_background": {},
            "status": "not_loaded",
        },
        "rules": {
            "structure_source": "czsc_snapshot_only",
            "background_role": "context_only",
            "structure_role": "decision_boundary",
            "conflict_policy": "structure_discipline_first",
            "no_direct_trade_instruction": True,
            "disclaimer_required": "仅供参考，不构成投资建议",
        },
    }


def _latest_fundamental_background(symbol: str) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    compact = canonical.replace(".", "")
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT symbol, strategy, llm_verdict, llm_summary,
                   llm_pros, llm_cons, llm_red_flags, fundamental_at, created_at
              FROM scan_results
             WHERE symbol IN (?, ?)
               AND (
                    llm_summary IS NOT NULL
                 OR llm_verdict IS NOT NULL
                 OR llm_pros IS NOT NULL
                 OR llm_cons IS NOT NULL
                 OR llm_red_flags IS NOT NULL
               )
             ORDER BY COALESCE(fundamental_at, created_at) DESC, id DESC
             LIMIT 1
            """,
            (canonical, compact),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {
            "status": "not_available",
            "role": "context_only",
            "summary": "",
            "verdict": "",
            "pros": [],
            "cons": [],
            "red_flags": [],
            "as_of": "",
            "source": "scan_results",
        }
    return {
        "status": "available",
        "role": "context_only",
        "summary": str(row["llm_summary"] or "")[:160],
        "verdict": str(row["llm_verdict"] or ""),
        "pros": _json_list(row["llm_pros"]),
        "cons": _json_list(row["llm_cons"]),
        "red_flags": _json_list(row["llm_red_flags"]),
        "as_of": str(row["fundamental_at"] or row["created_at"] or ""),
        "source": f"scan_results:{row['strategy'] or ''}",
    }


def _boundary_payload(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    levels = {}
    for item in snapshots:
        level = item.get("level") or ""
        payload = item.get("snapshot") or {}
        center = payload.get("active_zhongshu") or {}
        price = _num(payload.get("price"))
        evidence = {}
        if center:
            evidence["active_center"] = _evidence_id(item["snapshot_id"], level, "center", "active")
        zg = _num(center.get("zg"))
        zd = _num(center.get("zd"))
        if zg > 0:
            evidence["trigger_line"] = _evidence_id(item["snapshot_id"], level, "line", f"trigger:{zg:.4f}")
        if zd > 0:
            evidence["invalidation_line"] = _evidence_id(item["snapshot_id"], level, "line", f"invalidation:{zd:.4f}")
        if price > 0:
            evidence["current_price_line"] = _evidence_id(item["snapshot_id"], level, "line", f"current:{price:.4f}")
        levels[level] = {
            "snapshot_id": item["snapshot_id"],
            "level": level,
            "current_price": price,
            "active_center": center,
            "evidence": evidence,
        }
    return {
        "version": "ai_structure_boundaries.v1",
        "levels": levels,
        "primary_level": _primary_level({"levels": levels}),
    }


def _primary_level(boundary: dict[str, Any]) -> str:
    levels = boundary.get("levels") or {}
    for level in ("5", "30", "day", "week"):
        center = (levels.get(level) or {}).get("active_center") or {}
        if _num(center.get("zg")) > 0 and _num(center.get("zd")) > 0:
            return level
    return next(iter(levels.keys()), "")


def _summary_text(symbol: str, boundary: dict[str, Any], position: dict[str, Any]) -> str:
    primary = _primary_level(boundary)
    center = ((boundary.get("levels") or {}).get(primary) or {}).get("active_center") or {}
    if not primary or not center:
        return f"{symbol} 当前 CZSC 结构快照不足，先等待结构刷新。仅供参考，不构成投资建议"
    holding = "持仓" if position.get("has_position") else "空仓"
    return (
        f"{symbol} 当前以 {primary} 级别中枢为主要观察边界，"
        f"上沿 {center.get('zg')}、下沿 {center.get('zd')}；用户状态为{holding}。"
        "仅用于条件化观察，非交易指令。仅供参考，不构成投资建议"
    )


def _compact_snapshot(item: dict[str, Any]) -> dict[str, Any]:
    snapshot = item.get("snapshot") or {}
    return {
        "snapshot_id": item["snapshot_id"],
        "level": item["level"],
        "engine": item["engine"],
        "engine_version": item.get("engine_version") or "",
        "adapter_version": item.get("adapter_version") or "",
        "data_signature": item.get("data_signature") or "",
        "data_as_of": item.get("data_as_of") or "",
        "price": snapshot.get("price"),
        "active_zhongshu": snapshot.get("active_zhongshu") or {},
        "raw_bi_context": item.get("raw_bi_context") or {},
    }


def _snapshot_row(row) -> dict[str, Any]:
    data = dict(row)
    data["snapshot"] = json.loads(data.pop("snapshot_json") or "{}")
    data["raw_bi_context"] = json.loads(data.pop("raw_bi_context_json") or "{}")
    return data


def _context_row(row) -> dict[str, Any]:
    data = dict(row)
    data["source_snapshot_ids"] = json.loads(data.pop("source_snapshot_ids_json") or "[]")
    data["raw_context"] = json.loads(data.pop("raw_context_json") or "{}")
    data["background"] = json.loads(data.pop("background_json") or "{}")
    data["boundary"] = json.loads(data.pop("boundary_json") or "{}")
    return data


def _job_row(row, **extra) -> dict[str, Any]:
    if row is None:
        return {}
    data = dict(row)
    data.update(extra)
    data["source_snapshot_ids"] = json.loads(data.get("source_snapshot_ids_json") or "[]")
    return data


def _complete_job(job_id: str, *, context_id: str) -> None:
    _update_job(job_id, status="SUCCESS", finished_at=now_text(), result_context_id=context_id)


def _skip_job(job_id: str, *, reason: str) -> None:
    _update_job(job_id, status="SKIPPED", finished_at=now_text(), error_code=reason, error_message=reason)


def _fail_job(job_id: str, *, code: str, message: str, retryable: bool) -> None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT retry_count, max_retries FROM ai_structure_context_jobs WHERE job_id = ?", (job_id,)).fetchone()
        retry_count = int(row["retry_count"] or 0) + 1 if row else 1
        max_retries = int(row["max_retries"] or 3) if row else 3
        status = "FAILED_RETRYABLE" if retryable and retry_count < max_retries else "FAILED_FINAL"
        now = now_text()
        conn.execute(
            """
            UPDATE ai_structure_context_jobs
               SET status = ?,
                   retry_count = ?,
                   next_run_at = ?,
                   finished_at = ?,
                   error_code = ?,
                   error_message = ?,
                   updated_at = ?
             WHERE job_id = ?
            """,
            (status, retry_count, now, now, code, message, now, job_id),
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
        conn.execute(f"UPDATE ai_structure_context_jobs SET {assignments} WHERE job_id = ?", [*values, job_id])
        conn.commit()
    finally:
        conn.close()


def _evidence_id(snapshot_id: str, level: str, evidence_type: str, semantic_key: str) -> str:
    return f"{snapshot_id}:{level}:{evidence_type}:{semantic_key}"


def _level_rank(level: str | None) -> int:
    order = {"week": 0, "day": 1, "30": 2, "5": 3}
    return order.get(str(level), 99)


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _json_list(value) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item)[:80] for item in parsed if str(item or "").strip()][:5]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"
