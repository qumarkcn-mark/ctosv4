"""Compile old Radar contracts into compact AI-readable structure transcripts."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from server.config import AI_NATIVE_RADAR_FINGERPRINT_VERSION
from server.engines.ai_native.schemas import (
    AllowedPrice,
    LevelTranscript,
    PositionContext,
    ReasoningBoundaries,
    StructureTranscript,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def compile_structure_transcript(
    radar_contract: dict,
    *,
    market_context: str | None = None,
    fingerprint_version: str = AI_NATIVE_RADAR_FINGERPRINT_VERSION,
) -> StructureTranscript:
    """把 Radar contract 压缩成 AI 可以消费的结构事实。

    这里不调用 AI，不生成交易建议，只抽取结构、边界和允许引用的价格。
    """
    symbol = str(radar_contract.get("symbol") or "")
    mode = "HOLDING" if radar_contract.get("mode") == "HOLDING" else "EMPTY"
    structure = radar_contract.get("structure") or {}
    raw_levels = structure.get("levels") or {}
    algorithm = radar_contract.get("algorithm_v2") or {}
    freshness = radar_contract.get("freshness") or {}

    levels = [
        _level_transcript("L0", raw_levels.get("day") or {}, algorithm),
        _level_transcript("L1", raw_levels.get("30") or raw_levels.get("60") or {}, algorithm),
        _level_transcript("L2", raw_levels.get("5") or raw_levels.get("15") or {}, algorithm),
    ]
    position_context = _position_context(radar_contract)
    boundaries = _reasoning_boundaries(algorithm, radar_contract)
    allowed_prices = _dedupe_prices(_prices_from_levels(raw_levels) + _prices_from_boundaries(boundaries))
    fingerprint = build_structure_fingerprint(
        mode=mode,
        levels=levels,
        algorithm=algorithm,
        freshness=freshness,
        position_context=position_context,
    )
    return StructureTranscript(
        symbol=symbol,
        mode=mode,
        generated_at=datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
        fingerprint_version=fingerprint_version,
        structure_fingerprint=fingerprint,
        levels=levels,
        reasoning_boundaries=boundaries,
        position_context=position_context,
        market_context=market_context,
        allowed_prices=allowed_prices,
        stale=bool(freshness.get("is_stale")),
    )


def build_structure_fingerprint(
    *,
    mode: str,
    levels: list[LevelTranscript],
    algorithm: dict,
    freshness: dict,
    position_context: PositionContext | None,
) -> str:
    """Build stable v1 fingerprint for simple SQLite memory lookup."""
    by_role = {level.role: level for level in levels}
    l0 = by_role.get("L0")
    l1 = by_role.get("L1")
    l2 = by_role.get("L2")
    parts = [
        mode or "UNKNOWN",
        _unknown(algorithm.get("path") or (l0.path if l0 else None)),
        _unknown(algorithm.get("phase") or (l0.phase if l0 else None)),
        _unknown(l1.raw_state if l1 else None),
        _unknown(l1.position_state if l1 else None),
        _unknown(l2.raw_state if l2 else None),
        _unknown(l2.position_state if l2 else None),
        _unknown(algorithm.get("current_scenario_id") or _scenario_from_confirmation(algorithm)),
        _price_zone(algorithm),
        "STALE" if freshness.get("is_stale") else "FRESH",
        _holding_bucket(position_context),
        _pnl_bucket(position_context),
    ]
    return "|".join(parts)


def _level_transcript(role: str, level: dict, algorithm: dict) -> LevelTranscript:
    public_level = str(level.get("level") or _fallback_level(role))
    active = level.get("active_zhongshu") or {}
    price = _num(level.get("price"))
    zg = _num(level.get("zg") or active.get("zg")) or None
    zd = _num(level.get("zd") or active.get("zd")) or None
    evidence = []
    state = str(level.get("state") or "UNKNOWN")
    if state != "UNKNOWN":
        evidence.append(f"{public_level} state={state}")
    if zg:
        evidence.append(f"{public_level} ZG={zg:.2f}")
    if zd:
        evidence.append(f"{public_level} ZD={zd:.2f}")
    patterns = level.get("patterns") or []
    if patterns:
        evidence.append(f"{public_level} patterns={','.join(map(str, patterns[:3]))}")
    return LevelTranscript(
        role=role,  # type: ignore[arg-type]
        level=public_level,
        price=price,
        path=str(algorithm.get("path") or level.get("path") or "UNKNOWN") if role == "L0" else str(level.get("path") or "UNKNOWN"),
        phase=str(algorithm.get("phase") or level.get("phase") or "UNKNOWN") if role == "L0" else str(level.get("phase") or "UNKNOWN"),
        raw_state=state,
        position_state=str(level.get("position_state") or level.get("center_relation") or "UNKNOWN"),
        summary=str(level.get("summary") or ""),
        center_zg=zg,
        center_zd=zd,
        evidence=evidence,
    )


def _reasoning_boundaries(algorithm: dict, radar_contract: dict) -> ReasoningBoundaries:
    raw = algorithm.get("boundaries") or {}
    boundaries = ReasoningBoundaries(
        confirm=_boundary_group(raw, "confirm") + _boundary_group(raw, "pressure"),
        observe=_boundary_group(raw, "maintain"),
        invalidate=_boundary_group(raw, "invalidate"),
        support=_boundary_group(raw, "support"),
    )
    deduction = radar_contract.get("deduction") or {}
    thesis = deduction.get("path_thesis") or {}
    for item in thesis.get("boundaries") or []:
        price = _num(item.get("price"))
        if price > 0:
            boundary = AllowedPrice(
                label=str(item.get("label") or "结构边界"),
                value=price,
                source="deduction.path_thesis.boundaries",
            )
            boundaries.invalidate.append(boundary)
            boundaries.support.append(boundary)
    return boundaries


def _boundary_group(raw: dict, key: str) -> list[AllowedPrice]:
    prices = []
    for item in raw.get(key) or []:
        value = _num(item.get("value") or item.get("price"))
        if value <= 0:
            continue
        prices.append(
            AllowedPrice(
                label=str(item.get("label") or key),
                value=value,
                source=f"algorithm_v2.boundaries.{key}",
                level=item.get("level"),
            )
        )
    return prices


def _prices_from_boundaries(boundaries: ReasoningBoundaries) -> list[AllowedPrice]:
    return boundaries.confirm + boundaries.observe + boundaries.invalidate + boundaries.support


def _prices_from_levels(raw_levels: dict) -> list[AllowedPrice]:
    prices: list[AllowedPrice] = []
    for key, level in raw_levels.items():
        if not isinstance(level, dict):
            continue
        active = level.get("active_zhongshu") or {}
        for label, field in (("current_price", "price"), ("ZG", "zg"), ("ZD", "zd")):
            value = _num(level.get(field) or active.get(field))
            if value > 0:
                prices.append(
                    AllowedPrice(
                        label=f"{key}.{label}",
                        value=value,
                        source=f"structure.levels.{key}.{field}",
                        level=str(key),
                    )
                )
    return prices


def _position_context(radar_contract: dict) -> PositionContext | None:
    position = radar_contract.get("position_context") or {}
    holding_plan = radar_contract.get("holding_plan") or {}
    entry = holding_plan.get("entry_thesis") or {}
    is_holding = bool(position.get("is_holding") or radar_contract.get("mode") == "HOLDING")
    if not is_holding and not entry:
        return None
    return PositionContext(
        is_holding=is_holding,
        cost=_optional_num(position.get("cost") or entry.get("cost") or entry.get("avg_cost")),
        quantity=_optional_int(position.get("quantity") or entry.get("qty")),
        pnl_percentage=_optional_num(position.get("pnl_percentage")),
    )


def _dedupe_prices(prices: list[AllowedPrice]) -> list[AllowedPrice]:
    seen = set()
    result = []
    for price in prices:
        key = (round(price.value, 2), price.label, price.source)
        if key in seen:
            continue
        seen.add(key)
        result.append(price)
    return result


def _price_zone(algorithm: dict) -> str:
    path = str(algorithm.get("path") or "")
    phase = str(algorithm.get("phase") or "")
    if "BREAKOUT" in phase:
        return "NEAR_PREVIOUS_HIGH"
    if "PULLBACK" in path or "PULLBACK" in phase:
        return "PULLBACK_ZONE"
    if "OSCILLATION" in path:
        return "IN_RANGE"
    if "DOWN" in path:
        return "DEFENSE_ZONE"
    return "UNKNOWN"


def _holding_bucket(position_context: PositionContext | None) -> str:
    if not position_context or not position_context.is_holding:
        return "NO_HOLDING"
    return "HOLDING"


def _pnl_bucket(position_context: PositionContext | None) -> str:
    pnl = position_context.pnl_percentage if position_context else None
    if pnl is None:
        return "PNL_UNKNOWN"
    if pnl >= 50:
        return "BIG_PROFIT"
    if pnl >= 10:
        return "PROFIT"
    if pnl <= -10:
        return "LOSS"
    return "FLAT"


def _scenario_from_confirmation(algorithm: dict) -> str:
    state = str(algorithm.get("a_state") or (algorithm.get("confirmation") or {}).get("state") or "")
    if state.startswith("A_"):
        return "A"
    if state.startswith("B_"):
        return "B"
    if state.startswith("C_"):
        return "C"
    return "UNKNOWN"


def _fallback_level(role: str) -> str:
    return {"L0": "day", "L1": "30", "L2": "5", "L3": "1"}.get(role, "UNKNOWN")


def _unknown(value: object) -> str:
    text = str(value or "").strip()
    return text if text else "UNKNOWN"


def _num(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _optional_num(value: object) -> float | None:
    num = _num(value)
    return num if num != 0 else None


def _optional_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

