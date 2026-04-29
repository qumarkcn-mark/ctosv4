"""Read-only market data providers for the QMT bridge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from qmt_bridge.symbols import from_qmt_symbol, normalize_period, to_ctos_freq, to_qmt_symbol

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class MarketDataProvider(Protocol):
    """Minimal read-only provider contract used by the bridge API."""

    def health(self) -> dict:
        ...

    def subscribe(self, symbols: list[str], periods: list[str]) -> dict:
        ...

    def quotes(self, symbols: list[str]) -> list[dict]:
        ...

    def klines(self, symbol: str, period: str, limit: int = 240) -> list[dict]:
        ...


@dataclass
class FakeMarketDataProvider:
    """Deterministic provider for local development and tests."""

    price: float = 100.0

    def health(self) -> dict:
        return {
            "status": "ok",
            "provider": "fake",
            "read_only": True,
            "checked_at": _now_iso(),
        }

    def subscribe(self, symbols: list[str], periods: list[str]) -> dict:
        canonical_symbols = [from_qmt_symbol(to_qmt_symbol(symbol)) for symbol in symbols]
        qmt_periods = [normalize_period(period) for period in periods]
        return {
            "status": "ok",
            "provider": "fake",
            "symbols": canonical_symbols,
            "periods": qmt_periods,
        }

    def quotes(self, symbols: list[str]) -> list[dict]:
        rows = []
        for index, symbol in enumerate(symbols):
            canonical = from_qmt_symbol(to_qmt_symbol(symbol))
            price = round(self.price + index, 2)
            rows.append(
                {
                    "symbol": canonical,
                    "price": price,
                    "bid1": round(price - 0.01, 2),
                    "ask1": round(price + 0.01, 2),
                    "volume": 0,
                    "amount": 0,
                    "quote_time": _now_iso(),
                    "source": "qmt_fake",
                }
            )
        return rows

    def klines(self, symbol: str, period: str, limit: int = 240) -> list[dict]:
        canonical = from_qmt_symbol(to_qmt_symbol(symbol))
        qmt_period = normalize_period(period)
        freq = to_ctos_freq(qmt_period)
        count = max(1, min(int(limit), 500))
        start = datetime(2026, 4, 28, 9, 35, tzinfo=SHANGHAI_TZ)
        step = _period_delta(qmt_period)
        rows = []
        for idx in range(count):
            base = self.price + idx * 0.1
            bar_time = start + step * idx
            rows.append(
                {
                    "symbol": canonical,
                    "freq": freq,
                    "date": bar_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": round(base, 2),
                    "high": round(base + 0.2, 2),
                    "low": round(base - 0.2, 2),
                    "close": round(base + 0.05, 2),
                    "volume": 0,
                    "amount": 0,
                    "adjustflag": "3",
                    "bar_status": "CLOSED",
                    "source": "qmt_fake",
                    "received_at": _now_iso(),
                }
            )
        return rows


class XtDataProvider:
    """Thin wrapper around QMT xtquant.xtdata.

    The import is intentionally lazy so this package can be tested on non-Windows
    developer machines where QMT is unavailable.
    """

    def __init__(self):
        from xtquant import xtdata  # type: ignore

        self.xtdata = xtdata

    def health(self) -> dict:
        return {
            "status": "ok",
            "provider": "xtdata",
            "read_only": True,
            "checked_at": _now_iso(),
        }

    def subscribe(self, symbols: list[str], periods: list[str]) -> dict:
        qmt_symbols = [to_qmt_symbol(symbol) for symbol in symbols]
        qmt_periods = [normalize_period(period) for period in periods]
        for symbol in qmt_symbols:
            for period in qmt_periods:
                self.xtdata.subscribe_quote(symbol, period=period, count=-1)
        return {
            "status": "ok",
            "provider": "xtdata",
            "symbols": [from_qmt_symbol(symbol) for symbol in qmt_symbols],
            "periods": qmt_periods,
        }

    def quotes(self, symbols: list[str]) -> list[dict]:
        qmt_symbols = [to_qmt_symbol(symbol) for symbol in symbols]
        ticks = self.xtdata.get_full_tick(qmt_symbols)
        rows = []
        for qmt_symbol in qmt_symbols:
            item = ticks.get(qmt_symbol) or {}
            price = _float_first(item, ("lastPrice", "last_price", "price"))
            rows.append(
                {
                    "symbol": from_qmt_symbol(qmt_symbol),
                    "price": price,
                    "bid1": _float_first(item, ("bidPrice", "bid1", "bidPrice1")),
                    "ask1": _float_first(item, ("askPrice", "ask1", "askPrice1")),
                    "volume": _float_first(item, ("volume", "vol")),
                    "amount": _float_first(item, ("amount",)),
                    "quote_time": _now_iso(),
                    "source": "qmt",
                }
            )
        return rows

    def klines(self, symbol: str, period: str, limit: int = 240) -> list[dict]:
        qmt_symbol = to_qmt_symbol(symbol)
        qmt_period = normalize_period(period)
        freq = to_ctos_freq(qmt_period)
        raw = self.xtdata.get_market_data_ex(
            field_list=["time", "open", "high", "low", "close", "volume", "amount"],
            stock_list=[qmt_symbol],
            period=qmt_period,
            count=max(1, min(int(limit), 5000)),
        )
        frame = raw.get(qmt_symbol)
        if frame is None:
            return []
        rows = []
        for record in frame.to_dict("records"):
            rows.append(
                {
                    "symbol": from_qmt_symbol(qmt_symbol),
                    "freq": freq,
                    "date": _normalize_qmt_time(record.get("time")),
                    "open": float(record.get("open") or 0),
                    "high": float(record.get("high") or 0),
                    "low": float(record.get("low") or 0),
                    "close": float(record.get("close") or 0),
                    "volume": float(record.get("volume") or 0),
                    "amount": float(record.get("amount") or 0),
                    "adjustflag": "3",
                    "bar_status": "CLOSED",
                    "source": "qmt",
                    "received_at": _now_iso(),
                }
            )
        return rows


def provider_from_env(provider_name: str | None = None) -> MarketDataProvider:
    """Build provider from an explicit name or QMT_BRIDGE_PROVIDER."""
    import os

    name = (provider_name or os.getenv("QMT_BRIDGE_PROVIDER") or "fake").strip().lower()
    if name == "xtdata":
        return XtDataProvider()
    if name == "fake":
        return FakeMarketDataProvider()
    raise ValueError(f"unsupported QMT bridge provider: {name}")


def _float_first(item: dict, keys: tuple[str, ...]) -> float:
    for key in keys:
        value = item.get(key)
        if isinstance(value, (list, tuple)) and value:
            value = value[0]
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _normalize_qmt_time(value) -> str:
    if value is None:
        return _now_iso().replace("T", " ")[:19]
    text = str(value)
    if text.isdigit() and len(text) >= 13:
        dt = datetime.fromtimestamp(int(text[:13]) / 1000, tz=SHANGHAI_TZ)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    if text.isdigit() and len(text) == 8:
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text.replace("T", " ")[:19]


def _period_delta(qmt_period: str) -> timedelta:
    if qmt_period.endswith("m"):
        return timedelta(minutes=int(qmt_period[:-1]))
    if qmt_period == "1h":
        return timedelta(hours=1)
    return timedelta(days=1)


def _now_iso() -> str:
    return datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds")
