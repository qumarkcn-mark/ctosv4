"""Adapt existing structure transcripts into V4.5 Chan Fusion input."""

from __future__ import annotations

from server.engines.ai_native.fusion_schemas import (
    ChanAnalysisResult,
    ChanKeyLevel,
    ChanPathCandidate,
    LevelName,
)
from server.engines.ai_native.schemas import (
    AllowedPrice,
    DivergenceContext,
    ReasoningBoundaries,
    StructureSnapshotLevel,
    StructureTranscript,
)
from server.engines.ai_native.transcript_compiler import compile_structure_transcript


SUPPORTED_LEVELS = {"week", "day", "60", "30", "15", "5"}


def build_chan_analysis_from_radar_contract(radar_contract: dict) -> ChanAnalysisResult:
    """Build V4.5 Chan input from the existing Radar contract."""
    return build_chan_analysis_from_transcript(compile_structure_transcript(radar_contract))


def build_chan_analysis_from_transcript(transcript: StructureTranscript) -> ChanAnalysisResult:
    """Convert canonical structure facts into the Fusion Chan contract.

    这里不重新跑缠论，也不生成最终交易建议。它只把已有结构事实整理成
    “我在哪、有哪些路、哪些边界失效”的结构输入。
    """
    pivot = _pivot_level(transcript)
    current_position = _current_position(transcript, pivot)
    key_levels = _key_levels(transcript)
    complete_paths = _complete_paths(transcript, pivot)
    discipline_rules = _discipline_rules(transcript)
    warnings = list(transcript.structure_snapshot.consistency_warnings or [])
    if transcript.stale:
        warnings.append("structure_data_stale")

    return ChanAnalysisResult(
        symbol=transcript.symbol,
        generated_at=transcript.generated_at,
        primary_level=_safe_level(pivot.level, default="30"),
        current_position=current_position,
        structure_state=pivot.state or "UNKNOWN",
        trend_context=_trend_context(transcript),
        center_state=_center_state(pivot),
        buy_sell_candidates=[transcript.divergence_context.buy_sell_candidate.model_dump()],
        signal_v2=transcript.signal_v2 or {},
        complete_paths=complete_paths,
        key_levels=key_levels,
        discipline_rules=discipline_rules,
        warnings=warnings,
    )


def _pivot_level(transcript: StructureTranscript) -> StructureSnapshotLevel:
    snapshot_levels = transcript.structure_snapshot.levels
    for role in ("pivot", "pivot_alt", "context"):
        for level in snapshot_levels:
            if level.role == role:
                return level
    if snapshot_levels:
        return snapshot_levels[0]
    return StructureSnapshotLevel(role="pivot", level="30")


def _current_position(transcript: StructureTranscript, pivot: StructureSnapshotLevel) -> str:
    evidence_pack = transcript.reasoning_evidence_pack or {}
    commander = (evidence_pack.get("commander_context") or {}).get("primary_context") or {}
    if commander.get("code"):
        return f"{commander.get('code')} / {commander.get('bias') or 'UNKNOWN'}"
    parts = [
        f"{pivot.level} {pivot.state}",
        f"price_relation={pivot.price_relation}",
        f"divergence={transcript.divergence_context.alignment}",
    ]
    return " / ".join(part for part in parts if part)


def _trend_context(transcript: StructureTranscript) -> str:
    l0 = next((item for item in transcript.levels if item.role == "L0"), None)
    if not l0:
        return "UNKNOWN"
    return f"{l0.level} path={l0.path}, phase={l0.phase}, state={l0.raw_state}"


def _center_state(pivot: StructureSnapshotLevel) -> str:
    center = pivot.center
    if center.zd and center.zg:
        return f"{pivot.level} center ZD={center.zd:.2f}, ZG={center.zg:.2f}, relation={pivot.price_relation}"
    return f"{pivot.level} center unavailable, relation={pivot.price_relation}"


