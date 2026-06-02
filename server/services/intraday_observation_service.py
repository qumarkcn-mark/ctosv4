"""Intraday preview observations built from realtime quotes.

This module intentionally stays outside the formal CZSC snapshot pipeline.
It builds a lightweight preview layer for live coaching: quote-derived 1m bars,
CZSC-compatible 5m / 30m aggregation, and MACD dynamics with explicit coverage
metadata.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from czsc.py.bar_generator import freq_end_time, resample_bars

from server.db.kline_lake import query_intraday_bars, query_klines, upsert_intraday_bars
from server.domain.symbols import normalize_symbol, to_tencent_symbol
from server.engines.ai_native.dynamics_hydrator import hydrate_dynamics
from server.services.price_service import get_current_price, get_minute_klines
from server.services.tdx_bridge_client import fetch_tdx_quote

_LIVE_1M_BY_SYMBOL: dict[str, list[dict[str, Any]]] = {}


async def get_intraday_observation(symbol: str, *, quote: dict | None = None) -> dict[str, Any]:
    """Return intraday preview facts for a symbol.

    The returned object is safe for AI payloads because it marks source,
    coverage, and FORMING/CLOSED basis explicitly. It must not be written back
    into formal CZSC snapshots.
    """
    canonical = normalize_symbol(symbol)
    live_quote = quote if quote is not None else await fetch_tdx_quote(canonical)
    if not live_quote:
        live_quote = await get_current_price(canonical)
    return get_intraday_observation_snapshot(canonical, quote=live_quote)


def get_intraday_observation_snapshot(symbol: str, *, quote: dict | None = None) -> dict[str, Any]:
    """Return an intraday snapshot without async network calls."""
    canonical = normalize_symbol(symbol)
    compact = to_tencent_symbol(canonical)
    live_quote = quote
    if live_quote:
        ingest_intraday_quote(canonical, live_quote)

    lake_today_1m = _today_lake_1m_rows(canonical)
    today_1m = _merge_rows(lake_today_1m, list(_LIVE_1M_BY_SYMBOL.get(compact, [])))
    as_of = _quote_datetime(live_quote) or _quote_minute(live_quote or {}) or (today_1m[-1]["date"] if today_1m else "")
    coverage = _coverage(today_1m, as_of)

    levels = {
        "1m": _level_payload("1m", today_1m, today_1m, today_1m),
    }
    for level, period, target_freq in (
        ("5m", "m5", "5分钟"),
        ("30m", "m30", "30分钟"),
    ):
        history = _lake_minute_history(canonical, period)
        closed_today = _resample_today(today_1m, target_freq, drop_unfinished=True)
        preview_today = _resample_today(today_1m, target_freq, drop_unfinished=False)
        closed_rows = _merge_rows(history, closed_today)
        preview_rows = _merge_rows(history, preview_today)
        levels[level] = _level_payload(level, preview_today, closed_rows, preview_rows)

    return {
        "source": _observation_source(live_quote),
        "usage": "intraday_preview",
        "symbol": canonical,
        "as_of": as_of,
        "quote": _quote_payload(live_quote),
        "coverage": coverage,
        "levels": levels,
    }


def ingest_intraday_quote(symbol: str, quote: dict[str, Any]) -> bool:
    """Add one realtime quote into the intraday aggregation cache.

    Returns True only when the quote becomes a valid A-share trading-minute bar.
    """
    return _ingest_quote(to_tencent_symbol(normalize_symbol(symbol)), quote)


def reset_intraday_observation_cache(symbol: str | None = None) -> None:
    """Clear in-memory quote aggregation cache; intended for tests."""
    if symbol:
        _LIVE_1M_BY_SYMBOL.pop(to_tencent_symbol(normalize_symbol(symbol)), None)
        return
    _LIVE_1M_BY_SYMBOL.clear()


def _today_lake_1m_rows(symbol: str) -> list[dict[str, Any]]:
    """Read today's 1m bars without letting intraday preview rows hide TDX rows."""
    today = datetime.now().strftime("%Y-%m-%d")
    rows_by_date: dict[str, dict[str, Any]] = {}

    for row in _today_intraday_1m_rows(symbol, today):
        date = row.get("date")
        if date:
            rows_by_date[date] = row

    # Formal/post-market TDX rows win for the same minute because they carry
    # complete OHLCV. Intraday preview remains only for not-yet-official minutes.
    for source, adjustflag in (
        ("tdx", "3"),
        ("tdx", "2"),
    ):
        try:
            rows = query_klines(
                symbol,
                "1",
                start_date=today,
                limit=360,
                adjustflag=adjustflag,
                source=source,
            )
        except Exception:
            rows = []
        normalized = [_lake_1m_row(symbol, row, source=source, adjustflag=adjustflag) for row in rows]
        normalized = [row for row in normalized if row]
        for row in normalized:
            date = row.get("date")
            if not date:
                continue
            rows_by_date[date] = row
    return [rows_by_date[key] for key in sorted(rows_by_date)][-360:]


