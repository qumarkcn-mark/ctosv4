"""Pattern layer for Radar algorithm v2.

本模块只消费 radar_algorithm_v2 已经构造好的 LevelAtom，不取行情、不查库。
Pattern 用来解释结构模板，Path 仍然负责当前主状态。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class PatternEvidence:
    level_role: str
    level: str
    field: str
    value: float | str
    meaning: str


@dataclass(frozen=True)
class RadarPattern:
    code: str
    name: str
    confidence: str
    path_hint: str
    evidence: list[PatternEvidence] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def detect_patterns(
    atoms: dict[str, Any],
    classification: dict | None = None,
    center_nesting: dict | None = None,
) -> list[dict]:
    """Detect structural Chan patterns from already-normalized atoms."""
    classification = classification or {}
    context = {"center_nesting": center_nesting or {}}
    detectors = (
        _detect_big_center_small_center_up_break,
        _detect_third_buy_retest_up,
        _detect_historical_high_pressure,
        _detect_third_buy_fast_sell_risk,
        _detect_big_center_small_center_pullback_repair,
        _detect_small_turn_big_fast_b2_b3,
        _detect_micro_conversion_breakout,
    )
    patterns: list[RadarPattern] = []
    for detector in detectors:
        pattern = detector(atoms, classification, context)
        if pattern:
            patterns.append(pattern)
    return [_pattern_to_dict(pattern) for pattern in patterns]


def build_transition(
    patterns: list[dict],
    classification: dict | None = None,
    scenarios: list[dict] | None = None,
) -> dict:
    """Build a human-readable state transition from detected patterns."""
    classification = classification or {}
    scenarios = scenarios or []
    pattern_codes = [pattern.get("code") for pattern in patterns]

    if "SMALL_TURN_BIG_FAST_B2_B3" in pattern_codes:
        pattern = _pattern_by_code(patterns, "SMALL_TURN_BIG_FAST_B2_B3")
        return _transition(
            from_state="CENTER_REBOUND",
            to_state="UPWARD_MAJOR_WAVE",
            status="CONFIRMED",
            trigger="B3A above repair center ZG",
            pattern=pattern,
            meaning="小转大后二买/类二买快速完成，三买确认后从修复路径升级为主升维持。",
        )

    if "THIRD_BUY_RETEST_UP" in pattern_codes:
        pattern = _pattern_by_code(patterns, "THIRD_BUY_RETEST_UP")
        return _transition(
            from_state="PULLBACK_IN_UPTREND",
            to_state="UPWARD_MAJOR_WAVE",
            status="CONFIRMED",
            trigger="hold above L2 ZG after third-buy retest",
            pattern=pattern,
            meaning="中枢上方回踩不跌回，随后重新向上，回落验证升级为三买回踩向上。",
        )

    if "BIG_CENTER_SMALL_CENTER_PULLBACK_REPAIR" in pattern_codes:
        pattern = _pattern_by_code(patterns, "BIG_CENTER_SMALL_CENTER_PULLBACK_REPAIR")
        return _transition(
            from_state="C_TRIGGERED",
            to_state="A_PARTIAL_TRIGGERED",
            status="PARTIAL",
            trigger="reclaim small center ZG before breaking sell pressure",
            pattern=pattern,
            meaning="跌回原中枢后出现小转大拉回，但前卖点压力未完全化解，只能算部分修复。",
        )

    if "THIRD_BUY_FAST_SELL_RISK" in pattern_codes:
        pattern = _pattern_by_code(patterns, "THIRD_BUY_FAST_SELL_RISK")
        return _transition(
            from_state="UPWARD_MAJOR_WAVE",
            to_state="HIGH_VOLATILITY_OSCILLATION",
            status="RISK",
            trigger="S2 below prior S1 high after third-buy extension",
            pattern=pattern,
            meaning="三买后的上攻已经兑现，随后一卖/二卖未过前高，主升持有路径转入高位风险。",
        )

    if "BIG_CENTER_SMALL_CENTER_UP_BREAK" in pattern_codes:
        pattern = _pattern_by_code(patterns, "BIG_CENTER_SMALL_CENTER_UP_BREAK")
        return _transition(
            from_state="UPWARD_MAJOR_WAVE",
            to_state="UPWARD_MAJOR_WAVE",
            status="MAINTAINED",
            trigger="small center holds above big center and breaks upward",
            pattern=pattern,
            meaning="大中枢上方小中枢向上离开，主升路径维持并强化。",
        )

    if "MICRO_CONVERSION_BREAKOUT" in pattern_codes:
        pattern = _pattern_by_code(patterns, "MICRO_CONVERSION_BREAKOUT")
        return _transition(
            from_state="PULLBACK_IN_UPTREND",
            to_state="UPWARD_MAJOR_WAVE",
            status="PENDING",
            trigger="break L2 ZG and recent sell pressure",
            pattern=pattern,
            meaning="5分钟转换点等待向上确认，突破后才从回落验证升级。",
        )

    if pattern_codes == ["HISTORICAL_HIGH_PRESSURE"]:
        pattern = _pattern_by_code(patterns, "HISTORICAL_HIGH_PRESSURE")
        return _transition(
            from_state=classification.get("path") or "UNKNOWN",
            to_state=classification.get("path") or "UNKNOWN",
            status="WATCH",
            trigger="near or above historical high",
            pattern=pattern,
            meaning="接近或突破历史前高，只作为压力/突破观察，不直接覆盖当前 A/B/C 路径。",
        )

    return {
        "from": classification.get("path") or "UNKNOWN",
        "to": classification.get("path") or "UNKNOWN",
        "status": "UNCHANGED",
        "trigger": "",
        "pattern_code": "",
        "meaning": "当前没有识别到明确结构迁移。",
        "evidence": [],
    }


def _detect_big_center_small_center_up_break(
    atoms: dict[str, Any],
    classification: dict,
    context: dict,
) -> RadarPattern | None:
    l0 = atoms.get("L0")
    l1 = atoms.get("L1")
    l2 = atoms.get("L2")
    if not l0 or not l1:
        return None
    if classification.get("path") != "UPWARD_MAJOR_WAVE":
        return None
    if not (_valid_center(l0) and _valid_center(l1)):
        return None
    if not _has_third_buy(l0):
        return None
    if _has_sell_risk(l1) or (l2 and _has_sell_risk(l2)):
        return None
    nesting = _nesting(context, "L0_L1")
    if nesting.get("relation") != "CHILD_ABOVE_PARENT":
        return None
    if l1.price <= l1.center.zg:
        return None

    return RadarPattern(
        code="BIG_CENTER_SMALL_CENTER_UP_BREAK",
        name="大中枢上小中枢震荡后强势离开",
        confidence="HIGH",
        path_hint="UPWARD_MAJOR_WAVE",
        evidence=[
            _ev("L0", l0, "B3", _latest_event_price(l0, "buy"), "大级别三买位于大中枢上沿附近"),
            _ev("L0", l0, "ZG", l0.center.zg, "大级别中枢上沿"),
            _ev("L1", l1, "ZD", l1.center.zd, "小中枢整体位于大中枢上方"),
            _ev("L1", l1, "ZG", l1.center.zg, "价格向上离开小中枢上沿"),
        ],
        notes=["小中枢不跌回大中枢内部，向上离开可按主升延伸观察。"],
    )


def _detect_third_buy_retest_up(
    atoms: dict[str, Any],
    classification: dict,
    context: dict,
) -> RadarPattern | None:
    l0 = atoms.get("L0")
    l1 = atoms.get("L1")
    l2 = atoms.get("L2")
    if classification.get("path") != "PULLBACK_IN_UPTREND":
        return None
    if not l1 or not l2 or not (_valid_center(l1) and _valid_center(l2)):
        return None
    if not _has_third_buy_context(l1, l2):
        return None
    if _has_sell_risk(l2) and not _sell_pressure_cleared(l2):
        return None
    if _num(getattr(l2, "price", 0)) <= _num(getattr(l2.center, "zg", 0)):
        return None
    if l0 and _num(getattr(l0, "price", 0)) <= 0:
        return None

    distance = _distance_ratio(getattr(l2, "price", 0), getattr(l2.center, "zg", 0))
    confidence = "HIGH" if _has_third_buy(l2) or distance >= 0.03 else "MEDIUM"
    return RadarPattern(
        code="THIRD_BUY_RETEST_UP",
        name="三买回踩向上",
        confidence=confidence,
        path_hint="PULLBACK_IN_UPTREND_TO_UPWARD_MAJOR_WAVE",
        evidence=[
            _ev("L1", l1, "ZG", l1.center.zg, "中级别中枢上沿，不能重新跌回的结构底线"),
            _ev("L2", l2, "ZG", l2.center.zg, "短级别旧中枢上沿，回踩不跌回则三买有效"),
            _ev("L2", l2, "PRICE", getattr(l2, "price", 0), "当前价格已经重新向上离开短级别旧中枢"),
            _ev("L2", l2, "DISTANCE", round(distance, 4), "当前价相对短级别中枢上沿的脱离比例"),
        ],
        notes=["回落验证已经完成，不再按等待转强处理，后续重点管理三买有效性和失效线。"],
    )


def _detect_historical_high_pressure(
    atoms: dict[str, Any],
    classification: dict,
    context: dict,
) -> RadarPattern | None:
    l0 = atoms.get("L0")
    if not l0 or getattr(l0, "public_level", "") != "day":
        return None
    high = dict(getattr(l0, "historical_high", {}) or {})
    price = _num(high.get("price"))
    if price <= 0:
        return None
    is_near = bool(high.get("is_near"))
    is_breakout = bool(high.get("is_breakout"))
    if not (is_near or is_breakout):
        return None
    distance_pct = _num(high.get("distance_pct"))
    meaning = "已经突破历史前高" if is_breakout else "接近历史前高压力"
    return RadarPattern(
        code="HISTORICAL_HIGH_PRESSURE",
        name="历史前高压力观察",
        confidence="HIGH" if abs(distance_pct) <= 0.03 or is_breakout else "MEDIUM",
        path_hint=classification.get("path") or "UNKNOWN",
        evidence=[
            _ev("L0", l0, "ATH", price, meaning),
            _ev("L0", l0, "DISTANCE", round(distance_pct, 4), "当前价距离历史前高的比例"),
        ],
        notes=["历史前高属于价格记忆边界，不等同于当前中枢边界，需结合放量滞涨、顶背驰或突破后回踩确认。"],
    )


def _detect_third_buy_fast_sell_risk(
    atoms: dict[str, Any],
    classification: dict,
    context: dict,
) -> RadarPattern | None:
    l1 = atoms.get("L1")
    if not l1:
        return None
    buy_codes = _event_codes(l1, "buy")
    if not ("B3A" in buy_codes or "B3B" in buy_codes or "THIRD_BUY" in getattr(l1, "tags", [])):
        return None
    sequence = _sequence(l1)
    ordered_events = _latest_ordered_code_events(sequence, ("B3A", "B3B"), ("S1", "S1P"), ("S2", "S2S"))
    if not ordered_events:
        return None
    third_buy, first_sell, second_sell = ordered_events
    if second_sell.get("price", 0) >= first_sell.get("price", 0):
        return None
    if _has_repair_buy_after(sequence, second_sell):
        return None
    momentum = _momentum_compare(l1)

    return RadarPattern(
        code="THIRD_BUY_FAST_SELL_RISK",
        name="三买后快速一卖二卖转风险",
        confidence="HIGH" if momentum.get("is_weaker") else "MEDIUM",
        path_hint="HIGH_VOLATILITY_OSCILLATION",
        evidence=[
            _ev("L1", l1, "B3", third_buy.get("price", 0), "三买后曾经向上离开"),
            _ev("L1", l1, "S1", first_sell.get("price", 0), "第一卖点提示上攻完成风险"),
            _ev("L1", l1, "S2", second_sell.get("price", 0), "二卖未过前高，风险确认"),
            _ev("L1", l1, "MOMENTUM_RATIO", momentum.get("area_ratio", 0.0), "后一段同向笔 MACD 面积相对前强笔的比例"),
        ],
        notes=["三买成功后出现一卖/二卖，不能继续按三买持有逻辑硬扛。"],
    )


def _detect_big_center_small_center_pullback_repair(
    atoms: dict[str, Any],
    classification: dict,
    context: dict,
) -> RadarPattern | None:
    l1 = atoms.get("L1")
    l2 = atoms.get("L2")
    if not l1 or not l2:
        return None
    if not (_valid_center(l1) and _valid_center(l2)):
        return None
    if not _has_first_and_second_buy(l2):
        return None
    if not _has_third_buy(l2):
        return None
    if not _has_sell_risk(l1) and not _has_sell_risk(l2):
        return None
    if l2.price <= l2.center.zg:
        return None

    confidence = "HIGH" if l2.price > l1.center.zg else "MEDIUM"
    leave_status = _leave_status(l1)
    return RadarPattern(
        code="BIG_CENTER_SMALL_CENTER_PULLBACK_REPAIR",
        name="跌回原中枢后小转大拉回",
        confidence=confidence,
        path_hint="HIGH_VOLATILITY_OSCILLATION",
        evidence=[
            _ev("L1", l1, "ZG", l1.center.zg, "原结构中枢上沿"),
            _ev("L2", l2, "B1/B2", _latest_event_price(l2, "buy"), "跌回后短级别买点链条形成"),
            _ev("L2", l2, "B3", _latest_third_buy_price(l2), "短级别三买拉回"),
            _ev("L2", l2, "ZG", l2.center.zg, "已重新站回短级别中枢上沿"),
            _ev("L1", l1, "LEAVE_RETURN", leave_status.get("status") or "UNKNOWN", "中级别离开/拉回状态"),
        ],
        notes=["拉回后仍需突破前卖点压力，未突破前只算修复而非完整主升。"],
    )


def _detect_small_turn_big_fast_b2_b3(
    atoms: dict[str, Any],
    classification: dict,
    context: dict,
) -> RadarPattern | None:
    l1 = atoms.get("L1")
    if not l1:
        return None
    buy_codes = _event_codes(l1, "buy")
    if not ({"B1P", "B2", "B2S"} & buy_codes):
        return None
    if not _has_third_buy(l1):
        return None
    sequence = _sequence(l1)
    ordered_events = _latest_ordered_code_events(sequence, ("B1", "B1P"), ("B2", "B2S"), ("B3A", "B3B"))
    if not ordered_events:
        return None
    third_buy = ordered_events[-1]
    if _has_event_after(sequence, third_buy, sides={"sell"}) or _has_event_after(sequence, third_buy, codes={"B1", "B1P", "B2", "B2S"}):
        return None
    if _num(third_buy.get("price", 0)) <= l1.center.zg:
        return None
    return RadarPattern(
        code="SMALL_TURN_BIG_FAST_B2_B3",
        name="小转大后二买三买快速确认",
        confidence="MEDIUM",
        path_hint="CENTER_REBOUND_TO_UPWARD_MAJOR_WAVE",
        evidence=[
            _ev("L1", l1, "BUY_CHAIN", ",".join(sorted(buy_codes)), "B1/B2/B3 修复链条密集出现"),
            _ev("L1", l1, "ZG", l1.center.zg, "三买位于修复中枢上沿之上"),
        ],
        notes=["小转大不要求舒适慢二买，B2/B3 可能快速压缩。"],
    )


def _detect_micro_conversion_breakout(
    atoms: dict[str, Any],
    classification: dict,
    context: dict,
) -> RadarPattern | None:
    if classification.get("path") != "PULLBACK_IN_UPTREND" or classification.get("phase") != "MICRO_CONVERSION":
        return None
    l2 = atoms.get("L2")
    if not l2 or not _valid_center(l2):
        return None
    if not (_has_buy_signal(l2) and _has_sell_risk(l2)):
        return None
    return RadarPattern(
        code="MICRO_CONVERSION_BREAKOUT",
        name="5分钟多空转换点",
        confidence="HIGH",
        path_hint="PULLBACK_IN_UPTREND",
        evidence=[
            _ev("L2", l2, "ZD", l2.center.zd, "转换点下沿"),
            _ev("L2", l2, "ZG", l2.center.zg, "转换点上沿"),
            _ev("L2", l2, "SELL", _latest_event_price(l2, "sell"), "短级别卖点压力"),
        ],
        notes=["突破 L2 中枢上沿并化解卖点压力，转换点向上确认。"],
    )


def _pattern_to_dict(pattern: RadarPattern) -> dict:
    result = asdict(pattern)
    result["evidence"] = [asdict(item) for item in pattern.evidence]
    return result


def _pattern_by_code(patterns: list[dict], code: str) -> dict:
    for pattern in patterns:
        if pattern.get("code") == code:
            return pattern
    return {}


def _transition(
    from_state: str,
    to_state: str,
    status: str,
    trigger: str,
    pattern: dict,
    meaning: str,
) -> dict:
    return {
        "from": from_state,
        "to": to_state,
        "status": status,
        "trigger": trigger,
        "pattern_code": pattern.get("code") or "",
        "meaning": meaning,
        "evidence": pattern.get("evidence") or [],
    }


def _valid_center(atom: Any) -> bool:
    return bool(getattr(getattr(atom, "center", None), "is_valid", False))


def _leave_status(atom: Any) -> dict:
    return dict(getattr(atom, "leave_return_status", {}) or {})


def _momentum_compare(atom: Any) -> dict:
    return dict(getattr(atom, "momentum_compare", {}) or {})


def _nesting(context: dict, key: str) -> dict:
    return dict((context.get("center_nesting") or {}).get(key) or {})


def _has_buy_signal(atom: Any) -> bool:
    return bool(getattr(atom, "buy_events", [])) or "BUY_SIGNAL" in getattr(atom, "tags", [])


def _has_sell_risk(atom: Any) -> bool:
    return bool(getattr(atom, "sell_events", [])) or "SELL_SIGNAL" in getattr(atom, "tags", []) or "TOP_DIVERGENCE" in getattr(atom, "tags", [])


def _has_third_buy(atom: Any) -> bool:
    return "THIRD_BUY" in getattr(atom, "tags", []) or any(
        getattr(event, "family", "") == "THIRD_BUY"
        for event in getattr(atom, "buy_events", [])
    )


def _has_third_buy_context(l1: Any, l2: Any) -> bool:
    return (
        _has_third_buy(l1)
        or _has_third_buy(l2)
        or getattr(l1, "raw_state", "") == "THIRD_BUY_CONFIRMED"
        or getattr(l2, "raw_state", "") == "THIRD_BUY_CONFIRMED"
    )


def _sell_pressure_cleared(atom: Any) -> bool:
    sell_price = _latest_event_price(atom, "sell")
    price = _num(getattr(atom, "price", 0))
    return sell_price > 0 and price > sell_price


def _has_first_and_second_buy(atom: Any) -> bool:
    codes = _event_codes(atom, "buy")
    return bool({"B1", "B1P"} & codes) and bool({"B2", "B2S"} & codes)


def _has_first_and_second_sell(atom: Any) -> bool:
    codes = _event_codes(atom, "sell")
    return bool({"S1", "S1P"} & codes) and bool({"S2", "S2S"} & codes)


def _sequence(atom: Any) -> list[dict]:
    return list(getattr(atom, "event_sequence", []) or [])


def _has_ordered_codes(sequence: list[dict], *code_groups: tuple[str, ...]) -> bool:
    return bool(_ordered_code_events(sequence, *code_groups))


def _ordered_code_events(sequence: list[dict], *code_groups: tuple[str, ...]) -> list[dict]:
    cursor = -1
    events = []
    for group in code_groups:
        found_at = -1
        found_event = {}
        for idx, event in enumerate(sequence):
            if idx <= cursor:
                continue
            if event.get("code") in group:
                found_at = idx
                found_event = event
                break
        if found_at < 0:
            return []
        cursor = found_at
        events.append(found_event)
    return events


def _latest_ordered_code_events(sequence: list[dict], *code_groups: tuple[str, ...]) -> list[dict]:
    for start_idx in range(len(sequence) - 1, -1, -1):
        if sequence[start_idx].get("code") not in code_groups[0]:
            continue
        ordered = _ordered_code_events(sequence[start_idx:], *code_groups)
        if ordered:
            return ordered
    return []


def _has_repair_buy_after(sequence: list[dict], event: dict) -> bool:
    return _has_event_after(sequence, event, codes={"B1", "B1P", "B2", "B2S"}, sides={"buy"})


def _has_event_after(
    sequence: list[dict],
    event: dict,
    codes: set[str] | None = None,
    sides: set[str] | None = None,
) -> bool:
    event_time = str(event.get("time") or "")
    if not event_time:
        return False
    return any(
        (not codes or item.get("code") in codes)
        and (not sides or item.get("side") in sides)
        and str(item.get("time") or "") > event_time
        for item in sequence
    )


def _event_codes(atom: Any, side: str) -> set[str]:
    events = getattr(atom, "buy_events" if side == "buy" else "sell_events", [])
    return {str(getattr(event, "code", "")) for event in events if getattr(event, "code", "")}


def _first_event(atom: Any, side: str, codes: set[str]) -> Any | None:
    events = getattr(atom, "buy_events" if side == "buy" else "sell_events", [])
    for event in events:
        if getattr(event, "code", "") in codes:
            return event
    return None


def _latest_event_price(atom: Any, side: str) -> float:
    events = getattr(atom, "buy_events" if side == "buy" else "sell_events", [])
    priced = [event for event in events if _num(getattr(event, "price", 0)) > 0]
    return _num(getattr(priced[-1], "price", 0)) if priced else 0.0


def _latest_third_buy_price(atom: Any) -> float:
    events = [
        event
        for event in getattr(atom, "buy_events", [])
        if getattr(event, "family", "") == "THIRD_BUY" and _num(getattr(event, "price", 0)) > 0
    ]
    return _num(getattr(events[-1], "price", 0)) if events else 0.0


def _distance_ratio(price, boundary) -> float:
    price_num = _num(price)
    boundary_num = _num(boundary)
    if price_num <= 0 or boundary_num <= 0:
        return 0.0
    return round((price_num - boundary_num) / price_num, 4)


def _ev(role: str, atom: Any, field: str, value: float | str, meaning: str) -> PatternEvidence:
    return PatternEvidence(
        level_role=role,
        level=str(getattr(atom, "public_level", "")),
        field=field,
        value=round(value, 4) if isinstance(value, float) else value,
        meaning=meaning,
    )


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
