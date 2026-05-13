"""Compare outputs from structure engines without changing either result."""

from __future__ import annotations

from typing import Any


def compare_structure_engines(primary: dict, shadow: dict) -> dict[str, Any]:
    primary_levels = primary.get("levels") or {}
    shadow_levels = shadow.get("levels") or {}
    level_names = sorted(set(primary_levels) | set(shadow_levels))
    levels = {
        level: _compare_level(primary_levels.get(level) or {}, shadow_levels.get(level) or {})
        for level in level_names
    }
    return {
        "version": "structure_engine_comparison.v1",
        "primary_engine": primary.get("engine") or "chan_py",
        "shadow_engine": shadow.get("engine") or "czsc",
        "shadow_available": not bool(shadow.get("error")),
        "levels": levels,
        "notes": _summary_notes(levels, shadow),
    }


def _compare_level(primary: dict, shadow: dict) -> dict[str, Any]:
    p_center = _active_center(primary)
    s_center = _active_center(shadow)
    return {
        "primary_counts": _counts(primary),
        "shadow_counts": _counts(shadow),
        "latest_center_match": _center_match(p_center, s_center),
        "center_delta_pct": {
            field: _delta_pct(_num(p_center.get(field)), _num(s_center.get(field)))
            for field in ("zg", "zd", "gg", "dd")
        },
        "primary_state": primary.get("state") or primary.get("state_hint") or "",
        "shadow_state": shadow.get("state") or shadow.get("state_hint") or "",
        "shadow_error": shadow.get("error") or "",
    }


def _active_center(level: dict) -> dict:
    center = level.get("active_zhongshu") or {}
    if center:
        return center
    centers = level.get("bi_zhongshus") or level.get("zhongshus") or []
    return centers[-1] if centers else {}


def _counts(level: dict) -> dict[str, int]:
    stats = level.get("stats") or {}
    return {
        "fx": int(stats.get("fx_count") or len(level.get("fxs") or [])),
        "bi": int(stats.get("bi_count") or len(level.get("bis") or [])),
        "zs": int(stats.get("bi_zs_count") or len(level.get("bi_zhongshus") or level.get("zhongshus") or [])),
    }


def _center_match(primary: dict, shadow: dict) -> bool:
    if not primary or not shadow:
        return False
    zg_delta = _delta_pct(_num(primary.get("zg")), _num(shadow.get("zg")))
    zd_delta = _delta_pct(_num(primary.get("zd")), _num(shadow.get("zd")))
    return zg_delta is not None and zd_delta is not None and abs(zg_delta) <= 0.5 and abs(zd_delta) <= 0.5


def _summary_notes(levels: dict, shadow: dict) -> list[str]:
    if shadow.get("error"):
        return [f"shadow_unavailable:{shadow.get('error')}"]
    notes = []
    for level, result in levels.items():
        if result.get("shadow_error"):
            notes.append(f"{level}:shadow_error")
        elif not result.get("latest_center_match"):
            notes.append(f"{level}:latest_center_differs")
    return notes


def _delta_pct(a: float, b: float) -> float | None:
    if a <= 0 or b <= 0:
        return None
    return round((b - a) / a * 100, 2)


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