def _today_intraday_1m_rows(symbol: str, today: str) -> list[dict[str, Any]]:
    try:
        rows = query_intraday_bars(symbol, "1", start_time=today, limit=360)
    except Exception:
        return []
    return [_intraday_1m_row(symbol, row) for row in rows if row]


def _intraday_1m_row(symbol: str, row: dict[str, Any]) -> dict[str, Any]:
    date = str(row.get("bar_time") or row.get("date") or "")
    if not _is_a_share_trading_minute(date) or _num(row.get("close")) <= 0:
        return {}
    return {
        "symbol": to_tencent_symbol(symbol),
        "freq": "1",
        "date": date,
        "open": _num(row.get("open")),
        "high": _num(row.get("high")),
        "low": _num(row.get("low")),
        "close": _num(row.get("close")),
        "volume": _num(row.get("volume") or row.get("vol")),
        "amount": _num(row.get("amount")),
        "adjustflag": "3",
        "bar_status": str(row.get("bar_status") or "FORMING"),
        "source": str(row.get("source") or "tdx_quote_aggregation"),
        "sample_count": int(row.get("sample_count") or 0),
        "first_quote_at": str(row.get("first_quote_at") or ""),
        "last_quote_at": str(row.get("last_quote_at") or ""),
        "quality": str(row.get("quality") or "partial"),
        "gap_reason": str(row.get("gap_reason") or ""),
    }


def _lake_1m_row(symbol: str, row: dict[str, Any], *, source: str, adjustflag: str) -> dict[str, Any]:
    date = str(row.get("date") or "")
    if not _is_a_share_trading_minute(date) or _num(row.get("close")) <= 0:
        return {}
    return {
        "symbol": to_tencent_symbol(symbol),
        "freq": "1",
        "date": date,
        "open": _num(row.get("open")),
        "high": _num(row.get("high")),
        "low": _num(row.get("low")),
        "close": _num(row.get("close")),
        "volume": _num(row.get("volume") or row.get("vol")),
        "amount": _num(row.get("amount")),
        "adjustflag": adjustflag,
        "bar_status": _lake_1m_bar_status(date, source),
        "source": f"{source}_lake_1m",
    }


def _lake_1m_bar_status(date: str, source: str) -> str:
    if source == "qmt" and date[:16] == datetime.now().strftime("%Y-%m-%d %H:%M"):
        return "FORMING"
    return "CLOSED"


def _lake_minute_history(symbol: str, interval: str) -> list[dict[str, Any]]:
    freq_map = {"m5": "5", "m30": "30"}
    freq = freq_map.get(interval)
    if not freq:
        return []
    try:
        return query_klines(symbol, freq, limit=240, adjustflag="2", source="tdx")
    except Exception:
        return []


def _ingest_quote(compact: str, quote: dict[str, Any]) -> bool:
    price = _num(quote.get("price"))
    minute = _quote_minute(quote)
    if price <= 0 or not minute:
        return False
    sample_at = _quote_datetime(quote) or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows = [
        dict(row)
        for row in _LIVE_1M_BY_SYMBOL.get(compact, [])
        if str(row.get("date") or "")[:10] == minute[:10]
    ]
    for row in rows:
        if row.get("bar_status") == "FORMING" and row.get("date") != minute:
            row["bar_status"] = "CLOSED"

    now_volume = _num(quote.get("now_volume") or quote.get("nowVolume"))
    current = next((row for row in rows if row.get("date") == minute), None)
    if current:
        current["high"] = round(max(_num(current.get("high")), price), 4)
        current["low"] = round(min(_num(current.get("low")) or price, price), 4)
        current["close"] = round(price, 4)
        current["volume"] = max(_num(current.get("volume")), now_volume)
        current["amount"] = max(_num(current.get("amount")), _num(quote.get("amount")))
        current["bar_status"] = "FORMING"
        current["source"] = "tdx_quote_aggregation"
        current["sample_count"] = int(current.get("sample_count") or 0) + 1
        current["first_quote_at"] = current.get("first_quote_at") or sample_at
        current["last_quote_at"] = sample_at
        current["quality"] = _intraday_row_quality(current)
        current["gap_reason"] = "" if current["quality"] == "full" else "LOW_SAMPLE_COUNT"
    else:
        rows.append(
            {
                "symbol": compact,
                "freq": "1",
                "date": minute,
                "open": round(price, 4),
                "high": round(price, 4),
                "low": round(price, 4),
                "close": round(price, 4),
                "volume": now_volume,
                "amount": _num(quote.get("amount")),
                "adjustflag": "3",
                "bar_status": "FORMING",
                "source": "tdx_quote_aggregation",
                "sample_count": 1,
                "first_quote_at": sample_at,
                "last_quote_at": sample_at,
                "quality": "partial",
                "gap_reason": "LOW_SAMPLE_COUNT",
            }
        )

    rows = sorted(rows, key=lambda item: item.get("date") or "")[-360:]
    _LIVE_1M_BY_SYMBOL[compact] = rows
    _persist_live_1m_row(compact, next((row for row in rows if row.get("date") == minute), None))
    return True


