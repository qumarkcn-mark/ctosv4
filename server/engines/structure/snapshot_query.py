"""Snapshot-first Chan detail query service.

The old `get_chan_detail()` remains the compatibility calculator. This module
owns job/snapshot orchestration so scheduling does not leak into legacy services.
"""

from __future__ import annotations

from typing import Any, Optional

from server.db.kline_lake import get_kline_window_signature
from server.domain.symbols import normalize_symbol
from server.engines.structure.chan_snapshot_cache import (
    load_chan_snapshot_by_key_hash,
    load_latest_chan_snapshot_for_series,
    save_chan_snapshot,
)
from server.engines.structure.structure_jobs import enqueue_structure_job, get_structure_job_by_hash
from server.engines.structure.structure_key import (
    ADAPTER_VERSION,
    ENGINE_VERSION,
    FORMAL_ADJUSTFLAG,
    FORMAL_SOURCE,
    SNAPSHOT_SCHEMA_VERSION,
    StructureKey,
    build_structure_key,
    normalize_freq,
    resolve_compute_bars,
)


def build_formal_structure_key(
    *,
    symbol: str,
    freq: str,
    cchan_preset: str = "live_tolerant",
    compute_profile: str = "chart_standard_v1",
) -> tuple[Optional[StructureKey], dict[str, Any]]:
    canonical_symbol = normalize_symbol(symbol)
    normalized_freq = normalize_freq(freq)
    compute_bars = resolve_compute_bars(compute_profile, normalized_freq)
    signature = get_kline_window_signature(
        canonical_symbol,
        normalized_freq,
        limit=compute_bars,
        adjustflag=FORMAL_ADJUSTFLAG,
        source=FORMAL_SOURCE,
    )
    if not signature.get("signature"):
        return None, {
            "symbol": canonical_symbol,
            "freq": normalized_freq,
            "compute_bars": compute_bars,
            "data_signature": "",
            "freshness": _freshness_from_signature(signature, stale_reason="NO_DATA"),
        }
    structure_key = build_structure_key(
        symbol=canonical_symbol,
        freq=normalized_freq,
        data_signature=signature["signature"],
        cchan_preset=cchan_preset,
        compute_profile=compute_profile,
    )
    return structure_key, {
        "symbol": canonical_symbol,
        "freq": normalized_freq,
        "compute_bars": compute_bars,
        "data_signature": signature["signature"],
        "freshness": _freshness_from_signature(signature),
    }


async def get_structure_snapshot_or_enqueue(
    *,
    symbol: str,
    freq: str = "day",
    display_count: int = 500,
    cchan_preset: str = "live_tolerant",
    compute_profile: str = "chart_standard_v1",
    snapshot_mode: str = "prefer_stale",
    sync_if_missing: bool = False,
    priority: int = 90,
    requested_by_user_id: Optional[int] = None,
) -> dict[str, Any]:
    structure_key, context = build_formal_structure_key(
        symbol=symbol,
        freq=freq,
        cchan_preset=cchan_preset,
        compute_profile=compute_profile,
    )
    if structure_key is None:
        return _envelope(
            snapshot_status="missing",
            structure_key=None,
            context=context,
            job=None,
            result={"error": "NO_DATA"},
        )

    fresh = load_chan_snapshot_by_key_hash(structure_key.hash)
    if fresh is not None:
        return _envelope(
            snapshot_status="fresh",
            structure_key=structure_key,
            context=context,
            job=None,
            result=_slice_result(fresh, display_count),
        )

    if sync_if_missing:
        result = await _compute_and_save_now(
            structure_key=structure_key,
            context=context,
            display_count=display_count,
        )
        if result and not result.get("error"):
            return _envelope(
                snapshot_status="fresh",
                structure_key=structure_key,
                context=context,
                job=None,
                result=_slice_result(result, display_count),
            )

    job = None
    if snapshot_mode != "fresh_only":
        job = enqueue_structure_job(
            structure_key,
            priority=priority,
            reason="snapshot_query",
            requested_by_user_id=requested_by_user_id,
            recompute_completed=True,
        )
    else:
        job = get_structure_job_by_hash(structure_key.hash)

    latest = None
    if snapshot_mode == "prefer_stale":
        latest = load_latest_chan_snapshot_for_series(
            symbol=structure_key.symbol,
            freq=structure_key.freq,
            cchan_preset=structure_key.cchan_preset,
            compute_profile=structure_key.compute_profile,
            kline_source=structure_key.source,
            adjustflag=structure_key.adjustflag,
        )
    if latest:
        result = latest["result"]
        result["snapshot"] = latest["snapshot"]
        return _envelope(
            snapshot_status="stale",
            structure_key=structure_key,
            context=context,
            job=job,
            result=_slice_result(result, display_count),
        )

    failed_job = job if job and str(job.get("status")) == "FAILED_FINAL" else None
    if failed_job:
        return _envelope(
            snapshot_status="failed",
            structure_key=structure_key,
            context=context,
            job=failed_job,
            result={"error": failed_job.get("error_code") or "ENGINE_ERROR"},
        )
    return _envelope(
        snapshot_status="pending",
        structure_key=structure_key,
        context=context,
        job=job,
        result={"klines": [], "bis": [], "segs": [], "bi_zhongshus": [], "seg_zhongshus": [], "bsps": []},
    )


