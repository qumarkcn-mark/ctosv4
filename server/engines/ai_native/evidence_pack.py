"""Build fact-first evidence packs for AI Native Radar.

This module intentionally avoids path labels like "THIRD_SELL_CONFIRMED".
It packages price/location/event relationships so the model can reason from
facts instead of repeating rule-engine conclusions.
"""

from __future__ import annotations


from server.engines.ai_native.divergence_context import DivergenceContext
from server.engines.structure.czsc_evidence import build_czsc_evidence

def build_reasoning_evidence_pack(radar_contract: dict, divergence_context: DivergenceContext | None = None) -> dict:
    structure = radar_contract.get("structure") or {}
    tactical_structure = radar_contract.get("tactical_structure") or {}
    reasoning_structure = tactical_structure or structure
    raw_levels = structure.get("levels") or {}
    reasoning_levels = reasoning_structure.get("levels") or raw_levels
    algorithm = radar_contract.get("algorithm_v2") or {}
    quote = radar_contract.get("quote") or {}
    current_price = _num(quote.get("price") or radar_contract.get("price"))
    if current_price <= 0:
        current_price = _num((algorithm.get("atoms") or {}).get("L1", {}).get("price"))

    levels = {
        key: _level_pack(key, level, current_price)
        for key, level in reasoning_levels.items()
        if isinstance(level, dict)
    }
    key_levels = _key_levels(algorithm, current_price)
    operative_context = _operative_context(levels, key_levels, current_price)
    assertions = _semantic_assertions(levels, divergence_context)
    quote_anchors = _quote_anchors(quote)
    commander_context = _commander_context(
        levels=levels,
        operative_context=operative_context,
        semantic_assertions=assertions,
        quote_anchors=quote_anchors,
        current_price=current_price,
    )
    czsc_structure = radar_contract.get("shadow_structure") or radar_contract.get("czsc_structure") or {}
    return {
        "version": "reasoning_evidence_pack.v1",
        "symbol": radar_contract.get("symbol") or "",
        "as_of": radar_contract.get("as_of") or "",
        "current_price": current_price,
        "quote_anchors": quote_anchors,
        "commander_context": commander_context,
        "structure_scope": "tactical_day_30_5" if tactical_structure else "radar_full_levels",
        "levels": levels,
        "event_stream": _event_stream(levels),
        "operative_context": operative_context,
        "key_levels": key_levels,
        "position_context": _position_pack(radar_contract, current_price),
        "semantic_assertions": assertions,
        "czsc_shadow": build_czsc_evidence(czsc_structure),
        "structure_engine_comparison": radar_contract.get("structure_engine_comparison") or {},
        "data_quality": {
            "freshness": radar_contract.get("freshness") or {},
            "tactical_freshness": radar_contract.get("tactical_freshness") or {},
            "source": radar_contract.get("data_source") or {},
        },
    }


def _quote_anchors(quote: dict) -> dict:
    return {
        "current_price": _num(quote.get("price")),
        "intraday_high": _num(quote.get("high")),
        "intraday_low": _num(quote.get("low")),
        "prev_close": _num(quote.get("prev_close")),
        "time": str(quote.get("time") or quote.get("datetime") or ""),
    }


def _level_pack(level_key: str, level: dict, current_price: float) -> dict:
    active = level.get("active_zhongshu") or {}
    center = {
        "zd": _num(level.get("zd") or active.get("zd")),
        "zg": _num(level.get("zg") or active.get("zg")),
        "dd": _num(level.get("dd") or active.get("dd")),
        "gg": _num(level.get("gg") or active.get("gg")),
        "start": str(active.get("begin_date") or level.get("center_start") or ""),
        "end": str(active.get("end_date") or level.get("center_end") or ""),
    }
    price = _num(level.get("price")) or current_price
    relation = _price_vs_center(price, center)
    return {
        "level": level_key,
        "price": price,
        "center": center,
        "recent_centers": _recent_centers(level),
        "price_vs_center": relation,
        "last_bi_dir": str(level.get("last_bi_dir") or "unknown"),
        "recent_bis": _recent_bis(level),
        "recent_bsp_events": _bsp_events(level_key, level, center, price),
        "momentum": _momentum(level),
    }


def _recent_centers(level: dict) -> list[dict]:
    centers = level.get("bi_zhongshus") or level.get("zhongshus") or []
    result = []
    for item in centers[-3:]:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "zd": _num(item.get("zd")),
                "zg": _num(item.get("zg")),
                "dd": _num(item.get("dd")),
                "gg": _num(item.get("gg")),
                "start": str(item.get("begin_date") or ""),
                "end": str(item.get("end_date") or ""),
            }
        )
    return result


