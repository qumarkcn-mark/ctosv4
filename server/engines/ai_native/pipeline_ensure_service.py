"""AI Native V5 data pipeline ensure service.

This module keeps the page request path light: it may fetch a minimal K-line
cache, but it only enqueues CZSC snapshot/context jobs instead of computing
heavy structure synchronously.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from server import config
from server.db.kline_lake import count_klines
from server.domain.symbols import normalize_symbol
from server.engines.ai_native.czsc_snapshot_service import (
    DEFAULT_COMPUTE_PROFILE,
    DEFAULT_LEVELS,
    prewarm_structure_snapshots,
)
from server.engines.ai_native.structure_context_service import prewarm_ai_structure_contexts
from server.engines.structure.structure_key import normalize_freq, resolve_compute_bars
from server.services.baostock_service import fetch_klines_quick, fetch_klines_sync

logger = logging.getLogger(__name__)


async def ensure_ai_structure_pipeline(
    *,
    user_id: int,
    symbols: list[str],
    levels: list[str] | None = None,
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
    priority: int = 85,
    reason: str = "ai_structure_pipeline_ensure",
    allow_context_enqueue: bool = False,
) -> dict[str, Any]:
    """Ensure K-lines exist, then enqueue CZSC snapshots and AI contexts.

    The K-line step fetches a quick cache to unblock cold-start symbols.
    Historical backfill is scheduled asynchronously and rewarms snapshots and
    contexts after full data lands.
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
        allow_when_auto_disabled=allow_context_enqueue,
    )
    if config.BAOSTOCK_AUTO_SYNC_ENABLED:
        _schedule_backfill_rewarm(
            user_id=user_id,
            symbols=normalized_symbols,
            levels=normalized_levels,
            compute_profile=compute_profile,
            priority=priority,
            reason=f"{reason}_backfill",
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
    if not config.BAOSTOCK_AUTO_SYNC_ENABLED:
        target_bars = resolve_compute_bars(compute_profile, level)
        return {
            "symbol": symbol,
            "level": level,
            "before": 0,
            "after": 0,
            "target_bars": target_bars,
            "ready": True,
            "status": "skipped",
            "reason": "BAOSTOCK_AUTO_SYNC_DISABLED",
        }

    before = count_klines(symbol, level)
    target_bars = resolve_compute_bars(compute_profile, level)
    try:
        if before <= 0:
            await asyncio.to_thread(fetch_klines_quick, symbol, level)
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
    ready = after > 0
    return {
        "symbol": symbol,
        "level": level,
        "before": before,
        "after": after,
        "target_bars": target_bars,
        "ready": bool(ready),
        "status": "ready" if ready else "no_data",
    }


def _schedule_backfill_rewarm(
    *,
    user_id: int,
    symbols: list[str],
    levels: list[str],
    compute_profile: str,
    priority: int,
    reason: str,
) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(
        _backfill_and_rewarm(
            user_id=user_id,
            symbols=symbols,
            levels=levels,
            compute_profile=compute_profile,
            priority=priority,
            reason=reason,
        )
    )


async def _backfill_and_rewarm(
    *,
    user_id: int,
    symbols: list[str],
    levels: list[str],
    compute_profile: str,
    priority: int,
    reason: str,
) -> None:
    changed: dict[str, set[str]] = {}
    for symbol in symbols:
        for level in levels:
            before = count_klines(symbol, level)
            try:
                written = await asyncio.to_thread(fetch_klines_sync, symbol, level)
            except Exception as exc:
                logger.warning("AI structure backfill failed %s/%s: %s", symbol, level, exc)
                continue
            after = count_klines(symbol, level)
            if written > 0 or after != before:
                changed.setdefault(symbol, set()).add(level)

    if not changed:
        return

    for symbol, changed_levels in sorted(changed.items()):
        prewarm_structure_snapshots(
            symbols=[symbol],
            levels=sorted(changed_levels),
            compute_profile=compute_profile,
            priority=priority,
            reason=reason,
            requested_by_user_id=user_id,
        )
        prewarm_ai_structure_contexts(
            user_id=user_id,
            symbols=[symbol],
            levels=levels,
            compute_profile=compute_profile,
            priority=max(1, priority - 10),
            reason=reason,
            allow_when_auto_disabled=False,
        )


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
