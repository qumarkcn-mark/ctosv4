"""Compile old Radar contracts into compact AI-readable structure transcripts."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from server.config import AI_NATIVE_RADAR_FINGERPRINT_VERSION
from server.engines.ai_native.schemas import (
    AllowedPrice,
    ChartOverlayAlignment,
    ChartOverlayAlignmentLevel,
    CenterSnapshot,
    DivergenceContext,
    LevelTranscript,
    PositionContext,
    ReasoningBoundaries,
    StructureSnapshot,
    StructureSnapshotLevel,
    StructureTranscript,
)
from server.engines.ai_native.agent_observers import build_agent_observations
from server.engines.ai_native.divergence_context import build_divergence_context
from server.engines.ai_native.evidence_pack import build_reasoning_evidence_pack


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
    tactical_structure = radar_contract.get("tactical_structure") or {}
    tactical_levels = tactical_structure.get("levels") or {}
    reasoning_levels = tactical_levels or raw_levels
    reasoning_contract = (
        {**radar_contract, "structure": tactical_structure}
        if tactical_levels
        else radar_contract
    )
    algorithm = radar_contract.get("algorithm_v2") or {}
    freshness = radar_contract.get("freshness") or {}
    signal_v2 = _signal_v2_context(radar_contract.get("signals_v2") or {}, algorithm)

    levels = [
        _level_transcript("L0", reasoning_levels.get("day") or {}, algorithm),
        _level_transcript("L1", _preferred_l1_level(reasoning_levels), algorithm),
        _level_transcript("L2", reasoning_levels.get("5") or reasoning_levels.get("15") or {}, algorithm),
    ]
    position_context = _position_context(radar_contract)
    boundaries = _reasoning_boundaries(algorithm, radar_contract)
    divergence_context = build_divergence_context(reasoning_contract)
    reasoning_evidence_pack = build_reasoning_evidence_pack(radar_contract, divergence_context)
    if signal_v2:
        reasoning_evidence_pack = {
            **reasoning_evidence_pack,
            "semantic_signal": signal_v2,
        }
    structure_kernel = radar_contract.get("structure_kernel") or {}
    if structure_kernel:
        reasoning_evidence_pack = {
            **reasoning_evidence_pack,
            "structure_kernel": {
                "version": structure_kernel.get("version"),
                "profile": structure_kernel.get("profile"),
                "structure_fingerprint": structure_kernel.get("structure_fingerprint"),
                "facts_digest": structure_kernel.get("facts_digest") or {},
                "data_quality": structure_kernel.get("data_quality") or {},
            },
        }
    allowed_prices = _dedupe_prices(
        _prices_from_levels(raw_levels)
        + _prices_from_levels(tactical_levels)
        + _prices_from_boundaries(boundaries)
        + _prices_from_position(radar_contract)
    )
    generated_at = datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds")
    chart_alignment = _chart_overlay_alignment(raw_levels)
    snapshot = build_structure_snapshot(
        symbol=symbol,
        mode=mode,
        generated_at=generated_at,
        radar_contract=radar_contract,
        raw_levels=raw_levels,
        boundaries=boundaries,
        chart_alignment=chart_alignment,
        divergence_context=divergence_context,
        allowed_prices=allowed_prices,
    )
    fingerprint = build_structure_fingerprint(
        mode=mode,
        levels=levels,
        algorithm=algorithm,
        freshness=freshness,
        position_context=position_context,
        divergence_context=divergence_context,
        chart_alignment=chart_alignment,
        signal_v2=signal_v2,
    )
    transcript = StructureTranscript(
        symbol=symbol,
        mode=mode,
        generated_at=generated_at,
        fingerprint_version=fingerprint_version,
        structure_fingerprint=fingerprint,
        structure_snapshot=snapshot,
        levels=levels,
        reasoning_boundaries=boundaries,
        divergence_context=divergence_context,
        position_context=position_context,
        market_context=market_context,
        reasoning_evidence_pack=reasoning_evidence_pack,
        signal_v2=signal_v2,
        allowed_prices=allowed_prices,
        stale=bool(freshness.get("is_stale")),
    )
    transcript.agent_observations = build_agent_observations(transcript)
    return transcript


def build_structure_snapshot(
    *,
    symbol: str,
    mode: str,
    generated_at: str,
    radar_contract: dict,
    raw_levels: dict,
    boundaries: ReasoningBoundaries,
    chart_alignment: ChartOverlayAlignment,
    divergence_context: DivergenceContext,
    allowed_prices: list[AllowedPrice],
) -> StructureSnapshot:
    """Build the canonical machine-readable fact packet for AI Native agents.

    Snapshot 不重新计算结构，只整理 Kline/chan_adapter 已经产出的事实，避免出现第二套真相。
    """
    freshness = radar_contract.get("freshness") or {}
    source = radar_contract.get("data_source") or {}
    ordered_levels = [level for level in ("week", "day", "60", "30", "15", "5", "1") if isinstance(raw_levels.get(level), dict)]
    levels = [
        _snapshot_level(level_key, raw_levels[level_key], freshness)
        for level_key in ordered_levels
    ]
    return StructureSnapshot(
        symbol=symbol,
        mode=mode,  # type: ignore[arg-type]
        generated_at=generated_at,
        available_levels=ordered_levels,
        levels=levels,
        key_boundaries=boundaries,
        chart_alignment=chart_alignment,
        divergence_context=divergence_context,
        allowed_prices=allowed_prices,
        data_health=freshness,
        source=source,
        consistency_warnings=_snapshot_warnings(raw_levels),
    )


def _chart_overlay_alignment(raw_levels: dict) -> ChartOverlayAlignment:
    """Summarize the exact structure objects Kline overlays would draw.

    这个对账包不重新判定笔、线段、中枢、买卖点，只把 `/api/chan/detail`
    同源字段压成 AI 可读摘要，防止推演和图上结构各说各话。
    """
    levels = []
    warnings = []
    required_levels = {"day", "30", "5"}
    for level_key in ("week", "day", "60", "30", "15", "5"):
        level = raw_levels.get(level_key)
        if not isinstance(level, dict):
            if level_key in required_levels:
                warnings.append(f"missing_chart_level:{level_key}")
            continue
        alignment_level = _chart_overlay_alignment_level(level_key, level)
        levels.append(alignment_level)
        warnings.extend(f"{level_key}:{warning}" for warning in alignment_level.warnings)

    if not levels:
        status = "MISSING"
    elif warnings:
        status = "PARTIAL"
    else:
        status = "ALIGNED"
    return ChartOverlayAlignment(status=status, levels=levels, warnings=warnings)


def _chart_overlay_alignment_level(level_key: str, level: dict) -> ChartOverlayAlignmentLevel:
    active = level.get("active_zhongshu") or {}
    active_center = CenterSnapshot(
        zg=_optional_num(level.get("zg") or active.get("zg")),
        zd=_optional_num(level.get("zd") or active.get("zd")),
        gg=_optional_num(level.get("gg") or active.get("gg")),
        dd=_optional_num(level.get("dd") or active.get("dd")),
        source="active_zhongshu" if active else "level",
    )
    counts = {
        "bi": len(level.get("bis") or []),
        "segment": len(level.get("segs") or []),
        "bi_center": len(level.get("bi_zhongshus") or []),
        "seg_center": len(level.get("seg_zhongshus") or []),
        "buy_sell_point": len(level.get("bsps") or []),
    }
    expected = {
        "bi": _optional_int(level.get("bi_count")),
        "segment": _optional_int(level.get("seg_count")),
        "bi_center": _optional_int(level.get("zhongshu_count")),
    }
    warnings = []
    for key, expected_count in expected.items():
        if expected_count is not None and expected_count != counts[key]:
            warnings.append(f"{key}_count_mismatch:{expected_count}!={counts[key]}")
    if not counts["bi_center"] and not (active_center.zd and active_center.zg):
        warnings.append("missing_visible_center")
    return ChartOverlayAlignmentLevel(
        level=level_key,
        display_freq=_display_freq(level_key),
        active_center=active_center,
        counts=counts,
        recent_buy_sell_points=_recent_dicts(level.get("bsps"), limit=4),
        warnings=warnings,
    )


def _display_freq(level_key: str) -> str:
    return {
        "week": "week",
        "day": "day",
        "60": "60",
        "30": "30",
        "15": "15",
        "5": "5",
    }.get(level_key, level_key)


def _snapshot_level(level_key: str, level: dict, freshness: dict) -> StructureSnapshotLevel:
    active = level.get("active_zhongshu") or {}
    center = CenterSnapshot(
        zg=_optional_num(level.get("zg") or active.get("zg")),
        zd=_optional_num(level.get("zd") or active.get("zd")),
        gg=_optional_num(level.get("gg") or active.get("gg")),
        dd=_optional_num(level.get("dd") or active.get("dd")),
        source="active_zhongshu" if active else "level",
    )
    patterns = [str(item) for item in (level.get("patterns") or [])[:8]]
    return StructureSnapshotLevel(
        role=_snapshot_role(level_key),
        level=level_key,
        price=_num(level.get("price")),
        state=str(level.get("state") or "UNKNOWN"),
        position_state=str(level.get("position_state") or level.get("center_relation") or "UNKNOWN"),
        center_relation=str(level.get("center_relation") or "UNKNOWN"),
        price_relation=str(level.get("price_relation") or level.get("position_state") or level.get("center_relation") or "UNKNOWN"),
        center=center,
        counts={
            "bi": _optional_int(level.get("bi_count")) or len(level.get("bis") or []),
            "segment": _optional_int(level.get("seg_count")) or len(level.get("segs") or []),
            "center": _optional_int(level.get("zhongshu_count")) or len(level.get("bi_zhongshus") or []),
        },
        last_bi_dir=str(level.get("last_bi_dir") or "unknown"),
        patterns=patterns,
        buy_sell_points=_recent_dicts(level.get("bsps"), limit=6),
        freshness=(freshness.get("levels") or {}).get(level_key) or {},
        source=level.get("source") or {},
        evidence=_snapshot_evidence(level_key, level, center, patterns),
    )


def _snapshot_role(level_key: str) -> str:
    return {
        "week": "macro",
        "day": "context",
        "60": "pivot_alt",
        "30": "pivot",
        "15": "trigger_alt",
        "5": "trigger",
        "1": "execution_preview",
    }.get(level_key, "extra")


def _recent_dicts(value: object, *, limit: int) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [item for item in value[-limit:] if isinstance(item, dict)]


def _snapshot_evidence(level_key: str, level: dict, center: CenterSnapshot, patterns: list[str]) -> list[str]:
    evidence = [f"{level_key} state={level.get('state') or 'UNKNOWN'}"]
    if center.zd:
        evidence.append(f"{level_key} ZD={center.zd:.2f}")
    if center.zg:
        evidence.append(f"{level_key} ZG={center.zg:.2f}")
    if center.dd:
        evidence.append(f"{level_key} DD={center.dd:.2f}")
    if center.gg:
        evidence.append(f"{level_key} GG={center.gg:.2f}")
    if patterns:
        evidence.append(f"{level_key} patterns={','.join(patterns[:4])}")
    return evidence


def _snapshot_warnings(raw_levels: dict) -> list[str]:
    warnings = []
    if not isinstance(raw_levels.get("week"), dict):
        warnings.append("missing_week_level")
    if not isinstance(raw_levels.get("day"), dict):
        warnings.append("missing_day_level")
    if not any(isinstance(raw_levels.get(level), dict) and _has_center_boundary(raw_levels[level]) for level in ("30", "60")):
        warnings.append("missing_pivot_center")
    if not any(isinstance(raw_levels.get(level), dict) for level in ("5", "15")):
        warnings.append("missing_trigger_level")
    return warnings


def build_structure_fingerprint(
    *,
    mode: str,
    levels: list[LevelTranscript],
    algorithm: dict,
    freshness: dict,
    position_context: PositionContext | None,
    divergence_context: DivergenceContext | None = None,
    chart_alignment: ChartOverlayAlignment | None = None,
    signal_v2: dict | None = None,
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
        _unknown(divergence_context.alignment if divergence_context else None),
        _unknown(divergence_context.pivot_level if divergence_context else None),
        _unknown(divergence_context.pivot_position if divergence_context else None),
        _unknown(divergence_context.chain_direction if divergence_context else None),
        _unknown(divergence_context.chain_status if divergence_context else None),
        _buy_sell_candidate_bucket(divergence_context),
        _unknown(_divergence_signal_bucket(divergence_context) if divergence_context else None),
        _chart_alignment_bucket(chart_alignment),
        _chart_alignment_warning_bucket(chart_alignment),
        _price_zone(algorithm),
        "STALE" if freshness.get("is_stale") else "FRESH",
        _holding_bucket(position_context),
        _pnl_bucket(position_context),
        _signal_bucket(signal_v2),
    ]
    return "|".join(parts)


def _signal_v2_context(signal: dict, algorithm: dict | None = None) -> dict:
    if not isinstance(signal, dict):
        return {}
    primary = signal.get("primary") if isinstance(signal.get("primary"), dict) else {}
    context = signal.get("context") if isinstance(signal.get("context"), dict) else {}
    if not primary.get("code") and not context.get("signal_code"):
        return {}
    deterministic_scenarios = _deterministic_scenarios_from_algorithm(algorithm or {})
    ai_classification = []
    for item in signal.get("classification") or []:
        if isinstance(item, dict):
            ai_classification.append(item)
    return {
        "version": signal.get("version") or "semantic_signal.v2",
        "state": signal.get("state") or "",
        "primary": {
            "code": primary.get("code") or context.get("signal_code") or "",
            "label_plain": primary.get("label_plain") or context.get("label_plain") or "",
            "label_expert": primary.get("label_expert") or context.get("label_expert") or "",
            "action": primary.get("action") or context.get("action") or "",
            "level": primary.get("level") or context.get("level") or "",
            "pattern": primary.get("pattern") or "",
            "strength": primary.get("strength") or "",
        },
        "context": {
            "key_price": context.get("key_price"),
            "boundary_state": context.get("boundary_state"),
            "stop_loss_price": context.get("stop_loss_price"),
            "risk_reward_ratio": context.get("risk_reward_ratio"),
            "action_rule": context.get("action_rule"),
        },
        "resonance": signal.get("resonance") or [],
        "deterministic_scenarios": deterministic_scenarios,
        "ai_classification": ai_classification,
        "disclaimer": signal.get("disclaimer") or context.get("disclaimer") or "",
    }


def _deterministic_scenarios_from_algorithm(algorithm: dict) -> list[dict]:
    if not isinstance(algorithm, dict):
        return []
    result = []
    for item in algorithm.get("scenarios") or []:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "id": item.get("id") or item.get("scenario_id") or "",
                "name": item.get("name") or item.get("title") or "",
                "type": item.get("type") or item.get("kind") or "",
                "status": item.get("status") or "",
                "triggers": item.get("triggers") or item.get("conditions") or [],
                "meaning": item.get("meaning") or item.get("summary") or "",
                "boundaries": item.get("boundaries") or [],
            }
        )
    if result:
        return result[:3]
    boundaries = algorithm.get("boundaries") if isinstance(algorithm.get("boundaries"), dict) else {}
    scenario_specs = [
        ("A", "confirm", "确认路径", boundaries.get("confirm") or []),
        ("B", "maintain", "维持路径", boundaries.get("maintain") or []),
        ("C", "invalidate", "失效路径", boundaries.get("invalidate") or []),
    ]
    for scenario_id, scenario_type, name, items in scenario_specs:
        result.append(
            {
                "id": scenario_id,
                "name": name,
                "type": scenario_type,
                "status": "CURRENT" if scenario_id == str(algorithm.get("current_scenario_id") or "") else "",
                "triggers": [],
                "meaning": "",
                "boundaries": items,
            }
        )
    return result


def _signal_bucket(signal_v2: dict | None) -> str:
    if not isinstance(signal_v2, dict):
        return "SIGNAL_NONE"
    primary = signal_v2.get("primary") if isinstance(signal_v2.get("primary"), dict) else {}
    code = str(primary.get("code") or "").strip()
    state = str(signal_v2.get("state") or "").strip()
    return f"SIGNAL_{state or 'UNKNOWN'}_{code or 'NONE'}"


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


def _preferred_l1_level(raw_levels: dict) -> dict:
    for key in ("30", "60"):
        level = raw_levels.get(key)
        if isinstance(level, dict) and _has_center_boundary(level):
            return level
    return raw_levels.get("30") or raw_levels.get("60") or {}


def _has_center_boundary(level: dict) -> bool:
    active = level.get("active_zhongshu") or {}
    return any(_num(level.get(key) or active.get(key)) > 0 for key in ("zd", "zg", "dd", "gg"))


def _reasoning_boundaries(algorithm: dict, radar_contract: dict) -> ReasoningBoundaries:
    raw = algorithm.get("boundaries") or {}
    boundaries = ReasoningBoundaries(
        confirm=_boundary_group(raw, "confirm") + _boundary_group(raw, "pressure"),
        observe=_boundary_group(raw, "maintain"),
        invalidate=_boundary_group(raw, "invalidate", exclude_triggers={"risk_event", "watch"}),
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


def _boundary_group(raw: dict, key: str, *, exclude_triggers: set[str] | None = None) -> list[AllowedPrice]:
    prices = []
    for item in raw.get(key) or []:
        trigger = str(item.get("trigger") or "").strip().lower()
        if exclude_triggers and trigger in exclude_triggers:
            continue
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
        for label, field in (("current_price", "price"), ("ZG", "zg"), ("ZD", "zd"), ("GG", "gg"), ("DD", "dd")):
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
        for bsp in level.get("bsps") or []:
            if not isinstance(bsp, dict):
                continue
            value = _num(bsp.get("price"))
            if value <= 0:
                continue
            bsp_type = str(bsp.get("type") or "BSP")
            prices.append(
                AllowedPrice(
                    label=f"{key}.BSP.{bsp_type}",
                    value=value,
                    source=f"structure.levels.{key}.bsps",
                    level=str(key),
                )
            )
        for bi in (level.get("detail_bis") or level.get("recent_bis") or level.get("bis") or [])[-5:]:
            if not isinstance(bi, dict):
                continue
            for label, field in (("bi.start", "y0"), ("bi.end", "y1")):
                value = _num(bi.get(field) or bi.get("start_price" if field == "y0" else "end_price"))
                if value <= 0:
                    continue
                prices.append(
                    AllowedPrice(
                        label=f"{key}.{label}",
                        value=value,
                        source=f"structure.levels.{key}.bis",
                        level=str(key),
                    )
                )
    return prices


def _prices_from_position(radar_contract: dict) -> list[AllowedPrice]:
    position = radar_contract.get("position_context") or {}
    coach = radar_contract.get("coach_action") or {}
    prices: list[AllowedPrice] = []
    for label, field in (
        ("holding.current_price", "current_price"),
        ("holding.avg_cost", "avg_cost"),
        ("holding.cost", "cost"),
        ("holding.stop_loss", "stop_loss_price"),
        ("holding.trailing_stop", "trailing_stop_price"),
        ("holding.m5_entry_zg", "m5_entry_zg"),
    ):
        value = _num(position.get(field))
        if value > 0:
            prices.append(AllowedPrice(label=label, value=value, source=f"position_context.{field}", level="holding"))
    for item in (coach.get("risk_lines") or []):
        if not isinstance(item, dict):
            continue
        value = _num(item.get("price"))
        if value > 0:
            name = str(item.get("label") or item.get("type") or "risk_line")
            prices.append(AllowedPrice(label=f"holding.{name}", value=value, source="coach_action.risk_lines", level="holding"))
    nearest = coach.get("nearest_risk_line") or {}
    if isinstance(nearest, dict):
        value = _num(nearest.get("price"))
        if value > 0:
            name = str(nearest.get("label") or nearest.get("type") or "nearest_risk_line")
            prices.append(AllowedPrice(label=f"holding.nearest.{name}", value=value, source="coach_action.nearest_risk_line", level="holding"))
    return prices


def _position_context(radar_contract: dict) -> PositionContext | None:
    position = radar_contract.get("position_context") or {}
    coach = radar_contract.get("coach_action") or {}
    holding_plan = radar_contract.get("holding_plan") or {}
    entry = holding_plan.get("entry_thesis") or {}
    is_holding = bool(position.get("is_holding") or radar_contract.get("mode") == "HOLDING")
    if not is_holding and not entry:
        return None
    cost = _optional_num(position.get("cost") or position.get("avg_cost") or entry.get("cost") or entry.get("avg_cost"))
    risk_lines = [item for item in (coach.get("risk_lines") or []) if isinstance(item, dict)]
    nearest_risk_line = coach.get("nearest_risk_line") if isinstance(coach.get("nearest_risk_line"), dict) else None
    return PositionContext(
        is_holding=is_holding,
        state=str(position.get("state") or ("HOLDING" if is_holding else "EMPTY")),
        label=str(position.get("label") or ("持仓" if is_holding else "空仓")),
        cost=cost,
        avg_cost=cost,
        quantity=_optional_int(position.get("quantity") or entry.get("qty")),
        current_price=_optional_num(position.get("current_price") or position.get("structure_price")),
        pnl_percentage=_optional_num(position.get("pnl_pct") or position.get("pnl_percentage")),
        position_value=_optional_num(position.get("position_value")),
        weight_pct=_optional_num(position.get("weight_pct")),
        risk_flags=[str(item) for item in (position.get("risk_flags") or []) if item],
        risk_lines=risk_lines,
        nearest_risk_line=nearest_risk_line,
        coach_summary=str(coach.get("summary") or ""),
        coach_focus=str(coach.get("focus") or ""),
        coach_reason=str(coach.get("reason") or ""),
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


def _divergence_signal_bucket(divergence_context: DivergenceContext) -> str:
    signals = divergence_context.lower_level_signals + divergence_context.pivot_signals
    if not signals:
        return "NO_SIGNAL"
    parts = [
        f"{signal.level}:{signal.type}:{signal.status}"
        for signal in sorted(signals, key=lambda item: (item.level, item.type, item.status))
    ]
    return ",".join(parts[:6])


def _chart_alignment_bucket(chart_alignment: ChartOverlayAlignment | None) -> str:
    if not chart_alignment:
        return "CHART_UNKNOWN"
    return f"CHART_{chart_alignment.status}"


def _buy_sell_candidate_bucket(divergence_context: DivergenceContext | None) -> str:
    if not divergence_context:
        return "BSP_NONE"
    candidate = divergence_context.buy_sell_candidate
    return f"BSP_{candidate.side}_{candidate.kind}_{candidate.status}"


def _chart_alignment_warning_bucket(chart_alignment: ChartOverlayAlignment | None) -> str:
    if not chart_alignment or not chart_alignment.warnings:
        return "CHART_WARN_NONE"
    return ",".join(sorted(chart_alignment.warnings)[:3])


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