def _price_vs_center(price: float, center: dict) -> dict:
    zd = _num(center.get("zd"))
    zg = _num(center.get("zg"))
    dd = _num(center.get("dd"))
    gg = _num(center.get("gg"))
    if price <= 0 or (zd <= 0 and zg <= 0):
        return {"position": "unknown"}
    if zg > 0 and price > zg:
        position = "above_zg"
    elif zd > 0 and price < zd:
        position = "below_zd"
    else:
        position = "inside_center"
    return {
        "position": position,
        "distance_to_zg_pct": _pct(price, zg) if zg > 0 else None,
        "distance_to_zd_pct": _pct(price, zd) if zd > 0 else None,
        "distance_to_gg_pct": _pct(price, gg) if gg > 0 else None,
        "distance_to_dd_pct": _pct(price, dd) if dd > 0 else None,
        "has_left_up": bool(zg > 0 and price > zg),
        "has_left_down": bool(zd > 0 and price < zd),
        "has_returned_to_center": bool(zd > 0 and zg > 0 and zd <= price <= zg),
    }


def _recent_bis(level: dict) -> list[dict]:
    bis = level.get("detail_bis") or level.get("recent_bis") or level.get("bis") or []
    result = []
    for item in bis[-5:]:
        if not isinstance(item, dict):
            continue
        is_up = bool(item.get("is_up") or item.get("isUp"))
        result.append(
            {
                "dir": "up" if is_up else "down",
                "from": _num(item.get("y0") or item.get("start_price")),
                "to": _num(item.get("y1") or item.get("end_price")),
                "start": str(item.get("x0") or item.get("start_date") or ""),
                "end": str(item.get("x1") or item.get("end_date") or ""),
                "is_sure": bool(item.get("is_sure", item.get("isSure", True))),
            }
        )
    return result


def _bsp_events(level_key: str, level: dict, center: dict, current_price: float) -> list[dict]:
    events = []
    for bsp in (level.get("bsps") or [])[-8:]:
        if not isinstance(bsp, dict):
            continue
        price = _num(bsp.get("price"))
        side = "buy" if bool(bsp.get("is_buy")) else "sell"
        raw_type = str(bsp.get("type") or "")
        events.append(
            {
                "level": level_key,
                "type": raw_type,
                "side": side,
                "time": str(bsp.get("time") or ""),
                "price": price,
                "distance_to_current_pct": _pct(current_price, price) if price > 0 and current_price > 0 else None,
                "center_binding": _event_binding(price, center),
                "semantic_hint": _event_semantic_hint(raw_type, side, price, center, current_price),
            }
        )
    return events


def _event_binding(price: float, center: dict) -> dict:
    relation = _price_vs_center(price, center)
    return {
        "position": relation.get("position", "unknown"),
        "distance_to_zg_pct": relation.get("distance_to_zg_pct"),
        "distance_to_zd_pct": relation.get("distance_to_zd_pct"),
    }


def _event_semantic_hint(raw_type: str, side: str, price: float, center: dict, current_price: float) -> str:
    relation = _price_vs_center(price, center)
    current_relation = _price_vs_center(current_price, center)
    if side == "sell" and raw_type.startswith("3"):
        if relation.get("position") in {"below_zd", "inside_center"} or current_relation.get("position") in {"below_zd", "inside_center"}:
            return "third_sell_event_needs_manual_confirmation"
        return "third_sell_like_event_above_center_not_standard_confirmation"
    if side == "sell" and raw_type.startswith(("1", "2")):
        return "high_area_sell_risk_or_rebound_pressure"
    if side == "buy" and raw_type.startswith("3"):
        return "third_buy_like_event"
    if side == "buy":
        return "repair_or_support_candidate"
    return "raw_event"


def _momentum(level: dict) -> dict:
    compare = level.get("momentum_compare") or {}
    if isinstance(compare, dict):
        return compare
    return {}


def _event_stream(levels: dict) -> list[dict]:
    events = []
    for level in levels.values():
        events.extend(level.get("recent_bsp_events") or [])
    return sorted(events, key=lambda item: (str(item.get("time") or ""), str(item.get("level") or "")))[-20:]


