"""AI Native V5 data pipeline ensure service.

This module keeps the page request path light: it may fetch a minimal K-line
cache, but it only enqueues CZSC snapshot/context jobs instead of computing
heavy structure synchronously.
"""

from __future__ import annotations

import logging
from typing import Any

from server.db.kline_lake import count_klines
from server.domain.symbols import normalize_symbol
from server.engines.ai_native.czsc_snapshot_service import (
    DEFAULT_COMPUTE_PROFILE,
    DEFAULT_LEVELS,
    prewarm_structure_snapshots,
)
from server.engines.ai_native.structure_context_service import prewarm_ai_structure_contexts
from server.engines.structure.structure_key import normalize_freq, resolve_compute_bars
from server.services.baostock_service import ensure_klines_cached

logger = logging.getLogger(__name__)


async def ensure_ai_structure_pipeline(
    *,
    user_id: int,
    symbols: list[str],
    levels: list[str] | None = None,
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
    priority: int = 85,
    reason: str = "ai_structure_pipeline_ensure",
) -> dict[str, Any]:
    """Ensure K-lines exist, then enqueue CZSC snapshots and AI contexts.

    The K-line step uses a tiny minimum (`min_count=1`) to unblock cold-start
    symbols quickly. Historical backfill is handled asynchronously by the
    BaoStock service, while snapshot/context jobs stay in the existing workers.
    """
    normalized_symbols = _unique_symbols(symbols)
    normalized_levels = _unique_levels(levels or list(DEFAULT_LEVELS))
    kline_items: list[dict[str, Any]] = []

    for symbol in normalized_symbols:
        for level in normalized_levels:
            kline_items.append(
                await _ensure_symbol_level_kline(
                    symbol=symbol,
                    level=level,
                    compute_profile=compute_profile,
                )
            )

    snapshot_result = prewarm_structure_snapshots(
        symbols=normalized_symbols,
        levels=normalized_levels,
        compute_profile=compute_profile,
        priority=priority,
        reason=reason,
        requested_by_user_id=user_id,
    )
    context_result = prewarm_ai_structure_contexts(
        user_id=user_id,
        symbols=normalized_symbols,
        levels=normalized_levels,
        compute_profile=compute_profile,
        priority=max(1, priority - 10),
        reason=reason,
    )

    return {
        "symbols": normalized_symbols,
        "levels": normalized_levels,
        "kline": {
            "items": kline_items,
            "ready": all(item["ready"] for item in kline_items) if kline_items else False,
            "errors": [item for item in kline_items if item["status"] == "error"],
        },
        "snapshots": snapshot_result,
        "contexts": context_result,
        "engine": "czsc",
        "reason": reason,
    }


async def _ensure_symbol_level_kline(
    *,
    symbol: str,
    level: str,
    compute_profile: str,
) -> dict[str, Any]:
    before = count_klines(symbol, level)
    target_bars = resolve_compute_bars(compute_profile, level)
    try:
        ready = await ensure_klines_cached(symbol, level, min_count=1)
    except Exception as exc:
        logger.warning("AI structure K-line ensure failed %s/%s: %s", symbol, level, exc)
        after = count_klines(symbol, level)
        return {
            "symbol": symbol,
            "level": level,
            "before": before,
            "after": after,
            "target_bars": target_bars,
            "ready": False,
            "status": "error",
            "error": str(exc),
        }

    after = count_klines(symbol, level)
    return {
        "symbol": symbol,
        "level": level,
        "before": before,
        "after": after,
        "target_bars": target_bars,
        "ready": bool(ready),
        "status": "ready" if ready else "no_data",
    }


def _unique_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in symbols:
        symbol = normalize_symbol(raw)
        if symbol in seen:
            continue
        seen.add(symbol)
        normalized.append(symbol)
    if not normalized:
        raise ValueError("symbols required")
    return normalized


def _unique_levels(levels: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in levels:
        level = normalize_freq(raw)
        if level in seen:
            continue
        seen.add(level)
        normalized.append(level)
    if not normalized:
        raise ValueError("levels required")
    return normalized
