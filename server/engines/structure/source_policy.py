"""Formal structure K-line source policy.

盘中 preview 可以用聚合数据，但正式 CZSC snapshot 必须只使用通过校验
的 CLOSED K 线。这个模块只做选择和校验，不调用 CZSC。
"""

from __future__ import annotations

from typing import Any

from server.db.kline_lake import (
    get_adjusted_bars_window_signature,
    get_kline_window_signature,
    query_adjusted_bars,
    query_klines,
)
from server.domain.symbols import normalize_symbol
from server.engines.structure.structure_key import FORMAL_ADJUSTFLAG, FORMAL_SOURCE, normalize_freq


TDX_NATIVE_ADJUSTFLAG = "2"
TDX_NATIVE_SOURCE = "tdx"
TDX_QFQ_DATASET = "tdx_qfq"
BAOSTOCK_SOURCE = FORMAL_SOURCE
BAOSTOCK_ADJUSTFLAG = FORMAL_ADJUSTFLAG
BAOSTOCK_QFQ_DATASET = "baostock_qfq"
TDX_NATIVE_LEVELS = {"week", "day", "60", "30", "15", "5", "1"}


def resolve_structure_source_policy(
    *,
    symbol: str,
    level: str,
    limit: int,
    prefer_tdx_native: bool = True,
) -> dict[str, Any]:
    """Return the formal structure source choice for one symbol/level."""
    canonical = normalize_symbol(symbol)
    freq = normalize_freq(level)
    candidates = []
    tdx_candidate = None
    tdx_candidates = []
    if prefer_tdx_native and freq in TDX_NATIVE_LEVELS:
        tdx_candidate = _adjusted_candidate(
            canonical,
            freq,
            limit,
            source=TDX_NATIVE_SOURCE,
            dataset=TDX_QFQ_DATASET,
            adjustflag=TDX_NATIVE_ADJUSTFLAG,
        )
        if freq != "day" and tdx_candidate["usable"] and not _has_tdx_day_factor(canonical):
            tdx_candidate["usable"] = False
            tdx_candidate["reject_reason"] = "MISSING_TDX_DAY_FACTOR"
        if tdx_candidate["usable"] and _has_fresher_tdx_raw(canonical, freq, tdx_candidate["last_bar_at"]):
            tdx_candidate["usable"] = False
            tdx_candidate["reject_reason"] = "STALE_VS_TDX_RAW"
        tdx_candidates = [tdx_candidate]
        candidates.extend(tdx_candidates)
    fallback_candidate = _adjusted_candidate(
        canonical,
        freq,
        limit,
        source=BAOSTOCK_SOURCE,
        dataset=BAOSTOCK_QFQ_DATASET,
        adjustflag=BAOSTOCK_ADJUSTFLAG,
    )
    candidates.append(fallback_candidate)
    if prefer_tdx_native and freq in TDX_NATIVE_LEVELS:
        tdx_legacy_candidate = _legacy_candidate(
            canonical,
            freq,
            limit,
            source=TDX_NATIVE_SOURCE,
            adjustflag=TDX_NATIVE_ADJUSTFLAG,
        )
        if freq != "day" and tdx_legacy_candidate["usable"] and not _has_tdx_day_factor(canonical):
            tdx_legacy_candidate["usable"] = False
            tdx_legacy_candidate["reject_reason"] = "MISSING_TDX_DAY_FACTOR"
        if tdx_legacy_candidate["usable"] and _has_fresher_tdx_raw(
            canonical, freq, tdx_legacy_candidate["last_bar_at"]
        ):
            tdx_legacy_candidate["usable"] = False
            tdx_legacy_candidate["reject_reason"] = "STALE_VS_TDX_RAW"
        tdx_candidates.append(tdx_legacy_candidate)
        candidates.append(tdx_legacy_candidate)
    candidates.append(
        _legacy_candidate(canonical, freq, limit, source=BAOSTOCK_SOURCE, adjustflag=BAOSTOCK_ADJUSTFLAG)
    )
    fallback_last_bar_at = max(
        (str(item.get("last_bar_at") or "") for item in candidates if item.get("source") == BAOSTOCK_SOURCE),
        default="",
    )
    for item in tdx_candidates:
        if (
            item["usable"]
            and fallback_last_bar_at
            and item["last_bar_at"] < fallback_last_bar_at
        ):
            item["usable"] = False
            item["reject_reason"] = "STALE_VS_FALLBACK"

    selected = next((item for item in candidates if item["usable"]), candidates[-1])
    return {
        "version": "structure_source_policy.v2",
        "symbol": canonical,
        "level": freq,
        "selected": selected,
        "candidates": candidates,
    }