def _operative_context(levels: dict, key_levels: list[dict], current_price: float) -> dict:
    """Separate current operating levels from old/deep structural references."""
    if current_price <= 0:
        return {"status": "unknown"}

    points = []
    for level_key, level in levels.items():
        for point in _swing_points(level_key, level):
            points.append(point)
        for event in level.get("recent_bsp_events") or []:
            points.append(
                {
                    "level": level_key,
                    "price": _num(event.get("price")),
                    "kind": "bsp_event",
                    "label": f"{event.get('side')} {event.get('type')}",
                    "time": event.get("time") or "",
                }
            )
        center = level.get("center") or {}
        for field in ("zg", "zd", "gg", "dd"):
            points.append(
                {
                    "level": level_key,
                    "price": _num(center.get(field)),
                    "kind": f"center_{field}",
                    "label": f"{level_key}.{field.upper()}",
                    "time": center.get("end") or "",
                }
            )
        for idx, recent_center in enumerate(level.get("recent_centers") or []):
            for field in ("zg", "zd", "gg", "dd"):
                points.append(
                    {
                        "level": level_key,
                        "price": _num(recent_center.get(field)),
                        "kind": f"recent_center_{field}",
                        "label": f"{level_key}.recent_center[{idx}].{field.upper()}",
                        "time": recent_center.get("end") or "",
                    }
                )

    for item in key_levels:
        points.append(
            {
                "level": str(item.get("level") or ""),
                "price": _num(item.get("value")),
                "kind": f"rule_{item.get('group') or 'level'}",
                "label": str(item.get("meaning") or item.get("field") or item.get("group") or "rule_level"),
                "time": "",
            }
        )

    normalized = _dedupe_points(points, current_price)
    immediate = [item for item in normalized if item["distance_abs_pct"] <= 12]
    deep = [item for item in normalized if item["distance_abs_pct"] > 12]
    supports = sorted(
        [item for item in immediate if item["relation"] == "below_current"],
        key=lambda item: item["distance_abs_pct"],
    )[:6]
    resistances = sorted(
        [item for item in immediate if item["relation"] == "above_current"],
        key=lambda item: item["distance_abs_pct"],
    )[:6]
    deep_references = sorted(
        [item for item in deep if item["relation"] == "below_current"],
        key=lambda item: item["distance_abs_pct"],
    )[:16]

    nearest_known = normalized[0] if normalized else None
    has_structure_gap = bool(nearest_known and nearest_known["distance_abs_pct"] > 12)
    if supports and resistances:
        current_zone = "between_nearest_support_and_resistance"
    elif supports:
        current_zone = "above_near_support_without_near_resistance"
    elif resistances:
        current_zone = "below_near_resistance_without_near_support"
    else:
        current_zone = "price_structure_gap" if has_structure_gap else "far_from_known_operating_levels"

    return {
        "status": "ready",
        "current_price": current_price,
        "current_zone": current_zone,
        "rule": "Use immediate_supports/resistances for the next trading-day reasoning. Deep references are not current operating boundaries.",
        "nearest_known_level": nearest_known,
        "structure_gap": has_structure_gap,
        "gap_note": "Current price is far from all known structure points; do not use old centers as current operating boundaries." if has_structure_gap else "",
        "immediate_supports": supports,
        "immediate_resistances": resistances,
        "deep_references": deep_references,
    }


def _commander_context(
    *,
    levels: dict,
    operative_context: dict,
    semantic_assertions: list[dict],
    quote_anchors: dict,
    current_price: float,
) -> dict:
    """Build the battle-field packet the LLM must reason around first.

    The full multi-level structure remains available for debugging, but Commander
    should anchor on this smaller tactical packet so macro noise cannot steal the
    main narrative from an active 5m/15m setup.
    """
    dynamic_anchors = _dynamic_anchors(levels, quote_anchors, current_price)
    primary_context = _primary_context(levels, operative_context, dynamic_anchors, semantic_assertions, current_price)
    must_use = _must_use_levels(primary_context, levels, operative_context, dynamic_anchors, current_price)
    commander_assertions = _commander_assertions(primary_context, must_use, semantic_assertions)
    return {
        "primary_context": primary_context,
        "must_use_levels": must_use,
        "tactical_levels": {
            "supports": (operative_context.get("immediate_supports") or [])[:4],
            "resistances": (operative_context.get("immediate_resistances") or [])[:4],
            "dynamic_anchors": dynamic_anchors,
        },
        "semantic_assertions": commander_assertions,
        "secondary_risks": _secondary_risks(semantic_assertions),
        "instruction": (
            "Free Reasoning must center on primary_context and must_use_levels. "
            "secondary_risks may qualify the plan but must not rewrite the main battlefield."
        ),
    }