async def _compute_and_save_now(
    *,
    structure_key: StructureKey,
    context: dict[str, Any],
    display_count: int,
) -> dict[str, Any]:
    from server.services.chan_detail_service import get_chan_detail

    result = await get_chan_detail(
        structure_key.symbol,
        freq=structure_key.freq,
        count=int(context["compute_bars"]),
        cchan_preset=structure_key.cchan_preset,
        kline_source=structure_key.source,
        adjustflag=structure_key.adjustflag,
        max_compute_bars=int(context["compute_bars"]),
    )
    if result.get("error"):
        return result
    provider = (result.get("data_source") or {}).get("provider")
    if provider and provider != FORMAL_SOURCE:
        result["snapshot_status"] = "degraded"
        return result
    fingerprint = save_chan_snapshot(
        symbol=structure_key.symbol,
        freq=structure_key.freq,
        cchan_preset=structure_key.cchan_preset,
        kline_source=structure_key.source,
        adjustflag=structure_key.adjustflag,
        end_date="",
        max_compute_bars=int(context["compute_bars"]),
        data_signature=structure_key.data_signature,
        last_kline_time=context["freshness"].get("last_bar_at") or "",
        kline_count=int(context["freshness"].get("kline_count") or 0),
        compute_bars=int(result.get("compute_bars") or context["compute_bars"]),
        result=result,
        structure_key_hash=structure_key.hash,
        compute_profile=structure_key.compute_profile,
        engine_version=ENGINE_VERSION,
        adapter_version=ADAPTER_VERSION,
    )
    result["snapshot"] = {
        "hit": False,
        "source": "sync_if_missing",
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "structure_key_hash": structure_key.hash,
        "data_signature": structure_key.data_signature,
        "structure_fingerprint": fingerprint,
        "last_kline_time": context["freshness"].get("last_bar_at") or "",
        "kline_count": int(context["freshness"].get("kline_count") or 0),
        "compute_profile": structure_key.compute_profile,
    }
    return result


