"""TDX local vipdoc daily sync service."""

from __future__ import annotations

import os
import sqlite3
import struct
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

from server.config import TDX_VIPDOC
from server.db.kline_lake import LAKE_SCHEMA, get_lake_path, init_lake

RECORD_SIZE = 32
RECORD_FMT = "<IIIIIfII"
SH_PREFIXES = ("sh60", "sh68")
SZ_PREFIXES = ("sz00", "sz30")
BATCH_ROWS = 10000
TAIL_RECORDS = 120

DEFAULT_VIPDOC_CANDIDATES = (
    TDX_VIPDOC,
    "/Users/markqu/Desktop/tdx_vipdoc_mount/vipdoc",
    "/Users/markqu/Desktop/tdx_vipdoc_mount",
    "/Volumes/tdx_vipdoc",
)

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tdx-daily-sync")
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def resolve_vipdoc(vipdoc: Optional[str] = None) -> str:
    """Resolve the first usable vipdoc path."""
    candidates = [vipdoc] if vipdoc else list(DEFAULT_VIPDOC_CANDIDATES)
    for item in candidates:
        if not item:
            continue
        root = Path(item)
        if _has_vipdoc_shape(root):
            return str(root)
        nested = root / "vipdoc"
        if _has_vipdoc_shape(nested):
            return str(nested)
    return str(Path(candidates[0] or TDX_VIPDOC))


def vipdoc_status(vipdoc: Optional[str] = None) -> dict:
    root = Path(resolve_vipdoc(vipdoc))
    day_files = _collect_day_files(root)
    raw_bytes = sum(Path(path).stat().st_size for _, path in day_files)
    records = raw_bytes // RECORD_SIZE
    return {
        "vipdoc": str(root),
        "available": _has_vipdoc_shape(root),
        "a_share_day_files": len(day_files),
        "raw_bytes": raw_bytes,
        "raw_mib": round(raw_bytes / 1024 / 1024, 1),
        "records_estimate": records,
        "sqlite_size_estimate_mib": {
            "low_160b": round(records * 160 / 1024 / 1024, 1),
            "mid_180b": round(records * 180 / 1024 / 1024, 1),
            "high_220b": round(records * 220 / 1024 / 1024, 1),
        },
        "tdx_lake_path": get_lake_path("tdx"),
    }


def start_daily_sync(vipdoc: Optional[str] = None, mode: str = "incremental", reset: bool = False) -> dict:
    mode = mode.lower()
    if mode not in {"full", "incremental"}:
        raise ValueError("mode must be full or incremental")
    root = resolve_vipdoc(vipdoc)
    if not _has_vipdoc_shape(Path(root)):
        raise FileNotFoundError(f"TDX vipdoc 不可用: {root}")

    with _jobs_lock:
        for job in _jobs.values():
            if job.get("status") == "running":
                return job.copy()
        job_id = uuid.uuid4().hex[:12]
        job = {
            "job_id": job_id,
            "kind": "tdx_daily_sync",
            "status": "running",
            "vipdoc": root,
            "mode": mode,
            "reset": bool(reset),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "finished_at": "",
            "total_files": 0,
            "processed_files": 0,
            "synced_symbols": 0,
            "written_rows": 0,
            "skipped_files": 0,
            "error": "",
        }
        _jobs[job_id] = job

    _executor.submit(_run_daily_sync_job, job_id)
    return job.copy()


def get_sync_job(job_id: str) -> dict:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        return job.copy()


def latest_sync_job() -> dict:
    with _jobs_lock:
        if not _jobs:
            return {}
        job_id = next(reversed(_jobs))
        return _jobs[job_id].copy()


