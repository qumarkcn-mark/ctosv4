"""Shared contract for pluggable structure engines.

This module defines the stable envelope used by structure engine adapters.
Adapters can provide different levels of detail, but they should not invent
fields they cannot compute. Unsupported structures stay as empty lists.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


ENGINE_CZSC = "czsc"

SUPPORTED_ENGINES = {ENGINE_CZSC}


def normalize_engine_mode(engine: str | None) -> str:
    mode = str(engine or ENGINE_CZSC).strip().lower()
    if mode not in SUPPORTED_ENGINES:
        allowed = ", ".join(sorted(SUPPORTED_ENGINES))
        raise ValueError(f"unsupported structure engine: {engine}; allowed: {allowed}")
    return mode


def empty_level(level: str) -> dict[str, Any]:
    return {
        "level": level,
        "klines": [],
        "fxs": [],
        "bis": [],
        "segs": [],
        "bi_zhongshus": [],
        "seg_zhongshus": [],
        "zhongshus": [],
        "bsps": [],
        "metadata": {},
    }


def normalize_level_payload(level_key: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    normalized = empty_level(level_key)
    if payload:
        normalized.update(deepcopy(payload))
    normalized["level"] = str(normalized.get("level") or level_key)
    normalized.setdefault("zhongshus", normalized.get("bi_zhongshus") or [])
    normalized.setdefault("metadata", {})
    for key in ("klines", "fxs", "bis", "segs", "bi_zhongshus", "seg_zhongshus", "zhongshus", "bsps"):
        if normalized.get(key) is None:
            normalized[key] = []
    return normalized


def engine_envelope(
    *,
    engine: str,
    adapter_version: str,
    symbol: str,
    levels: dict[str, dict[str, Any]],
    data_source: dict[str, Any] | None = None,
    freshness: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "engine": engine,
        "adapter_version": adapter_version,
        "symbol": symbol,
        "data_source": data_source or {},
        "freshness": freshness or {},
        "levels": {
            key: normalize_level_payload(key, value)
            for key, value in (levels or {}).items()
        },
        "metadata": metadata or {},
        "error": error or "",
    }