def _envelope(
    *,
    snapshot_status: str,
    structure_key: Optional[StructureKey],
    context: dict[str, Any],
    job: Optional[dict[str, Any]],
    result: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(result or {})
    payload.setdefault("klines", [])
    payload.setdefault("bis", [])
    payload.setdefault("segs", [])
    payload.setdefault("bi_zhongshus", [])
    payload.setdefault("seg_zhongshus", [])
    payload.setdefault("bsps", [])
    _ensure_zhongshu_display_dates(payload)
    payload["snapshot_status"] = snapshot_status
    payload["job"] = _public_job(job)
    payload["structure_key_hash"] = structure_key.hash if structure_key else ""
    payload["structure_fingerprint"] = (
        (payload.get("snapshot") or {}).get("structure_fingerprint")
        or (job or {}).get("result_fingerprint")
        or ""
    )
    payload["data_signature"] = context.get("data_signature") or ""
    payload["freshness"] = context.get("freshness") or {}
    payload["compute_profile"] = structure_key.compute_profile if structure_key else ""
    return payload


def _slice_result(result: dict[str, Any], display_count: int) -> dict[str, Any]:
    safe_count = int(display_count or 0)
    if safe_count <= 0:
        return result
    cloned = dict(result)
    klines = list(cloned.get("klines") or [])
    if len(klines) <= safe_count:
        return cloned
    cutoff = str(klines[-safe_count].get("time") or "")
    cloned["klines"] = klines[-safe_count:]
    cloned["bis"] = [item for item in cloned.get("bis", []) if str(item.get("x1") or "") >= cutoff]
    cloned["segs"] = [item for item in cloned.get("segs", []) if str(item.get("x1") or "") >= cutoff]
    cloned["bi_zhongshus"] = [item for item in cloned.get("bi_zhongshus", []) if str(item.get("end_date") or "") >= cutoff]
    cloned["bi_zhongshus_decomp"] = [item for item in cloned.get("bi_zhongshus_decomp", []) if str(item.get("end_date") or "") >= cutoff]
    cloned["seg_zhongshus"] = [item for item in cloned.get("seg_zhongshus", []) if str(item.get("end_date") or "") >= cutoff]
    cloned["bsps"] = [item for item in cloned.get("bsps", []) if str(item.get("time") or "") >= cutoff]
    if cloned.get("stats"):
        cloned["stats"] = {**cloned["stats"], "kline_count": len(cloned["klines"])}
    _ensure_zhongshu_display_dates(cloned)
    return cloned


def _ensure_zhongshu_display_dates(payload: dict[str, Any]) -> None:
    """给旧 snapshot 补齐中枢视觉边界，避免前端继续按结构 begin/end 画框。"""
    klines = payload.get("klines") or []
    if not klines:
        return
    for key, strokes_key in (
        ("bi_zhongshus", "bis"),
        ("bi_zhongshus_decomp", "bis"),
        ("seg_zhongshus", "segs"),
        ("zhongshus", "bis"),
    ):
        centers = payload.get(key) or []
        strokes = payload.get(strokes_key) or []
        for center in centers:
            begin, end = _resolve_center_display_dates_from_payload(center, strokes, klines)
            center["display_begin_date"] = begin
            center["display_end_date"] = end


def _resolve_center_display_dates_from_payload(center: dict[str, Any], strokes: list[dict[str, Any]], klines: list[dict[str, Any]]) -> tuple[str, str]:
    zd = _safe_float(center.get("zd"))
    zg = _safe_float(center.get("zg"))
    begin_fallback = str(center.get("begin_date") or "")
    end_fallback = str(center.get("end_date") or "")
    if zd is None or zg is None or not strokes:
        return begin_fallback, end_fallback

    begin_index = _find_stroke_index_for_begin(strokes, begin_fallback)
    entry_stroke = strokes[begin_index - 1] if begin_index > 0 else (strokes[begin_index] if begin_index >= 0 else None)
    display_begin = _first_kline_entering_range(entry_stroke, klines, zd, zg) or begin_fallback

    end_index = _find_stroke_index_for_end(strokes, end_fallback)
    exit_stroke = strokes[end_index] if end_index >= 0 else None
    display_end = _first_kline_fully_outside(exit_stroke, klines, zd, zg) or end_fallback
    return display_begin, display_end


def _find_stroke_index_for_begin(strokes: list[dict[str, Any]], begin_date: str) -> int:
    for index, stroke in enumerate(strokes):
        if str(stroke.get("x0") or "") == begin_date:
            return index
    for index, stroke in enumerate(strokes):
        if str(stroke.get("x0") or "") <= begin_date <= str(stroke.get("x1") or ""):
            return index
    return -1


def _find_stroke_index_for_end(strokes: list[dict[str, Any]], end_date: str) -> int:
    for index, stroke in enumerate(strokes):
        if str(stroke.get("x1") or "") == end_date:
            return index
    for index, stroke in enumerate(strokes):
        if str(stroke.get("x0") or "") <= end_date <= str(stroke.get("x1") or ""):
            return index
    return -1


def _line_value_at_kline(stroke: Optional[dict[str, Any]], kline: dict[str, Any], klines: list[dict[str, Any]]) -> Optional[float]:
    if not stroke:
        return None
    x0_time = str(stroke.get("x0") or "")
    x1_time = str(stroke.get("x1") or "")
    x_time = str(kline.get("time") or "")
    index_by_time = {str(item.get("time") or ""): idx for idx, item in enumerate(klines)}
    if x0_time not in index_by_time or x1_time not in index_by_time or x_time not in index_by_time:
        return None
    x0 = index_by_time[x0_time]
    x1 = index_by_time[x1_time]
    x = index_by_time[x_time]
    y0 = _safe_float(stroke.get("y0"))
    y1 = _safe_float(stroke.get("y1"))
    if y0 is None or y1 is None:
        return None
    if x1 == x0:
        return y1
    ratio = (x - x0) / (x1 - x0)
    return y0 + (y1 - y0) * ratio


def _first_kline_entering_range(stroke: Optional[dict[str, Any]], klines: list[dict[str, Any]], zd: float, zg: float) -> str:
    is_up = bool((stroke or {}).get("is_up"))
    was_outside = False
    for kline in _iter_stroke_klines(stroke, klines):
        value = _line_value_at_kline(stroke, kline, klines)
        if value is None:
            continue
        inside = zd <= value <= zg
        if not inside:
            was_outside = True
            continue
        if not was_outside:
            continue
        if is_up and value >= zd:
            return str(kline.get("time") or "")
        if not is_up and value <= zg:
            return str(kline.get("time") or "")
    return ""


def _first_kline_fully_outside(stroke: Optional[dict[str, Any]], klines: list[dict[str, Any]], zd: float, zg: float) -> str:
    is_up = bool((stroke or {}).get("is_up"))
    was_inside = False
    for kline in _iter_stroke_klines(stroke, klines):
        value = _line_value_at_kline(stroke, kline, klines)
        if value is None:
            continue
        inside = zd <= value <= zg
        if inside:
            was_inside = True
            continue
        if not was_inside:
            continue
        if is_up and value > zg:
            return str(kline.get("time") or "")
        if not is_up and value < zd:
            return str(kline.get("time") or "")
    return ""


def _iter_stroke_klines(stroke: Optional[dict[str, Any]], klines: list[dict[str, Any]]):
    if not stroke:
        return
    start = str(stroke.get("x0") or "")
    end = str(stroke.get("x1") or "")
    if not start or not end:
        return
    for kline in klines:
        time = str(kline.get("time") or "")
        if start <= time <= end:
            yield kline


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _freshness_from_signature(signature: dict[str, Any], stale_reason: str = "") -> dict[str, Any]:
    return {
        "source": signature.get("source") or FORMAL_SOURCE,
        "adjustflag": FORMAL_ADJUSTFLAG,
        "first_bar_at": signature.get("first_date") or "",
        "last_bar_at": signature.get("last_date") or "",
        "kline_count": int(signature.get("row_count") or 0),
        "is_stale": bool(stale_reason),
        "stale_reason": stale_reason,
    }


def _public_job(job: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not job:
        return None
    return {
        "job_id": job.get("job_id"),
        "status": job.get("status"),
        "priority": job.get("priority"),
        "retry_count": job.get("retry_count"),
        "error_code": job.get("error_code"),
        "error_message": job.get("error_message"),
        "enqueued": job.get("enqueued"),
        "bumped": job.get("bumped"),
    }
