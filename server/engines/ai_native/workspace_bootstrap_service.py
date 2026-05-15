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
DEFAULT_CLIENT = "web"
CLIENT_SECTION_PROFILES = {
    "web": ("context_status", "latest_context", "branches", "reminders", "outcomes"),
    "miniprogram": ("context_status", "reminders", "outcomes"),
    "worker": ("context_status",),
    "reminder": ("context_status", "reminders"),
}
ALLOWED_INCLUDE_SECTIONS = set(CLIENT_SECTION_PROFILES["web"])


async def bootstrap_ai_structure_workspace(
    *,
    user_id: int,
    sources: list[str] | None = None,
    focus_symbols: list[str] | None = None,
    levels: list[str] | None = None,
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
    limit: int = 20,
    ensure_pipeline: bool = False,
    priority: int = 85,
    reason: str = "workspace_bootstrap",
    client: str = DEFAULT_CLIENT,
    include_sections: list[str] | None = None,
) -> dict[str, Any]:
    """Return the minimum state a client needs to open the V5 workspace."""
    normalized_sources = _sources(sources)
    normalized_levels = list(levels or DEFAULT_LEVELS)
    normalized_client = _client(client)
    sections = _include_sections(normalized_client, include_sections)
    safe_limit = max(1, min(int(limit or 20), 50))
    universe = _merge_focus_symbols(
        focus_symbols=focus_symbols,
        universe=resolve_ai_native_universe(user_id, normalized_sources),
    )[:safe_limit]
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
        "client": normalized_client,
        "include": sections,
        "universe": universe,
        "ensure_pipeline": ensure_result,
        "symbols": [
            _symbol_workspace_state(
                user_id=user_id,
                universe_item=item,
                levels=normalized_levels,
                compute_profile=compute_profile,
                include_sections=sections,
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
    include_sections: list[str],
) -> dict[str, Any]:
    symbol = normalize_symbol(universe_item["symbol"])
    context = None
    state: dict[str, Any] = {
        "symbol": symbol,
        "name": universe_item.get("name") or symbol,
        "sources": universe_item.get("sources") or [],
        "priority": int(universe_item.get("priority") or 0),
        "has_position": bool(universe_item.get("has_position")),
    }
    if "context_status" in include_sections:
        state["context_status"] = _status_summary(get_ai_structure_context_status(
            user_id=user_id,
            symbol=symbol,
            levels=levels,
            compute_profile=compute_profile,
        ))
    if _needs_context(include_sections):
        context = get_latest_ai_structure_context(user_id=user_id, symbol=symbol)
    context_id = str((context or {}).get("context_id") or "")
    if "latest_context" in include_sections:
        state["latest_context"] = _context_summary(context)
    if "branches" in include_sections:
        branches = list_scenario_branches(user_id=user_id, symbol=symbol, context_id=context_id or None)
        state["branches"] = {
            "count": len(branches),
            "items": branches[:5],
        }
    if "reminders" in include_sections:
        state["reminders"] = list_structure_reminders(user_id=user_id, symbol=symbol, limit=10)
    if "outcomes" in include_sections:
        outcomes = list_symbol_outcome_reviews(user_id=user_id, symbol=symbol, limit=5)
        state["outcomes"] = {
            "count": outcomes.get("count", 0),
            "items": (outcomes.get("items") or [])[:5],
            "memory": outcomes.get("memory") or {},
        }
    return state


def _context_summary(context: dict[str, Any] | None) -> dict[str, Any] | None:
    if not context:
        return None
    boundary = context.get("boundary") or {}
    background = context.get("background") or {}
    reasoning = context.get("reasoning") or {}
    return {
        "context_id": context.get("context_id"),
        "symbol": context.get("symbol"),
        "status": context.get("status"),
        "stale_reason": context.get("stale_reason") or "",
        "prompt_version": context.get("prompt_version") or reasoning.get("version") or "",
        "main_level": context.get("main_level") or reasoning.get("main_level") or boundary.get("primary_level") or background.get("primary_level") or "",
        "trigger_level": context.get("trigger_level") or reasoning.get("trigger_level") or "",
        "coach_summary": context.get("coach_summary") or reasoning.get("coach_summary") or context.get("summary_text") or "",
        "trend_growth": reasoning.get("trend_growth") or {},
        "divergence_view": reasoning.get("divergence_view") or {},
        "resonance_view": reasoning.get("resonance_view") or {},
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


def _merge_focus_symbols(*, focus_symbols: list[str] | None, universe: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_symbol in focus_symbols or []:
        symbol = normalize_symbol(raw_symbol)
        if symbol in seen:
            continue
        seen.add(symbol)
        merged.append({
            "symbol": symbol,
            "name": symbol,
            "sources": ["focus"],
            "priority": 120,
            "has_position": False,
        })
    for item in universe:
        symbol = normalize_symbol(item["symbol"])
        if symbol in seen:
            existing = next(row for row in merged if row["symbol"] == symbol)
            existing["name"] = item.get("name") or existing["name"]
            existing["sources"] = sorted(set(existing.get("sources") or []) | set(item.get("sources") or []))
            existing["priority"] = max(int(existing.get("priority") or 0), int(item.get("priority") or 0))
            existing["has_position"] = bool(existing.get("has_position")) or bool(item.get("has_position"))
            continue
        seen.add(symbol)
        merged.append(item)
    return merged


def _client(client: str | None) -> str:
    normalized = str(client or DEFAULT_CLIENT).strip().lower()
    if normalized not in CLIENT_SECTION_PROFILES:
        raise ValueError(f"unsupported workspace client: {normalized}")
    return normalized


def _include_sections(client: str, include_sections: list[str] | None) -> list[str]:
    raw_sections = include_sections if include_sections is not None else list(CLIENT_SECTION_PROFILES[client])
    sections = [str(item).strip().lower() for item in raw_sections if str(item).strip()]
    invalid = sorted(set(sections) - ALLOWED_INCLUDE_SECTIONS)
    if invalid:
        raise ValueError(f"unsupported workspace include sections: {','.join(invalid)}")
    return sections or list(CLIENT_SECTION_PROFILES[client])


def _needs_context(include_sections: list[str]) -> bool:
    return bool({"latest_context", "branches"} & set(include_sections))
