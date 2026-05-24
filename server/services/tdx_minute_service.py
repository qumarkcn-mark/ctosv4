"""Read-only TDX local minute kline reader.

TDX local files provide native `.lc1` 1-minute and `.lc5` 5-minute bars.
Post-market flows should prefer native bars, then derive only missing higher
periods from 5-minute bars.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
from czsc.py.bar_generator import resample_bars

from server.config import TDX_VIPDOC
from server.domain.symbols import parse_symbol

RECORD_SIZE = 32
RECORD_FMT = "<HHfffffII"  # date, minute, open/high/low/close, amount, volume, reserved
DEFAULT_VIPDOC_CANDIDATES = (
    TDX_VIPDOC,
    "/Users/markqu/Desktop/tdx_vipdoc_mount/vipdoc",
    "/Users/markqu/Desktop/tdx_vipdoc_mount",
    "/Volumes/tdx_vipdoc",
)
TDX_AGGREGATE_FREQ_LABELS = {
    "15": "15分钟",
    "30": "30分钟",
    "60": "60分钟",
}


@dataclass(frozen=True)
class TdxMinuteStatus:
    available: bool
    path: str
    reason: str = ""


def resolve_tdx_minute_vipdoc(vipdoc: Optional[str] = None) -> str:
    """Resolve the first readable TDX vipdoc path for minute files."""
    candidates = [vipdoc] if vipdoc else list(DEFAULT_VIPDOC_CANDIDATES)
    for candidate in candidates:
        if not candidate:
            continue
        root = Path(candidate)
        if _has_minute_shape(root):
            return str(root)
        nested = root / "vipdoc"
        if _has_minute_shape(nested):
            return str(nested)
    return str(Path(candidates[0] or TDX_VIPDOC))


def tdx_minute_file_path(symbol: str, vipdoc: Optional[str] = None, freq: str = "1") -> str:
    """Return expected TDX minute file path for a symbol."""
    parsed = parse_symbol(symbol)
    is_1m = str(freq).lower() in {"1", "1m", "m1"}
    extension = ".lc1" if is_1m else ".lc5"
    filename = f"{parsed.market}{parsed.code}{extension}"
    folder = "minline" if is_1m else "fzline"
    return str(Path(resolve_tdx_minute_vipdoc(vipdoc)) / parsed.market / folder / filename)


def tdx_minute_status(symbol: Optional[str] = None, vipdoc: Optional[str] = None) -> dict:
    """Return local TDX minute source status."""
    root = Path(resolve_tdx_minute_vipdoc(vipdoc))
    if not root.exists():
        return {
            "available": False,
            "provider": "tdx_local_minute",
            "vipdoc": str(root),
            "reason": "VIPDOC_NOT_FOUND",
        }
    if symbol:
        path = tdx_minute_file_path(symbol, vipdoc)
        if not Path(path).exists():
            return {
                "available": False,
                "provider": "tdx_local_minute",
                "vipdoc": str(root),
                "path": path,
                "reason": "MINUTE_FILE_NOT_FOUND",
            }
        try:
            if os.path.getsize(path) <= 0:
                return {
                    "available": False,
                    "provider": "tdx_local_minute",
                    "vipdoc": str(root),
                    "path": path,
                    "reason": "MINUTE_FILE_EMPTY",
                }
            with open(path, "rb") as file:
                file.read(1)
        except OSError as exc:
            return {
                "available": False,
                "provider": "tdx_local_minute",
                "vipdoc": str(root),
                "path": path,
                "reason": "MINUTE_FILE_READ_ERROR",
                "error": str(exc),
            }
        return {
            "available": True,
            "provider": "tdx_local_minute",
            "vipdoc": str(root),
            "path": path,
            "reason": "",
        }
    return {
        "available": True,
        "provider": "tdx_local_minute",
        "vipdoc": str(root),
        "reason": "",
    }


def _read_tdx_native_minute_klines(
    symbol: str,
    freq: str,
    limit: int = 240,
    vipdoc: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict]:
    """Read local TDX native `.lc1` or `.lc5` rows and return CT-OS K lines.

    `end_date` accepts `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`. Rows are ordered
    oldest to newest.
    """
    parsed = parse_symbol(symbol)
    normalized_freq = str(freq).lower().replace("m", "")
    if normalized_freq not in {"1", "5"}:
        return []
    path = tdx_minute_file_path(parsed.canonical, vipdoc, freq=normalized_freq)
    if not os.path.isfile(path):
        return []

    try:
        row_count = os.path.getsize(path) // RECORD_SIZE
    except OSError:
        return []
    if row_count <= 0:
        return []

    read_count = max(1, min(int(limit), 20000))
    tail_count = row_count if start_date else min(row_count, max(read_count * 3, read_count))
    try:
        rows = _read_minute_tail(path, tail_count, parsed.canonical, normalized_freq)
    except OSError:
        return []
    if start_date:
        rows = [row for row in rows if row["date"] >= start_date]
    if end_date:
        rows = [row for row in rows if row["date"] <= end_date]
    return rows[-read_count:]


def read_tdx_1m_klines(
    symbol: str,
    limit: int = 240,
    vipdoc: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict]:
    """Read local TDX native `.lc1` rows."""
    return _read_tdx_native_minute_klines(
        symbol,
        "1",
        limit=limit,
        vipdoc=vipdoc,
        start_date=start_date,
        end_date=end_date,
    )


def read_tdx_5m_klines(
    symbol: str,
    limit: int = 240,
    vipdoc: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict]:
    """Read local TDX native `.lc5` rows."""
    return _read_tdx_native_minute_klines(
        symbol,
        "5",
        limit=limit,
        vipdoc=vipdoc,
        start_date=start_date,
        end_date=end_date,
    )


def aggregate_tdx_minute_klines(rows: list[dict], target_freq: str, *, base_freq: str = "5") -> list[dict]:
    """Aggregate local TDX minute rows into CZSC-compatible higher minute bars."""
    freq = str(target_freq).lower().replace("m", "")
    target_label = TDX_AGGREGATE_FREQ_LABELS.get(freq)
    base = str(base_freq).lower().replace("m", "")
    base_label = "1分钟" if base == "1" else "5分钟"
    if not target_label or len(rows) < 2:
        return []

    normalized_rows = [
        row
        for row in rows
        if row.get("date")
        and _is_a_share_trading_minute(str(row.get("date")))
        and _num(row.get("close")) > 0
    ]
    if len(normalized_rows) < 2:
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
            for row in normalized_rows
        ]
    )
    sampled = resample_bars(
        df,
        target_label,
        raw_bars=False,
        base_freq=base_label,
        drop_unfinished=True,
    )
    result = []
    for item in sampled.to_dict("records"):
        result.append(
            {
                "symbol": str(item.get("symbol") or ""),
                "freq": freq,
                "date": _fmt_dt(item.get("dt")),
                "open": round(_num(item.get("open")), 4),
                "high": round(_num(item.get("high")), 4),
                "low": round(_num(item.get("low")), 4),
                "close": round(_num(item.get("close")), 4),
                "volume": _num(item.get("vol")),
                "amount": round(_num(item.get("amount")), 2),
                "adjustflag": "3",
                "bar_status": "CLOSED",
                "source": f"tdx_local_{base}m_aggregation",
            }
        )
    return result


def aggregate_tdx_1m_klines(rows: list[dict], target_freq: str) -> list[dict]:
    """Backward-compatible 1m aggregation helper for tests and ad-hoc tools."""
    return aggregate_tdx_minute_klines(rows, target_freq, base_freq="1")


def read_tdx_derived_minute_klines(
    symbol: str,
    target_freq: str,
    limit: int = 240,
    vipdoc: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict]:
    """Read local TDX native rows and derive only higher periods.

    Rules:
    - `1` uses native `.lc1`.
    - `5` uses native `.lc5`.
    - `15/30/60` are derived from native `.lc5`, not from `.lc1`.
    """
    freq = str(target_freq).lower().replace("m", "")
    if freq == "1":
        return read_tdx_1m_klines(
            symbol,
            limit=limit,
            vipdoc=vipdoc,
            start_date=start_date,
            end_date=end_date,
        )
    if freq == "5":
        return read_tdx_5m_klines(
            symbol,
            limit=limit,
            vipdoc=vipdoc,
            start_date=start_date,
            end_date=end_date,
        )
    if freq not in TDX_AGGREGATE_FREQ_LABELS:
        return []
    source_limit = max(int(limit) * max(int(freq) // 5, 1) * 2, int(limit), 240)
    rows = read_tdx_5m_klines(
        symbol,
        limit=source_limit,
        vipdoc=vipdoc,
        start_date=start_date,
        end_date=end_date,
    )
    return aggregate_tdx_minute_klines(rows, freq, base_freq="5")[-int(limit):]


def _read_lc1_tail(path: str, tail_count: int, symbol: str) -> list[dict]:
    return _read_minute_tail(path, tail_count, symbol, "1")


def _read_minute_tail(path: str, tail_count: int, symbol: str, freq: str) -> list[dict]:
    rows = []
    with open(path, "rb") as file:
        file.seek(max(0, os.path.getsize(path) - tail_count * RECORD_SIZE))
        while True:
            raw = file.read(RECORD_SIZE)
            if len(raw) < RECORD_SIZE:
                break
            row = _parse_minute_record(raw, symbol, freq)
            if row:
                rows.append(row)
    return rows


def _has_minute_shape(root: Path) -> bool:
    return (
        (root / "sh" / "minline").is_dir()
        or (root / "sz" / "minline").is_dir()
        or (root / "sh" / "fzline").is_dir()
        or (root / "sz" / "fzline").is_dir()
    )


def _is_a_share_trading_minute(value: str) -> bool:
    if len(value) < 16:
        return False
    hm = value[11:16]
    return "09:30" <= hm <= "11:30" or "13:00" <= hm <= "15:00"


def _fmt_dt(value: object) -> str:
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value or "")
    return text[:19] if len(text) >= 19 else text


def _num(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_lc1_record(raw: bytes, symbol: str) -> Optional[dict]:
    return _parse_minute_record(raw, symbol, "1")


def _parse_minute_record(raw: bytes, symbol: str, freq: str) -> Optional[dict]:
    date_code, minute_code, open_, high, low, close, amount, volume, _reserved = struct.unpack(
        RECORD_FMT,
        raw,
    )
    timestamp = _decode_lc1_timestamp(date_code, minute_code)
    if not timestamp:
        return None
    if close <= 0 or high <= 0 or low <= 0:
        return None
    return {
        "symbol": symbol,
        "freq": str(freq),
        "date": timestamp,
        "open": round(float(open_), 4),
        "high": round(float(high), 4),
        "low": round(float(low), 4),
        "close": round(float(close), 4),
        "volume": float(volume),
        "amount": round(float(amount), 2),
        "adjustflag": "3",
        "bar_status": "CLOSED",
        "source": f"tdx_local_{freq}m",
    }


def _decode_lc1_timestamp(date_code: int, minute_code: int) -> Optional[str]:
    year = date_code // 2048 + 2004
    month = (date_code % 2048) // 100
    day = (date_code % 2048) % 100
    hour = minute_code // 60
    minute = minute_code % 60
    if not (1990 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:00"


def encode_lc1_date(year: int, month: int, day: int) -> int:
    """Encode a TDX lc1 date code. Intended for tests and fixture generation."""
    return (year - 2004) * 2048 + month * 100 + day
