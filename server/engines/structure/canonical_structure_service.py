"""Canonical CZSC structure cache.

This module is the single read-through entry for CZSC structure computation.
It deliberately reuses the existing structure_snapshots table instead of
introducing another store: consumers choose a minimum compute profile, and this
service returns a fresh cached snapshot at that depth or deeper, or computes and
persists one through the same core path.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from server.db.database import get_connection
from server.db.kline_lake import upsert_klines
from server.domain.symbols import normalize_symbol
from server.engines.structure import czsc_adapter
from server.engines.structure.source_policy import resolve_structure_source_policy, structure_signature_for_policy
from server.engines.structure.structure_key import COMPUTE_PROFILES, normalize_freq, resolve_compute_bars


SHANGHAI_TZ = timezone(timedelta(hours=8))
ENGINE = "czsc"
PROFILE_ORDER = ("chart_standard_v1", "tactical_v1", "deep_audit_v1")
logger = logging.getLogger(__name__)

WritePolicy = Literal["read_only", "read_through", "compute_only"]


class CanonicalStructureError(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def get_or_compute_structure(
    *,
    symbol: str,
    level: str,
    min_profile: str,
    write_policy: WritePolicy = "read_through",
) -> dict[str, Any] | None:
    """Return a fresh canonical snapshot at min_profile depth or deeper.

    Shallow consumers such as chart preview can reuse deeper fresh rows. If no
    suitable row exists, read_through computes only the requested min_profile so
    cold chart loads stay bounded.
    """
    canonical = normalize_symbol(symbol)
    normalized_level = normalize_freq(level)
    _assert_profile(min_profile)

    if write_policy != "compute_only":
        existing = _find_fresh_snapshot_at_or_above(
            symbol=canonical,
            level=normalized_level,
            min_profile=min_profile,
        )
        if existing:
            existing["canonical_cache_status"] = "hit"
            existing["requested_min_profile"] = min_profile
            return existing

    if write_policy == "read_only":
        return None

    return compute_and_persist_structure(
        symbol=canonical,
        level=normalized_level,
        compute_profile=min_profile,
        expected_data_signature=None,
    )


def get_latest_structure(
    *,
    symbol: str,
    level: str,
    min_profile: str,
    allow_bootstrap: bool = True,
) -> dict[str, Any] | None:
    """Read the fresh canonical row at min_profile depth or deeper."""
    canonical = normalize_symbol(symbol)
    normalized_level = normalize_freq(level)
    _assert_profile(min_profile)
    row = _find_fresh_snapshot_at_or_above(
        symbol=canonical,
        level=normalized_level,
        min_profile=min_profile,
        allow_bootstrap=allow_bootstrap,
    )
    if row:
        row["canonical_cache_status"] = "hit"
        row["requested_min_profile"] = min_profile
    return row


def compute_and_persist_structure(
    *,
    symbol: str,
    level: str,
    compute_profile: str,
    expected_data_signature: str | None = None,
) -> dict[str, Any]:
    """Compute one exact profile through the canonical core and upsert it."""
    canonical = normalize_symbol(symbol)
    normalized_level = normalize_freq(level)
    _assert_profile(compute_profile)

    signature = signature_for_level(symbol=canonical, level=normalized_level, compute_profile=compute_profile)
    data_signature = signature.get("signature") or ""
    if not data_signature:
        raise CanonicalStructureError("NO_DATA", "No CZSC input bars")
    if expected_data_signature and data_signature != expected_data_signature:
        raise CanonicalStructureError("STALE_INPUT", "Snapshot job input signature is stale")

    existing = _find_exact_snapshot(
        symbol=canonical,
        level=normalized_level,
        compute_profile=compute_profile,
        data_signature=data_signature,
    )
    if existing:
        existing["canonical_cache_status"] = "hit_exact"
        existing["requested_min_profile"] = compute_profile
        return existing

    result = _compute_and_serialize(
        symbol=canonical,
        level=normalized_level,
        compute_profile=compute_profile,
    )
    level_payload = result["snapshot_payload"]
    return save_canonical_snapshot(
        symbol=canonical,
        level=normalized_level,
        compute_profile=compute_profile,
        data_signature=data_signature,
        data_as_of=signature.get("last_date") or "",
        snapshot_payload=level_payload,
        raw_bi_context=result["raw_bi_context"],
        engine_version=result["engine_version"],
        adapter_version=result["adapter_version"],
        status="fresh",
    )


def signature_for_level(*, symbol: str, level: str, compute_profile: str, allow_bootstrap: bool = True) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    normalized_level = normalize_freq(level)
    compute_bars = resolve_compute_bars(compute_profile, level)
    policy = resolve_structure_source_policy(symbol=canonical, level=normalized_level, limit=compute_bars)
    signature = structure_signature_for_policy(symbol=canonical, level=normalized_level, limit=compute_bars, policy=policy)
    if (
        allow_bootstrap
        and not signature.get("signature")
        and _bootstrap_tdx_qfq_for_structure(canonical, normalized_level, compute_bars)
    ):
        policy = resolve_structure_source_policy(symbol=canonical, level=normalized_level, limit=compute_bars)
        signature = structure_signature_for_policy(symbol=canonical, level=normalized_level, limit=compute_bars, policy=policy)
    signature["source_policy"] = policy
    return signature


def save_canonical_snapshot(
    *,
    symbol: str,
    level: str,
    compute_profile: str,
    data_signature: str,
    data_as_of: str,
    snapshot_payload: dict[str, Any],
    raw_bi_context: dict[str, Any],
    engine_version: str,
    adapter_version: str,
    status: str = "fresh",
    error_code: str = "",
    error_message: str = "",
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    normalized_level = normalize_freq(level)
    payload_json = json.dumps(snapshot_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raw_json = json.dumps(raw_bi_context or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = _stable_hash({
        "engine": ENGINE,
        "engine_version": engine_version,
        "adapter_version": adapter_version,
        "symbol": canonical,
        "level": normalized_level,
        "compute_profile": compute_profile,
        "data_signature": data_signature,
        "snapshot": snapshot_payload,
    })
    snapshot_id = f"czsc_snapshot_{fingerprint[:16]}"
    now = _now_text()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO structure_snapshots (
                snapshot_id, symbol, level, engine, engine_version, adapter_version,
                compute_profile, data_signature, data_as_of, snapshot_json,
                raw_bi_context_json, structure_fingerprint, status, error_code,
                error_message, created_at, updated_at
            )
            VALUES (?, ?, ?, 'czsc', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, level, engine, compute_profile, data_signature)
            DO UPDATE SET
                snapshot_id = excluded.snapshot_id,
                engine_version = excluded.engine_version,
                adapter_version = excluded.adapter_version,
                data_as_of = excluded.data_as_of,
                snapshot_json = excluded.snapshot_json,
                raw_bi_context_json = excluded.raw_bi_context_json,
                structure_fingerprint = excluded.structure_fingerprint,
                status = excluded.status,
                error_code = excluded.error_code,
                error_message = excluded.error_message,
                updated_at = excluded.updated_at
            """,
            (
                snapshot_id,
                canonical,
                normalized_level,
                engine_version,
                adapter_version,
                compute_profile,
                data_signature,
                data_as_of or "",
                payload_json,
                raw_json,
                fingerprint,
                status,
                error_code,
                error_message,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM structure_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
        snapshot = _snapshot_row(row)
        snapshot["canonical_cache_status"] = "computed"
        snapshot["requested_min_profile"] = compute_profile
        return snapshot
    finally:
        conn.close()


def profile_satisfies(candidate: str, minimum: str) -> bool:
    return _profile_rank(candidate) >= _profile_rank(minimum)


def _compute_and_serialize(*, symbol: str, level: str, compute_profile: str) -> dict[str, Any]:
    count = resolve_compute_bars(compute_profile, level)
    result = czsc_adapter.analyze_czsc_structure_sync(
        symbol,
        levels=[level],
        count=count,
        compute_profile=compute_profile,
    )
    if result.get("error") == "CZSC_UNAVAILABLE":
        raise CanonicalStructureError("CZSC_UNAVAILABLE", "CZSC dependency unavailable")
    if result.get("error"):
        raise CanonicalStructureError(str(result.get("error")), str(result.get("message") or ""))
    level_payload = (result.get("levels") or {}).get(level) or {}
    if level_payload.get("error"):
        raise CanonicalStructureError(str(level_payload.get("error")), str(level_payload.get("message") or ""))

    raw_context = czsc_adapter.export_czsc_raw_bi_context_sync(
        symbol,
        levels=[level],
        count=count,
        compute_profile=compute_profile,
        precomputed_result=result,
    )
    return {
        "snapshot_payload": level_payload,
        "raw_bi_context": raw_context,
        "engine_version": czsc_adapter.get_czsc_engine_version(),
        "adapter_version": czsc_adapter.ADAPTER_VERSION,
    }


def _find_fresh_snapshot_at_or_above(
    *,
    symbol: str,
    level: str,
    min_profile: str,
    allow_bootstrap: bool = True,
) -> dict[str, Any] | None:
    candidates: list[tuple[str, dict[str, Any]]] = []
    for profile in _profiles_at_or_above(min_profile):
        signature = signature_for_level(
            symbol=symbol,
            level=level,
            compute_profile=profile,
            allow_bootstrap=allow_bootstrap,
        )
        if signature.get("signature"):
            candidates.append((profile, signature))
    if not candidates:
        return None

    conn = get_connection()
    try:
        for profile, signature in sorted(candidates, key=lambda item: _profile_rank(item[0]), reverse=True):
            row = conn.execute(
                """
                SELECT *
                  FROM structure_snapshots
                 WHERE symbol = ? AND level = ? AND engine = 'czsc'
                   AND compute_profile = ? AND data_signature = ?
                 ORDER BY updated_at DESC, id DESC
                 LIMIT 1
                """,
                (symbol, level, profile, signature["signature"]),
            ).fetchone()
            if row:
                return _snapshot_row(row)
    finally:
        conn.close()
    return None


def _find_exact_snapshot(*, symbol: str, level: str, compute_profile: str, data_signature: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
              FROM structure_snapshots
             WHERE symbol = ? AND level = ? AND engine = 'czsc'
               AND compute_profile = ? AND data_signature = ?
             ORDER BY updated_at DESC, id DESC
             LIMIT 1
            """,
            (symbol, level, compute_profile, data_signature),
        ).fetchone()
        return _snapshot_row(row) if row else None
    finally:
        conn.close()


def _snapshot_row(row) -> dict[str, Any]:
    data = dict(row)
    data["snapshot"] = json.loads(data.pop("snapshot_json") or "{}")
    data["raw_bi_context"] = json.loads(data.pop("raw_bi_context_json") or "{}")
    return data


def _profiles_at_or_above(min_profile: str) -> list[str]:
    minimum = _profile_rank(min_profile)
    return [profile for profile in PROFILE_ORDER if _profile_rank(profile) >= minimum and profile in COMPUTE_PROFILES]


def _profile_rank(profile: str) -> int:
    _assert_profile(profile)
    if profile in PROFILE_ORDER:
        return PROFILE_ORDER.index(profile)
    return len(PROFILE_ORDER)


def _assert_profile(profile: str) -> None:
    if profile not in COMPUTE_PROFILES:
        raise ValueError(f"unsupported compute profile: {profile}")


def _bootstrap_tdx_qfq_for_structure(symbol: str, level: str, limit: int) -> bool:
    """Warm local TDX raw/qfq rows for newly added symbols before CZSC runs."""
    try:
        from server.services.tdx_daily_sync_service import aggregate_tdx_week_klines, read_tdx_day_klines
        from server.services.tdx_minute_service import read_tdx_derived_minute_klines
        from server.services.tdx_qfq_normalizer import rebuild_tdx_qfq_from_existing_factors

        day_rows = read_tdx_day_klines(symbol, limit=max(int(limit or 0), 5000))
        if day_rows:
            upsert_klines(symbol, "day", day_rows, adjustflag="3", source="tdx")

        target_freqs: tuple[str, ...]
        if level == "day":
            target_freqs = ()
        elif level == "week":
            week_rows = aggregate_tdx_week_klines(day_rows)[-max(1, min(int(limit or 1200), 5000)):] if day_rows else []
            if week_rows:
                upsert_klines(symbol, "week", week_rows, adjustflag="3", source="tdx")
            target_freqs = ("week",)
        elif level in {"5", "15", "30", "60", "1"}:
            minute_rows = read_tdx_derived_minute_klines(symbol, level, limit=max(int(limit or 0), 5000))
            if minute_rows:
                upsert_klines(symbol, level, minute_rows, adjustflag="3", source="tdx")
            target_freqs = (level,)
        else:
            return False

        result = rebuild_tdx_qfq_from_existing_factors(symbol, target_freqs=target_freqs, limit=max(int(limit or 0), 5000))
        return result.total_written > 0 or result.day_factor_count > 0
    except Exception as exc:
        logger.debug("TDX structure bootstrap failed for %s/%s: %s", symbol, level, exc)
        return False


def _stable_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _now_text() -> str:
    return datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds")