def _dynamic_anchors(levels: dict, quote_anchors: dict, current_price: float) -> dict:
    intraday_low = _num(quote_anchors.get("intraday_low"))
    intraday_high = _num(quote_anchors.get("intraday_high"))
    micro_levels = ["1", "5", "15"]
    lows = []
    highs = []
    for level_key in micro_levels:
        level = levels.get(level_key) or {}
        for bi in level.get("recent_bis") or []:
            for key in ("from", "to"):
                price = _num(bi.get(key))
                if price <= 0:
                    continue
                point = {
                    "level": level_key,
                    "price": round(price, 2),
                    "time": bi.get("end") or bi.get("start") or "",
                    "source": "recent_bi_endpoint",
                }
                if current_price > 0 and price < current_price:
                    lows.append(point)
                if current_price > 0 and price > current_price:
                    highs.append(point)
    latest_micro_pivot_low = max(lows, key=lambda item: (item.get("time") or "", item["price"])) if lows else None
    latest_micro_pivot_high = max(highs, key=lambda item: (item.get("time") or "", item["price"])) if highs else None
    if intraday_low > 0 and current_price > 0 and intraday_low < current_price:
        lows.append({"level": "quote", "price": round(intraday_low, 2), "time": quote_anchors.get("time") or "", "source": "intraday_low"})
    if intraday_high > 0 and current_price > 0 and intraday_high > current_price:
        highs.append({"level": "quote", "price": round(intraday_high, 2), "time": quote_anchors.get("time") or "", "source": "intraday_high"})
    nearest_dynamic_low = max(lows, key=lambda item: item["price"]) if lows else None
    nearest_dynamic_high = min(highs, key=lambda item: item["price"]) if highs else None
    return {
        "recent_minute_low": round(intraday_low, 2) if intraday_low > 0 else None,
        "recent_minute_high": round(intraday_high, 2) if intraday_high > 0 else None,
        "latest_micro_pivot_low": latest_micro_pivot_low,
        "latest_micro_pivot_high": latest_micro_pivot_high,
        "nearest_dynamic_low": nearest_dynamic_low,
        "nearest_dynamic_high": nearest_dynamic_high,
    }


