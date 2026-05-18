"""Adapter boundary for the CZSC engine.

CZSC is an optional Rust/PyO3 dependency. This adapter returns explicit
unavailable/error envelopes when the package or input data cannot produce a
snapshot; it never falls back to a legacy structure engine.
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
from fastapi.concurrency import run_in_threadpool

from server.db.kline_lake import query_klines
from server.domain.symbols import normalize_symbol
from server.engines.structure.czsc_serializer import serialize_czsc_level
from server.engines.structure.engine_contract import ENGINE_CZSC, engine_envelope
from server.engines.structure.structure_key import FORMAL_ADJUSTFLAG, FORMAL_SOURCE, normalize_freq, resolve_compute_bars


logger = logging.getLogger(__name__)

ADAPTER_VERSION = "czsc_adapter.v1"
STRUCTURE_ENGINE = "czsc"
SUPPORTED_LEVELS = {"week", "day", "60", "30", "15", "5"}
_FREQ_NAMES = {
    "week": "周线",
    "day": "日线",
    "60": "60分钟",
    "30": "30分钟",
    "15": "15分钟",
    "5": "5分钟",
}


def analyze_czsc_structure_sync(
    symbol: str,
    levels: Optional[list[str]] = None,
    count: int = 800,
    compute_profile: Optional[str] = None,
) -> dict:
    canonical_symbol = normalize_symbol(symbol)
    requested_levels = [normalize_freq(level) for level in (levels or ["day", "30", "5"])]
    czsc_api = _load_czsc()
    if czsc_api is None:
        return engine_envelope(
            engine=ENGINE_CZSC,
            adapter_version=ADAPTER_VERSION,
            symbol=canonical_symbol,
            levels={},
            data_source=_source_meta(),
            metadata={"requested_levels": requested_levels},
            error="CZSC_UNAVAILABLE",
        )

    level_payloads = {}
    errors = {}
    for level in requested_levels:
        if level not in SUPPORTED_LEVELS:
            errors[level] = "UNSUPPORTED_LEVEL"
            continue
        limit = _compute_bars(level, count, compute_profile)
        rows = query_klines(
            canonical_symbol,
            level,
            limit=limit,
            adjustflag=FORMAL_ADJUSTFLAG,
            source=FORMAL_SOURCE,
        )
        if not rows:
            level_payloads[level] = {
                "level": level,
                "error": "NO_DATA",
                "metadata": {"requested_bars": limit},
            }
            continue
        try:
            czsc_obj = _run_czsc(czsc_api, canonical_symbol, level, rows)
            zhongshus = _derive_zs_list(czsc_api, list(getattr(czsc_obj, "bi_list", []) or []))
            level_payloads[level] = serialize_czsc_level(czsc_obj, rows, level, zhongshus=zhongshus)
            level_payloads[level]["source"] = _source_meta()
        except Exception as exc:
            logger.exception("CZSC adapter failed for %s/%s", canonical_symbol, level)
            level_payloads[level] = {
                "level": level,
                "error": "ENGINE_ERROR",
                "message": str(exc)[:200],
                "metadata": {"requested_bars": limit, "row_count": len(rows)},
            }

    return engine_envelope(
        engine=ENGINE_CZSC,
        adapter_version=ADAPTER_VERSION,
        symbol=canonical_symbol,
        levels=level_payloads,
        data_source=_source_meta(),
        metadata={
            "requested_levels": requested_levels,
            "compute_profile": compute_profile or "legacy_default",
            "level_errors": errors,
        },
    )


async def analyze_czsc_structure(
    symbol: str,
    levels: Optional[list[str]] = None,
    count: int = 800,
    compute_profile: Optional[str] = None,
) -> dict:
    return await run_in_threadpool(
        analyze_czsc_structure_sync,
        symbol,
        levels,
        count,
        compute_profile,
    )


def export_czsc_raw_bi_context_sync(
    symbol: str,
    levels: Optional[list[str]] = None,
    count: int = 1200,
    *,
    recent_bi_count: int = 20,
    compute_profile: str = "tactical_v1",
    precomputed_result: Optional[dict] = None,
) -> dict:
    """Export CZSC BI geometry for AI Structure Context.

    这里故意只输出几何事实和算法中枢参考，不输出路径结论。
    AI Structure Context 负责重新识别中枢、判断 30 分钟作战状态和 5 分钟触发。
    """
    canonical_symbol = normalize_symbol(symbol)
    requested_levels = [normalize_freq(level) for level in (levels or ["day", "30", "5"])]
    structure = precomputed_result or analyze_czsc_structure_sync(
        canonical_symbol,
        levels=requested_levels,
        count=count,
        compute_profile=compute_profile,
    )
    if structure.get("error"):
        return {
            "symbol": canonical_symbol,
            "version": "czsc_raw_bi_context.v1",
            "levels": {},
            "error": structure.get("error"),
            "requested_levels": requested_levels,
            "source": _source_meta(),
        }

    result_levels = {}
    for level, payload in (structure.get("levels") or {}).items():
        if not isinstance(payload, dict) or payload.get("error"):
            continue
        bis = list(payload.get("bis") or [])
        recent_bis = bis[-recent_bi_count:] if len(bis) > recent_bi_count else bis
        result_levels[level] = {
            "level": public_level_name(level),
            "bi_count_total": len(bis),
            "bi_sequence": [_raw_bi_from_serialized(bi) for bi in recent_bis],
            "last_close": payload.get("price"),
            "last_bar_time": _last_bar_time(payload),
            "algorithm_zhongshus": [_raw_zs_from_serialized(zs) for zs in list(payload.get("bi_zhongshus") or [])[-5:]],
            "note": "CZSC bi_sequence 是原始几何事实；algorithm_zhongshus 仅作 CZSC 中枢参考，AI 需要从笔序列重新验证。",
        }

    if not result_levels:
        return {
            "symbol": canonical_symbol,
            "version": "czsc_raw_bi_context.v1",
            "levels": {},
            "error": "no_data",
            "requested_levels": requested_levels,
            "source": _source_meta(),
        }

    return {
        "symbol": canonical_symbol,
        "version": "czsc_raw_bi_context.v1",
        "levels": result_levels,
        "requested_levels": requested_levels,
        "compute_profile": compute_profile,
        "compute_bars_by_level": {
            level: _compute_bars(level, count, compute_profile)
            for level in requested_levels
            if level in SUPPORTED_LEVELS
        },
        "source": _source_meta(),
        "instruction": "你收到的是 CZSC 多级别原始笔序列。请自行识别中枢（连续≥3笔的重叠区间）、判断当前位置（中枢内/离开/回拉）、确定防守位；algorithm_zhongshus 只是 CZSC 参考。",
    }


async def export_czsc_raw_bi_context(
    symbol: str,
    levels: Optional[list[str]] = None,
    count: int = 1200,
    *,
    recent_bi_count: int = 20,
    compute_profile: str = "tactical_v1",
    precomputed_result: Optional[dict] = None,
) -> dict:
    return await run_in_threadpool(
        export_czsc_raw_bi_context_sync,
        symbol,
        levels,
        count,
        recent_bi_count=recent_bi_count,
        compute_profile=compute_profile,
        precomputed_result=precomputed_result,
    )


def public_level_name(level: str) -> str:
    return {"day": "日线", "week": "周线", "60": "60分钟", "30": "30分钟", "15": "15分钟", "5": "5分钟"}.get(level, level)


def _load_czsc():
    try:
        import czsc

        return czsc
    except Exception as exc:
        logger.info("CZSC unavailable: %s", exc)
        return None


def get_czsc_engine_version() -> str:
    czsc_api = _load_czsc()
    if czsc_api is None:
        return "unavailable"
    return str(getattr(czsc_api, "__version__", "unknown") or "unknown")


def _run_czsc(czsc_api, symbol: str, level: str, rows: list[dict]):
    df = pd.DataFrame(
        {
            "dt": [row.get("date") for row in rows],
            "symbol": [symbol] * len(rows),
            "open": [row.get("open") for row in rows],
            "close": [row.get("close") for row in rows],
            "high": [row.get("high") for row in rows],
            "low": [row.get("low") for row in rows],
            "vol": [row.get("volume", 0) for row in rows],
            "amount": [row.get("amount", 0) for row in rows],
        }
    )
    bars = czsc_api.format_standard_kline(df, freq=_FREQ_NAMES[level])
    return czsc_api.CZSC(bars)


def _derive_zs_list(czsc_api, bis: list) -> list:
    """Derive valid CZSC ZS sequence when CZSC does not expose czsc_obj.zs_list.

    The public CZSC 1.0 object exposes BI/FX but not zs_list in the tested
    wheel. We keep this small and based on CZSC's own ZS type instead of
    hand-rolling a separate center object.
    """
    if not bis or not hasattr(czsc_api, "ZS"):
        return []

    groups: list[list] = []
    current: list = []
    for bi in bis:
        if not current:
            current = [bi]
            continue

        last_zs = czsc_api.ZS(current)
        if _bi_breaks_zs(bi, last_zs):
            if _valid_zs(czsc_api, current):
                groups.append(current)
            current = [bi]
        else:
            current = [*current, bi]

    if _valid_zs(czsc_api, current):
        groups.append(current)
    return [czsc_api.ZS(group) for group in groups]


def _bi_breaks_zs(bi, zs) -> bool:
    direction = str(getattr(bi, "direction", ""))
    high = _bi_high(bi)
    low = _bi_low(bi)
    if ("向上" in direction or direction.endswith("Up")) and high < float(getattr(zs, "zd", 0)):
        return True
    if ("向下" in direction or direction.endswith("Down")) and low > float(getattr(zs, "zg", 0)):
        return True
    return False


def _valid_zs(czsc_api, bis: list) -> bool:
    if len(bis) < 3:
        return False
    try:
        zs = czsc_api.ZS(bis)
        return bool(zs.is_valid()) and float(getattr(zs, "zg", 0)) >= float(getattr(zs, "zd", 0))
    except Exception:
        return False


def _bi_high(bi) -> float:
    return float(getattr(bi, "high", 0) or 0)


def _bi_low(bi) -> float:
    return float(getattr(bi, "low", 0) or 0)


def _raw_bi_from_serialized(bi: dict) -> dict:
    is_up = bool(bi.get("is_up"))
    return {
        "direction": "UP" if is_up else "DOWN",
        "begin_price": _round_price(bi.get("start_price") or bi.get("y0")),
        "end_price": _round_price(bi.get("end_price") or bi.get("y1")),
        "high": _round_price(bi.get("high")),
        "low": _round_price(bi.get("low")),
        "begin_time": str(bi.get("x0") or ""),
        "end_time": str(bi.get("x1") or ""),
        "bar_count": _int_value(bi.get("bar_count")),
        "is_sure": bool(bi.get("is_sure", True)),
    }


def _raw_zs_from_serialized(zs: dict) -> dict:
    return {
        "zg": _round_price(zs.get("zg")),
        "zd": _round_price(zs.get("zd")),
        "gg": _round_price(zs.get("gg")),
        "dd": _round_price(zs.get("dd")),
        "begin_time": str(zs.get("begin_date") or ""),
        "end_time": str(zs.get("end_date") or ""),
        "bi_count": _int_value(zs.get("bi_count")),
        "source": "czsc_algorithm_suggestion",
    }


def _last_bar_time(payload: dict) -> str:
    klines = payload.get("klines") if isinstance(payload.get("klines"), list) else []
    if not klines:
        return ""
    return str((klines[-1] or {}).get("time") or "")


def _round_price(value) -> float:
    try:
        return round(float(value or 0), 4)
    except (TypeError, ValueError):
        return 0.0


def _int_value(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _compute_bars(level: str, count: int, compute_profile: Optional[str]) -> int:
    if compute_profile:
        return resolve_compute_bars(compute_profile, level, fallback=count)
    return int(count or 800)


def _source_meta() -> dict:
    return {
        "provider": FORMAL_SOURCE,
        "adjustflag": FORMAL_ADJUSTFLAG,
        "engine": STRUCTURE_ENGINE,
        "adapter": "server.engines.structure.czsc_adapter",
        "adapter_version": ADAPTER_VERSION,
    }