def query_structure_klines(
    *,
    symbol: str,
    level: str,
    limit: int,
    policy: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Read K lines according to source policy."""
    canonical = normalize_symbol(symbol)
    freq = normalize_freq(level)
    selected = (policy or resolve_structure_source_policy(symbol=canonical, level=freq, limit=limit))["selected"]
    if selected.get("storage") == "adjusted_bars":
        return query_adjusted_bars(
            canonical,
            freq,
            dataset=selected.get("dataset") or BAOSTOCK_QFQ_DATASET,
            limit=limit,
            source=selected.get("source") or BAOSTOCK_SOURCE,
        )
    return query_klines(
        canonical,
        freq,
        limit=limit,
        adjustflag=selected.get("adjustflag") or BAOSTOCK_ADJUSTFLAG,
        source=selected.get("source") or BAOSTOCK_SOURCE,
    )


def structure_signature_for_policy(
    *,
    symbol: str,
    level: str,
    limit: int,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return data signature for the selected source."""
    canonical = normalize_symbol(symbol)
    freq = normalize_freq(level)
    selected = (policy or resolve_structure_source_policy(symbol=canonical, level=freq, limit=limit))["selected"]
    if selected.get("storage") == "adjusted_bars":
        return get_adjusted_bars_window_signature(
            canonical,
            freq,
            limit=limit,
            dataset=selected.get("dataset") or BAOSTOCK_QFQ_DATASET,
            source=selected.get("source") or BAOSTOCK_SOURCE,
        )
    return get_kline_window_signature(
        canonical,
        freq,
        limit=limit,
        adjustflag=selected.get("adjustflag") or BAOSTOCK_ADJUSTFLAG,
        source=selected.get("source") or BAOSTOCK_SOURCE,
    )


def _adjusted_candidate(
    symbol: str,
    freq: str,
    limit: int,
    *,
    source: str,
    dataset: str,
    adjustflag: str,
) -> dict[str, Any]:
    rows = query_adjusted_bars(symbol, freq, dataset=dataset, limit=max(1, min(int(limit or 1), 260)), source=source)
    return _candidate_from_rows(
        rows,
        freq=freq,
        source=source,
        adjustflag=adjustflag,
        storage="adjusted_bars",
        dataset=dataset,
    )


def _legacy_candidate(symbol: str, freq: str, limit: int, *, source: str, adjustflag: str) -> dict[str, Any]:
    rows = query_klines(symbol, freq, limit=max(1, min(int(limit or 1), 260)), adjustflag=adjustflag, source=source)
    return _candidate_from_rows(
        rows,
        freq=freq,
        source=source,
        adjustflag=adjustflag,
        storage="legacy_klines",
        dataset="klines",
    )


def _candidate_from_rows(
    rows: list[dict[str, Any]],
    *,
    freq: str,
    source: str,
    adjustflag: str,
    storage: str,
    dataset: str,
) -> dict[str, Any]:
    first = rows[0]["date"] if rows else ""
    last = rows[-1]["date"] if rows else ""
    reason = _reject_reason(freq, rows)
    return {
        "source": source,
        "storage": storage,
        "dataset": dataset,
        "adjustflag": adjustflag,
        "row_count": len(rows),
        "first_bar_at": first,
        "last_bar_at": last,
        "usable": not reason,
        "reject_reason": reason,
    }


def _has_tdx_day_factor(symbol: str) -> bool:
    """TDX non-day qfq data is trusted only when day/2 factor base exists.

    本地 TDX 原始分钟 / 周线可能被历史任务误标为 adjustflag=2。
    没有 day/2 复权基准时，不允许这些“孤儿 qfq”进入正式 CZSC。
    """
    rows = query_adjusted_bars(symbol, "day", dataset=TDX_QFQ_DATASET, limit=1, source=TDX_NATIVE_SOURCE)
    if rows:
        return True
    return bool(query_klines(symbol, "day", limit=1, adjustflag=TDX_NATIVE_ADJUSTFLAG, source=TDX_NATIVE_SOURCE))


def _has_fresher_tdx_raw(symbol: str, freq: str, qfq_last_bar_at: str) -> bool:
    if not qfq_last_bar_at:
        return False
    rows = query_klines(symbol, freq, limit=1, adjustflag="3", source=TDX_NATIVE_SOURCE)
    if not rows:
        return False
    raw_last = str(rows[-1].get("date") or "")
    return bool(raw_last and raw_last > qfq_last_bar_at)


def _reject_reason(freq: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "NO_DATA"
    last = str(rows[-1].get("date") or "")
    if freq in {"1", "5", "15", "30", "60"}:
        if not _looks_like_minute_close(freq, last):
            return "LAST_BAR_NOT_PERIOD_CLOSE"
    if len(rows) < 30 and freq != "day":
        return "TOO_FEW_BARS"
    return ""


def _looks_like_minute_close(freq: str, value: str) -> bool:
    if len(value) < 16:
        return False
    hm = value[11:16]
    if hm in {"11:30", "15:00"}:
        return True
    try:
        hour, minute = map(int, hm.split(":"))
    except ValueError:
        return False
    total = hour * 60 + minute
    if not ((9 * 60 + 30) <= total <= (11 * 60 + 30) or (13 * 60) <= total <= (15 * 60)):
        return False
    return total % int(freq) == 0