def _key_levels(transcript: StructureTranscript) -> list[ChanKeyLevel]:
    levels: list[ChanKeyLevel] = []
    boundaries = transcript.reasoning_boundaries
    levels.extend(_levels_from_prices(boundaries.confirm, role="trigger"))
    levels.extend(_levels_from_prices(boundaries.observe, role="support"))
    levels.extend(_levels_from_prices(boundaries.invalidate, role="invalidation"))
    levels.extend(_levels_from_prices(boundaries.support, role="support"))

    for level in transcript.structure_snapshot.levels:
        safe_level = _safe_level(level.level)
        if safe_level is None:
            continue
        center = level.center
        if center.zg:
            levels.append(
                ChanKeyLevel(
                    label=f"{safe_level}.ZG",
                    price=center.zg,
                    level=safe_level,
                    role="center_upper",
                    source="structure_snapshot.center",
                )
            )
        if center.zd:
            levels.append(
                ChanKeyLevel(
                    label=f"{safe_level}.ZD",
                    price=center.zd,
                    level=safe_level,
                    role="center_lower",
                    source="structure_snapshot.center",
                )
            )
    return _dedupe_key_levels(levels)


def _levels_from_prices(prices: list[AllowedPrice], *, role: str) -> list[ChanKeyLevel]:
    result: list[ChanKeyLevel] = []
    for price in prices:
        safe_level = _safe_level(price.level)
        if safe_level is None or price.value <= 0:
            continue
        result.append(
            ChanKeyLevel(
                label=price.label,
                price=price.value,
                level=safe_level,
                role=role,  # type: ignore[arg-type]
                source=price.source,
            )
        )
    return result


def _complete_paths(transcript: StructureTranscript, pivot: StructureSnapshotLevel) -> list[ChanPathCandidate]:
    boundaries = transcript.reasoning_boundaries
    key_levels = _key_levels(transcript)
    paths = [
        _confirm_path(transcript, pivot, boundaries, key_levels),
        _range_path(transcript, pivot, boundaries, key_levels),
        _invalidate_path(transcript, pivot, boundaries, key_levels),
    ]
    candidate = _divergence_path(transcript.divergence_context, pivot, key_levels)
    if candidate:
        paths.append(candidate)
    return paths


def _confirm_path(
    transcript: StructureTranscript,
    pivot: StructureSnapshotLevel,
    boundaries: ReasoningBoundaries,
    key_levels: list[ChanKeyLevel],
) -> ChanPathCandidate:
    trigger = _first_price(boundaries.confirm) or _center_price(pivot, "zg")
    trigger_text = _condition("站上或守住确认边界", trigger)
    invalidation = _first_price(boundaries.invalidate) or _downside_boundary(trigger, boundaries, pivot)
    return ChanPathCandidate(
        id="A",
        name="结构确认后延续",
        level=_safe_level(pivot.level, default="30"),
        status="WAITING",
        structure_logic=f"{pivot.level} 结构若完成确认，当前路径从观察转为右侧延续。",
        trigger_condition=trigger_text,
        invalidation_condition=_condition("跌破结构失效边界", invalidation),
        key_levels=_filter_levels(key_levels, trigger, invalidation),
        evidence=_path_evidence(transcript, pivot),
    )


def _range_path(
    transcript: StructureTranscript,
    pivot: StructureSnapshotLevel,
    boundaries: ReasoningBoundaries,
    key_levels: list[ChanKeyLevel],
) -> ChanPathCandidate:
    resistance = _first_price(boundaries.confirm) or _center_price(pivot, "zg")
    support = _downside_boundary(resistance, boundaries, pivot)
    return ChanPathCandidate(
        id="B",
        name="中枢震荡或回踩等待",
        level=_safe_level(pivot.level, default="30"),
        status="CURRENT",
        structure_logic="结构未给出明确确认或失效前，按中枢震荡/回踩等待处理。",
        trigger_condition=_condition("向上突破观察区间", resistance),
        invalidation_condition=_condition("跌破观察区间下沿", support),
        key_levels=_filter_levels(key_levels, support, resistance),
        evidence=_path_evidence(transcript, pivot),
    )


def _invalidate_path(
    transcript: StructureTranscript,
    pivot: StructureSnapshotLevel,
    boundaries: ReasoningBoundaries,
    key_levels: list[ChanKeyLevel],
) -> ChanPathCandidate:
    invalidation = _downside_boundary(None, boundaries, pivot)
    support = _first_price(boundaries.support)
    return ChanPathCandidate(
        id="C",
        name="跌破结构边界，原推演失效",
        level=_safe_level(pivot.level, default="30"),
        status="WAITING",
        structure_logic="一旦关键结构边界被跌破，原先的买点/持仓信心锚失效，进入防守推演。",
        trigger_condition=_condition("跌破失效边界", invalidation),
        invalidation_condition=_condition("重新收回失效边界", invalidation),
        key_levels=_filter_levels(key_levels, invalidation, support),
        evidence=_path_evidence(transcript, pivot),
    )


