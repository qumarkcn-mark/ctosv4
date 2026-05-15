"""Serialize CZSC objects into CT-OS structure contracts."""

from __future__ import annotations

from typing import Any


def serialize_czsc_level(czsc_obj: Any, rows: list[dict], level: str, zhongshus: list[Any] | None = None) -> dict[str, Any]:
    fxs = [_serialize_fx(item) for item in list(getattr(czsc_obj, "fx_list", []) or [])]
    bis = [_serialize_bi(item) for item in list(getattr(czsc_obj, "bi_list", []) or [])]
    segs, segment_source = _serialize_segments(czsc_obj, bis)
    zs_objects = list(zhongshus if zhongshus is not None else (getattr(czsc_obj, "zs_list", []) or []))
    serialized_zss = [_serialize_zs(item) for item in zs_objects]
    latest_zs = serialized_zss[-1] if serialized_zss else {}
    price = _last_price(rows, bis)
    unsupported_fields = ["seg_zhongshus", "bsps"]
    if not segs:
        unsupported_fields.append("segs")

    return {
        "level": level,
        "klines": [_serialize_row(row) for row in rows],
        "fxs": fxs,
        "bis": bis,
        "segs": segs,
        "segments": segs,
        "bi_zhongshus": serialized_zss,
        "seg_zhongshus": [],
        "zhongshus": serialized_zss,
        "bsps": [],
        "price": price,
        "last_bi_dir": _last_bi_dir(bis),
        "active_zhongshu": latest_zs,
        "zg": _num(latest_zs.get("zg")),
        "zd": _num(latest_zs.get("zd")),
        "state_hint": _state_hint(price, latest_zs),
        "price_vs_center": _price_vs_center(price, latest_zs),
        "stats": {
            "kline_count": len(rows),
            "fx_count": len(fxs),
            "bi_count": len(bis),
            "bi_zs_count": len(serialized_zss),
            "seg_count": len(segs),
            "seg_zs_count": 0,
            "bsp_count": 0,
        },
        "metadata": {
            "unsupported_fields": unsupported_fields,
            "segment_source": segment_source,
        },
    }


def _serialize_row(row: dict) -> dict[str, Any]:
    return {
        "time": str(row.get("date") or row.get("dt") or ""),
        "open": _num(row.get("open")),
        "high": _num(row.get("high")),
        "low": _num(row.get("low")),
        "close": _num(row.get("close")),
        "volume": _num(row.get("volume") or row.get("vol")),
        "amount": _num(row.get("amount")),
    }


def _serialize_fx(fx: Any) -> dict[str, Any]:
    return {
        "dt": _dt(getattr(fx, "dt", "")),
        "mark": _enum_value(getattr(fx, "mark", "")),
        "high": _num(getattr(fx, "high", 0)),
        "low": _num(getattr(fx, "low", 0)),
        "fx": _num(getattr(fx, "fx", 0)),
        "has_zs": bool(getattr(fx, "has_zs", False)),
    }


def _serialize_bi(bi: Any) -> dict[str, Any]:
    fx_a = getattr(bi, "fx_a", None)
    fx_b = getattr(bi, "fx_b", None)
    high = _call_or_attr(bi, "get_high", "high")
    low = _call_or_attr(bi, "get_low", "low")
    start_price = _num(getattr(fx_a, "fx", 0)) if fx_a else 0.0
    end_price = _num(getattr(fx_b, "fx", 0)) if fx_b else 0.0
    direction = _enum_value(getattr(bi, "direction", ""))
    is_up = direction.lower().endswith("up") or direction in {"向上", "Up"}
    return {
        "x0": _dt(getattr(fx_a, "dt", "")) if fx_a else "",
        "x1": _dt(getattr(fx_b, "dt", "")) if fx_b else "",
        "y0": start_price,
        "y1": end_price,
        "start_price": start_price,
        "end_price": end_price,
        "high": _num(high),
        "low": _num(low),
        "bar_count": _int_attr(bi, "length"),
        "is_up": bool(is_up),
        "direction": "up" if is_up else "down",
        "is_sure": True,
    }