def _run_daily_sync_job(job_id: str) -> None:
    try:
        with _jobs_lock:
            job = _jobs[job_id]
            root = Path(job["vipdoc"])
            mode = job["mode"]
            reset = bool(job["reset"])

        result = sync_daily_files(root, mode=mode, reset=reset, job_id=job_id)
        with _jobs_lock:
            _jobs[job_id].update(result)
            _jobs[job_id]["status"] = "success"
            _jobs[job_id]["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as exc:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(exc)
            _jobs[job_id]["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")


def sync_daily_files(root: Path, mode: str, reset: bool, job_id: Optional[str] = None) -> dict:
    init_lake()
    files = _collect_day_files(root)
    conn = sqlite3.connect(get_lake_path("tdx"), timeout=60)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA cache_size=-131072")
        conn.executescript(LAKE_SCHEMA)
        if reset or mode == "full":
            conn.execute("DELETE FROM klines WHERE freq='day' AND adjustflag='3'")
            conn.execute("DELETE FROM tdx_sync_meta WHERE freq='day'")
            conn.commit()
            last_dates = {}
        else:
            last_dates = {
                row[0]: row[1]
                for row in conn.execute("SELECT symbol, last_date FROM tdx_sync_meta WHERE freq='day'")
            }

        total_written = 0
        synced_symbols = 0
        skipped_files = 0
        pending = 0
        _update_job(job_id, total_files=len(files))

        for idx, (symbol, path) in enumerate(files, 1):
            rows = _parse_day_file(path, last_dates.get(symbol))
            if not rows:
                skipped_files += 1
                _update_job(job_id, processed_files=idx, skipped_files=skipped_files)
                continue

            _upsert_rows(conn, symbol, rows)
            total_written += len(rows)
            synced_symbols += 1
            pending += len(rows)
            if pending >= BATCH_ROWS:
                conn.commit()
                pending = 0
            _update_job(
                job_id,
                processed_files=idx,
                synced_symbols=synced_symbols,
                written_rows=total_written,
                skipped_files=skipped_files,
            )

        conn.commit()
        return {
            "total_files": len(files),
            "processed_files": len(files),
            "synced_symbols": synced_symbols,
            "written_rows": total_written,
            "skipped_files": skipped_files,
        }
    finally:
        conn.close()


def _has_vipdoc_shape(root: Path) -> bool:
    return (root / "sh" / "lday").is_dir() and (root / "sz" / "lday").is_dir()


def tdx_day_file_path(symbol: str, vipdoc: Optional[str] = None) -> str:
    from server.domain.symbols import parse_symbol

    parsed = parse_symbol(symbol)
    return str(Path(resolve_vipdoc(vipdoc)) / parsed.market / "lday" / f"{parsed.market}{parsed.code}.day")


def read_tdx_day_klines(
    symbol: str,
    limit: int = 5000,
    vipdoc: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict]:
    """Read local TDX .day rows for one A-share symbol."""
    path = tdx_day_file_path(symbol, vipdoc)
    if not os.path.isfile(path):
        return []
    try:
        rows = _parse_day_file(path, None)
    except OSError:
        return []
    if start_date:
        rows = [row for row in rows if row["date"] >= start_date[:10]]
    if end_date:
        rows = [row for row in rows if row["date"] <= end_date[:10]]
    return rows[-max(1, min(int(limit), 20000)):]


def aggregate_tdx_week_klines(day_rows: list[dict]) -> list[dict]:
    """Aggregate local TDX daily rows into week bars using the week's last trading day."""
    buckets: dict[tuple[int, int], list[dict]] = {}
    for row in day_rows:
        try:
            date_value = datetime.strptime(str(row.get("date") or "")[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        iso_year, iso_week, _ = date_value.isocalendar()
        buckets.setdefault((iso_year, iso_week), []).append(row)

    weeks = []
    for key in sorted(buckets):
        rows = sorted(buckets[key], key=lambda item: item["date"])
        first = rows[0]
        last = rows[-1]
        weeks.append(
            {
                "date": str(last["date"])[:10],
                "open": float(first["open"]),
                "high": max(float(row["high"]) for row in rows),
                "low": min(float(row["low"]) for row in rows),
                "close": float(last["close"]),
                "volume": sum(float(row.get("volume", 0) or 0) for row in rows),
                "amount": sum(float(row.get("amount", 0) or 0) for row in rows),
            }
        )
    return weeks


def read_tdx_week_klines(
    symbol: str,
    limit: int = 1200,
    vipdoc: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> list[dict]:
    """Read local TDX .day rows and aggregate them into week bars."""
    day_rows = read_tdx_day_klines(
        symbol,
        limit=20000,
        vipdoc=vipdoc,
        start_date=start_date,
        end_date=end_date,
    )
    return aggregate_tdx_week_klines(day_rows)[-max(1, min(int(limit), 5000)):]


def _collect_day_files(root: Path) -> list[tuple[str, str]]:
    result = []
    for market in ("sh", "sz"):
        lday_dir = root / market / "lday"
        if not lday_dir.is_dir():
            continue
        for path in sorted(lday_dir.glob("*.day")):
            name = path.name.lower()
            if _is_astock(name):
                result.append((_tdx_code_to_symbol(name), str(path)))
    return result


def _is_astock(filename: str) -> bool:
    name = filename.lower().replace(".day", "")
    return any(name.startswith(prefix) for prefix in SH_PREFIXES + SZ_PREFIXES)


def _tdx_code_to_symbol(filename: str) -> str:
    name = filename.replace(".day", "")
    return f"{name[:2]}.{name[2:]}"


def _parse_day_file(path: str, last_date: Optional[str]) -> list[dict]:
    rows = []
    size = os.path.getsize(path)
    count = size // RECORD_SIZE
    start = 0 if not last_date else max(0, count - TAIL_RECORDS)
    with open(path, "rb") as file:
        file.seek(start * RECORD_SIZE)
        while True:
            raw = file.read(RECORD_SIZE)
            if len(raw) < RECORD_SIZE:
                break
            date_int, open_, high, low, close, amount, volume, _reserved = struct.unpack(RECORD_FMT, raw)
            if date_int < 19900101 or date_int > 20991231 or close <= 0 or volume <= 0:
                continue
            date_value = _format_date(date_int)
            if last_date and date_value <= last_date:
                continue
            rows.append({
                "date": date_value,
                "open": round(open_ / 100, 2),
                "high": round(high / 100, 2),
                "low": round(low / 100, 2),
                "close": round(close / 100, 2),
                "volume": float(volume),
                "amount": round(float(amount), 2),
            })
    return rows


def _format_date(date_int: int) -> str:
    year = date_int // 10000
    month = (date_int % 10000) // 100
    day = date_int % 100
    return f"{year:04d}-{month:02d}-{day:02d}"


def _upsert_rows(conn: sqlite3.Connection, symbol: str, rows: list[dict]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO klines
            (symbol, freq, date, open, high, low, close, volume, amount, adjustflag)
        VALUES (?, 'day', ?, ?, ?, ?, ?, ?, ?, '3')
        """,
        [
            (symbol, row["date"], row["open"], row["high"], row["low"], row["close"], row["volume"], row["amount"])
            for row in rows
        ],
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO tdx_sync_meta (symbol, freq, last_date)
        VALUES (?, 'day', ?)
        """,
        (symbol, max(row["date"] for row in rows)),
    )


def _update_job(job_id: Optional[str], **updates) -> None:
    if not job_id:
        return
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(updates)
