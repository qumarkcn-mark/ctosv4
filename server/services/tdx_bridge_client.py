"""Read-only client for the Windows TDX bridge.

The bridge is an optional realtime/display source. It must never become the
only market data path: callers keep their existing fallback behavior when this
client returns no data.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import httpx

from server.config import TDX_BRIDGE_TIMEOUT, TDX_BRIDGE_URL
from server.domain.symbols import parse_symbol, to_tencent_symbol

logger = logging.getLogger(__name__)
_LIVE_1M_BARS: dict[tuple[str, str], dict] = {}


def is_tdx_bridge_enabled() -> bool:
    return bool(TDX_BRIDGE_URL)


def to_tdx_symbol(symbol: str) -> str:
    """Convert any CT-OS symbol format to TDX bridge format, e.g. 600519.SH."""
    parsed = parse_symbol(symbol)
    return f"{parsed.code}.{parsed.market.upper()}"


def from_tdx_symbol(symbol: str) -> str:
    """Convert TDX bridge format to CT-OS compact quote key, e.g. sh600519."""
    text = str(symbol or "").strip().upper()
    if "." in text:
        code, market = text.split(".", 1)
        if market in {"SH", "SZ"} and len(code) == 6:
            return f"{market.lower()}{code}"
    return to_tencent_symbol(text)


async def fetch_tdx_quotes(symbols: list[str]) -> dict[str, dict]:
    """Fetch realtime quotes from the optional TDX bridge.

    Returns keys in CT-OS compact Tencent style (`sh600519`) to match the
    existing `price_service.get_batch_prices` contract.
    """
    if not is_tdx_bridge_enabled() or not symbols:
        return {}

    bridge_symbols = []
    for symbol in symbols:
        try:
            bridge_symbols.append(to_tdx_symbol(symbol))
        except ValueError:
            logger.debug("跳过无法归一的 TDX 行情代码: %s", symbol)

    if not bridge_symbols:
        return {}

    try:
        async with httpx.AsyncClient(timeout=TDX_BRIDGE_TIMEOUT) as client:
            response = await client.get(
                f"{TDX_BRIDGE_URL}/quotes",
                params={"symbols": ",".join(bridge_symbols)},
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.debug("TDX bridge 行情查询失败: %s", exc)
        return {}

    results: dict[str, dict] = {}
    for raw_symbol, item in (payload or {}).items():
        normalized = _normalize_quote(raw_symbol, item)
        if not normalized:
            continue
        compact = normalized["symbol"]
        results[compact] = normalized
    return results


async def fetch_tdx_quote(symbol: str) -> Optional[dict]:
    quotes = await fetch_tdx_quotes([symbol])
    compact = to_tencent_symbol(symbol)
    return quotes.get(compact)


async def fetch_tdx_klines(
    symbol: str,
    period: str = "1m",
    count: int = 1200,
    dividend_type: str = "front",
    refresh: bool = False,
) -> list[dict]:
    """Fetch K lines from the optional TDX bridge `/kline` endpoint.

    The Windows bridge exposes TDX local minute cache as a columnar payload on
    `/kline`. Older local bridges used `/klines`; keep that as a compatibility
    fallback while CT-OS migrates.
    """
    if not is_tdx_bridge_enabled():
        return []
    bridge_symbol = to_tdx_symbol(symbol)
    params = {
        "symbol": bridge_symbol,
        "period": period,
        "count": int(count),
        "dividend_type": dividend_type,
    }
    if refresh:
        params["refresh"] = "1"
    try:
        async with httpx.AsyncClient(timeout=TDX_BRIDGE_TIMEOUT) as client:
            response = await client.get(f"{TDX_BRIDGE_URL}/kline", params=params)
            if response.status_code == 404:
                response = await client.get(f"{TDX_BRIDGE_URL}/klines", params=params)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.debug("TDX bridge K线查询失败 %s/%s: %s", symbol, period, exc)
        return []

    rows = _columnar_klines_to_rows(payload, bridge_symbol) if isinstance(payload, dict) else payload
    if isinstance(rows, dict):
        rows = rows.get("klines")
    if not isinstance(rows, list):
        return []
    normalized_rows = []
    for row in rows:
        normalized = _normalize_kline(symbol, row, period)
        if normalized:
            normalized_rows.append(normalized)
    return normalized_rows


def append_live_quote_1m_bar(rows: list[dict], quote: Optional[dict], symbol: str, count: int) -> list[dict]:
    """Append/update the current forming 1m bar from realtime TDX quote snapshots."""
    if not quote:
        return rows[-count:]
    price = _num(quote.get("price"))
    minute = _quote_minute(quote)
    if price <= 0 or not minute:
        return rows[-count:]

    normalized = list(rows)
    now_volume = int(_num(quote.get("now_volume") or quote.get("nowVolume")))
    compact = to_tencent_symbol(symbol)
    seed = _LIVE_1M_BARS.get((compact, minute))
    if normalized and normalized[-1].get("date") == minute:
        previous = normalized[-1]
        seed = {
            "open": _num(seed.get("open")) if seed else _num(previous.get("open")),
            "high": max(_num(seed.get("high")) if seed else 0, _num(previous.get("high"))),
            "low": min(
                _num(seed.get("low")) if seed and _num(seed.get("low")) else price,
                _num(previous.get("low")) or price,
            ),
            "volume": max(_num(seed.get("volume")) if seed else 0, _num(previous.get("volume"))),
        }
    live_bar = {
        "symbol": compact,
        "freq": "1",
        "date": minute,
        "open": round(_num(seed.get("open")) if seed else price, 4),
        "high": round(max(_num(seed.get("high")) if seed else price, price), 4),
        "low": round(min(_num(seed.get("low")) if seed and _num(seed.get("low")) else price, price), 4),
        "close": round(price, 4),
        "volume": max(_num(seed.get("volume")) if seed else 0, float(now_volume)),
        "amount": 0.0,
        "adjustflag": "2",
        "bar_status": "FORMING",
        "source": "tdx_live_quote_1m",
    }
    _LIVE_1M_BARS[(compact, minute)] = live_bar
    _prune_live_1m_bars(compact, minute)

    if normalized and normalized[-1].get("date") == minute:
        normalized[-1] = live_bar
    elif not normalized or str(normalized[-1].get("date") or "") < minute:
        normalized.append(live_bar)
    return normalized[-count:]


def _prune_live_1m_bars(compact: str, current_minute: str) -> None:
    stale_keys = [
        key
        for key in _LIVE_1M_BARS
        if key[0] == compact and key[1] != current_minute
    ]
    for key in stale_keys:
        _LIVE_1M_BARS.pop(key, None)


def _normalize_quote(raw_symbol: str, item: object) -> Optional[dict]:
    if not isinstance(item, dict):
        return None
    try:
        compact = from_tdx_symbol(item.get("symbol") or raw_symbol)
    except ValueError:
        return None
    price = _num(item.get("price"))
    prev_close = _num(item.get("preClose") or item.get("prev_close"))
    if price <= 0:
        return None
    return {
        "symbol": compact,
        "name": item.get("name") or compact,
        "price": price,
        "change": round(price - prev_close, 2) if prev_close else 0,
        "change_pct": round((price - prev_close) / prev_close * 100, 2) if prev_close else 0,
        "volume": int(_num(item.get("volume"))),
        "high": _num(item.get("high")),
        "low": _num(item.get("low")),
        "open": _num(item.get("open")),
        "prev_close": prev_close,
        "quote_time": _format_trade_time(item.get("tradeTime")),
        "trade_datetime": _format_kline_time(item.get("tradeTime")),
        "source": item.get("source") or "tdx_bridge",
        "average": _num(item.get("average")),
        "amount": _num(item.get("amount")),
        "now_volume": int(_num(item.get("nowVolume"))),
        "inside": int(_num(item.get("inside"))),
        "outside": int(_num(item.get("outside"))),
        "bid_price": item.get("bidPrice") or [],
        "ask_price": item.get("askPrice") or [],
        "bid_volume": item.get("bidVolume") or [],
        "ask_volume": item.get("askVolume") or [],
        "received_at": item.get("receivedAt"),
    }


def _normalize_kline(symbol: str, row: object, period: str) -> Optional[dict]:
    if not isinstance(row, dict):
        return None
    close = _num(row.get("close") or row.get("Close") or row.get("price"))
    open_ = _num(row.get("open") or row.get("Open") or close)
    high = _num(row.get("high") or row.get("High") or max(open_, close))
    low = _num(row.get("low") or row.get("Low") or min(open_, close))
    date_value = row.get("date") or row.get("time") or row.get("datetime") or row.get("timestamp")
    if not date_value or close <= 0 or open_ <= 0 or high <= 0 or low <= 0:
        return None
    freq = _period_to_freq(period)
    date_text = _format_kline_time(date_value)
    if freq in {"day", "week"}:
        date_text = date_text[:10]
    return {
        "symbol": to_tencent_symbol(symbol),
        "freq": freq,
        "date": date_text,
        "open": round(open_, 4),
        "high": round(high, 4),
        "low": round(low, 4),
        "close": round(close, 4),
        "volume": _num(row.get("volume") or row.get("Volume")),
        "amount": _num(row.get("amount") or row.get("Amount")),
        "adjustflag": "2",
        "bar_status": row.get("bar_status") or row.get("barStatus") or "CLOSED",
        "source": row.get("source") or "tdx_bridge",
    }


def _columnar_klines_to_rows(payload: dict, bridge_symbol: str) -> list[dict]:
    fields = {
        "open": payload.get("Open"),
        "high": payload.get("High"),
        "low": payload.get("Low"),
        "close": payload.get("Close"),
        "volume": payload.get("Volume"),
        "amount": payload.get("Amount"),
    }
    close_rows = fields["close"]
    if not isinstance(close_rows, list):
        return []

    by_time: dict[str, dict] = {}
    for name, values in fields.items():
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            timestamp = item.get("time")
            if not timestamp:
                continue
            row = by_time.setdefault(str(timestamp), {"time": timestamp})
            row[name] = item.get(bridge_symbol)

    return [by_time[key] for key in sorted(by_time)]


def _period_to_freq(period: str) -> str:
    aliases = {
        "1m": "1",
        "m1": "1",
        "5m": "5",
        "m5": "5",
        "15m": "15",
        "m15": "15",
        "30m": "30",
        "m30": "30",
        "1h": "60",
        "60m": "60",
        "m60": "60",
        "1d": "day",
        "day": "day",
        "d": "day",
        "1w": "week",
        "week": "week",
        "w": "week",
    }
    return aliases.get(str(period or "").lower(), str(period or ""))


def _format_trade_time(value: object) -> str:
    text = str(value or "").strip()
    if len(text) == 14 and text.isdigit():
        return f"{text[8:10]}:{text[10:12]}:{text[12:14]}"
    return text


def _format_kline_time(value: object) -> str:
    text = str(value or "").strip()
    if len(text) == 14 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]} {text[8:10]}:{text[10:12]}:{text[12:14]}"
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _quote_minute(quote: dict) -> str:
    trade_datetime = str(quote.get("trade_datetime") or "").strip()
    if len(trade_datetime) >= 16:
        minute = f"{trade_datetime[:16]}:00"
        return minute if _is_a_share_trading_minute(minute) else ""

    quote_time = str(quote.get("quote_time") or "").strip()
    if len(quote_time) >= 5:
        try:
            received = datetime.fromtimestamp(float(quote.get("received_at") or 0))
        except (TypeError, ValueError, OSError):
            received = datetime.now()
        minute = f"{received:%Y-%m-%d} {quote_time[:5]}:00"
        return minute if _is_a_share_trading_minute(minute) else ""
    return ""


def _is_a_share_trading_minute(value: str) -> bool:
    """Return True only for regular A-share trading minutes."""
    if len(value) < 16:
        return False
    hm = value[11:16]
    return "09:30" <= hm <= "11:30" or "13:00" <= hm <= "15:00"


def _num(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
