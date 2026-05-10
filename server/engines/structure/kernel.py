"""Structure Kernel envelope for deterministic Chan facts.

P1 收权：硬编码结构层只产出事实、边界、数据质量和 AI digest，
不在这里给交易动作或主路径概率下结论。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from server.db.kline_lake import query_klines


SHANGHAI_TZ = timezone(timedelta(hours=8))
KERNEL_VERSION = "structure_kernel.v1"


def build_structure_kernel(
    *,
    symbol: str,
    profile: str,
    levels: list[str],
    adapter_result: dict[str, Any],
    data_signature: str = "",
) -> dict[str, Any]:
    """Build a compact deterministic fact envelope from chan_adapter output."""
    raw_levels = adapter_result.get("levels") or {}
    level_digests = {
        level: _level_digest(raw_levels.get(level) or {})
        for level in levels
        if isinstance(raw_levels.get(level), dict)
    }
    facts_digest = {
        "symbol": symbol,
        "profile": profile,
        "levels": level_digests,
        "available_levels": list(level_digests.keys()),
        "boundaries": _boundary_digest(level_digests),
        "data_quality": _data_quality(adapter_result, levels),
    }
    fingerprint_payload = {
        "version": KERNEL_VERSION,
        "symbol": symbol,
        "profile": profile,
        "data_signature": data_signature,
        "facts_digest": facts_digest,
    }
    return {
        "version": KERNEL_VERSION,
        "symbol": symbol,
        "profile": profile,
        "levels": levels,
        "data_signature": data_signature,
        "structure_fingerprint": _stable_hash(fingerprint_payload),
        "facts_digest": facts_digest,
        "data_quality": facts_digest["data_quality"],
        "generated_at": datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
        "source": {
            "engine": "chan.py",
            "adapter": "server.engines.structure.chan_adapter",
            "role": "deterministic_structure_kernel",
        },
    }


def build_structure_data_signature(symbol: str, levels: list[str]) -> str:
    """Build a cheap data signature from latest formal K-line bars."""
    parts = []
    for level in levels:
        latest = _latest_kline(symbol, level)
        if not latest:
            return ""
        parts.append(
            {
                "level": level,
                "date": latest.get("date"),
                "close": latest.get("close"),
                "volume": latest.get("volume"),
            }
        )
    return _stable_hash({"symbol": symbol, "levels": parts})


def _latest_kline(symbol: str, level: str) -> dict[str, Any]:
    try:
        rows = query_klines(symbol, level, limit=1, adjustflag="2")
    except Exception:
        return {}
    return rows[-1] if rows else {}


def _level_digest(level: dict[str, Any]) -> dict[str, Any]:
    active = level.get("active_zhongshu") or {}
    zhongshus = level.get("bi_zhongshus") or level.get("zhongshus") or []
    latest_center = active or (zhongshus[-1] if zhongshus else {})
    klines = level.get("klines") or []
    return {
        "level": level.get("level"),
        "price": level.get("price") or _last_close(klines),
        "state": level.get("state") or "UNKNOWN",
        "last_bi_dir": level.get("last_bi_dir") or "unknown",
        "center": {
            "zg": level.get("zg") or latest_center.get("zg"),
            "zd": level.get("zd") or latest_center.get("zd"),
            "gg": latest_center.get("gg"),
            "dd": latest_center.get("dd"),
        },
        "counts": {
            "klines": len(klines),
            "bis": len(level.get("bis") or []),
            "segments": len(level.get("segs") or []),
            "centers": len(zhongshus),
            "bsps": len(level.get("bsps") or []),
        },
        "recent_bis": _tail(level.get("bis"), 3),
        "recent_bsps": _tail(level.get("bsps"), 3),
        "patterns": _tail(level.get("patterns"), 5),
    }


def _boundary_digest(levels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for level, digest in levels.items():
        center = digest.get("center") or {}
        result[level] = {
            "confirm": center.get("zg"),
            "invalidate": center.get("zd"),
            "observe": digest.get("price"),
        }
    return result


def _data_quality(adapter_result: dict[str, Any], expected_levels: list[str]) -> dict[str, Any]:
    freshness = adapter_result.get("freshness") or {}
    raw_levels = adapter_result.get("levels") or {}
    missing = [level for level in expected_levels if level not in raw_levels]
    stale_levels = [
        level
        for level, item in (freshness.get("levels") or {}).items()
        if isinstance(item, dict) and item.get("is_stale")
    ]
    return {
        "is_stale": bool(freshness.get("is_stale")) or bool(stale_levels),
        "stale_reason": freshness.get("stale_reason") or "",
        "missing_levels": missing,
        "stale_levels": stale_levels,
        "last_bar_at": freshness.get("last_bar_at") or "",
    }


def _last_close(klines: list[dict[str, Any]]) -> float:
    if not klines:
        return 0.0
    try:
        return float(klines[-1].get("close") or 0)
    except (TypeError, ValueError):
        return 0.0


def _tail(value: Any, limit: int) -> list[Any]:
    return list(value or [])[-limit:] if isinstance(value, list) else []


def _stable_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
