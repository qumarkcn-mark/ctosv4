"""QMT bridge symbol and period normalization."""

from server.domain.symbols import normalize_symbol, parse_symbol


_PERIOD_TO_QMT = {
    "1": "1m",
    "1m": "1m",
    "m1": "1m",
    "5": "5m",
    "5m": "5m",
    "m5": "5m",
    "15": "15m",
    "15m": "15m",
    "m15": "15m",
    "30": "30m",
    "30m": "30m",
    "m30": "30m",
    "60": "1h",
    "60m": "1h",
    "m60": "1h",
    "1h": "1h",
    "day": "1d",
    "d": "1d",
    "1d": "1d",
}

_PERIOD_TO_CTOS = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "1d": "day",
}


def to_qmt_symbol(raw_symbol: str) -> str:
    """Convert CT-OS canonical/compact/plain symbol to QMT format."""
    parsed = parse_symbol(raw_symbol)
    return f"{parsed.code}.{parsed.market.upper()}"


def from_qmt_symbol(raw_symbol: str) -> str:
    """Convert QMT symbol format to CT-OS canonical format."""
    value = str(raw_symbol or "").strip().upper()
    if "." not in value:
        return normalize_symbol(value)
    code, market = value.split(".", 1)
    market = market.lower()
    if market not in {"sh", "sz"}:
        raise ValueError(f"unsupported QMT market: {raw_symbol}")
    return normalize_symbol(f"{market}.{code}")


def normalize_period(period: str) -> str:
    """Convert CT-OS period input to QMT period name."""
    value = str(period or "").strip().lower()
    if value not in _PERIOD_TO_QMT:
        raise ValueError(f"unsupported QMT period: {period}")
    return _PERIOD_TO_QMT[value]


def to_ctos_freq(period: str) -> str:
    """Convert QMT period name to CT-OS kline freq."""
    qmt_period = normalize_period(period)
    return _PERIOD_TO_CTOS[qmt_period]

