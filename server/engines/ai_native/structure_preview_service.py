"""Live CZSC structure preview for Kline overlays.

Preview is chart-only as an AI trigger boundary: it reads the canonical
structure cache, enqueues snapshot compute on cache miss, and never enqueues
reasoning jobs.
"""

from __future__ import annotations

from typing import Any

from server.domain.symbols import normalize_symbol
from server.engines.ai_native.czsc_snapshot_service import DEFAULT_COMPUTE_PROFILE, enqueue_snapshot_job
from server.engines.ai_native.structure_view_service import build_structure_view_from_snapshot_payload
from server.engines.structure.canonical_structure_service import get_latest_structure, signature_for_level
from server.engines.structure.structure_key import normalize_freq


def get_structure_preview(
    *,
    symbol: str,
    level: str,
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
    count: int = 1200,
) -> dict[str, Any] | None:
    """Return live CZSC overlay geometry from cache, or enqueue background compute."""
    canonical = normalize_symbol(symbol)
    normalized_level = normalize_freq(level)
    snapshot_row = get_latest_structure(
        symbol=canonical,
        level=normalized_level,
        min_profile=compute_profile,
        allow_bootstrap=False,
    )
    if not snapshot_row:
        signature = signature_for_level(
            symbol=canonical,
            level=normalized_level,
            compute_profile=compute_profile,
            allow_bootstrap=False,
        )
        if not signature.get("signature"):
            return {
                "status": "missing_data",
                "symbol": canonical,
                "level": normalized_level,
                "compute_profile": compute_profile,
                "message": "结构数据未就绪",
            }
        job = enqueue_snapshot_job(
            symbol=canonical,
            level=normalized_level,
            compute_profile=compute_profile,
            data_signature=signature["signature"],
            priority=88,
            reason="kline_preview_cache_miss",
            retry_terminal=True,
            force_rebuild=False,
        )
        return {
            "status": "queued",
            "symbol": canonical,
            "level": normalized_level,
            "compute_profile": compute_profile,
            "data_signature": signature.get("signature") or "",
            "data_as_of": signature.get("last_date") or "",
            "job": {
                "job_id": job.get("job_id") or "",
                "status": job.get("status") or "",
                "enqueued": bool(job.get("enqueued")),
                "bumped": bool(job.get("bumped")),
            },
        }
    payload = snapshot_row.get("snapshot") or {}
    if not isinstance(payload, dict) or payload.get("error"):
        return None

    return build_structure_view_from_snapshot_payload(
        symbol=canonical,
        level=normalized_level,
        snapshot=payload,
        count=count,
        mode="preview",
        persisted=True,
        snapshot_id=snapshot_row.get("snapshot_id") or "",
        engine=snapshot_row.get("engine") or "czsc",
        engine_version=snapshot_row.get("engine_version") or czsc_adapter.get_czsc_engine_version(),
        adapter_version=snapshot_row.get("adapter_version") or czsc_adapter.ADAPTER_VERSION,
        compute_profile=snapshot_row.get("compute_profile") or compute_profile,
        data_signature=snapshot_row.get("data_signature") or "",
        data_as_of=snapshot_row.get("data_as_of") or "",
        updated_at=snapshot_row.get("updated_at") or "",
        status="preview",
    )
