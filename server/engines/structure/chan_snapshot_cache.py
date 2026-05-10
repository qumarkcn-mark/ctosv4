"""Persistent snapshots for Kline Chan detail responses."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from server.db.database import get_connection


logger = logging.getLogger(__name__)
SHANGHAI_TZ = timezone(timedelta(hours=8))
SNAPSHOT_SCHEMA_VERSION = "chan_detail_snapshot:v2"
SNAPSHOT_RETENTION_DAYS = 30
SNAPSHOT_MAX_PER_SERIES = 6


def load_chan_snapshot(
    *,
    symbol: str,
    freq: str,
    cchan_preset: str,
    kline_source: str,
    adjustflag: str,
    end_date: str,
    max_compute_bars: int,
    data_signature: str,
) -> Optional[dict[str, Any]]:
    if not data_signature:
        return None
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT result_json, structure_fingerprint, updated_at, last_kline_time, kline_count
                  FROM chan_structure_snapshots
                 WHERE symbol = ?
                   AND freq = ?
                   AND cchan_preset = ?
                   AND kline_source = ?
                   AND adjustflag = ?
                   AND end_date = ?
                   AND max_compute_bars = ?
                   AND data_signature = ?
                 ORDER BY id DESC
                 LIMIT 1
                """,
                (
                    symbol,
                    freq,
                    cchan_preset,
                    kline_source or "",
                    adjustflag,
                    end_date or "",
                    int(max_compute_bars or 0),
                    data_signature,
                ),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Chan snapshot lookup skipped: %s", exc)
        return None

    if not row:
        return None
    try:
        result = json.loads(row["result_json"])
        if not isinstance(result, dict):
            return None
        result = copy.deepcopy(result)
        result["snapshot"] = {
            "hit": True,
            "source": "persistent",
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "data_signature": data_signature,
            "structure_fingerprint": row["structure_fingerprint"],
            "last_kline_time": row["last_kline_time"],
            "kline_count": row["kline_count"],
            "updated_at": row["updated_at"],
        }
        return result
    except Exception as exc:
        logger.warning("Chan snapshot decode skipped: %s", exc)
        return None


def load_latest_chan_snapshot(
    *,
    symbol: str,
    freq: str,
    cchan_preset: str,
    kline_source: str,
    adjustflag: str,
    end_date: str,
    max_compute_bars: int,
) -> Optional[dict[str, Any]]:
    """Load the newest snapshot for the same structural parameters, regardless of data signature."""
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT result_json, data_signature, structure_fingerprint, updated_at, last_kline_time, kline_count
                  FROM chan_structure_snapshots
                 WHERE symbol = ?
                   AND freq = ?
                   AND cchan_preset = ?
                   AND kline_source = ?
                   AND adjustflag = ?
                   AND end_date = ?
                   AND max_compute_bars = ?
                 ORDER BY updated_at DESC, id DESC
                 LIMIT 1
                """,
                (
                    symbol,
                    freq,
                    cchan_preset,
                    kline_source or "",
                    adjustflag,
                    end_date or "",
                    int(max_compute_bars or 0),
                ),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Latest Chan snapshot lookup skipped: %s", exc)
        return None

    if not row:
        return None
    try:
        result = json.loads(row["result_json"])
        if not isinstance(result, dict):
            return None
        return {
            "result": copy.deepcopy(result),
            "snapshot": {
                "hit": True,
                "source": "latest_persistent",
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "data_signature": row["data_signature"],
                "structure_fingerprint": row["structure_fingerprint"],
                "last_kline_time": row["last_kline_time"],
                "kline_count": row["kline_count"],
                "updated_at": row["updated_at"],
            },
        }
    except Exception as exc:
        logger.warning("Latest Chan snapshot decode skipped: %s", exc)
        return None


def load_chan_snapshot_by_key_hash(structure_key_hash: str) -> Optional[dict[str, Any]]:
    """Load a fresh v2 snapshot by stable structure key hash."""
    if not structure_key_hash:
        return None
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT result_json, data_signature, structure_fingerprint, updated_at,
                       last_kline_time, kline_count, compute_profile
                  FROM chan_structure_snapshots
                 WHERE structure_key_hash = ?
                 ORDER BY updated_at DESC, id DESC
                 LIMIT 1
                """,
                (structure_key_hash,),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Chan snapshot key-hash lookup skipped: %s", exc)
        return None

    if not row:
        return None
    try:
        result = json.loads(row["result_json"])
        if not isinstance(result, dict):
            return None
        result = copy.deepcopy(result)
        result["snapshot"] = {
            "hit": True,
            "source": "persistent_key_hash",
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "structure_key_hash": structure_key_hash,
            "data_signature": row["data_signature"],
            "structure_fingerprint": row["structure_fingerprint"],
            "last_kline_time": row["last_kline_time"],
            "kline_count": row["kline_count"],
            "compute_profile": row["compute_profile"],
            "updated_at": row["updated_at"],
        }
        return result
    except Exception as exc:
        logger.warning("Chan snapshot key-hash decode skipped: %s", exc)
        return None


