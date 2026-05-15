"""Public CZSC structure view for Kline overlays.

This service is read-only. It consumes persisted V5 CZSC snapshots and never
invokes the structure engine inline.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from server.domain.symbols import normalize_symbol
from server.engines.ai_native.czsc_snapshot_service import (
    DEFAULT_COMPUTE_PROFILE,
    get_latest_snapshot,
)
from server.engines.structure.czsc_serializer import derive_segments_from_serialized_bis
from server.engines.structure.structure_key import normalize_freq


def get_structure_view(
    *,
    symbol: str,
    level: str,
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
    count: int = 1200,
) -> dict[str, Any] | None:
    """Return CZSC bi / segment / center geometry for chart display."""
    canonical = normalize_symbol(symbol)
    normalized_level = normalize_freq(level)
    snapshot_row = get_latest_snapshot(
        symbol=canonical,
        level=normalized_level,
        compute_profile=compute_profile,
    )
    if not snapshot_row:
        return None

    snapshot = snapshot_row.get("snapshot") or {}
    klines = list(snapshot.get("klines") or [])
    if count > 0:
        klines = klines[-count:]
    time_axis = _build_time_axis(klines)

    bis = [
        _normalize_bi(item, index=index, time_axis=time_axis, snapshot_id=snapshot_row["snapshot_id"])
        for index, item in enumerate(snapshot.get("bis") or [])
        if isinstance(item, dict)
    ]
    centers = [
        _normalize_center(
            item,
            index=index,
            time_axis=time_axis,
            klines=klines,
            snapshot_id=snapshot_row["snapshot_id"],
            active=False,
        )
        for index, item in enumerate(snapshot.get("bi_zhongshus") or [])
        if isinstance(item, dict)
    ]
    active_center = None
    if isinstance(snapshot.get("active_zhongshu"), dict) and snapshot.get("active_zhongshu"):
        active_center = _normalize_center(
            snapshot["active_zhongshu"],
            index=len(centers),
            time_axis=time_axis,
            klines=klines,
            snapshot_id=snapshot_row["snapshot_id"],
            active=True,
        )
        centers = _mark_active_center(centers, active_center)
    segments = _normalize_segments(snapshot, time_axis=time_axis, snapshot_id=snapshot_row["snapshot_id"])
    if not segments:
        derived_snapshot = {**snapshot, "segs": derive_segments_from_serialized_bis(snapshot.get("bis") or [])}
        segments = _normalize_segments(derived_snapshot, time_axis=time_axis, snapshot_id=snapshot_row["snapshot_id"])
    unsupported_fields = _unsupported_fields(snapshot)
    segment_source = _segment_source(snapshot, segments)

    return {
        "version": "structure_view.v1",
        "symbol": canonical,
        "level": normalized_level,
        "engine": snapshot_row.get("engine") or "czsc",
        "engine_version": snapshot_row.get("engine_version") or "",
        "adapter_version": snapshot_row.get("adapter_version") or "",
        "compute_profile": snapshot_row.get("compute_profile") or compute_profile,
        "snapshot_id": snapshot_row["snapshot_id"],
        "data_signature": snapshot_row.get("data_signature") or "",
        "data_as_of": snapshot_row.get("data_as_of") or "",
        "updated_at": snapshot_row.get("updated_at") or "",
        "status": snapshot_row.get("status") or "fresh",
        "price": _num(snapshot.get("price")),
        "capabilities": {
            "bis": bool([item for item in bis if item]),
            "segments": bool(segments),
            "centers": bool([item for item in centers if item] or active_center),
            "segment_status": "ready" if segments else "unavailable",
            "segment_source": segment_source,
            "segment_reason": segment_source if segments else _segment_unavailable_reason(unsupported_fields),
        },
        "bar_axis": {
            "count": len(klines),
            "first_time": _bar_time(klines[0]) if klines else "",
            "last_time": _bar_time(klines[-1]) if klines else "",
        },
        "bis": [item for item in bis if item],
        "segments": segments,
        "centers": [item for item in centers if item],
        "active_center": active_center,
    }


def _normalize_bi(item: dict[str, Any], *, index: int, time_axis: dict[int, int], snapshot_id: str) -> dict[str, Any] | None:
    start_time = _time_text(item.get("x0") or item.get("start_time") or item.get("begin_time") or item.get("begin_date"))
    end_time = _time_text(item.get("x1") or item.get("end_time") or item.get("end_date"))
    start_ts = _parse_time(start_time)
    end_ts = _parse_time(end_time)
    start_price = _num(item.get("y0") or item.get("start_price"))
    end_price = _num(item.get("y1") or item.get("end_price"))
    if not start_ts or not end_ts or start_price <= 0 or end_price <= 0:
        return None
    return {
        "id": f"{snapshot_id}:bi:{index}",
        "index": index,
        "direction": item.get("direction") or ("up" if end_price >= start_price else "down"),
        "is_up": bool(item.get("is_up", end_price >= start_price)),
        "is_sure": bool(item.get("is_sure", True)),
        "start_time": start_time,
        "end_time": end_time,
        "start_timestamp": start_ts,
        "end_timestamp": end_ts,
        "start_index": _nearest_index(time_axis, start_ts),
        "end_index": _nearest_index(time_axis, end_ts),
        "start_price": start_price,
        "end_price": end_price,
        "high": _num(item.get("high")),
        "low": _num(item.get("low")),
        "bar_count": int(_num(item.get("bar_count"))),
        "source": item.get("source") or "",
        "start_bi_index": _optional_int(item.get("start_bi_index")),
        "end_bi_index": _optional_int(item.get("end_bi_index")),
    }


def _normalize_center(
    item: dict[str, Any],
    *,
    index: int,
    time_axis: dict[int, int],
    klines: list[dict[str, Any]],
    snapshot_id: str,
    active: bool,
) -> dict[str, Any] | None:
    begin_time = _time_text(item.get("begin_date") or item.get("begin_time") or item.get("start_time"))
    end_time = _time_text(item.get("end_date") or item.get("end_time"))
    begin_ts = _parse_time(begin_time)
    end_ts = _parse_time(end_time)
    zd = _num(item.get("zd"))
    zg = _num(item.get("zg"))
    if not begin_ts or not end_ts or zd <= 0 or zg <= 0:
        return None
    raw_begin_index = _nearest_index(time_axis, begin_ts)
    raw_end_index = _nearest_index(time_axis, end_ts)
    begin_index = _center_entry_index(klines, zd=zd, zg=zg, fallback=raw_begin_index, raw_end=raw_end_index)
    end_index = _center_exit_index(klines, zd=zd, zg=zg, fallback=raw_end_index, raw_begin=begin_index)
    return {
        "id": f"{snapshot_id}:center:{'active' if active else index}",
        "index": index,
        "active": active,
        "begin_time": begin_time,
        "end_time": end_time,
        "begin_timestamp": begin_ts,
        "end_timestamp": end_ts,
        "begin_index": begin_index,
        "end_index": end_index,
        "raw_begin_index": raw_begin_index,
        "raw_end_index": raw_end_index,
        "zd": zd,
        "zg": zg,
        "zz": _num(item.get("zz")),
        "gg": _num(item.get("gg")),
        "dd": _num(item.get("dd")),
        "bi_count": int(_num(item.get("bi_count"))),
        "sdir": item.get("sdir") or "",
        "edir": item.get("edir") or "",
        "is_valid": bool(item.get("is_valid", True)),
    }


def _normalize_segments(snapshot: dict[str, Any], *, time_axis: dict[int, int], snapshot_id: str) -> list[dict[str, Any]]:
    raw_segments = (
        snapshot.get("segs")
        or snapshot.get("segments")
        or snapshot.get("xds")
        or snapshot.get("duans")
        or snapshot.get("line_segments")
        or []
    )
    result = []
    for index, item in enumerate(raw_segments if isinstance(raw_segments, list) else []):
        if not isinstance(item, dict):
            continue
        segment = _normalize_bi(item, index=index, time_axis=time_axis, snapshot_id=f"{snapshot_id}:segment")
        if segment:
            segment["id"] = f"{snapshot_id}:segment:{index}"
            result.append(segment)
    return result


def _unsupported_fields(snapshot: dict[str, Any]) -> list[str]:
    metadata = snapshot.get("metadata") if isinstance(snapshot.get("metadata"), dict) else {}
    fields = metadata.get("unsupported_fields") if isinstance(metadata, dict) else []
    return [str(item) for item in fields] if isinstance(fields, list) else []


def _segment_unavailable_reason(unsupported_fields: list[str]) -> str:
    if "segs" in unsupported_fields:
        return "czsc_object_does_not_expose_segments"
    return "snapshot_has_no_segments"


def _segment_source(snapshot: dict[str, Any], segments: list[dict[str, Any]]) -> str:
    metadata = snapshot.get("metadata") if isinstance(snapshot.get("metadata"), dict) else {}
    raw_source = str(metadata.get("segment_source") or "") if isinstance(metadata, dict) else ""
    if segments and raw_source == "unavailable_in_czsc_object" and segments[0].get("source"):
        return str(segments[0]["source"])
    if raw_source:
        return str(metadata["segment_source"])
    for item in segments:
        if item.get("source"):
            return str(item["source"])
    return "snapshot_has_no_segments"


def _mark_active_center(centers: list[dict[str, Any] | None], active_center: dict[str, Any]) -> list[dict[str, Any] | None]:
    marked = []
    for item in centers:
        if not item:
            marked.append(item)
            continue
        same_range = (
            item.get("begin_time") == active_center.get("begin_time")
            and item.get("end_time") == active_center.get("end_time")
            and abs(_num(item.get("zd")) - _num(active_center.get("zd"))) < 1e-8
            and abs(_num(item.get("zg")) - _num(active_center.get("zg"))) < 1e-8
        )
        marked.append({**item, "active": same_range})
    return marked


def _build_time_axis(klines: list[dict[str, Any]]) -> dict[int, int]:
    axis: dict[int, int] = {}
    for index, item in enumerate(klines):
        ts = _parse_time(_bar_time(item))
        if ts:
            axis[ts] = index
    return axis


def _nearest_index(time_axis: dict[int, int], timestamp: int) -> int | None:
    if not time_axis or not timestamp:
        return None
    if timestamp in time_axis:
        return time_axis[timestamp]
    nearest = min(time_axis, key=lambda item: abs(item - timestamp))
    return time_axis[nearest]


def _center_entry_index(
    klines: list[dict[str, Any]],
    *,
    zd: float,
    zg: float,
    fallback: int | None,
    raw_end: int | None,
) -> int | None:
    """Find the first bar of the entering leg that touches the center zone."""
    if fallback is None:
        return None
    start = max(0, fallback)
    end = min(len(klines) - 1, raw_end if raw_end is not None else len(klines) - 1)
    for index in range(start, end + 1):
        if _bar_touches_zone(klines[index], zd=zd, zg=zg):
            return index
    return fallback


def _center_exit_index(
    klines: list[dict[str, Any]],
    *,
    zd: float,
    zg: float,
    fallback: int | None,
    raw_begin: int | None,
) -> int | None:
    """Find the first bar of the leaving leg that exits the center zone."""
    if fallback is None:
        return None
    start = max(raw_begin or 0, fallback)
    for index in range(start, len(klines)):
        if _bar_leaves_zone(klines[index], zd=zd, zg=zg):
            return index
    return fallback


def _bar_touches_zone(bar: dict[str, Any], *, zd: float, zg: float) -> bool:
    high = _num(bar.get("high"))
    low = _num(bar.get("low"))
    close = _num(bar.get("close"))
    if close > 0 and zd <= close <= zg:
        return True
    if high <= 0 or low <= 0:
        return False
    return high >= zd and low <= zg


def _bar_leaves_zone(bar: dict[str, Any], *, zd: float, zg: float) -> bool:
    close = _num(bar.get("close"))
    if close > 0:
        return close > zg or close < zd
    high = _num(bar.get("high"))
    low = _num(bar.get("low"))
    return high > 0 and low > 0 and (low > zg or high < zd)


def _bar_time(item: dict[str, Any]) -> str:
    return _time_text(item.get("time") or item.get("date") or item.get("datetime") or item.get("timestamp"))


def _time_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_time(value: str) -> int:
    text = _time_text(value)
    if not text:
        return 0
    if text.isdigit():
        raw = int(text)
        return raw * 1000 if raw < 10_000_000_000 else raw
    normalized = text.replace("/", "-").replace("T", " ")
    parse_candidates = [normalized]
    if len(normalized) >= 16:
        parse_candidates.append(normalized[:16])
    if len(normalized) >= 10:
        parse_candidates.append(normalized[:10])
    for candidate in parse_candidates:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return int(datetime.strptime(candidate, fmt).timestamp() * 1000)
            except ValueError:
                continue
    return 0


def _num(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed == parsed else 0.0


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
