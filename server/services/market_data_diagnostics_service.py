"""Market data diagnostics for one symbol.

This is an operator-facing view of the data middle layer. It explains which
store will feed formal CZSC, which store will feed intraday preview, and whether
the realtime sampler is actually able to write current bars.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from server.db.kline_lake import query_adjusted_bars, query_intraday_bars, query_klines
from server.domain.symbols import normalize_symbol
from server.engines.structure.source_policy import resolve_structure_source_policy, structure_signature_for_policy


DEFAULT_STRUCTURE_LEVELS = ("week", "day", "30", "5")


def diagnose_market_data_symbols(
    symbols: list[str],
    *,
    sampler_status: dict[str, Any] | None = None,
    trade_date: str | None = None,
    limit: int = 60,
) -> dict[str, Any]:
    """Return batch diagnostics with compact summary counts."""
    selected = []
    seen = set()
    for raw_symbol in symbols:
        try:
            canonical = normalize_symbol(raw_symbol)
        except ValueError:
            continue
        if canonical in seen:
            continue
        selected.append(canonical)
        seen.add(canonical)
        if len(selected) >= max(1, int(limit or 1)):
            break
    items = [
        diagnose_market_data_symbol(symbol, sampler_status=sampler_status, trade_date=trade_date)
        for symbol in selected
    ]
    return {
        "version": "market_data_diagnostics_batch.v1",
        "count": len(items),
        "summary": _batch_summary(items),
        "items": items,
    }


def diagnose_market_data_symbol(
    symbol: str,
    *,
    sampler_status: dict[str, Any] | None = None,
    trade_date: str | None = None,
) -> dict[str, Any]:
    """Return a compact diagnosis of formal and intraday data routing."""
    canonical = normalize_symbol(symbol)
    target_date = (trade_date or datetime.now().strftime("%Y-%m-%d"))[:10]
    intraday = _intraday_summary(canonical, target_date)
    official_1m = _official_1m_summary(canonical)
    structure = _structure_summary(canonical)
    sampler = sampler_status or {}
    return {
        "version": "market_data_diagnostics.v1",
        "symbol": canonical,
        "date": target_date,
        "formal_structure": structure,
        "official_1m": official_1m,
        "intraday_preview": intraday,
        "sampler": sampler,
        "routing": {
            "m1_display_primary": _m1_display_primary(intraday, official_1m),
            "ai_intraday_snapshot_primary": _ai_intraday_primary(intraday, official_1m),
            "formal_czsc_primary": _formal_primary(structure),
        },
        "readiness": _readiness(sampler, intraday),
    }


def _batch_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    readiness: dict[str, int] = {}
    m1_routes: dict[str, int] = {}
    formal_routes: dict[str, int] = {}
    for item in items:
        readiness_status = str(((item.get("readiness") or {}).get("status")) or "unknown")
        m1_route = str(((item.get("routing") or {}).get("m1_display_primary")) or "unknown")
        formal_route = str(((item.get("routing") or {}).get("formal_czsc_primary")) or "unknown")
        readiness[readiness_status] = readiness.get(readiness_status, 0) + 1
        m1_routes[m1_route] = m1_routes.get(m1_route, 0) + 1
        formal_routes[formal_route] = formal_routes.get(formal_route, 0) + 1
    return {
        "readiness": readiness,
        "m1_display_primary": m1_routes,
        "formal_czsc_primary": formal_routes,
    }


def _intraday_summary(symbol: str, trade_date: str) -> dict[str, Any]:
    active_rows = query_intraday_bars(symbol, "1", start_time=trade_date, limit=10000)
    all_rows = query_intraday_bars(symbol, "1", start_time=trade_date, limit=10000, include_replaced=True)
    replaced_rows = [row for row in all_rows if int(row.get("replaced_by_official") or 0)]
    return {
        "source": "intraday_bars",
        "active_rows": len(active_rows),
        "all_rows": len(all_rows),
        "replaced_rows": len(replaced_rows),
        "first_active_at": str(active_rows[0].get("bar_time") or active_rows[0].get("date") or "") if active_rows else "",
        "last_active_at": str(active_rows[-1].get("bar_time") or active_rows[-1].get("date") or "") if active_rows else "",
        "last_quality": str(active_rows[-1].get("quality") or "") if active_rows else "",
        "last_status": str(active_rows[-1].get("bar_status") or "") if active_rows else "",
    }


def _official_1m_summary(symbol: str) -> dict[str, Any]:
    qfq = query_klines(symbol, "1", limit=1, adjustflag="2", source="tdx")
    raw = query_klines(symbol, "1", limit=1, adjustflag="3", source="tdx")
    selected = qfq or raw
    return {
        "source": "tdx_lake",
        "qfq_last_at": str((qfq[-1] if qfq else {}).get("date") or ""),
        "raw_last_at": str((raw[-1] if raw else {}).get("date") or ""),
        "display_last_at": str((selected[-1] if selected else {}).get("date") or ""),
        "display_adjustflag": "2" if qfq else ("3" if raw else ""),
    }


def _structure_summary(symbol: str) -> dict[str, Any]:
    levels = {}
    for level in DEFAULT_STRUCTURE_LEVELS:
        try:
            policy = resolve_structure_source_policy(symbol=symbol, level=level, limit=1200)
            signature = structure_signature_for_policy(symbol=symbol, level=level, limit=1200, policy=policy)
            selected = policy.get("selected") or {}
            levels[level] = {
                "source": selected.get("source") or "",
                "storage": selected.get("storage") or "",
                "dataset": selected.get("dataset") or "",
                "adjustflag": selected.get("adjustflag") or "",
                "last_bar_at": selected.get("last_bar_at") or "",
                "row_count": selected.get("row_count") or 0,
                "reject_reason": selected.get("reject_reason") or "",
                "signature_rows": signature.get("row_count") or 0,
                "signature_last_at": signature.get("last_date") or "",
            }
        except Exception as exc:
            levels[level] = {"error": str(exc)}
    return {"levels": levels}


def _m1_display_primary(intraday: dict[str, Any], official_1m: dict[str, Any]) -> str:
    if int(intraday.get("active_rows") or 0) > 0:
        return "intraday_bars"
    if official_1m.get("display_last_at"):
        return "tdx_lake"
    return "missing"


def _ai_intraday_primary(intraday: dict[str, Any], official_1m: dict[str, Any]) -> str:
    return _m1_display_primary(intraday, official_1m)


def _formal_primary(structure: dict[str, Any]) -> str:
    day = ((structure.get("levels") or {}).get("day") or {})
    if day.get("storage") and day.get("dataset"):
        return f"{day.get('source')}:{day.get('storage')}:{day.get('dataset')}"
    return "missing"


def _readiness(sampler: dict[str, Any], intraday: dict[str, Any]) -> dict[str, Any]:
    if int(intraday.get("active_rows") or 0) > 0:
        return {"status": "ready", "reason": "INTRADAY_ROWS_ACTIVE"}
    if sampler and not sampler.get("bridge_enabled"):
        return {"status": "blocked", "reason": "TDX_BRIDGE_DISABLED"}
    last_error = str((sampler or {}).get("last_error") or "")
    if last_error == "NO_VALID_TRADING_MINUTE_QUOTES":
        return {"status": "waiting", "reason": last_error}
    if last_error:
        return {"status": "blocked", "reason": last_error}
    return {"status": "unknown", "reason": "NO_INTRADAY_ROWS"}
