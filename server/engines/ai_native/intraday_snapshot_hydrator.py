"""Hydrate today's 1-minute intraday facts for AI coaching.

This layer is read-only preview data. It validates or challenges the previous
plan, but it must not write into formal CZSC snapshots.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from server.db.kline_lake import query_klines
from server.domain.symbols import normalize_symbol, to_tencent_symbol
from server.engines.ai_native.dynamics_hydrator import hydrate_dynamics
from server.services.tdx_daily_sync_service import read_tdx_day_klines
from server.services.tdx_minute_service import read_tdx_1m_klines


def hydrate_intraday_snapshot(
    symbol: str,
    *,
    trade_date: str | None = None,
    limit: int = 360,
    include_recent_bars: bool = True,
    recent_bar_count: int = 80,
) -> dict[str, Any]:
    """Return a compact 1m preview snapshot for intraday reasoning."""
    canonical = normalize_symbol(symbol)
    target_date, date_basis = _resolve_target_date(canonical, trade_date)
    rows = [
        _normalize_1m_row(row, canonical)
        for row in _read_1m_rows_for_date(canonical, target_date, limit=max(int(limit), 240))
    ]
    rows = [
        row
        for row in rows
        if str(row.get("date") or "").startswith(target_date)
        and _is_a_share_trading_minute(str(row.get("date") or ""))
        and _num(row.get("close")) > 0
    ]
    if not rows:
        return {
            "version": "intraday_snapshot.v1",
            "available": False,
            "symbol": canonical,
            "source": "tdx_local_1m_preview",
            "usage": "validate_previous_plan",
            "date": target_date,
            "date_basis": date_basis,
            "reason": "NO_TDX_1M_TODAY",
            "risk_boundary": "盘中快照仅用于验证预案，不是正式 CZSC 结构。",
        }

    rows = sorted(rows, key=lambda item: item.get("date") or "")
    prev_close = _previous_close(canonical, target_date)
    price = _price_payload(rows, prev_close)
    path_facts = _path_facts(rows)
    macd = _macd_payload(rows)
    result = {
        "version": "intraday_snapshot.v1",
        "available": True,
        "symbol": canonical,
        "source": "tdx_local_1m_preview",
        "usage": "validate_previous_plan",
        "date": target_date,
        "date_basis": date_basis,
        "coverage": _coverage(rows),
        "price": price,
        "path_facts": path_facts,
        "macd_1m": macd,
        "relation_to_previous_plan": {
            "status": "ai_should_judge",
            "note": "程序只提供盘中事实，不硬编码确认/否定/等待结论。",
        },
        "risk_boundary": "盘中快照仅用于验证预案，不是正式 CZSC 结构。",
    }
    if include_recent_bars:
        result["recent_1m_bars"] = [_compact_bar(row) for row in rows[-max(1, min(int(recent_bar_count), 120)):]]
    return result


def _resolve_target_date(symbol: str, trade_date: str | None) -> tuple[str, str]:
    if trade_date:
        return trade_date[:10], "requested_trade_date"
    latest = _latest_available_1m_date(symbol)
    if latest:
        return latest, "latest_available_tdx_1m"
    return datetime.now().strftime("%Y-%m-%d"), "calendar_today"


def _latest_available_1m_date(symbol: str) -> str:
    for row in read_tdx_1m_klines(symbol, limit=1):
        date = str(row.get("date") or "")[:10]
        if date:
            return date
    for adjustflag in ("2", "3"):
        try:
            rows = query_klines(symbol, "1", limit=1, adjustflag=adjustflag, source="tdx")
        except Exception:
            rows = []
        if rows:
            date = str(rows[-1].get("date") or "")[:10]
            if date:
                return date
    return ""


def _read_1m_rows_for_date(symbol: str, target_date: str, *, limit: int) -> list[dict[str, Any]]:
    rows = read_tdx_1m_klines(
        symbol,
        limit=limit,
        start_date=target_date,
        end_date=f"{target_date} 15:00:00",
    )
    if rows:
        return rows
    for adjustflag in ("2", "3"):
        try:
            rows = query_klines(
                symbol,
                "1",
                start_date=target_date,
                end_date=f"{target_date} 15:00:00",
                limit=limit,
                adjustflag=adjustflag,
                source="tdx",
            )
        except Exception:
            rows = []
        if rows:
            return [dict(row, adjustflag=adjustflag, source=f"tdx_lake_1m_{adjustflag}") for row in rows]
    return []


def _normalize_1m_row(row: dict[str, Any], symbol: str) -> dict[str, Any]:
    return {
        "symbol": row.get("symbol") or to_tencent_symbol(symbol),
        "freq": "1",
        "date": str(row.get("date") or ""),
        "open": _num(row.get("open")),
        "high": _num(row.get("high")),
        "low": _num(row.get("low")),
        "close": _num(row.get("close")),
        "volume": _num(row.get("volume") or row.get("vol")),
        "amount": _num(row.get("amount")),
        "adjustflag": str(row.get("adjustflag") or "3"),
        "bar_status": str(row.get("bar_status") or "CLOSED"),
        "source": str(row.get("source") or "tdx_local_1m"),
    }


def _previous_close(symbol: str, target_date: str) -> float:
    rows = read_tdx_day_klines(symbol, limit=12)
    previous = [
        row
        for row in rows
        if str(row.get("date") or "")[:10] < target_date and _num(row.get("close")) > 0
    ]
    return _num(previous[-1].get("close")) if previous else 0.0


def _coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "quality": "complete_from_open" if len(rows) >= 238 else "partial",
        "bar_count": len(rows),
        "first_bar": rows[0].get("date") or "",
        "last_bar": rows[-1].get("date") or "",
        "has_forming_bar": any(str(row.get("bar_status") or "").upper() == "FORMING" for row in rows),
    }


def _price_payload(rows: list[dict[str, Any]], prev_close: float) -> dict[str, Any]:
    open_price = _num(rows[0].get("open"))
    close = _num(rows[-1].get("close"))
    high = max(_num(row.get("high")) for row in rows)
    low = min(_num(row.get("low")) for row in rows)
    return {
        "prev_close": round(prev_close, 4),
        "open": round(open_price, 4),
        "close": round(close, 4),
        "high": round(high, 4),
        "low": round(low, 4),
        "gap_pct": _pct(open_price, prev_close),
        "day_pct": _pct(close, prev_close),
        "range_pct": round((high - low) / prev_close * 100, 3) if prev_close > 0 else 0.0,
    }


def _path_facts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    open_price = _num(rows[0].get("open"))
    close = _num(rows[-1].get("close"))
    high_index = max(range(len(rows)), key=lambda idx: _num(rows[idx].get("high")))
    low_index = min(range(len(rows)), key=lambda idx: _num(rows[idx].get("low")))
    high = _num(rows[high_index].get("high"))
    low = _num(rows[low_index].get("low"))
    first30 = rows[: min(30, len(rows))]
    last30 = rows[-min(30, len(rows)) :]
    first30_close = _num(first30[-1].get("close")) if first30 else close
    return {
        "open_behavior": _open_behavior(open_price, close, first30_close),
        "micro_path": _micro_path(rows, high_index, low_index, high, low, close),
        "day_high_time": _time_part(rows[high_index].get("date")),
        "day_low_time": _time_part(rows[low_index].get("date")),
        "first30": _window_payload(first30),
        "last30": _window_payload(last30),
        "volume_read": _volume_read(first30, last30),
    }


def _open_behavior(open_price: float, close: float, first30_close: float) -> str:
    if open_price <= 0:
        return "unknown"
    first30_change = _pct(first30_close, open_price)
    day_change_from_open = _pct(close, open_price)
    if first30_change <= -1.0 and day_change_from_open > 0:
        return "低开或开盘承压后拉回"
    if first30_change <= -1.0:
        return "开盘后承压下杀"
    if first30_change >= 1.0 and day_change_from_open >= 0:
        return "开盘后维持强势"
    if first30_change >= 1.0 and day_change_from_open < 0:
        return "开盘冲高回落"
    if day_change_from_open >= 1.0:
        return "盘中震荡后走强"
    if day_change_from_open <= -1.0:
        return "盘中震荡后走弱"
    return "平开震荡"


def _micro_path(
    rows: list[dict[str, Any]],
    high_index: int,
    low_index: int,
    high: float,
    low: float,
    close: float,
) -> str:
    if high <= low:
        return "日内窄幅震荡"
    position = (close - low) / max(high - low, 1e-9)
    if low_index < high_index and position >= 0.55:
        return "先探低后回拉，收盘靠近日内上半区"
    if high_index < low_index and position <= 0.45:
        return "先冲高后回落，收盘靠近日内下半区"
    recent = rows[-min(60, len(rows)) :]
    recent_high = max(_num(row.get("high")) for row in recent)
    recent_low = min(_num(row.get("low")) for row in recent)
    if close >= recent_high * 0.995:
        return "尾盘接近最近60分钟高位"
    if close <= recent_low * 1.005:
        return "尾盘接近最近60分钟低位"
    return "日内区间震荡"


def _window_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"high": 0, "low": 0, "close": 0, "volume": 0}
    return {
        "high": round(max(_num(row.get("high")) for row in rows), 4),
        "low": round(min(_num(row.get("low")) for row in rows), 4),
        "close": round(_num(rows[-1].get("close")), 4),
        "volume": round(sum(_num(row.get("volume")) for row in rows), 2),
    }


def _volume_read(first30: list[dict[str, Any]], last30: list[dict[str, Any]]) -> str:
    first_volume = sum(_num(row.get("volume")) for row in first30)
    last_volume = sum(_num(row.get("volume")) for row in last30)
    if first_volume <= 0 or last_volume <= 0:
        return "unknown"
    if last_volume > first_volume * 1.2:
        return "尾盘放量"
    if first_volume > last_volume * 1.2:
        return "早盘放量"
    return "量能均衡"


def _macd_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    dynamics = hydrate_dynamics(rows)
    closes = [_num(row.get("close")) for row in rows if _num(row.get("close")) > 0]
    raw = _raw_macd(closes)
    return {
        "basis": "closed_1m",
        "bar_count": len(rows),
        "state": dynamics.get("macd_state") or "unknown",
        "momentum": dynamics.get("macd_momentum") or "unknown",
        "zero_axis": "above" if _num(raw.get("dif")) > 0 else "below",
        "dif": raw.get("dif", 0),
        "dea": raw.get("dea", 0),
        "hist": raw.get("hist", 0),
        "hist_prev": raw.get("hist_prev", 0),
        "hist_direction": raw.get("hist_direction", "unknown"),
        "cross": raw.get("cross", "none"),
    }


def _raw_macd(closes: list[float]) -> dict[str, Any]:
    if len(closes) < 35:
        return {}
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = [fast - slow for fast, slow in zip(ema12, ema26)]
    dea = _ema(dif, 9)
    hist = [(d - m) * 2 for d, m in zip(dif, dea)]
    cross = "none"
    if len(dif) >= 2:
        if dif[-2] <= dea[-2] and dif[-1] > dea[-1]:
            cross = "golden_cross"
        elif dif[-2] >= dea[-2] and dif[-1] < dea[-1]:
            cross = "death_cross"
    return {
        "dif": round(dif[-1], 4),
        "dea": round(dea[-1], 4),
        "hist": round(hist[-1], 4),
        "hist_prev": round(hist[-2], 4),
        "hist_direction": "expanding" if abs(hist[-1]) > abs(hist[-2]) else "contracting",
        "cross": cross,
    }


def _ema(values: list[float], span: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (span + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def _compact_bar(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "t": _time_part(row.get("date")),
        "o": round(_num(row.get("open")), 4),
        "h": round(_num(row.get("high")), 4),
        "l": round(_num(row.get("low")), 4),
        "c": round(_num(row.get("close")), 4),
        "v": round(_num(row.get("volume")), 2),
    }


def _time_part(value: Any) -> str:
    text = str(value or "")
    return text[11:16] if len(text) >= 16 else text


def _is_a_share_trading_minute(value: str) -> bool:
    if len(value) < 16:
        return False
    hhmm = value[11:16]
    return ("09:31" <= hhmm <= "11:30") or ("13:01" <= hhmm <= "15:00")


def _pct(value: float, base: float) -> float:
    return round((value - base) / base * 100, 3) if base > 0 else 0.0


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
