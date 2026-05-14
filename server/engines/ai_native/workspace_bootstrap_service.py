"""AI Native V5 workspace bootstrap service.

This is a thin read model for Web and miniprogram clients. It reuses the V5
services as sources of truth and keeps page bootstrap free of inline CZSC work.
"""

from __future__ import annotations

from typing import Any

from server.domain.symbols import normalize_symbol
from server.engines.ai_native.czsc_snapshot_service import DEFAULT_COMPUTE_PROFILE, DEFAULT_LEVELS
from server.engines.ai_native.pipeline_ensure_service import ensure_ai_structure_pipeline
from server.engines.ai_native.scenario_branch_service import list_scenario_branches
from server.engines.ai_native.scenario_outcome_service import list_symbol_outcome_reviews
from server.engines.ai_native.structure_context_service import (
    get_ai_structure_context_status,
    get_latest_ai_structure_context,
)
from server.engines.ai_native.structure_reminder_service import list_structure_reminders
from server.engines.ai_native.universe_resolver import DEFAULT_SOURCES, resolve_ai_native_universe


MAX_INLINE_ENSURE_SYMBOLS = 5


async def bootstrap_ai_structure_workspace(
    *,
    user_id: int,
    sources: list[str] | None = None,
    levels: list[str] | None = None,
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
    limit: int = 20,
    ensure_pipeline: bool = False,
    priority: int = 85,
    reason: str = "workspace_bootstrap",
) -> dict[str, Any]:
    """Return the minimum state a client needs to open the V5 workspace."""
    normalized_sources = _sources(sources)
    normalized_levels = list(levels or DEFAULT_LEVELS)
    safe_limit = max(1, min(int(limit or 20), 50))
    universe = resolve_ai_native_universe(user_id, normalized_sources)[:safe_limit]
    symbols = [item["symbol"] for item in universe]
    ensure_result = None
    if ensure_pipeline and symbols:
        ensure_symbols = symbols[:MAX_INLINE_ENSURE_SYMBOLS]
        raw_ensure_result = await ensure_ai_structure_pipeline(
            user_id=user_id,
            symbols=ensure_symbols,
            levels=normalized_levels,
            compute_profile=compute_profile,
            priority=priority,
            reason=reason,
        )
        ensure_result = _ensure_pipeline_summary(raw_ensure_result)
        ensure_result["scope"] = {
            "requested_symbol_count": len(symbols),
            "ensured_symbol_count": len(ensure_symbols),
            "skipped_symbols": symbols[MAX_INLINE_ENSURE_SYMBOLS:],
        }

    return {
        "user_id": int(user_id),
        "sources": normalized_sources,
        "levels": normalized_levels,
        "compute_profile": compute_profile,
        "universe": universe,
        "ensure_pipeline": ensure_result,
        "symbols": [
            _symbol_workspace_state(
                user_id=user_id,
                universe_item=item,
                levels=normalized_levels,
                compute_profile=compute_profile,
            )
            for item in universe
        ],
    }


def _symbol_workspace_state(
    *,
    user_id: int,
    universe_item: dict[str, Any],
    levels: list[str],
    compute_profile: str,
) -> dict[str, Any]:
    symbol = normalize_symbol(universe_item["symbol"])
    context_status = _status_summary(get_ai_structure_context_status(
        user_id=user_id,
        symbol=symbol,
        levels=levels,
        compute_profile=compute_profile,
    ))
    context = get_latest_ai_structure_context(user_id=user_id, symbol=symbol)
    context_id = str((context or {}).get("context_id") or "")
    branches = list_scenario_branches(user_id=user_id, symbol=symbol, context_id=context_id or None)
    reminders = list_structure_reminders(user_id=user_id, symbol=symbol, limit=10)
    outcomes = list_symbol_outcome_reviews(user_id=user_id, symbol=symbol, limit=5)
    return {
        "symbol": symbol,
        "name": universe_item.get("name") or symbol,
        "sources": universe_item.get("sources") or [],
        "priority": int(universe_item.get("priority") or 0),
        "has_position": bool(universe_item.get("has_position")),
        "context_status": context_status,
        "latest_context": _context_summary(context),
        "branches": {
            "count": len(branches),
            "items": branches[:5],
        },
        "reminders": reminders,
        "outcomes": {
            "count": outcomes.get("count", 0),
            "items": (outcomes.get("items") or [])[:5],
            "memory": outcomes.get("memory") or {},
        },
    }


def _context_summary(context: dict[str, Any] | None) -> dict[str, Any] | None:
    if not context:
        return None
    boundary = context.get("boundary") or {}
    background = context.get("background") or {}
    return {
        "context_id": context.get("context_id"),
        "symbol": context.get("symbol"),
        "status": context.get("status"),
        "stale_reason": context.get("stale_reason") or "",
        "main_level": context.get("main_level") or boundary.get("primary_level") or background.get("primary_level") or "",
        "boundary": boundary,
        "background": background,
        "updated_at": context.get("updated_at"),
    }


def _status_summary(status: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": status.get("symbol"),
        "user_id": status.get("user_id"),
        "status": status.get("status") or "unknown",
        "stale_reason": status.get("stale_reason") or "",
        "missing_levels": status.get("missing_levels") or [],
        "job": _job_summary(status.get("job")),
    }


def _job_summary(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if not job:
        return None
    return {
        "job_id": job.get("job_id") or "",
        "status": job.get("status") or "",
        "reason": job.get("reason") or "",
        "error_code": job.get("error_code") or "",
        "next_run_at": job.get("next_run_at") or "",
        "updated_at": job.get("updated_at") or "",
    }


def _ensure_pipeline_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbols": result.get("symbols") or [],
        "levels": result.get("levels") or [],
        "engine": result.get("engine") or "czsc",
        "reason": result.get("reason") or "",
        "kline": _kline_summary(result.get("kline") or {}),
        "snapshots": _result_summary(result.get("snapshots") or {}),
        "contexts": _result_summary(result.get("contexts") or {}),
    }


def _kline_summary(kline: dict[str, Any]) -> dict[str, Any]:
    items = kline.get("items") or []
    return {
        "ready": bool(kline.get("ready")),
        "count": len(items),
        "ready_count": sum(1 for item in items if item.get("ready")),
        "error_count": len(kline.get("errors") or []),
        "status_counts": _status_counts(items),
    }


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    items = result.get("items") or []
    return {
        "count": int(result.get("count") or len(items) or 0),
        "status_counts": _status_counts(items),
        "skipped": bool(result.get("skipped", False)),
        "reason": result.get("reason") or "",
    }


def _status_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _sources(sources: list[str] | None) -> list[str]:
    selected = [str(item).strip().lower() for item in (sources or list(DEFAULT_SOURCES)) if str(item).strip()]
    return selected or list(DEFAULT_SOURCES)
