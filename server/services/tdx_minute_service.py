"""Read-only TDX local 1-minute kline reader.

TDX minute data is a display/replay supplement. It must not override the
formal BaoStock + CZSC structure source.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from server.config import TDX_VIPDOC
from server.domain.symbols import parse_symbol

RECORD_SIZE = 32
RECORD_FMT = "<HHfffffII"  # date, minute, open/high/low/close, amount, volume, reserved


@dataclass(frozen=True)
class TdxMinuteStatus:
    available: bool
    path: str
    reason: str = ""


def tdx_minute_file_path(symbol: str, vipdoc: str = TDX_VIPDOC, freq: str = "1") -> str:
    """Return expected TDX minute file path for a symbol."""
    parsed = parse_symbol(symbol)
    extension = ".lc1" if str(freq).lower() in {"1", "1m", "m1"} else ".lc5"
    filename = f"{parsed.market}{parsed.code}{extension}"
    return str(Path(vipdoc) / parsed.market / "minline" / filename)


def tdx_minute_status(symbol: Optional[str] = None, vipdoc: str = TDX_VIPDOC) -> dict:
    """Return local TDX minute source status."""
    root = Path(vipdoc)
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


def read_tdx_1m_klines(
    symbol: str,
    limit: int = 240,
    vipdoc: str = TDX_VIPDOC,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict]:
    """Read local TDX .lc1 rows and return CT-OS kline dicts.

    `end_date` accepts `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`. Rows are ordered
    oldest to newest.
    """
    parsed = parse_symbol(symbol)
    path = tdx_minute_file_path(parsed.canonical, vipdoc, freq="1")
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
        rows = _read_lc1_tail(path, tail_count, parsed.canonical)
    except OSError:
        return []
    if start_date:
        rows = [row for row in rows if row["date"] >= start_date]
    if end_date:
        rows = [row for row in rows if row["date"] <= end_date]
    return rows[-read_count:]


def _read_lc1_tail(path: str, tail_count: int, symbol: str) -> list[dict]:
    rows = []
    with open(path, "rb") as file:
        file.seek(max(0, os.path.getsize(path) - tail_count * RECORD_SIZE))
        while True:
            raw = file.read(RECORD_SIZE)
            if len(raw) < RECORD_SIZE:
                break
            row = _parse_lc1_record(raw, symbol)
            if row:
                rows.append(row)
    return rows


def _parse_lc1_record(raw: bytes, symbol: str) -> Optional[dict]:
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
        "freq": "1",
        "date": timestamp,
        "open": round(float(open_), 4),
        "high": round(float(high), 4),
        "low": round(float(low), 4),
        "close": round(float(close), 4),
        "volume": float(volume),
        "amount": round(float(amount), 2),
        "adjustflag": "3",
        "bar_status": "CLOSED",
        "source": "tdx_local_1m",
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