def _persist_live_1m_row(compact: str, row: dict[str, Any] | None) -> None:
    """Persist the live preview 1m row so restart can continue today's tape."""
    if not row:
        return
    try:
        upsert_intraday_bars(
            normalize_symbol(compact),
            "1",
            [
                {
                    "bar_time": row.get("date") or "",
                    "open": _num(row.get("open")),
                    "high": _num(row.get("high")),
                    "low": _num(row.get("low")),
                    "close": _num(row.get("close")),
                    "volume": _num(row.get("volume")),
                    "amount": _num(row.get("amount")),
                    "bar_status": row.get("bar_status") or "FORMING",
                    "source": row.get("source") or "tdx_quote_aggregation",
                    "sample_count": int(row.get("sample_count") or 0),
                    "first_quote_at": row.get("first_quote_at") or "",
                    "last_quote_at": row.get("last_quote_at") or "",
                    "quality": row.get("quality") or "partial",
                    "gap_reason": row.get("gap_reason") or "",
                }
            ],
        )
    except Exception:
        pass


def _intraday_row_quality(row: dict[str, Any]) -> str:
    # 单根 1m 至少有 2 次 quote 才认为高低点有基本可信度。
    return "full" if int(row.get("sample_count") or 0) >= 2 else "partial"


def _resample_today(rows: list[dict[str, Any]], target_freq: str, *, drop_unfinished: bool) -> list[dict[str, Any]]:
    if not rows:
        return []
    rows = [row for row in rows if _is_a_share_trading_minute(str(row.get("date") or ""))]
    if not rows:
        return []
    if drop_unfinished:
        rows = [row for row in rows if row.get("bar_status") != "FORMING"]
        if not rows:
            return []
    df = pd.DataFrame(
        [
            {
                "symbol": row.get("symbol") or "",
                "dt": pd.to_datetime(row.get("date")),
                "open": _num(row.get("open")),
                "close": _num(row.get("close")),
                "high": _num(row.get("high")),
                "low": _num(row.get("low")),
                "vol": _num(row.get("volume")),
                "amount": _num(row.get("amount")),
            }
            for row in rows
            if row.get("date") and _num(row.get("close")) > 0
        ]
    )
    if len(df) == 1:
        row = rows[-1]
        if drop_unfinished and _single_row_is_unfinished(row, target_freq):
            return []
        return [_single_resampled_row(row, target_freq)]
    if len(df) < 1:
        return []
    sampled = resample_bars(
        df,
        target_freq,
        raw_bars=False,
        base_freq="1分钟",
        drop_unfinished=drop_unfinished,
    )
    result = []
    forming_source = any(row.get("bar_status") == "FORMING" for row in rows)
    for item in sampled.to_dict("records"):
        status = "CLOSED"
        if forming_source and str(item.get("dt")) == str(sampled.iloc[-1]["dt"]):
            status = "FORMING"
        result.append(
            {
                "symbol": str(item.get("symbol") or ""),
                "freq": target_freq.replace("分钟", ""),
                "date": _fmt_dt(item.get("dt")),
                "open": round(_num(item.get("open")), 4),
                "high": round(_num(item.get("high")), 4),
                "low": round(_num(item.get("low")), 4),
                "close": round(_num(item.get("close")), 4),
                "volume": _num(item.get("vol")),
                "amount": _num(item.get("amount")),
                "bar_status": status,
                "source": "tdx_quote_aggregation",
            }
        )
    return result


def _single_resampled_row(row: dict[str, Any], target_freq: str) -> dict[str, Any]:
    dt = pd.to_datetime(row.get("date")).to_pydatetime()
    end_dt = freq_end_time(dt, target_freq, market="A股")
    return {
        "symbol": row.get("symbol") or "",
        "freq": target_freq.replace("分钟", ""),
        "date": _fmt_dt(end_dt),
        "open": round(_num(row.get("open")), 4),
        "high": round(_num(row.get("high")), 4),
        "low": round(_num(row.get("low")), 4),
        "close": round(_num(row.get("close")), 4),
        "volume": _num(row.get("volume")),
        "amount": _num(row.get("amount")),
        "bar_status": row.get("bar_status") or "FORMING",
        "source": "tdx_quote_aggregation",
    }


