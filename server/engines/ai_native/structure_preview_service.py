"""Live CZSC structure preview for Kline overlays.

Preview is intentionally chart-only: it computes the current CZSC geometry from
the latest formal K-line source, but never writes snapshots or triggers AI.
"""

from __future__ import annotations

from typing import Any

from server.domain.symbols import normalize_symbol
from server.engines.ai_native.czsc_snapshot_service import DEFAULT_COMPUTE_PROFILE, now_text
from server.engines.ai_native.structure_view_service import build_structure_view_from_snapshot_payload
from server.engines.structure import czsc_adapter
from server.engines.structure.source_policy import structure_signature_for_policy
from server.engines.structure.structure_key import normalize_freq, resolve_compute_bars


def get_structure_preview(
    *,
    symbol: str,
    level: str,
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
    count: int = 1200,
) -> dict[str, Any] | None:
    """Return live CZSC overlay geometry without persisting a snapshot."""
    canonical = normalize_symbol(symbol)
    normalized_level = normalize_freq(level)
    limit = resolve_compute_bars(compute_profile, normalized_level, fallback=count)
    result = czsc_adapter.analyze_czsc_structure_sync(
        canonical,
        levels=[normalized_level],
        count=limit,
        compute_profile=compute_profile,
    )
    if result.get("error"):
        return None
    payload = (result.get("levels") or {}).get(normalized_level) or {}
    if not isinstance(payload, dict) or payload.get("error"):
        return None

    signature = structure_signature_for_policy(symbol=canonical, level=normalized_level, limit=limit)
    return build_structure_view_from_snapshot_payload(
        symbol=canonical,
        level=normalized_level,
        snapshot=payload,
        count=count,
        mode="preview",
        persisted=False,
        snapshot_id="",
        engine=result.get("engine") or "czsc",
        engine_version=czsc_adapter.get_czsc_engine_version(),
        adapter_version=czsc_adapter.ADAPTER_VERSION,
        compute_profile=compute_profile,
        data_signature=signature.get("signature") or "",
        data_as_of=signature.get("last_date") or "",
        updated_at=now_text(),
        status="preview",
    )
