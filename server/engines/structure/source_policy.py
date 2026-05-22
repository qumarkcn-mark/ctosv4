"""Formal structure K-line source policy.

盘中 preview 可以用聚合数据，但正式 CZSC snapshot 必须只使用通过校验
的 CLOSED K 线。这个模块只做选择和校验，不调用 CZSC。
"""

from __future__ import annotations

from typing import Any

from server.db.kline_lake import get_kline_window_signature, query_klines
from server.domain.symbols import normalize_symbol
from server.engines.structure.structure_key import FORMAL_ADJUSTFLAG, FORMAL_SOURCE, normalize_freq


TDX_NATIVE_ADJUSTFLAG = "2"
TDX_NATIVE_SOURCE = "tdx"
BAOSTOCK_SOURCE = FORMAL_SOURCE
BAOSTOCK_ADJUSTFLAG = FORMAL_ADJUSTFLAG
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
    if prefer_tdx_native and freq in TDX_NATIVE_LEVELS:
        tdx_candidate = _candidate(canonical, freq, limit, source=TDX_NATIVE_SOURCE, adjustflag=TDX_NATIVE_ADJUSTFLAG)
        candidates.append(tdx_candidate)
    fallback_candidate = _candidate(canonical, freq, limit, source=BAOSTOCK_SOURCE, adjustflag=BAOSTOCK_ADJUSTFLAG)
    candidates.append(fallback_candidate)
    if (
        tdx_candidate
        and tdx_candidate["usable"]
        and fallback_candidate["last_bar_at"]
        and tdx_candidate["last_bar_at"] < fallback_candidate["last_bar_at"]
    ):
        tdx_candidate["usable"] = False
        tdx_candidate["reject_reason"] = "STALE_VS_FALLBACK"

    selected = next((item for item in candidates if item["usable"]), candidates[-1])
    return {
        "version": "structure_source_policy.v1",
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
    return get_kline_window_signature(
        canonical,
        freq,
        limit=limit,
        adjustflag=selected.get("adjustflag") or BAOSTOCK_ADJUSTFLAG,
        source=selected.get("source") or BAOSTOCK_SOURCE,
    )


def _candidate(symbol: str, freq: str, limit: int, *, source: str, adjustflag: str) -> dict[str, Any]:
    rows = query_klines(symbol, freq, limit=max(1, min(int(limit or 1), 260)), adjustflag=adjustflag, source=source)
    first = rows[0]["date"] if rows else ""
    last = rows[-1]["date"] if rows else ""
    reason = _reject_reason(freq, rows)
    return {
        "source": source,
        "adjustflag": adjustflag,
        "row_count": len(rows),
        "first_bar_at": first,
        "last_bar_at": last,
        "usable": not reason,
        "reject_reason": reason,
    }


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