def _primary_context(
    levels: dict,
    operative_context: dict,
    dynamic_anchors: dict,
    semantic_assertions: list[dict],
    current_price: float,
) -> dict:
    day = levels.get("day") or {}
    m30 = levels.get("30") or levels.get("60") or {}
    m5 = levels.get("5") or levels.get("15") or {}
    day_center = day.get("center") or {}
    m30_center = m30.get("center") or {}
    m5_center = m5.get("center") or {}
    day_relation = (day.get("price_vs_center") or {}).get("position")
    m30_relation = (m30.get("price_vs_center") or {}).get("position")
    m5_relation = (m5.get("price_vs_center") or {}).get("position")
    has_low_level_reclaim = _has_level5_reclaim_support(operative_context)

    if operative_context.get("structure_gap"):
        return {
            "code": "EXTREME_ABOVE_ALL_STRUCTURES",
            "bias": "PROFIT_PROTECTION_IN_STRUCTURE_GAP",
            "reason": "current price is far above all known operating levels; use dynamic anchors before old centers",
        }

    day_zg = _num(day_center.get("zg"))
    day_gg = _num(day_center.get("gg"))
    if (
        _has_two_recent_5m_centers_around_price(m5, current_price)
        and _num(dynamic_anchors.get("recent_minute_high")) > current_price
    ):
        return {
            "code": "REBOUND_BETWEEN_TWO_5M_ZHONGSHUS",
            "bias": "NECKLINE_REBOUND_TEST",
            "reason": "price is between two recent 5m centers and is testing the upper center lower edge",
        }
    if (
        _num(m30_center.get("zd")) > 0
        and _num(m30_center.get("zg")) > 0
        and _num(m30_center.get("zd")) <= current_price <= _num(m30_center.get("zg"))
        and m5_relation == "above_zg"
    ):
        return {
            "code": "REBOUND_INTO_30M_ZHONGSHU",
            "bias": "CENTER_REENTRY_ATTACK",
            "reason": "price has rebounded into the 30m center while the 5m center has been reclaimed",
        }

    if day_relation == "above_zg" and day_zg > 0 and _distance_abs_pct(current_price, day_zg) <= 3:
        return {
            "code": "MACRO_BREAKOUT_EDGE",
            "bias": "BREAKOUT_CONFIRMATION",
            "reason": "price is holding above daily center ZG and still below/near upper macro resistance",
        }
    if day_relation == "above_zg" and day_gg > 0 and _distance_abs_pct(current_price, day_gg) <= 4:
        return {
            "code": "MACRO_BREAKOUT_EDGE",
            "bias": "BREAKOUT_CONFIRMATION",
            "reason": "price is operating around daily center upper edge / GG pressure",
        }

    if (
        has_low_level_reclaim
        or (
            m5_relation == "above_zg"
            and m30_relation in {"above_zg", "inside_center"}
            and _is_near_center_edge(current_price, m5_center.get("zg"), max_pct=3.0)
        )
    ):
        return {
            "code": "RETRACE_TESTING_3RD_BUY",
            "bias": "RIGHT_SIDE_PULLBACK_CONFIRMATION",
            "reason": "low-level price has reclaimed a recent 5m structure and is testing whether pullback holds",
        }

    if (
        not has_low_level_reclaim
        and day_relation == "above_zg"
        and m30_relation == "above_zg"
        and day_zg > 0
        and _distance_abs_pct(current_price, day_zg) > 12
    ):
        return {
            "code": "EXTREME_ABOVE_ALL_STRUCTURES",
            "bias": "PROFIT_PROTECTION_IN_STRUCTURE_GAP",
            "reason": "current price is materially above day and pivot centers; use dynamic anchors before old centers",
        }
    if _has_secondary_claim(semantic_assertions, "active_high_area_sell_risk") and _num(dynamic_anchors.get("recent_minute_high")) > current_price:
        return {
            "code": "REBOUND_BETWEEN_TWO_5M_ZHONGSHUS",
            "bias": "NECKLINE_REBOUND_TEST",
            "reason": "price is rebounding toward a nearby high-area sell-risk zone; dynamic anchors define the tactical range",
        }

    m30_zd = _num(m30_center.get("zd"))
    if m30_zd > 0 and current_price < m30_zd:
        return {
            "code": "BREAKDOWN_BELOW_30M",
            "bias": "DEFENSIVE_REBOUND_OR_CONTINUATION_DOWN",
            "reason": "price is below the 30m center lower edge; rebounds must first reclaim near resistance",
        }

    if _num(m30_center.get("zd")) > 0 and _num(m30_center.get("zg")) > 0:
        if _num(m30_center.get("zd")) <= current_price <= _num(m30_center.get("zg")):
            return {
                "code": "REBOUND_INTO_30M_ZHONGSHU",
                "bias": "CENTER_REENTRY_ATTACK",
                "reason": "price is trading inside the 30m center after a lower-level repair",
            }

    return {
        "code": operative_context.get("current_zone") or "TACTICAL_RANGE",
        "bias": "TACTICAL_RANGE_MANAGEMENT",
        "reason": "fallback to nearest support/resistance because no stronger commander context was detected",
    }


def _has_level5_reclaim_support(operative_context: dict) -> bool:
    for item in operative_context.get("immediate_supports") or []:
        label = str(item.get("label") or "")
        level = str(item.get("level") or "")
        kind = str(item.get("kind") or "")
        if level in {"5", "15"} and ("突破" in label or "转强" in label or kind in {"rule_confirm", "rule_pressure"}):
            return True
    return False


def _has_two_recent_5m_centers_around_price(m5: dict, current_price: float) -> bool:
    centers = m5.get("recent_centers") or []
    if len(centers) < 2 or current_price <= 0:
        return False
    previous = centers[-2]
    current = centers[-1]
    lower_zg = _num(current.get("zg"))
    upper_zd = _num(previous.get("zd"))
    upper_zg = _num(previous.get("zg"))
    if lower_zg <= 0 or upper_zd <= 0:
        return False
    return lower_zg < current_price < (upper_zg or upper_zd) and current_price <= upper_zd * 1.02


def _is_near_center_edge(current_price: float, edge: object, *, max_pct: float) -> bool:
    price = _num(edge)
    return price > 0 and _distance_abs_pct(current_price, price) <= max_pct


