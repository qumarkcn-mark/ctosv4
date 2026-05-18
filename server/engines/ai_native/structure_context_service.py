"""AI Native V5 user-scoped structure context jobs.

This layer is a pure downstream consumer of CZSC snapshots. It never calls
heavy structure computation and never reads legacy radar outputs.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi.concurrency import run_in_threadpool

from server.config import AI_NATIVE_LLM_TIMEOUT, AI_NATIVE_MAX_TOKENS, STRUCTURE_JOB_TIMEOUT_SECONDS
from server.db.database import get_connection
from server.domain.symbols import normalize_symbol
from server.engines.ai_native.czsc_snapshot_service import (
    DEFAULT_COMPUTE_PROFILE,
    DEFAULT_LEVELS,
    JOB_ACTIVE_STATUSES,
    get_latest_snapshot,
    get_snapshot_status,
    get_snapshot_status_batch,
    now_text,
    stable_hash,
)
from server.engines.ai_native.scenario_branch_service import (
    list_scenario_branches,
    upsert_scenario_branches_for_context,
)
from server.prompts.ai_structure_reasoning_prompt import (
    AI_STRUCTURE_REASONING_PROMPT_VERSION,
    AI_STRUCTURE_REASONING_SYSTEM_PROMPT,
    build_local_reasoning_fallback,
    build_reasoning_input,
    normalize_reasoning_payload,
)


PROMPT_VERSION = AI_STRUCTURE_REASONING_PROMPT_VERSION


FULL_REASONING_PROMPT_VERSION = f"{AI_STRUCTURE_REASONING_PROMPT_VERSION}.full_text"

FULL_REASONING_SYSTEM_PROMPT = """你是 CT-OS AI Native V5 的缠论结构推演层。

你的任务不是给买卖指令，而是基于 CZSC 已经计算完成的结构事实，解释当前走势可能如何生长。

你只能使用输入中的 structure_facts、position_context、symbol_memory 和 background_context。
CZSC snapshot 是唯一结构来源；你不能重新计算结构，不能引入旧 radar、旧 chan.py、旧 matrix 或任何其他结构引擎。

请输出完整自然语言推演全文，使用纯净缠论语言。重点说明：
- 周线、日线、30分钟、5分钟分别在干什么，以及它们之间是共振还是冲突。
- 当前笔、中枢、离开段、回拉段可能如何继续生长。
- 是否存在背驰、不背驰延伸、潜在背驰或背驰尚不清楚。
- 如果是 A+小b，请解释“大级别中枢上沿/历史关键位置 + 小级别震荡承接”的含义。
- 分支数量由结构决定，不强制三条。每条分支说明触发、失效、观察级别和图表 focus。

边界：
- 不要使用 Commander、战星、绝对分类等叙事。
- 不要直接说买入、卖出、满仓、清仓。
- 允许回答“进入观察窗口”“分支失效”“等待确认”“提醒复核”。
- 必须保留“仅供参考，不构成投资建议”的风险边界。

不要返回 JSON。"""

SUMMARY_SYSTEM_PROMPT = """你是 CT-OS AI Native V5 的 Think 前端摘要层。

你只负责把 DeepSeek Pro Think 的完整缠论推演原文压缩成前端和问答可消费的 JSON。
不得重新计算中枢、笔、背驰或级别结构；不得引入新价格、新分支或新结论。
前端摘要要服务右侧雷达面板，不要写成逐级别笔走势清单。

走势生长字段只写当前最关键的演化链条：
- 小级别中枢如何下破、背驰、回拉、回不到中枢后形成三卖、再试探下一支撑。
- 不要逐条罗列周线、日线、30分钟、5分钟每一笔怎么走。
- 日线若只是顶分型或高位回落，必须写“顶分型/待确认”，不得提前写成“日线向下笔已形成”。
- `main_level` 和 `trigger_level` 只能填 week/day/30/5，不要填“30分钟中枢上沿177.36元”这种条件文本。
- `failure_path` 在前端会显示为“风险演化”，必须写小级别转弱后的下探路径和下一观察位，例如“回不到5分钟中枢则形成三卖，跌破243后先试探231/233-236区域”。不要把它写成空头路径的失效条件。

