"""Route structure analysis through pluggable engines."""

from __future__ import annotations

from typing import Optional

from fastapi.concurrency import run_in_threadpool

from server.engines.structure import chan_adapter
from server.engines.structure.czsc_adapter import analyze_czsc_structure_sync
from server.engines.structure.engine_comparison import compare_structure_engines
from server.engines.structure.engine_contract import ENGINE_CHAN_PY, ENGINE_CZSC, ENGINE_DUAL, normalize_engine_mode


def analyze_structure_with_engine_sync(
    symbol: str,
    levels: Optional[list[str]] = None,
    count: int = 800,
    *,
    structure_engine: str = ENGINE_CHAN_PY,
    cchan_preset: str = "live_tolerant",
    compute_profile: Optional[str] = None,
) -> dict:
    mode = normalize_engine_mode(structure_engine)
    if mode == ENGINE_CHAN_PY:
        result = chan_adapter.analyze_structure_sync(symbol, levels, count, cchan_preset, compute_profile)
        return _as_engine_result(result, ENGINE_CHAN_PY)
    if mode == ENGINE_CZSC:
        return analyze_czsc_structure_sync(symbol, levels, count, compute_profile)

    primary = _as_engine_result(
        chan_adapter.analyze_structure_sync(symbol, levels, count, cchan_preset, compute_profile),
        ENGINE_CHAN_PY,
    )
    shadow = analyze_czsc_structure_sync(symbol, levels, count, compute_profile)
    primary["shadow_structure"] = shadow
    primary["structure_engine_comparison"] = compare_structure_engines(primary, shadow)
    primary["engine_mode"] = ENGINE_DUAL
    return primary


async def analyze_structure_with_engine(
    symbol: str,
    levels: Optional[list[str]] = None,
    count: int = 800,
    *,
    structure_engine: str = ENGINE_CHAN_PY,
    cchan_preset: str = "live_tolerant",
    compute_profile: Optional[str] = None,
) -> dict:
    return await run_in_threadpool(
        analyze_structure_with_engine_sync,
        symbol,
        levels,
        count,
        structure_engine=structure_engine,
        cchan_preset=cchan_preset,
        compute_profile=compute_profile,
    )


def _as_engine_result(result: dict, engine: str) -> dict:
    result = dict(result or {})
    result.setdefault("engine", engine)
    result.setdefault("engine_mode", engine)
    return result