def _single_row_is_unfinished(row: dict[str, Any], target_freq: str) -> bool:
    dt = pd.to_datetime(row.get("date")).to_pydatetime()
    end_dt = freq_end_time(dt, target_freq, market="A股")
    return row.get("bar_status") == "FORMING" or dt < end_dt


def _merge_rows(history: list[dict[str, Any]], today: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [_normalize_history_row(row) for row in history if _num(row.get("close")) > 0]
    today_dates = {row.get("date") for row in today}
    rows = [row for row in rows if row.get("date") not in today_dates]
    return sorted([*rows, *today], key=lambda item: item.get("date") or "")[-260:]


def _level_payload(
    level: str,
    intraday_rows: list[dict[str, Any]],
    closed_rows: list[dict[str, Any]],
    preview_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    last = preview_rows[-1] if preview_rows else {}
    last_closed = next((row for row in reversed(preview_rows) if row.get("bar_status") != "FORMING"), {})
    return {
        "level": level,
        "bar_count": len(preview_rows),
        "intraday_bar_count": len(intraday_rows),
        "last_bar_at": last.get("date") or "",
        "last_bar_status": last.get("bar_status") or "",
        "last_closed_bar_at": last_closed.get("date") or "",
        "last_close": _num(last.get("close")),
        "macd_closed_only": _macd_payload(closed_rows, "closed_only"),
        "macd_with_forming": _macd_payload(preview_rows, "with_forming"),
    }


def _macd_payload(rows: list[dict[str, Any]], basis: str) -> dict[str, Any]:
    dynamics = hydrate_dynamics(rows)
    return {
        "basis": basis,
        "bar_count": len(rows),
        "status": dynamics.get("status") or "ok",
        "macd_state": dynamics.get("macd_state") or "unknown",
        "macd_momentum": dynamics.get("macd_momentum") or "unknown",
        "macd_zero_axis_tightness": dynamics.get("macd_zero_axis_tightness"),
        "volume_state": dynamics.get("volume_state") or "unknown",
        "volume_ratio_5_20": dynamics.get("volume_ratio_5_20"),
        "ma_posture": dynamics.get("ma_posture") or "unknown",
        "atr_volatility": dynamics.get("atr_volatility") or "unknown",
    }


def _coverage(rows: list[dict[str, Any]], as_of: str) -> dict[str, Any]:
    start = rows[0]["date"] if rows else ""
    count = len(rows)
    starts_at_open = bool(start and start[11:16] <= "09:31")
    return {
        "start": start,
        "as_of": as_of,
        "bar_count_1m": count,
        "quality": "full" if starts_at_open and count >= 120 else ("partial" if count else "none"),
        "missing_open_session": not starts_at_open,
    }


def _quote_payload(quote: dict | None) -> dict[str, Any]:
    if not quote:
        return {}
    return {
        "price": _num(quote.get("price")),
        "quote_time": quote.get("quote_time") or "",
        "trade_datetime": quote.get("trade_datetime") or "",
        "source": quote.get("source") or "",
        "change_pct": quote.get("change_pct"),
    }


def _observation_source(quote: dict | None) -> str:
    source = str((quote or {}).get("source") or "")
    if source.startswith("tdx"):
        return "tdx_quote_aggregation"
    if source:
        return f"{source}_aggregation"
    return "quote_aggregation"


def _normalize_history_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": row.get("symbol") or "",
        "freq": row.get("freq") or "",
        "date": row.get("date") or row.get("time") or "",
        "open": _num(row.get("open")),
        "high": _num(row.get("high")),
        "low": _num(row.get("low")),
        "close": _num(row.get("close")),
        "volume": _num(row.get("volume") or row.get("vol")),
        "amount": _num(row.get("amount")),
        "bar_status": row.get("bar_status") or "CLOSED",
        "source": row.get("source") or "history",
    }


def _quote_datetime(quote: dict | None) -> str:
    if not quote:
        return ""
    return str(quote.get("trade_datetime") or "").strip()


def _quote_minute(quote: dict[str, Any]) -> str:
    trade_datetime = _quote_datetime(quote)
    if len(trade_datetime) >= 16:
        minute = f"{trade_datetime[:16]}:00"
        return minute if _is_a_share_trading_minute(minute) else ""
    return ""


def _is_a_share_trading_minute(value: str) -> bool:
    """Return True only for regular A-share trading minutes."""
    if len(value) < 16:
        return False
    hm = value[11:16]
    return "09:30" <= hm <= "11:30" or "13:00" <= hm <= "15:00"


def _fmt_dt(value: object) -> str:
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value or "")
    return text[:19] if len(text) >= 19 else text


def _num(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