def _must_use_levels(
    primary_context: dict,
    levels: dict,
    operative_context: dict,
    dynamic_anchors: dict,
    current_price: float,
) -> dict:
    code = primary_context.get("code")
    supports = operative_context.get("immediate_supports") or []
    resistances = operative_context.get("immediate_resistances") or []
    day_center = (levels.get("day") or {}).get("center") or {}
    m30_center = ((levels.get("30") or levels.get("60") or {}).get("center") or {})
    m5_center = ((levels.get("5") or levels.get("15") or {}).get("center") or {})

    if code == "EXTREME_ABOVE_ALL_STRUCTURES":
        dynamic_low = dynamic_anchors.get("nearest_dynamic_low")
        return {
            "support": dynamic_low or _nearest(supports),
            "resistance": dynamic_anchors.get("nearest_dynamic_high"),
            "deep_support": _below_current_level_point("5", "center_zg", "5.ZG", m5_center.get("zg"), current_price)
            or _level_point("day", "center_zg", "day.ZG", day_center.get("zg")),
        }
    if code == "MACRO_BREAKOUT_EDGE":
        return {
            "support": _level_point("day", "center_zg", "day.ZG", day_center.get("zg")),
            "resistance": _level_point("day", "center_gg", "day.GG", day_center.get("gg")) or _nearest(resistances),
            "deep_support": _nearest(supports),
        }
    if code == "RETRACE_TESTING_3RD_BUY":
        reclaim = _first_matching(
            supports,
            lambda item: str(item.get("level")) in {"5", "15"} and (
                "突破" in str(item.get("label") or "")
                or "最近5分钟卖点" in str(item.get("label") or "")
                or str(item.get("kind")) in {"rule_confirm", "rule_pressure"}
            ),
        )
        return {
            "support": reclaim or _level_point("5", "center_zg", "5.ZG", m5_center.get("zg")) or _nearest(supports),
            "resistance": _nearest(resistances),
            "deep_support": _level_point("5", "center_zd", "5.ZD", m5_center.get("zd")),
        }
    if code == "BREAKDOWN_BELOW_30M":
        return {
            "support": _nearest(supports),
            "resistance": _above_current_level_point("5", "center_zd", "5.ZD", m5_center.get("zd"), current_price)
            or _nearest(resistances)
            or _level_point("30", "center_zd", "30.ZD", m30_center.get("zd")),
            "deep_support": _current_day_center_support(day_center, current_price)
            or _deep_support_below(operative_context, day_center, current_price),
        }
    if code == "REBOUND_INTO_30M_ZHONGSHU":
        return {
            "support": _level_point("5", "center_zg", "5.ZG", m5_center.get("zg")) or _nearest(supports),
            "resistance": _level_point("30", "center_zg", "30.ZG", m30_center.get("zg")) or _nearest(resistances),
            "deep_support": _level_point("5", "center_zd", "5.ZD", m5_center.get("zd")),
        }
    if code == "REBOUND_BETWEEN_TWO_5M_ZHONGSHUS":
        centers = m5_center_pair(levels)
        if centers:
            lower_center, upper_center = centers
            return {
                "support": _level_point("5", "recent_center_zg", "5.current.ZG", lower_center.get("zg")),
                "resistance": _level_point("5", "recent_center_zd", "5.previous.ZD", upper_center.get("zd")),
                "deep_support": _level_point("5", "recent_center_dd", "5.current.DD", lower_center.get("dd"))
                or _deep_support_below(operative_context, day_center, current_price),
            }
        return {
            "support": dynamic_anchors.get("nearest_dynamic_low") or _nearest(supports),
            "resistance": dynamic_anchors.get("nearest_dynamic_high") or _nearest(resistances),
            "deep_support": _deep_support_below(operative_context, day_center, current_price),
        }
    return {
        "support": _nearest(supports),
        "resistance": _nearest(resistances),
        "deep_support": None,
    }


def _commander_assertions(primary_context: dict, must_use: dict, semantic_assertions: list[dict]) -> list[dict]:
    code = primary_context.get("code")
    result = []
    if code == "MACRO_BREAKOUT_EDGE":
        result.append({"claim": "ATTEMPTING_MACRO_BREAKOUT", "confidence": 0.78, "evidence": [primary_context.get("reason", "")]})
    elif code == "RETRACE_TESTING_3RD_BUY":
        result.extend(
            [
                {"claim": "BREAKOUT_PULLBACK", "confidence": 0.74, "evidence": [primary_context.get("reason", "")]},
                {"claim": "POTENTIAL_THIRD_BUY_FORMING", "confidence": 0.68, "evidence": [f"support={_price_of(must_use.get('support'))}"]},
            ]
        )
    elif code == "EXTREME_ABOVE_ALL_STRUCTURES":
        result.append({"claim": "STRUCTURE_GAP_DYNAMIC_DEFENSE_REQUIRED", "confidence": 0.82, "evidence": [primary_context.get("reason", "")]})
    elif code == "BREAKDOWN_BELOW_30M":
        result.append({"claim": "BREAKDOWN_BELOW_30M", "confidence": 0.8, "evidence": [primary_context.get("reason", "")]})
    elif code == "REBOUND_BETWEEN_TWO_5M_ZHONGSHUS":
        result.append({"claim": "REBOUND_TESTING_THIRD_SELL", "confidence": 0.7, "evidence": [primary_context.get("reason", "")]})
    return result + [
        {**item, "role": "secondary_risk"}
        for item in semantic_assertions
        if isinstance(item, dict)
    ]


