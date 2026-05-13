"""Build compact AI evidence from CZSC structure output."""

from __future__ import annotations

from typing import Any


def build_czsc_evidence(czsc_structure: dict | None) -> dict[str, Any]:
    if not czsc_structure:
        return {"available": False, "reason": "missing"}
    if czsc_structure.get("error"):
        return {"available": False, "reason": czsc_structure.get("error")}

    levels = {}
    for level_key, level in (czsc_structure.get("levels") or {}).items():
        center = level.get("active_zhongshu") or {}
        levels[level_key] = {
            "state_hint": level.get("state_hint") or "",
            "last_bi_dir": level.get("last_bi_dir") or "unknown",
            "price_vs_center": level.get("price_vs_center") or {},
            "active_center": {
                "zg": center.get("zg", 0),
                "zd": center.get("zd", 0),
                "zz": center.get("zz", 0),
                "gg": center.get("gg", 0),
                "dd": center.get("dd", 0),
                "begin": center.get("begin_date", ""),
                "end": center.get("end_date", ""),
            },
            "counts": {
                "fx": (level.get("stats") or {}).get("fx_count", 0),
                "bi": (level.get("stats") or {}).get("bi_count", 0),
                "zs": (level.get("stats") or {}).get("bi_zs_count", 0),
            },
        }
    return {
        "available": True,
        "engine": "czsc",
        "adapter_version": czsc_structure.get("adapter_version") or "",
        "levels": levels,
    }
