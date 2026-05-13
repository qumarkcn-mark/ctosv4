"""Serialize CZSC objects into CT-OS structure contracts."""

from __future__ import annotations

from typing import Any


def serialize_czsc_level(czsc_obj: Any, rows: list[dict], level: str, zhongshus: list[Any] | None = None) -> dict[str, Any]:
    fxs = [_serialize_fx(item) for item in list(getattr(czsc_obj, "fx_list", []) or [])]
    bis = [_serialize_bi(item) for item in list(getattr(czsc_obj, "bi_list", []) or [])]
    zs_objects = list(zhongshus if zhongshus is not None else (getattr(czsc_obj, "zs_list", []) or []))
    serialized_zss = [_serialize_zs(item) for item in zs_objects]
    latest_zs = serialized_zss[-1] if serialized_zss else {}
    price = _last_price(rows, bis)

    return {
        "level": level,
        "klines": [_serialize_row(row) for row in rows],
        "fxs": fxs,
        "bis": bis,
        "segs": [],
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
            "seg_zs_count": 0,
            "bsp_count": 0,
        },
        "metadata": {
            "unsupported_fields": ["segs", "seg_zhongshus", "bsps"],
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


def _pct(a: float, b: float) -> float | None:
    if a <= 0 or b <= 0:
        return None
    return round((a - b) / b * 100, 2)