def _divergence_path(
    divergence: DivergenceContext,
    pivot: StructureSnapshotLevel,
    key_levels: list[ChanKeyLevel],
) -> ChanPathCandidate | None:
    candidate = divergence.buy_sell_candidate
    if candidate.side == "NONE" or candidate.kind == "NONE":
        return None
    prefix = "买点" if candidate.side == "BUY" else "卖点风险"
    trigger = candidate.trigger_boundary
    invalidation = candidate.invalidation_boundary
    return ChanPathCandidate(
        id="D",
        name=f"{prefix}候选等待验证",
        level=_safe_level(candidate.level or pivot.level, default="30"),
        status="WAITING" if candidate.status in {"SIGNAL_ONLY", "WAITING_CONFIRM"} else "CURRENT",
        structure_logic=f"{candidate.kind} / {candidate.status}: {candidate.note or '等待多级别结构确认。'}",
        trigger_condition=candidate.note or _condition("等待触发边界确认", trigger),
        invalidation_condition=_condition("候选信号失效", invalidation),
        key_levels=_filter_levels(key_levels, trigger, invalidation),
        evidence=candidate.evidence + [step.note for step in divergence.chain if step.note],
    )


def _discipline_rules(transcript: StructureTranscript) -> list[str]:
    rules = [
        "结构未确认前，AI Fusion 只能输出等待或观察，不能输出自动交易指令。",
        "Kronos 仅提供时间窗口和价格区间参考，不能替代缠论结构触发或路径概率判断。",
        "涉及交易动作必须保留“仅供参考，不构成投资建议”。",
    ]
    divergence = transcript.divergence_context
    if divergence.failure_condition:
        rules.append(divergence.failure_condition)
    if transcript.stale:
        rules.append("结构数据过期时，不做强推演。")
    return rules


def _path_evidence(transcript: StructureTranscript, pivot: StructureSnapshotLevel) -> list[str]:
    evidence = list(pivot.evidence or [])
    evidence.append(f"divergence_alignment={transcript.divergence_context.alignment}")
    evidence.extend(item.verdict for item in transcript.agent_observations[:3])
    return [item for item in evidence if item]


def _condition(prefix: str, price: float | None) -> str:
    if price and price > 0:
        return f"{prefix} {price:.2f}"
    return f"{prefix}，等待结构边界补齐"


def _first_price(prices: list[AllowedPrice]) -> float | None:
    for item in prices:
        if item.value and item.value > 0:
            return item.value
    return None


def _downside_boundary(reference: float | None, boundaries: ReasoningBoundaries, pivot: StructureSnapshotLevel) -> float | None:
    candidates: list[float] = []
    for group in (boundaries.invalidate, boundaries.observe, boundaries.support):
        for item in group:
            if item.value and item.value > 0:
                candidates.append(float(item.value))
    for value in (_center_price(pivot, "zd"), _center_price(pivot, "dd")):
        if value and value > 0:
            candidates.append(float(value))
    if not candidates:
        return None
    if reference and reference > 0:
        lower = [value for value in candidates if value < reference]
        if lower:
            return max(lower)
    return candidates[0]


def _center_price(pivot: StructureSnapshotLevel, field: str) -> float | None:
    value = getattr(pivot.center, field, None)
    return value if value and value > 0 else None


def _filter_levels(key_levels: list[ChanKeyLevel], *prices: float | None) -> list[ChanKeyLevel]:
    wanted = {round(price, 2) for price in prices if price and price > 0}
    if not wanted:
        return key_levels[:4]
    matched = [item for item in key_levels if round(item.price, 2) in wanted]
    return matched or key_levels[:4]


def _dedupe_key_levels(levels: list[ChanKeyLevel]) -> list[ChanKeyLevel]:
    seen = set()
    result = []
    for level in levels:
        key = (level.label, round(level.price, 2), level.level, level.role)
        if key in seen:
            continue
        seen.add(key)
        result.append(level)
    return result


def _safe_level(level: object, *, default: LevelName | None = None) -> LevelName | None:
    value = str(level or "").strip()
    if value in SUPPORTED_LEVELS:
        return value  # type: ignore[return-value]
    return default