def m5_center_pair(levels: dict) -> tuple[dict, dict] | None:
    m5 = levels.get("5") or levels.get("15") or {}
    centers = m5.get("recent_centers") or []
    if len(centers) < 2:
        return None
    return centers[-1], centers[-2]


def _secondary_risks(semantic_assertions: list[dict]) -> list[dict]:
    return [
        item for item in semantic_assertions
        if isinstance(item, dict) and str(item.get("claim") or "").startswith(("active_high_area", "not_standard"))
    ][:4]


def _has_secondary_claim(semantic_assertions: list[dict], claim: str) -> bool:
    return any(isinstance(item, dict) and item.get("claim") == claim for item in semantic_assertions)


def _nearest(items: list[dict]) -> dict | None:
    return items[0] if items else None


def _first_matching(items: list[dict], predicate) -> dict | None:
    for item in items:
        if predicate(item):
            return item
    return None


def _level_point(level: str, kind: str, label: str, price: object) -> dict | None:
    value = _num(price)
    if value <= 0:
        return None
    return {"level": level, "kind": kind, "label": label, "price": round(value, 2)}


def _below_current_level_point(level: str, kind: str, label: str, price: object, current_price: float) -> dict | None:
    value = _num(price)
    if value <= 0 or value >= current_price:
        return None
    return _level_point(level, kind, label, value)


def _above_current_level_point(level: str, kind: str, label: str, price: object, current_price: float) -> dict | None:
    value = _num(price)
    if value <= 0 or value <= current_price:
        return None
    return _level_point(level, kind, label, value)


def _current_day_center_support(day_center: dict, current_price: float) -> dict | None:
    """Use the active day center before falling back to old centers."""
    zd = _num(day_center.get("zd"))
    zg = _num(day_center.get("zg"))
    dd = _num(day_center.get("dd"))
    if zd > 0 and zg > 0 and zd <= current_price <= zg:
        return _level_point("day", "center_zd", "day.ZD", zd)
    if zg > 0 and current_price > zg:
        return _level_point("day", "center_zg", "day.ZG", zg)
    if dd > 0 and current_price > dd:
        return _level_point("day", "center_dd", "day.DD", dd)
    if zd > 0 and current_price > zd:
        return _level_point("day", "center_zd", "day.ZD", zd)
    return None


def _deep_support_below(operative_context: dict, day_center: dict, current_price: float) -> dict | None:
    for item in operative_context.get("deep_references") or []:
        if _num(item.get("price")) < current_price:
            return item
    for field in ("zg", "zd", "dd"):
        price = _num(day_center.get(field))
        if 0 < price < current_price:
            return _level_point("day", f"center_{field}", f"day.{field.upper()}", price)
    return None


def _price_of(point: dict | None) -> float | None:
    if not point:
        return None
    value = _num(point.get("price"))
    return round(value, 2) if value > 0 else None


def _distance_abs_pct(current: float, reference: float) -> float:
    if current <= 0 or reference <= 0:
        return 999.0
    return abs(current - reference) / current * 100


def _swing_points(level_key: str, level: dict) -> list[dict]:
    points = []
    for bi in level.get("recent_bis") or []:
        for endpoint, price_key, time_key in (("start", "from", "start"), ("end", "to", "end")):
            price = _num(bi.get(price_key))
            if price <= 0:
                continue
            points.append(
                {
                    "level": level_key,
                    "price": price,
                    "kind": "recent_bi_endpoint",
                    "label": f"{level_key} bi {endpoint}",
                    "time": bi.get(time_key) or "",
                }
            )
    return points


def _dedupe_points(points: list[dict], current_price: float) -> list[dict]:
    result = {}
    for point in points:
        price = _num(point.get("price"))
        if price <= 0:
            continue
        distance_abs_pct = round(abs(current_price - price) / current_price * 100, 2)
        if price < current_price:
            relation = "below_current"
        elif price > current_price:
            relation = "above_current"
        else:
            relation = "at_current"
        key = (round(price, 2), str(point.get("level") or ""), str(point.get("kind") or ""))
        candidate = {
            "level": point.get("level") or "",
            "price": round(price, 2),
            "kind": point.get("kind") or "",
            "label": point.get("label") or "",
            "time": point.get("time") or "",
            "relation": relation,
            "distance_abs_pct": distance_abs_pct,
        }
        current = result.get(key)
        if current is None or candidate["time"] > current.get("time", ""):
            result[key] = candidate
    return sorted(result.values(), key=lambda item: (item["distance_abs_pct"], item["level"], item["kind"]))