def load_latest_chan_snapshot_for_series(
    *,
    symbol: str,
    freq: str,
    cchan_preset: str,
    compute_profile: str,
    kline_source: str = "baostock",
    adjustflag: str = "2",
) -> Optional[dict[str, Any]]:
    """Load latest v2 snapshot for stale display while a refresh is queued."""
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT result_json, data_signature, structure_fingerprint, updated_at,
                       last_kline_time, kline_count, structure_key_hash, compute_profile
                  FROM chan_structure_snapshots
                 WHERE symbol = ?
                   AND freq = ?
                   AND cchan_preset = ?
                   AND kline_source = ?
                   AND adjustflag = ?
                   AND compute_profile = ?
                 ORDER BY updated_at DESC, id DESC
                 LIMIT 1
                """,
                (symbol, freq, cchan_preset, kline_source, adjustflag, compute_profile),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Latest Chan v2 snapshot lookup skipped: %s", exc)
        return None

    if not row:
        return None
    try:
        result = json.loads(row["result_json"])
        if not isinstance(result, dict):
            return None
        return {
            "result": copy.deepcopy(result),
            "snapshot": {
                "hit": True,
                "source": "latest_series",
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
                "structure_key_hash": row["structure_key_hash"],
                "data_signature": row["data_signature"],
                "structure_fingerprint": row["structure_fingerprint"],
                "last_kline_time": row["last_kline_time"],
                "kline_count": row["kline_count"],
                "compute_profile": row["compute_profile"],
                "updated_at": row["updated_at"],
            },
        }
    except Exception as exc:
        logger.warning("Latest Chan v2 snapshot decode skipped: %s", exc)
        return None


def save_chan_snapshot(
    *,
    symbol: str,
    freq: str,
    cchan_preset: str,
    kline_source: str,
    adjustflag: str,
    end_date: str,
    max_compute_bars: int,
    data_signature: str,
    last_kline_time: str,
    kline_count: int,
    compute_bars: int,
    result: dict[str, Any],
    structure_key_hash: str = "",
    compute_profile: str = "",
    engine_version: str = "",
    adapter_version: str = "",
    payload_kind: str = "full_geometry",
    payload_uri: str = "",
    compressed_size_bytes: int = 0,
) -> str:
    if not data_signature or not last_kline_time or result.get("error"):
        return ""
    if not _is_formal_snapshot_payload(
        result=result,
        structure_key_hash=structure_key_hash,
        compute_profile=compute_profile,
        kline_source=kline_source,
        adjustflag=adjustflag,
        payload_kind=payload_kind,
    ):
        return ""

    result_to_store = copy.deepcopy(result)
    result_to_store.pop("cache", None)
    result_to_store.pop("snapshot", None)
    structure_fingerprint = _fingerprint_structure(result_to_store)
    now = datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds")

    try:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO chan_structure_snapshots (
                    symbol, freq, cchan_preset, kline_source, adjustflag, end_date,
                    max_compute_bars, compute_bars, last_kline_time, kline_count,
                    data_signature, structure_fingerprint, result_json,
                    structure_key_hash, compute_profile, engine_version, adapter_version,
                    payload_kind, payload_uri, compressed_size_bytes,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, freq, cchan_preset, kline_source, adjustflag, end_date, max_compute_bars, data_signature)
                DO UPDATE SET
                    compute_bars = excluded.compute_bars,
                    last_kline_time = excluded.last_kline_time,
                    kline_count = excluded.kline_count,
                    structure_fingerprint = excluded.structure_fingerprint,
                    result_json = excluded.result_json,
                    structure_key_hash = excluded.structure_key_hash,
                    compute_profile = excluded.compute_profile,
                    engine_version = excluded.engine_version,
                    adapter_version = excluded.adapter_version,
                    payload_kind = excluded.payload_kind,
                    payload_uri = excluded.payload_uri,
                    compressed_size_bytes = excluded.compressed_size_bytes,
                    updated_at = excluded.updated_at
                """,
                (
                    symbol,
                    freq,
                    cchan_preset,
                    kline_source or "",
                    adjustflag,
                    end_date or "",
                    int(max_compute_bars or 0),
                    int(compute_bars or 0),
                    last_kline_time,
                    int(kline_count or 0),
                    data_signature,
                    structure_fingerprint,
                    json.dumps(result_to_store, ensure_ascii=False, separators=(",", ":")),
                    structure_key_hash or "",
                    compute_profile or "",
                    engine_version or "",
                    adapter_version or "",
                    payload_kind or "full_geometry",
                    payload_uri or "",
                    int(compressed_size_bytes or 0),
                    now,
                    now,
                ),
            )
            _prune_chan_snapshots(
                conn,
                symbol=symbol,
                freq=freq,
                cchan_preset=cchan_preset,
                kline_source=kline_source or "",
                adjustflag=adjustflag,
                end_date=end_date or "",
                max_compute_bars=int(max_compute_bars or 0),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Chan snapshot save skipped: %s", exc)
        return ""

    return structure_fingerprint


def _is_formal_snapshot_payload(
    *,
    result: dict[str, Any],
    structure_key_hash: str,
    compute_profile: str,
    kline_source: str,
    adjustflag: str,
    payload_kind: str,
) -> bool:
    """Guard v2 formal snapshots from preview/fallback data.

    Legacy cache rows may still be written by old code without a structure key.
    Once a v2 structure key is present, the row must represent a formal BaoStock
    full-geometry compute so every consumer can safely reuse it globally.
    """
    if not (structure_key_hash or compute_profile):
        return True
    if (kline_source or "") != "baostock" or str(adjustflag or "") != "2":
        logger.warning(
            "Formal Chan snapshot rejected: source=%s adjustflag=%s",
            kline_source,
            adjustflag,
        )
        return False
    if (payload_kind or "full_geometry") != "full_geometry":
        logger.warning("Formal Chan snapshot rejected: payload_kind=%s", payload_kind)
        return False
    provider = ((result.get("data_source") or {}).get("provider") or "baostock")
    if provider != "baostock":
        logger.warning("Formal Chan snapshot rejected: provider=%s", provider)
        return False
    return True


def _prune_chan_snapshots(
    conn,
    *,
    symbol: str,
    freq: str,
    cchan_preset: str,
    kline_source: str,
    adjustflag: str,
    end_date: str,
    max_compute_bars: int,
    retention_days: int = SNAPSHOT_RETENTION_DAYS,
    max_per_series: int = SNAPSHOT_MAX_PER_SERIES,
) -> None:
    """Bound snapshot growth for one symbol/freq/preset series."""
    cutoff = (datetime.now(SHANGHAI_TZ) - timedelta(days=retention_days)).isoformat(timespec="seconds")
    params = (
        symbol,
        freq,
        cchan_preset,
        kline_source or "",
        adjustflag,
        end_date or "",
        int(max_compute_bars or 0),
    )
    conn.execute(
        """
        DELETE FROM chan_structure_snapshots
         WHERE symbol = ?
           AND freq = ?
           AND cchan_preset = ?
           AND kline_source = ?
           AND adjustflag = ?
           AND end_date = ?
           AND max_compute_bars = ?
           AND updated_at < ?
        """,
        (*params, cutoff),
    )
    rows = conn.execute(
        """
        SELECT id
          FROM chan_structure_snapshots
         WHERE symbol = ?
           AND freq = ?
           AND cchan_preset = ?
           AND kline_source = ?
           AND adjustflag = ?
           AND end_date = ?
           AND max_compute_bars = ?
         ORDER BY updated_at DESC, id DESC
        """,
        params,
    ).fetchall()
    stale_ids = [row["id"] for row in rows[max_per_series:]]
    if stale_ids:
        conn.executemany(
            "DELETE FROM chan_structure_snapshots WHERE id = ?",
            [(snapshot_id,) for snapshot_id in stale_ids],
        )


def _fingerprint_structure(result: dict[str, Any]) -> str:
    relevant = {
        "schema": SNAPSHOT_SCHEMA_VERSION,
        "symbol": result.get("symbol"),
        "freq": result.get("freq"),
        "compute_bars": result.get("compute_bars"),
        "data_source": result.get("data_source"),
        "stats": result.get("stats"),
        "bis": result.get("bis", []),
        "segs": result.get("segs", []),
        "bi_zhongshus": result.get("bi_zhongshus", []),
        "seg_zhongshus": result.get("seg_zhongshus", []),
        "bsps": result.get("bsps", []),
    }
    return hashlib.sha256(
        json.dumps(relevant, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
