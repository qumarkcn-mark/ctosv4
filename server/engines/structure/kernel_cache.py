"""SQLite persistence for Structure Kernel results."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from server.db.database import get_connection


logger = logging.getLogger(__name__)
SHANGHAI_TZ = timezone(timedelta(hours=8))
DEFAULT_TTL_SECONDS = 30 * 60


def load_structure_kernel_cache(
    *,
    symbol: str,
    profile: str,
    cchan_preset: str,
    data_signature: str,
) -> Optional[dict[str, Any]]:
    if not data_signature:
        return None
    try:
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT result_json
                  FROM structure_kernel_cache
                 WHERE symbol = ?
                   AND profile = ?
                   AND cchan_preset = ?
                   AND data_signature = ?
                   AND datetime(expires_at) > datetime('now')
                 ORDER BY id DESC
                 LIMIT 1
                """,
                (symbol, profile, cchan_preset, data_signature),
            ).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Structure kernel cache lookup skipped: %s", exc)
        return None

    if not row:
        return None
    try:
        payload = json.loads(row["result_json"])
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def save_structure_kernel_cache(
    *,
    symbol: str,
    profile: str,
    cchan_preset: str,
    data_signature: str,
    structure_fingerprint: str,
    result: dict[str, Any],
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> None:
    if not data_signature:
        return
    now = datetime.now(SHANGHAI_TZ)
    expires_at = now + timedelta(seconds=ttl_seconds)
    try:
        conn = get_connection()
        try:
            conn.execute(
                """
                INSERT INTO structure_kernel_cache (
                    symbol, profile, cchan_preset, data_signature,
                    structure_fingerprint, result_json, created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, profile, cchan_preset, data_signature)
                DO UPDATE SET
                    structure_fingerprint = excluded.structure_fingerprint,
                    result_json = excluded.result_json,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at
                """,
                (
                    symbol,
                    profile,
                    cchan_preset,
                    data_signature,
                    structure_fingerprint,
                    json.dumps(result, ensure_ascii=False),
                    now.isoformat(timespec="seconds"),
                    expires_at.isoformat(timespec="seconds"),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("Structure kernel cache save skipped: %s", exc)