只返回 JSON 对象，不要 Markdown 代码块。"""

SUMMARY_OUTPUT_CONTRACT = {
    "main_level": "string",
    "trigger_level": "string",
    "structure_summary": "string",
    "trend_growth": {
        "current_state": "string",
        "growth_path": "string",
        "next_confirmation": "string",
        "failure_path": "string",
    },
    "divergence_view": "object",
    "resonance_view": "object",
    "scenario_branches": "array",
    "key_boundaries": "array",
    "coach_summary": "string",
    "front_panel_text": "string",
    "risk_notes": "array",
}


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
    force_rebuild: bool = False,
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
            force_rebuild=force_rebuild,
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
    force_rebuild: bool = False,
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
            if force_rebuild and row["status"] != "RUNNING":
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
                rebuilt = conn.execute("SELECT * FROM ai_structure_context_jobs WHERE id = ?", (row["id"],)).fetchone()
                return _job_row(rebuilt, enqueued=True, bumped=False, forced=True)
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
        rows = conn.execute(
            """
            SELECT *
              FROM ai_structure_contexts
             WHERE user_id = ? AND symbol = ?
             ORDER BY updated_at DESC, id DESC
             LIMIT 12
            """,
            (int(user_id), canonical),
        ).fetchall()
        if not rows:
            return None
        contexts = [_context_row(row) for row in rows]
        context = next((item for item in contexts if reasoning_availability(item).get("ready")), contexts[0])
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
    snapshot_statuses: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    requested_levels = levels or list(DEFAULT_LEVELS)
    latest = get_latest_ai_structure_context(user_id=user_id, symbol=canonical)
    snapshot_statuses = snapshot_statuses or get_snapshot_status_batch(
        symbols=[canonical],
        levels=requested_levels,
        compute_profile=compute_profile,
    ).get(canonical, {})
    snapshot_set = _snapshot_set_from_statuses(levels=requested_levels, status_items=snapshot_statuses)
    freshness_report = _snapshot_freshness_report(
        symbol=canonical,
        levels=requested_levels,
        compute_profile=compute_profile,
        statuses_by_level=snapshot_statuses,
    )
    latest_ids = set(snapshot_set["snapshot_ids"])
    conn = get_connection()
    try:
        job = None
        if latest_ids:
            key = context_job_key(user_id=user_id, symbol=canonical, source_snapshot_ids=list(latest_ids))
            job = conn.execute("SELECT * FROM ai_structure_context_jobs WHERE idempotency_key = ?", (key,)).fetchone()
        if latest:
            source_ids = set(latest.get("source_snapshot_ids") or [])
            snapshot_stale = bool(freshness_report["stale_levels"])
            source_changed = not latest_ids or source_ids != latest_ids
            status = "stale" if source_changed or snapshot_stale else "fresh"
            if source_changed:
                stale_reason = "SOURCE_SNAPSHOT_CHANGED"
            elif snapshot_stale:
                stale_reason = "SNAPSHOT_REFRESH_PENDING" if freshness_report.get("active_job") else "KLINE_AHEAD_OF_SNAPSHOT"
            else:
                stale_reason = ""
            return {
                "symbol": canonical,
                "user_id": int(user_id),
                "status": status,
                "stale_reason": stale_reason,
                "context": latest,
                "reasoning_status": reasoning_availability(latest),
                "job": _job_row(job) if job else freshness_report.get("active_job"),
                "missing_levels": snapshot_set["missing_levels"],
                "stale_levels": freshness_report["stale_levels"],
                "level_freshness": freshness_report["levels"],
            }
        snapshot_gate = _snapshot_gate_status(
            symbol=canonical,
            levels=requested_levels,
            compute_profile=compute_profile,
            status_items=freshness_report["raw_statuses"],
        )
        if job and job["status"] in JOB_ACTIVE_STATUSES:
            return {
                "symbol": canonical,
                "user_id": int(user_id),
                "status": "pending",
                "stale_reason": "",
                "context": None,
                "reasoning_status": reasoning_availability(None),
                "job": _job_row(job),
                "missing_levels": snapshot_set["missing_levels"],
                "stale_levels": freshness_report["stale_levels"],
                "level_freshness": freshness_report["levels"],
            }
        if snapshot_gate:
            return {
                "symbol": canonical,
                "user_id": int(user_id),
                "status": snapshot_gate["status"],
                "stale_reason": snapshot_gate["stale_reason"],
                "context": None,
                "reasoning_status": reasoning_availability(None),
                "job": snapshot_gate["job"],
                "missing_levels": snapshot_set["missing_levels"],
                "stale_levels": freshness_report["stale_levels"],
                "level_freshness": freshness_report["levels"],
            }
        return {
            "symbol": canonical,
            "user_id": int(user_id),
            "status": "no_snapshot" if not latest_ids else "pending",
            "stale_reason": "NO_SNAPSHOT" if not latest_ids else "",
            "context": None,
            "reasoning_status": reasoning_availability(None),
            "job": _job_row(job) if job else None,
            "missing_levels": snapshot_set["missing_levels"],
            "stale_levels": freshness_report["stale_levels"],
            "level_freshness": freshness_report["levels"],
        }
    finally:
        conn.close()


def get_ai_structure_context_statuses(
    *,
    user_id: int,
    symbols: list[str],
    levels: list[str] | None = None,
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
) -> dict[str, dict[str, Any]]:
    requested_levels = levels or list(DEFAULT_LEVELS)
    canonical_symbols = _unique_symbols(symbols)
    if not canonical_symbols:
        return {}
    snapshot_statuses = get_snapshot_status_batch(
        symbols=canonical_symbols,
        levels=requested_levels,
        compute_profile=compute_profile,
    )
    return {
        symbol: get_ai_structure_context_status(
            user_id=user_id,
            symbol=symbol,
            levels=requested_levels,
            compute_profile=compute_profile,
            snapshot_statuses=snapshot_statuses.get(symbol, {}),
        )
        for symbol in canonical_symbols
    }


def reasoning_availability(context: dict[str, Any] | None) -> dict[str, Any]:
    if not context:
        return {
            "status": "pending",
            "ready": False,
            "title": "AI 推演生成中",
            "message": "AI 推演正在生成中，完成后会自动展示完整走势推演。",
            "provider": "",
            "llm_status": "",
            "error": "",
            "context_updated_at": "",
        }
    meta = ((context.get("reasoning") or {}).get("reasoning_meta") or context.get("reasoning_meta") or {})
    provider = str(meta.get("provider") or "")
    llm_status = str(meta.get("llm_status") or "")
    if provider == "llm" and llm_status == "success":
        return {
            "status": "success",
            "ready": True,
            "title": "AI 推演已完成",
            "message": "",
            "provider": provider,
            "llm_status": llm_status,
            "error": str(meta.get("error") or ""),
            "context_updated_at": str(context.get("updated_at") or ""),
        }
    if llm_status == "failed":
        status = "failed"
        title = "AI 推演暂未完成"
        message = "AI 推演返回异常，当前不展示本地算法边界。系统会在下一次刷新时重新生成完整推演。"
    elif provider == "local_fallback" or llm_status in {"not_invoked", ""}:
        status = "unavailable"
        title = "AI 推演暂未完成"
        message = "AI 推演暂未完成，当前不展示本地算法边界。系统会在下一次刷新时重新生成完整推演。"
    else:
        status = "pending"
        title = "AI 推演生成中"
        message = "AI 推演正在生成中，完成后会自动展示完整走势推演。"
    return {
        "status": status,
        "ready": False,
        "title": title,
        "message": message,
        "provider": provider,
        "llm_status": llm_status,
        "error": str(meta.get("error") or ""),
        "context_updated_at": str(context.get("updated_at") or ""),
    }


def _snapshot_freshness_report(
    *,
    symbol: str,
    levels: list[str],
    compute_profile: str,
    statuses_by_level: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    items = []
    raw_statuses = []
    stale_levels = []
    active_job = None
    statuses_by_level = statuses_by_level or get_snapshot_status_batch(
        symbols=[symbol],
        levels=levels,
        compute_profile=compute_profile,
    ).get(normalize_symbol(symbol), {})
    for level in levels:
        status = statuses_by_level.get(level) or get_snapshot_status(symbol=symbol, level=level, compute_profile=compute_profile)
        raw_statuses.append(status)
        snapshot = status.get("snapshot") or {}
        freshness = status.get("freshness") or {}
        job = status.get("job")
        level_status = status.get("status") or "unknown"
        item = {
            "level": level,
            "status": level_status,
            "data_as_of": snapshot.get("data_as_of") or "",
            "kline_last_bar_at": freshness.get("last_bar_at") or "",
            "kline_count": int(freshness.get("kline_count") or 0),
            "stale_reason": freshness.get("stale_reason") or "",
            "job": job,
        }
        items.append(item)
        if level_status in {"stale", "pending", "failed"}:
            stale_levels.append(level)
        if not active_job and job and job.get("status") in JOB_ACTIVE_STATUSES:
            active_job = job
    return {
        "levels": items,
        "stale_levels": stale_levels,
        "active_job": active_job,
        "raw_statuses": raw_statuses,
    }


def _snapshot_gate_status(
    *,
    symbol: str,
    levels: list[str],
    compute_profile: str,
    status_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    pending = None
    statuses = status_items or [
        get_snapshot_status(symbol=symbol, level=level, compute_profile=compute_profile)
        for level in levels
    ]
    for status in statuses:
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

        context = await build_ai_structure_context_async(
            user_id=int(job["user_id"]),
            symbol=job["symbol"],
            snapshots=snapshots,
            prompt_version=job["prompt_version"],
        )
        saved = save_ai_structure_context(**context)
        attach_reasoning_run_to_context(saved)
        branches = upsert_scenario_branches_for_context(saved)
        _complete_job(job_id, context_id=saved["context_id"])
        return {"status": "success", "context_id": saved["context_id"], "branch_count": len(branches)}
    except Exception as exc:
        _fail_job(job_id, code="CONTEXT_ERROR", message=str(exc)[:300], retryable=True)
        return {"status": "failed", "error_code": "CONTEXT_ERROR", "error_message": str(exc)[:300]}


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
        attach_reasoning_run_to_context(saved)
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
    reasoning_input = build_reasoning_input(
        symbol=canonical,
        source_snapshot_ids=source_snapshot_ids,
        raw_context=raw_context,
        boundary=boundary,
        background=background,
    )
    reasoning = _generate_reasoning_payload(symbol=canonical, reasoning_input=reasoning_input)
    return _context_payload(
        user_id=user_id,
        symbol=canonical,
        prompt_version=prompt_version,
        source_snapshot_ids=source_snapshot_ids,
        position=position,
        background=background,
        boundary=boundary,
        raw_context=raw_context,
        reasoning=reasoning,
    )


async def build_ai_structure_context_async(
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
    reasoning_input = build_reasoning_input(
        symbol=canonical,
        source_snapshot_ids=source_snapshot_ids,
        raw_context=raw_context,
        boundary=boundary,
        background=background,
    )
    reasoning = await _generate_reasoning_payload_async(
        user_id=int(user_id),
        symbol=canonical,
        reasoning_input=reasoning_input,
    )
    return _context_payload(
        user_id=user_id,
        symbol=canonical,
        prompt_version=prompt_version,
        source_snapshot_ids=source_snapshot_ids,
        position=position,
        background=background,
        boundary=boundary,
        raw_context=raw_context,
        reasoning=reasoning,
    )


def _context_payload(
    *,
    user_id: int,
    symbol: str,
    prompt_version: str,
    source_snapshot_ids: list[str],
    position: dict[str, Any],
    background: dict[str, Any],
    boundary: dict[str, Any],
    raw_context: dict[str, Any],
    reasoning: dict[str, Any],
) -> dict[str, Any]:
    summary_text = str(reasoning.get("coach_summary") or reasoning.get("front_panel_text") or "") or _summary_text(symbol, boundary, position)
    fingerprint = stable_hash({
        "user_id": int(user_id),
        "symbol": symbol,
        "prompt_version": prompt_version,
        "source_snapshot_ids": source_snapshot_ids,
        "boundary": boundary,
        "position": position,
        "background": background,
        "reasoning": reasoning,
    })
    return {
        "user_id": int(user_id),
        "symbol": symbol,
        "prompt_version": prompt_version,
        "context_fingerprint": fingerprint,
        "source_snapshot_ids": source_snapshot_ids,
        "raw_context": raw_context,
        "reasoning": reasoning,
        "main_level": str(reasoning.get("main_level") or ""),
        "trigger_level": str(reasoning.get("trigger_level") or ""),
        "coach_summary": str(reasoning.get("coach_summary") or reasoning.get("front_panel_text") or ""),
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
    reasoning: dict[str, Any],
    background: dict[str, Any],
    boundary: dict[str, Any],
    summary_text: str,
    main_level: str = "",
    trigger_level: str = "",
    coach_summary: str = "",
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
                source_snapshot_ids_json, raw_context_json, reasoning_json,
                main_level, trigger_level, coach_summary, background_json,
                boundary_json, summary_text, status, stale_reason, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, symbol, context_fingerprint)
            DO UPDATE SET
                source_snapshot_ids_json = excluded.source_snapshot_ids_json,
                raw_context_json = excluded.raw_context_json,
                reasoning_json = excluded.reasoning_json,
                main_level = excluded.main_level,
                trigger_level = excluded.trigger_level,
                coach_summary = excluded.coach_summary,
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
                _json(reasoning),
                main_level,
                trigger_level,
                coach_summary,
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


def _generate_reasoning_payload(*, symbol: str, reasoning_input: dict[str, Any]) -> dict[str, Any]:
    # P0 先使用本地推演契约兜底，避免 worker 在无 API Key 环境下伪造 LLM 成功。
    # 后续接入 LLM 时仍必须经过 normalize，保证落库 schema 稳定。
    fallback = build_local_reasoning_fallback(symbol=symbol, reasoning_input=reasoning_input)
    return normalize_reasoning_payload(fallback, symbol=symbol, reasoning_input=reasoning_input)


async def _generate_reasoning_payload_async(
    *,
    user_id: int,
    symbol: str,
    reasoning_input: dict[str, Any],
) -> dict[str, Any]:
    fallback = build_local_reasoning_fallback(symbol=symbol, reasoning_input=reasoning_input)
    if not _has_configured_ai_native_key(user_id):
        return normalize_reasoning_payload(fallback, symbol=symbol, reasoning_input=reasoning_input)
    try:
        from server.services.llm_service import AIModelRoute, LLMService

        service = LLMService()
        think_route = AIModelRoute(
            thinking_enabled=True,
            reasoning_effort="high",
            timeout_seconds=max(float(AI_NATIVE_LLM_TIMEOUT), 150),
            max_tokens=max(int(AI_NATIVE_MAX_TOKENS), 12000),
        )
        summary_route = AIModelRoute(
            thinking_enabled=True,
            reasoning_effort="high",
            timeout_seconds=max(float(AI_NATIVE_LLM_TIMEOUT), 150),
            max_tokens=6000,
        )
        full_text = await service.infer_ai_native_markdown(
            FULL_REASONING_SYSTEM_PROMPT,
            _json(reasoning_input),
            user_id=user_id,
            model_route=think_route,
        )
        if not str(full_text or "").strip():
            raise RuntimeError("AI Think full reasoning returned empty content")
        run = save_reasoning_run(
            user_id=user_id,
            symbol=symbol,
            source_snapshot_ids=reasoning_input.get("source_snapshot_ids") or [],
            prompt_version=FULL_REASONING_PROMPT_VERSION,
            think_model=think_route.model_name,
            summary_model=summary_route.model_name,
            status="THINK_SUCCESS",
            full_reasoning_text=str(full_text),
            summary={},
            error_message="",
        )
        summary_input = {
            "version": "ai_structure_reasoning_summary_input.v1",
            "symbol": symbol,
            "structure_context": reasoning_input,
            "full_reasoning_text": full_text,
            "output_contract": SUMMARY_OUTPUT_CONTRACT,
            "rules": {
                "summarize_only": True,
                "do_not_recalculate_structure": True,
                "do_not_add_new_prices": True,
                "risk_disclaimer_required": "仅供参考，不构成投资建议",
            },
        }
        summary_text = await service.infer_ai_native_markdown(
            SUMMARY_SYSTEM_PROMPT,
            _json(summary_input),
            user_id=user_id,
            model_route=summary_route,
        )
        from server.services.llm_service import loads_lenient_json_object

        payload = loads_lenient_json_object(summary_text)
        normalized = normalize_reasoning_payload(payload, symbol=symbol, reasoning_input=reasoning_input)
        if normalized.get("front_panel_text") and not normalized.get("coach_summary"):
            normalized["coach_summary"] = str(normalized.get("front_panel_text") or "")
        meta = dict(normalized.get("reasoning_meta") or {})
        meta.update({
            "provider": "llm",
            "llm_status": "success",
            "pipeline": "think_full_text_then_think_panel_summary",
            "full_reasoning_run_id": run["run_id"],
            "full_reasoning_available": True,
        })
        normalized["reasoning_meta"] = meta
        save_reasoning_run(
            user_id=user_id,
            symbol=symbol,
            source_snapshot_ids=reasoning_input.get("source_snapshot_ids") or [],
            prompt_version=FULL_REASONING_PROMPT_VERSION,
            think_model=think_route.model_name,
            summary_model=summary_route.model_name,
            status="SUCCESS",
            full_reasoning_text=str(full_text),
            summary=normalized,
            error_message="",
        )
        return normalized
    except Exception as exc:
        save_reasoning_run(
            user_id=user_id,
            symbol=symbol,
            source_snapshot_ids=reasoning_input.get("source_snapshot_ids") or [],
            prompt_version=FULL_REASONING_PROMPT_VERSION,
            think_model="",
            summary_model="",
            status="FAILED",
            full_reasoning_text="",
            summary={},
            error_message=str(exc)[:300],
        )
        degraded = normalize_reasoning_payload(fallback, symbol=symbol, reasoning_input=reasoning_input)
        meta = dict(degraded.get("reasoning_meta") or {})
        meta.update({
            "provider": "local_fallback",
            "llm_status": "failed",
            "error": str(exc)[:180],
        })
        degraded["reasoning_meta"] = meta
        return degraded


def save_reasoning_run(
    *,
    user_id: int,
    symbol: str,
    source_snapshot_ids: list[str],
    prompt_version: str,
    think_model: str = "",
    summary_model: str = "",
    status: str = "PENDING",
    full_reasoning_text: str = "",
    summary: dict[str, Any] | None = None,
    error_message: str = "",
    context_id: str = "",
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    snapshot_ids = sorted({item for item in source_snapshot_ids if item})
    source_json = _json(snapshot_ids)
    run_id = f"v5reason_{stable_hash({'user_id': int(user_id), 'symbol': canonical, 'prompt_version': prompt_version, 'source_snapshot_ids': snapshot_ids})[:16]}"
    now = now_text()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO ai_structure_reasoning_runs (
                run_id, user_id, symbol, context_id, source_snapshot_ids_json,
                prompt_version, think_model, summary_model, status,
                full_reasoning_text, summary_json, error_message, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, symbol, prompt_version, source_snapshot_ids_json)
            DO UPDATE SET
                context_id = CASE WHEN excluded.context_id != '' THEN excluded.context_id ELSE context_id END,
                think_model = excluded.think_model,
                summary_model = excluded.summary_model,
                status = CASE
                    WHEN status = 'SUCCESS' AND excluded.status = 'FAILED' THEN status
                    ELSE excluded.status
                END,
                full_reasoning_text = CASE
                    WHEN excluded.full_reasoning_text != '' THEN excluded.full_reasoning_text
                    ELSE full_reasoning_text
                END,
                summary_json = CASE
                    WHEN status = 'SUCCESS' AND excluded.status = 'FAILED' THEN summary_json
                    ELSE excluded.summary_json
                END,
                error_message = CASE
                    WHEN status = 'SUCCESS' AND excluded.status = 'FAILED' THEN error_message
                    ELSE excluded.error_message
                END,
                updated_at = excluded.updated_at
            """,
            (
                run_id,
                int(user_id),
                canonical,
                context_id,
                source_json,
                prompt_version,
                think_model,
                summary_model,
                status,
                full_reasoning_text,
                _json(summary or {}),
                error_message,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM ai_structure_reasoning_runs WHERE run_id = ?", (run_id,)).fetchone()
        return _reasoning_run_row(row)
    finally:
        conn.close()


def attach_reasoning_run_to_context(context: dict[str, Any]) -> None:
    meta = ((context.get("reasoning") or {}).get("reasoning_meta") or {})
    run_id = str(meta.get("full_reasoning_run_id") or "")
    if not run_id:
        return
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE ai_structure_reasoning_runs
               SET context_id = ?,
                   updated_at = ?
             WHERE run_id = ? AND user_id = ? AND symbol = ?
            """,
            (context.get("context_id") or "", now_text(), run_id, int(context.get("user_id") or 0), normalize_symbol(context.get("symbol") or "")),
        )
        conn.commit()
    finally:
        conn.close()


def get_reasoning_run_for_context(
    *,
    user_id: int,
    symbol: str,
    context_id: str = "",
    source_snapshot_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    canonical = normalize_symbol(symbol)
    conn = get_connection()
    try:
        if context_id:
            row = conn.execute(
                """
                SELECT *
                  FROM ai_structure_reasoning_runs
                 WHERE user_id = ? AND symbol = ? AND context_id = ? AND status = 'SUCCESS'
                 ORDER BY updated_at DESC, id DESC
                 LIMIT 1
                """,
                (int(user_id), canonical, context_id),
            ).fetchone()
            if row:
                return _reasoning_run_row(row)
        snapshot_ids = sorted({item for item in (source_snapshot_ids or []) if item})
        if snapshot_ids:
            row = conn.execute(
                """
                SELECT *
                  FROM ai_structure_reasoning_runs
                 WHERE user_id = ?
                   AND symbol = ?
                   AND source_snapshot_ids_json = ?
                   AND status = 'SUCCESS'
                 ORDER BY updated_at DESC, id DESC
                 LIMIT 1
                """,
                (int(user_id), canonical, _json(snapshot_ids)),
            ).fetchone()
            if row:
                return _reasoning_run_row(row)
        if context_id:
            return None
        row = conn.execute(
            """
            SELECT *
              FROM ai_structure_reasoning_runs
             WHERE user_id = ? AND symbol = ? AND status = 'SUCCESS'
             ORDER BY updated_at DESC, id DESC
             LIMIT 1
            """,
            (int(user_id), canonical),
        ).fetchone()
        return _reasoning_run_row(row) if row else None
    finally:
        conn.close()


def _has_configured_ai_native_key(user_id: int) -> bool:
    conn = get_connection()
    try:
        row = conn.execute("SELECT settings_json FROM users WHERE id = ?", (int(user_id),)).fetchone()
        settings = json.loads(row["settings_json"]) if row and row["settings_json"] else {}
    except Exception:
        settings = {}
    finally:
        conn.close()
    provider = str(settings.get("ai_native_provider") or "deepseek").strip().lower()
    if provider == "gemini":
        key = settings.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY")
    else:
        key = settings.get("deepseek_api_key") or os.environ.get("LLM_API_KEY")
    return bool(key and key != "dummy_key_replace_in_prod")


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


def _snapshot_set_from_statuses(*, levels: list[str], status_items: dict[str, dict[str, Any]]) -> dict[str, Any]:
    snapshots = []
    missing = []
    for level in levels:
        snapshot = (status_items.get(level) or {}).get("snapshot")
        if snapshot:
            snapshots.append(snapshot)
        else:
            missing.append(level)
    return {
        "snapshots": snapshots,
        "snapshot_ids": [item["snapshot_id"] for item in snapshots],
        "missing_levels": missing,
    }


def _unique_symbols(symbols: list[str]) -> list[str]:
    output = []
    seen = set()
    for raw_symbol in symbols:
        symbol = normalize_symbol(raw_symbol)
        if symbol in seen:
            continue
        seen.add(symbol)
        output.append(symbol)
    return output


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
    data["reasoning"] = json.loads(data.pop("reasoning_json", "{}") or "{}")
    data["background"] = json.loads(data.pop("background_json") or "{}")
    data["boundary"] = json.loads(data.pop("boundary_json") or "{}")
    return data


def _reasoning_run_row(row) -> dict[str, Any]:
    data = dict(row)
    data["source_snapshot_ids"] = json.loads(data.pop("source_snapshot_ids_json") or "[]")
    data["summary"] = json.loads(data.pop("summary_json") or "{}")
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