def _serialize_segments(czsc_obj: Any, bis: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    for attr_name in ("seg_list", "segs", "segments", "xd_list", "xds", "duans", "line_segments"):
        raw = getattr(czsc_obj, attr_name, None)
        if callable(raw) or not raw:
            continue
        try:
            items = list(raw)
        except TypeError:
            continue
        segments = [_serialize_bi(item) for item in items]
        segments = [item for item in segments if item.get("x0") and item.get("x1")]
        if segments:
            for item in segments:
                item["source"] = "czsc_object"
            return segments, "czsc_object"

    derived = derive_segments_from_serialized_bis(bis)
    if derived:
        return derived, "derived_from_czsc_bis_v1"
    return [], "unavailable_in_czsc_object"


def derive_segments_from_serialized_bis(bis: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive coarse line segments from confirmed CZSC BI endpoints.

    CZSC 0.10.12 exposes BI but not native line segments. This keeps the
    dependency boundary honest: the derived segments are display geometry from
    CZSC BI facts, not a restored legacy Chan implementation.
    """
    confirmed = [item for item in bis if item.get("is_sure", True) and item.get("x0") and item.get("x1")]
    if len(confirmed) < 3:
        return []

    pivots = [_start_pivot(confirmed[0], 0)]
    pivots.extend(_local_extreme_pivots(confirmed))
    pivots.append(_end_pivot(confirmed[-1], len(confirmed) - 1))
    pivots = _dedupe_and_alternate_pivots(sorted(pivots, key=lambda item: item["bi_index"]))

    segments = []
    for index in range(1, len(pivots)):
        start = pivots[index - 1]
        end = pivots[index]
        if end["bi_index"] - start["bi_index"] < 2:
            continue
        is_up = end["price"] >= start["price"]
        segments.append(
            {
                "x0": start["time"],
                "x1": end["time"],
                "y0": start["price"],
                "y1": end["price"],
                "start_price": start["price"],
                "end_price": end["price"],
                "high": max(start["price"], end["price"]),
                "low": min(start["price"], end["price"]),
                "bar_count": sum(_int_value(item.get("bar_count")) for item in confirmed[start["bi_index"] : end["bi_index"] + 1]),
                "is_up": is_up,
                "direction": "up" if is_up else "down",
                "is_sure": bool(start.get("is_sure", True) and end.get("is_sure", True)),
                "source": "derived_from_czsc_bis_v1",
                "start_bi_index": start["bi_index"],
                "end_bi_index": end["bi_index"],
            }
        )
    return segments


def _local_extreme_pivots(bis: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tops = [(idx, _num(item.get("end_price") or item.get("y1")), item) for idx, item in enumerate(bis) if item.get("is_up")]
    bottoms = [(idx, _num(item.get("end_price") or item.get("y1")), item) for idx, item in enumerate(bis) if not item.get("is_up")]
    pivots = []
    for left, current, right in zip(tops, tops[1:], tops[2:]):
        if current[1] >= left[1] and current[1] > right[1]:
            pivots.append(_pivot_from_bi(current[2], current[0], "top", is_sure=True))
    for left, current, right in zip(bottoms, bottoms[1:], bottoms[2:]):
        if current[1] <= left[1] and current[1] < right[1]:
            pivots.append(_pivot_from_bi(current[2], current[0], "bottom", is_sure=True))
    return pivots


def _dedupe_and_alternate_pivots(pivots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for pivot in pivots:
        if not result:
            result.append(pivot)
            continue
        prev = result[-1]
        if pivot["bi_index"] == prev["bi_index"]:
            if _is_more_extreme(pivot, prev):
                result[-1] = pivot
            continue
        if pivot["kind"] == prev["kind"]:
            if _is_more_extreme(pivot, prev):
                result[-1] = pivot
            continue
        result.append(pivot)
    return result


def _is_more_extreme(candidate: dict[str, Any], existing: dict[str, Any]) -> bool:
    if candidate["kind"] == "top":
        return candidate["price"] >= existing["price"]
    return candidate["price"] <= existing["price"]


def _start_pivot(bi: dict[str, Any], bi_index: int) -> dict[str, Any]:
    return {
        "kind": "bottom" if bi.get("is_up") else "top",
        "time": bi.get("x0") or "",
        "price": _num(bi.get("start_price") or bi.get("y0")),
        "bi_index": bi_index,
        "is_sure": True,
    }


def _end_pivot(bi: dict[str, Any], bi_index: int) -> dict[str, Any]:
    return _pivot_from_bi(bi, bi_index, "top" if bi.get("is_up") else "bottom", is_sure=False)


def _pivot_from_bi(bi: dict[str, Any], bi_index: int, kind: str, *, is_sure: bool) -> dict[str, Any]:
    return {
        "kind": kind,
        "time": bi.get("x1") or "",
        "price": _num(bi.get("end_price") or bi.get("y1")),
        "bi_index": bi_index,
        "is_sure": is_sure,
    }


def _serialize_zs(zs: Any) -> dict[str, Any]:
    bis = list(getattr(zs, "bis", []) or [])
    return {
        "begin_date": _dt(getattr(zs, "sdt", "")),
        "end_date": _dt(getattr(zs, "edt", "")),
        "zg": _num(getattr(zs, "zg", 0)),
        "zd": _num(getattr(zs, "zd", 0)),
        "zz": _num(getattr(zs, "zz", 0)),
        "gg": _num(getattr(zs, "gg", 0)),
        "dd": _num(getattr(zs, "dd", 0)),
        "bi_count": len(bis),
        "sdir": _enum_value(getattr(zs, "sdir", "")),
        "edir": _enum_value(getattr(zs, "edir", "")),
        "is_valid": bool(zs.is_valid()) if hasattr(zs, "is_valid") else True,
    }


def _last_price(rows: list[dict], bis: list[dict]) -> float:
    if rows:
        return _num(rows[-1].get("close"))
    if bis:
        return _num(bis[-1].get("y1"))
    return 0.0


def _last_bi_dir(bis: list[dict]) -> str:
    if not bis:
        return "unknown"
    return "up" if bis[-1].get("is_up") else "down"


def _state_hint(price: float, center: dict) -> str:
    relation = _price_vs_center(price, center)
    return str(relation.get("position") or "no_center")


def _price_vs_center(price: float, center: dict) -> dict[str, Any]:
    zg = _num(center.get("zg"))
    zd = _num(center.get("zd"))
    if price <= 0 or zg <= 0 or zd <= 0:
        return {"position": "no_center"}
    if price > zg:
        position = "above_zg"
    elif price < zd:
        position = "below_zd"
    else:
        position = "inside_center"
    return {
        "position": position,
        "distance_to_zg_pct": _pct(price, zg),
        "distance_to_zd_pct": _pct(price, zd),
    }


def _call_or_attr(obj: Any, method_name: str, attr_name: str) -> Any:
    if hasattr(obj, method_name):
        try:
            return getattr(obj, method_name)()
        except TypeError:
            pass
    return getattr(obj, attr_name, 0)


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "")


def _dt(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return text.replace("T", " ")[:19]


def _num(value: Any) -> float:
    try:
        return round(float(value or 0), 4)
    except (TypeError, ValueError):
        return 0.0


def _int_attr(obj: Any, attr_name: str) -> int:
    try:
        return int(getattr(obj, attr_name, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _pct(a: float, b: float) -> float | None:
    if a <= 0 or b <= 0:
        return None
    return round((a - b) / b * 100, 2)