def _semantic_assertions(levels: dict, divergence_context: DivergenceContext | None = None) -> list[dict]:
    assertions = []
    pivot = levels.get("30") or levels.get("60") or {}
    pivot_events = pivot.get("recent_bsp_events") or []
    pivot_relation = (pivot.get("price_vs_center") or {}).get("position")
    if pivot and pivot_relation == "above_zg":
        assertions.append(
            {
                "claim": f"not_standard_{pivot.get('level')}_third_sell",
                "confidence": 0.86,
                "evidence": [
                    f"{pivot.get('level')} price is above center zg",
                    "standard third sell needs failed rebound around/below center, not an extended price above center",
                ],
            }
        )
    if pivot and pivot_relation == "above_zg":
        sell_events = [event for event in pivot_events if event.get("side") == "sell"]
        if sell_events:
            assertions.append(
                {
                    "claim": "active_high_area_sell_risk",
                    "confidence": 0.72,
                    "evidence": [
                        f"latest pivot sell event {sell_events[-1].get('type')} at {sell_events[-1].get('price')}",
                        f"{pivot.get('level')} remains above center zg",
                    ],
                }
            )

    if divergence_context:
        if divergence_context.alignment == "ALIGNING" and divergence_context.chain_direction == "BOTTOM":
            assertions.append({
                "claim": "MACD_BOTTOM_DIVERGENCE_CONFIRMED" if divergence_context.chain_status == "CONFIRMED" else "POTENTIAL_BOTTOM_DIVERGENCE",
                "confidence": 0.85 if divergence_context.chain_status == "CONFIRMED" else 0.6,
                "evidence": [f"Divergence detected on {divergence_context.pivot_level}m pivot level."]
            })
        elif divergence_context.alignment == "ALIGNING" and divergence_context.chain_direction == "TOP":
            assertions.append({
                "claim": "MACD_TOP_DIVERGENCE_CONFIRMED" if divergence_context.chain_status == "CONFIRMED" else "POTENTIAL_TOP_DIVERGENCE",
                "confidence": 0.85 if divergence_context.chain_status == "CONFIRMED" else 0.6,
                "evidence": [f"Divergence detected on {divergence_context.pivot_level}m pivot level."]
            })

        for sig in divergence_context.lower_level_signals + divergence_context.pivot_signals:
            if sig.type == "BOTTOM":
                assertions.append({
                    "claim": f"{sig.level}m_BOTTOM_DIVERGENCE",
                    "confidence": 0.8,
                    "evidence": _divergence_signal_evidence(sig)
                })
            elif sig.type == "TOP":
                assertions.append({
                    "claim": f"{sig.level}m_TOP_DIVERGENCE",
                    "confidence": 0.8,
                    "evidence": _divergence_signal_evidence(sig)
                })

    return assertions


def _divergence_signal_evidence(sig) -> list[str]:
    evidence = [item for item in sig.evidence if item]
    if evidence:
        return evidence[:3]
    return [f"{sig.level}m {sig.type} divergence signal status={sig.status} quality={sig.quality}"]


def _key_levels(algorithm: dict, current_price: float) -> list[dict]:
    result = []
    for group, items in (algorithm.get("boundaries") or {}).items():
        for item in items or []:
            if not isinstance(item, dict):
                continue
            value = _num(item.get("value") or item.get("price"))
            if value <= 0:
                continue
            result.append(
                {
                    "group": group,
                    "level": item.get("level"),
                    "field": item.get("field"),
                    "value": value,
                    "distance_to_current_pct": _pct(current_price, value) if current_price > 0 else None,
                    "meaning": item.get("meaning") or "",
                }
            )
    return result


def _position_pack(radar_contract: dict, current_price: float) -> dict:
    position = radar_contract.get("position_context") or {}
    if not position:
        return {}
    avg_cost = _num(position.get("avg_cost") or position.get("cost"))
    return {
        "is_holding": bool(position.get("is_holding")),
        "avg_cost": avg_cost,
        "current_price": _num(position.get("current_price")) or current_price,
        "pnl_pct": _num(position.get("pnl_pct") or position.get("pnl_percentage")),
        "distance_to_cost_pct": _pct(current_price, avg_cost) if avg_cost > 0 and current_price > 0 else None,
    }
def _num(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _pct(current: float, reference: float) -> float | None:
    if current <= 0 or reference <= 0:
        return None
    return round((current - reference) / current * 100, 2)
