"""V4.5 first-stage AI Chan reasoning."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from server import config
from server.engines.ai_native.ai_fusion_engine import _elapsed_ms
from server.engines.ai_native.fusion_schemas import (
    AIChanInference,
    AIChanPath,
    AIClassificationOutput,
    AIPathScenario,
    ChanAnalysisResult,
    LevelPosition,
    MarketRawFacts,
    TacticalGuide,
)
from server.engines.ai_native.schemas import DISCLAIMER, ModelRoute, PositionContext
from server.engines.structure.chan_adapter import export_raw_bi_context
from server.prompts.ai_chan_reasoning_prompt import AI_CHAN_REASONING_PROMPT, AI_CHAN_REASONING_PROMPT_VERSION
from server.services.llm_service import LLMService


logger = logging.getLogger(__name__)


async def build_ai_chan_inference(
    *,
    chan_analysis: ChanAnalysisResult,
    position_context: PositionContext | None = None,
    raw_bi_context: dict | None = None,
    user_id: int = 1,
    llm_service: LLMService | None = None,
    model_route: ModelRoute | None = None,
    fallback_on_error: bool = False,
) -> AIChanInference:
    """Run first-stage AI Chan reasoning from raw bi sequence.

    V4.5 实战教练版：优先使用 raw_bi_context（原始笔序列），
    AI 自行识别中枢、判断位置。chan_analysis 降级为算法参考。
    """
    service = llm_service or LLMService()

    # P4: 如果没有传入 raw_bi_context，尝试自动获取
    if raw_bi_context is None:
        try:
            raw_bi_context = await export_raw_bi_context(chan_analysis.symbol)
        except Exception as exc:
            logger.warning("Failed to export raw bi context: %s", exc)
            raw_bi_context = None

    if not _has_minimum_reasoning_data(chan_analysis, raw_bi_context):
        reason = "AI Chan 输入数据不足：缺少可推演的笔序列、结构路径和关键价位"
        logger.warning("%s: symbol=%s", reason, chan_analysis.symbol)
        return _waiting_ai_chan(chan_analysis, reason=reason)

    context = _ai_chan_context(chan_analysis, position_context, raw_bi_context)
    context_json = json.dumps(context, ensure_ascii=False, indent=2)
    effective_route = model_route or _default_ai_chan_model_route()
    diagnostics = {
        "prompt_chars": len(context_json),
        "llm_timeout_seconds": effective_route.timeout_seconds,
        "model_name": effective_route.model_name,
        "thinking_enabled": effective_route.thinking_enabled,
        "reasoning_effort": effective_route.reasoning_effort,
    }
    try:
        llm_started = time.perf_counter()
        raw_output = await asyncio.wait_for(
            service.infer_ai_native_radar(
                AI_CHAN_REASONING_PROMPT,
                context_json,
                user_id=user_id,
                model_route=effective_route,
            ),
            timeout=effective_route.timeout_seconds,
        )
        output = _coerce_ai_chan_output(raw_output, chan_analysis)
        output.source_versions = {
            **(output.source_versions or {}),
            "chan": chan_analysis.version,
            "prompt": AI_CHAN_REASONING_PROMPT_VERSION,
            "signal_code": _signal_code(chan_analysis),
            "llm_ms": _elapsed_ms(llm_started),
            "fallback_triggered": False,
            **diagnostics,
        }
        return output
    except asyncio.TimeoutError:
        reason = f"AI 服务忙，请稍后重试：AI Chan 推演超时 {effective_route.timeout_seconds:.0f}s"
        logger.error("%s: symbol=%s", reason, chan_analysis.symbol)
        if fallback_on_error:
            return _waiting_ai_chan(chan_analysis, reason=reason)
        raise RuntimeError(reason) from None
    except Exception as exc:
        reason = f"AI 服务忙，请稍后重试：AI Chan 推演异常 {str(exc)[:120]}"
        logger.error("AI Chan reasoning failed: %s", exc)
        if fallback_on_error:
            return _waiting_ai_chan(chan_analysis, reason=reason)
        raise RuntimeError(reason) from exc


def _default_ai_chan_model_route() -> ModelRoute:
    effort = config.AI_NATIVE_RADAR_REASONING_EFFORT
    reasoning_effort = "max" if effort == "max" else "high"
    return ModelRoute(
        tier="simple",
        difficulty_score=0,
        model_name=config.AI_NATIVE_FUSION_MODEL,
        thinking_enabled=config.AI_NATIVE_FUSION_THINKING_ENABLED,
        reasoning_effort=reasoning_effort,
        max_tokens=config.AI_NATIVE_FUSION_MAX_TOKENS,
        timeout_seconds=max(config.AI_NATIVE_FUSION_LLM_TIMEOUT, config.AI_NATIVE_RADAR_LLM_TIMEOUT),
        reasons=["AI Chan V7 六步推理链较长，使用 Radar 级 SLA；失败直接返回 AI 服务忙。"],
    )


def _ai_chan_context(
    chan_analysis: ChanAnalysisResult,
    position_context: PositionContext | None,
    raw_bi_context: dict | None = None,
) -> dict:
    # P4: raw_bi_context 是主输入，chan_structure 降级为算法参考
    context = {
        "version": "ai_chan_input.v70_six_step",
        "raw_bi_context": raw_bi_context if raw_bi_context else {"levels": {}},
        "current_price": _estimate_current_price(raw_bi_context, chan_analysis),
        "semantic_signal": _semantic_signal_context(chan_analysis),
        "algorithm_reference": {
            "note": "以下是硬编码算法的结构判断，仅供参考。AI 应基于 raw_bi_context 自行推演，可修正或推翻。",
            "current_position": chan_analysis.current_position,
            "structure_state": chan_analysis.structure_state,
            "key_levels": [
                {"label": lv.label, "price": lv.price, "level": lv.level, "role": lv.role}
                for lv in chan_analysis.key_levels[:8]
            ],
            "buy_sell_candidates": chan_analysis.buy_sell_candidates[:3],
        },
        "position_context": position_context.model_dump() if position_context else None,
        "rules": {
            "input_priority": "raw_bi_context 是主输入，semantic_signal 与 algorithm_reference 仅供参考和验证。",
            "reasoner_role": "从原始笔序列自行识别中枢、判断位置、给出三段式作战指令。",
            "must_verify": "algorithm_zhongshus 可能有错，必须从笔序列验证后才能引用。",
            "semantic_signal_rule": "必须引用 semantic_signal.primary.code；若推演结论不同，必须在 corrections 中说明为什么 raw_bi_context 推翻了短码动作。",
            "do_not_use": ["Kronos", "final_path_probability", "automatic_trade"],
            "output_format": "展示优先级：level_positions + synthesis + paths(ABC)。main_deduction 只作内部长摘要，不作为前端主展示。",
            "disclaimer": DISCLAIMER,
        },
    }
    return context


def _semantic_signal_context(chan_analysis: ChanAnalysisResult) -> dict | None:
    signal = chan_analysis.signal_v2 if isinstance(chan_analysis.signal_v2, dict) else {}
    primary = signal.get("primary") if isinstance(signal.get("primary"), dict) else {}
    code = str(primary.get("code") or "").strip()
    if not code:
        return None
    return {
        "version": signal.get("version") or "semantic_signal.v2",
        "state": signal.get("state") or "",
        "primary": primary,
        "context": signal.get("context") or {},
        "resonance": signal.get("resonance") or [],
        "deterministic_scenarios": signal.get("deterministic_scenarios") or [],
        "ai_classification": signal.get("ai_classification") or [],
        "usage": "参考锚点。输出需引用 primary.code；如与 raw_bi_context 推导冲突，写入 corrections。",
    }


def _signal_code(chan_analysis: ChanAnalysisResult) -> str:
    signal = chan_analysis.signal_v2 if isinstance(chan_analysis.signal_v2, dict) else {}
    primary = signal.get("primary") if isinstance(signal.get("primary"), dict) else {}
    return str(primary.get("code") or "").strip()


def _estimate_current_price(raw_bi_context: dict | None, chan_analysis: ChanAnalysisResult) -> float | None:
    """从 raw_bi_context 或 chan_analysis 估算当前价格。"""
    if raw_bi_context and isinstance(raw_bi_context.get("levels"), dict):
        # 优先取 30 分钟级别的 last_close
        for level_key in ("30", "day", "5"):
            level_data = raw_bi_context["levels"].get(level_key)
            if isinstance(level_data, dict) and level_data.get("last_close"):
                return level_data["last_close"]
    # 从 key_levels 取第一个价格
    if chan_analysis.key_levels:
        return chan_analysis.key_levels[0].price
    return None


def _chan_raw_facts(chan_analysis: ChanAnalysisResult) -> MarketRawFacts:
    """Build first-stage facts from Chan structure only.

    AI Chan 是两段式链路的第一段，不能提前消费 Kronos 预测价或动力学结论。
    当前结构合同暂不携带实时成交价，因此这里只陈列结构边界价位。
    """
    return MarketRawFacts(
        symbol=chan_analysis.symbol,
        generated_at=chan_analysis.generated_at,
        current_price=None,
        distance_to_high=None,
        distance_to_low=None,
        key_price_facts=[
            {
                "label": level.label,
                "price": level.price,
                "level": level.level,
                "role": level.role,
                "source": level.source,
            }
            for level in chan_analysis.key_levels[:12]
        ],
        source="chan_structure_only_snapshot",
    )


def _coerce_ai_chan_output(raw_output: Any, chan_analysis: ChanAnalysisResult) -> AIChanInference:
    if not isinstance(raw_output, dict):
        raise ValueError("AI Chan output is not a JSON object")
    paths = _coerce_ai_chan_paths(raw_output.get("paths") or raw_output.get("complete_paths"), chan_analysis)
    signal_code = _signal_code(chan_analysis)
    classification, classification_compat = _coerce_classification(raw_output.get("classification"), paths, chan_analysis, signal_code)
    classification_violations = validate_classification(classification)
    primary_path_id = str(raw_output.get("primary_path_id") or raw_output.get("primaryPathId") or "")
    if not primary_path_id and paths:
        current = next((path for path in paths if path.status == "CURRENT"), paths[0])
        primary_path_id = current.id
    corrections = _string_list(raw_output.get("corrections"))
    if classification_violations:
        corrections.append("实时完全分类格式未达标：" + "；".join(classification_violations[:4]))
    payload = {
        "version": "ai_chan_inference.v70",
        "symbol": str(raw_output.get("symbol") or chan_analysis.symbol),
        "generated_at": str(raw_output.get("generated_at") or raw_output.get("generatedAt") or chan_analysis.generated_at),
        "current_position": _text(raw_output, "current_position", "currentPosition", "position", default=chan_analysis.current_position),
        "structure_confidence": _confidence_float(raw_output.get("structure_confidence") or raw_output.get("structureConfidence"), 0.55),
        "level_positions": [item.model_dump() for item in _coerce_level_positions(raw_output.get("level_positions") or raw_output.get("levelPositions"))],
        "main_deduction": _text_or_none(raw_output, "main_deduction", "mainDeduction"),
        "synthesis": _text_or_none(raw_output, "synthesis", "summary", "final_judgement", "finalJudgement"),
        "tactical_guide": _coerce_tactical_guide(raw_output.get("tactical_guide") or raw_output.get("tacticalGuide")),
        "classification": classification.model_dump(),
        "primary_path_id": primary_path_id,
        "paths": [path.model_dump() for path in paths],
        "defense_line": _line_price(raw_output.get("defense_line") or raw_output.get("defenseLine"), chan_analysis, "invalidation"),
        "observation_line": _line_price(raw_output.get("observation_line") or raw_output.get("observationLine"), chan_analysis, "trigger"),
        "wait_for": _string_list(raw_output.get("wait_for") or raw_output.get("waitFor") or [_default_trigger(chan_analysis)]),
        "invalidation": _string_list(raw_output.get("invalidation") or raw_output.get("invalidations") or [_default_invalidation(chan_analysis)]),
        "discipline": _with_disclaimer(_text(raw_output, "discipline", "position_discipline", default="未确认前只观察，按结构边界防守。")),
        "corrections": corrections,
        "uncertainty": _string_list(raw_output.get("uncertainty")),
        "source_versions": {
            **(raw_output.get("source_versions") if isinstance(raw_output.get("source_versions"), dict) else {}),
            "classification_compat_mode": classification_compat,
            "classification_validation_violations": classification_violations,
        },
        "disclaimer": _with_disclaimer(str(raw_output.get("disclaimer") or "")),
    }
    return AIChanInference(**payload)


def _coerce_classification(
    raw: Any,
    compat_paths: list[AIChanPath],
    chan_analysis: ChanAnalysisResult,
    signal_code: str,
) -> tuple[AIClassificationOutput, bool]:
    if isinstance(raw, dict):
        paths = []
        for index, item in enumerate(raw.get("paths") or [], start=1):
            scenario = _coerce_path_scenario(item, index, chan_analysis)
            if scenario:
                paths.append(scenario)
        classification = AIClassificationOutput(
            current_signal=str(raw.get("current_signal") or raw.get("currentSignal") or signal_code),
            structure_basis=str(raw.get("structure_basis") or raw.get("structureBasis") or chan_analysis.current_position),
            paths=paths,
        )
        return classification, False

    scenarios = []
    for index, path in enumerate(compat_paths[:5], start=1):
        scenarios.append(
            AIPathScenario(
                path_id=index,
                current_state=path.description or chan_analysis.current_position,
                description=path.name or path.description,
                next_boundary=_next_boundary_from_path(path),
                trigger_condition=path.entry_condition,
                target_price=_path_target_price(path),
                invalidate_price=_path_invalidate_price(path, chan_analysis),
                action=_path_action(path),
                requires_confirmation=True,
                evidence=[signal_code] if signal_code else [],
            )
        )
    return AIClassificationOutput(
        current_signal=signal_code,
        structure_basis=chan_analysis.current_position,
        paths=scenarios,
    ), True


def _coerce_path_scenario(raw_item: Any, index: int, chan_analysis: ChanAnalysisResult) -> AIPathScenario | None:
    raw = raw_item if isinstance(raw_item, dict) else {}
    if not raw:
        return None
    return AIPathScenario(
        path_id=int(_safe_float(raw.get("path_id") or raw.get("pathId") or index) or index),
        current_state=_text(raw, "current_state", "currentState", default=chan_analysis.current_position),
        description=_text(raw, "description", "summary", default=f"路径 {index}"),
        next_boundary=_text(raw, "next_boundary", "nextBoundary", default=_default_trigger(chan_analysis)),
        trigger_condition=_text(raw, "trigger_condition", "triggerCondition", "trigger", default=_default_trigger(chan_analysis)),
        target_price=_safe_float(raw.get("target_price") or raw.get("targetPrice") or raw.get("target")),
        invalidate_price=_safe_float(raw.get("invalidate_price") or raw.get("invalidatePrice") or raw.get("invalidation_price") or raw.get("invalidationPrice")),
        action=_text(raw, "action", "operation", default="等待确认。"),
        requires_confirmation=bool(raw.get("requires_confirmation", raw.get("requiresConfirmation", True))),
        evidence=_string_list(raw.get("evidence")),
    )


def validate_classification(output: AIClassificationOutput) -> list[str]:
    violations = []
    if not output.current_signal:
        violations.append("MISSING_SIGNAL: 缺少 current_signal")
    if len(output.paths) < 2:
        violations.append("TOO_FEW_PATHS: 实时完全分类至少需要 2 条路径")
    for path in output.paths:
        if not path.current_state:
            violations.append(f"MISSING_CURRENT_STATE: 路径 {path.path_id}")
        if not path.next_boundary:
            violations.append(f"MISSING_NEXT_BOUNDARY: 路径 {path.path_id}")
        if not path.trigger_condition:
            violations.append(f"MISSING_TRIGGER: 路径 {path.path_id}")
        if path.invalidate_price is None:
            violations.append(f"MISSING_INVALIDATE_PRICE: 路径 {path.path_id}")
        if not path.action:
            violations.append(f"MISSING_ACTION: 路径 {path.path_id}")
        if not _trigger_mentions_realtime_anchor(path.trigger_condition, path.next_boundary):
            violations.append(f"TRIGGER_NOT_REALTIME_ANCHORED: 路径 {path.path_id}")
    return violations


def _coerce_level_positions(raw: Any) -> list[LevelPosition]:
    if not isinstance(raw, list):
        return []
    positions: list[LevelPosition] = []
    for item in raw[:5]:
        if not isinstance(item, dict):
            continue
        level = str(item.get("level") or item.get("timeframe") or "").strip()
        position = str(item.get("position") or item.get("summary") or item.get("description") or "").strip()
        if not level or not position:
            continue
        positions.append(
            LevelPosition(
                level=level,
                position=position,
                key_price=_safe_float(item.get("key_price") or item.get("keyPrice")),
                key_price_label=_text_or_none(item, "key_price_label", "keyPriceLabel", "key_label", "keyLabel"),
            )
        )
    return positions


def _coerce_ai_chan_paths(raw_paths: Any, chan_analysis: ChanAnalysisResult) -> list[AIChanPath]:
    items = raw_paths if isinstance(raw_paths, list) and raw_paths else []
    if not items:
        items = [
            {
                "id": path.id,
                "name": path.name,
                "description": path.structure_logic,
                "status": "CURRENT" if path.status == "CURRENT" else "CANDIDATE",
                "entry_condition": path.trigger_condition,
                "invalidation": path.invalidation_condition,
                "chan_basis": path.structure_logic,
                "confidence": 0.5 if path.status == "CURRENT" else 0.35,
            }
            for path in chan_analysis.complete_paths
        ]
    if not items:
        items = [{
            "id": "UNKNOWN",
            "name": "结构观察路径",
            "description": chan_analysis.current_position,
            "status": "UNKNOWN",
            "entry_condition": "等待结构边界补齐。",
            "invalidation": "结构不清晰时不做强推演。",
            "chan_basis": chan_analysis.current_position,
            "confidence": 0.2,
        }]

    paths: list[AIChanPath] = []
    for index, item in enumerate(items[:5], start=1):
        raw = item if isinstance(item, dict) else {"description": str(item)}
        paths.append(
            AIChanPath(
                id=str(raw.get("id") or raw.get("chan_path_id") or f"path-{index}"),
                name=_text(raw, "name", "title", default=f"路径 {index}"),
                description=_text(raw, "description", "structure_logic", "summary", default=chan_analysis.current_position),
                status=_ai_chan_path_status(raw.get("status")),
                entry_condition=_text(raw, "entry_condition", "entryCondition", "trigger_condition", default=_default_trigger(chan_analysis)),
                invalidation=_text(raw, "invalidation", "invalidation_condition", default=_default_invalidation(chan_analysis)),
                chan_basis=_text(raw, "chan_basis", "chanBasis", "basis", default=chan_analysis.current_position),
                confidence=_confidence_float(raw.get("confidence"), 0.35),
            )
        )
    return paths


def _has_minimum_reasoning_data(chan_analysis: ChanAnalysisResult, raw_bi_context: dict | None) -> bool:
    if chan_analysis.complete_paths or chan_analysis.key_levels or chan_analysis.buy_sell_candidates:
        return True
    levels = raw_bi_context.get("levels") if isinstance(raw_bi_context, dict) else None
    if not isinstance(levels, dict):
        return False
    for level_data in levels.values():
        if not isinstance(level_data, dict):
            continue
        for key in ("bis", "bi_list", "raw_bis", "pens", "bi_sequence"):
            value = level_data.get(key)
            if isinstance(value, list) and value:
                return True
    return False


def _waiting_ai_chan(chan_analysis: ChanAnalysisResult, *, reason: str) -> AIChanInference:
    path = AIChanPath(
        id="WAITING",
        name="等待结构数据补齐",
        description="当前 K 线结构数据不足，暂不做 AI 自由推演。",
        status="UNKNOWN",
        entry_condition="等待笔序列、关键价位或买卖点候选补齐后重新推演。",
        invalidation="数据不足时不做方向性判断。",
        chan_basis=reason,
        confidence=0.0,
    )
    return AIChanInference(
        symbol=chan_analysis.symbol,
        generated_at=chan_analysis.generated_at,
        current_position="WAITING",
        structure_confidence=0.0,
        classification=AIClassificationOutput(
            current_signal=_signal_code(chan_analysis),
            structure_basis=reason,
            paths=[
                AIPathScenario(
                    path_id=1,
                    current_state="结构数据不足",
                    description="等待结构数据补齐后重新推演。",
                    next_boundary="等待笔序列、关键价位或买卖点候选补齐。",
                    trigger_condition="等待结构数据补齐。",
                    invalidate_price=None,
                    action="观察，不输出交易建议。",
                    requires_confirmation=True,
                    evidence=[],
                )
            ],
        ),
        primary_path_id=path.id,
        paths=[path],
        defense_line=None,
        observation_line=None,
        wait_for=["等待结构数据补齐后再推演。"],
        invalidation=["数据不足时不做方向性判断。"],
        discipline=f"{reason}，本轮只观察，不输出交易建议。{DISCLAIMER}",
        corrections=[],
        uncertainty=[reason],
        source_versions={
            "chan": chan_analysis.version,
            "prompt": AI_CHAN_REASONING_PROMPT_VERSION,
            "signal_code": _signal_code(chan_analysis),
            "waiting_triggered": True,
            "waiting_reason": reason,
        },
        fallback_reason=reason,
        disclaimer=DISCLAIMER,
    )


def _text(data: dict[str, Any], *keys: str, default: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _text_or_none(data: dict[str, Any], *keys: str) -> str | None:
    """Extract optional text field — returns None if not present."""
    for key in keys:
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _coerce_tactical_guide(raw: Any) -> TacticalGuide | None:
    """Parse tactical_guide from LLM output into TacticalGuide model.

    Handles both exact schema match and common LLM naming variants.
    Returns None if the field is missing or unparseable.
    """
    if not isinstance(raw, dict):
        return None
    try:
        return TacticalGuide(
            current_price=_safe_float(raw.get("current_price") or raw.get("currentPrice")),
            defense_price=_safe_float(raw.get("defense_price") or raw.get("defensePrice")),
            space_to_defense_pct=_safe_float(raw.get("space_to_defense_pct") or raw.get("spaceToDefensePct") or raw.get("space_to_defense")),
            immediate_action=str(raw.get("immediate_action") or raw.get("immediateAction") or "观察不动").strip(),
            test_zone=_safe_price_zone(raw.get("test_zone") or raw.get("testZone")),
            test_basis=_text_or_none(raw, "test_basis", "testBasis"),
            add_zone=_safe_price_zone(raw.get("add_zone") or raw.get("addZone")),
            add_basis=_text_or_none(raw, "add_basis", "addBasis"),
            stop_anchor=_safe_float(raw.get("stop_anchor") or raw.get("stopAnchor")),
            stop_basis=_text_or_none(raw, "stop_basis", "stopBasis"),
            risk_reward_note=_text_or_none(raw, "risk_reward_note", "riskRewardNote"),
        )
    except Exception as exc:
        logger.warning("Failed to parse tactical_guide: %s", exc)
        return None


def _safe_float(value: Any) -> float | None:
    """Convert to float or return None."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_price_zone(value: Any) -> list[float] | None:
    """Parse a [low, high] price zone from LLM output."""
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    try:
        return [float(value[0]), float(value[1])]
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    return [str(value).strip()] if str(value).strip() else []


def _with_disclaimer(value: str) -> str:
    text = str(value or "").strip()
    if "仅供参考" in text or "投资建议" in text:
        return text
    return f"{text} {DISCLAIMER}".strip()


def _confidence_float(value: Any, default: float) -> float:
    try:
        number = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return default
    if number > 1:
        number /= 100
    return max(0.0, min(1.0, number))


def _ai_chan_path_status(value: Any) -> str:
    text = str(value or "").upper()
    if text in {"CURRENT", "CANDIDATE", "INVALIDATED", "UNKNOWN"}:
        return text
    if text in {"WAITING", "PENDING"}:
        return "CANDIDATE"
    if text in {"INVALID", "FAILED"}:
        return "INVALIDATED"
    return "UNKNOWN"


def _line_price(value: Any, chan_analysis: ChanAnalysisResult, role: str) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _next_boundary_from_path(path: AIChanPath) -> str:
    if path.empty_position and path.empty_position.trigger:
        return path.empty_position.trigger
    if path.holding_position and path.holding_position.trigger:
        return path.holding_position.trigger
    return path.entry_condition


def _path_target_price(path: AIChanPath) -> float | None:
    if path.empty_position and path.empty_position.target_price is not None:
        return path.empty_position.target_price
    if path.holding_position and path.holding_position.target_price is not None:
        return path.holding_position.target_price
    return None


def _path_invalidate_price(path: AIChanPath, chan_analysis: ChanAnalysisResult) -> float | None:
    if path.empty_position and path.empty_position.stop_price is not None:
        return path.empty_position.stop_price
    if path.holding_position and path.holding_position.stop_price is not None:
        return path.holding_position.stop_price
    for level in chan_analysis.key_levels:
        if level.role in {"invalidation", "support"}:
            return level.price
    return None


def _path_action(path: AIChanPath) -> str:
    if path.empty_position and path.empty_position.action:
        return path.empty_position.action
    if path.holding_position and path.holding_position.action:
        return path.holding_position.action
    return "等待确认。"


def _trigger_mentions_realtime_anchor(trigger: str, next_boundary: str) -> bool:
    text = f"{trigger} {next_boundary}"
    if any(marker in text for marker in ("现价", "当前", "已跌破", "已突破", "收回", "反弹", "回抽", "下一步")):
        return True
    return any(ch.isdigit() for ch in text)


def _default_trigger(chan_analysis: ChanAnalysisResult) -> str:
    primary = _primary_chan_path(chan_analysis)
    return primary.trigger_condition if primary else "等待结构边界补齐。"


def _default_invalidation(chan_analysis: ChanAnalysisResult) -> str:
    primary = _primary_chan_path(chan_analysis)
    return primary.invalidation_condition if primary else "结构不清晰时不做强推演。"


def _primary_chan_path(chan_analysis: ChanAnalysisResult):
    for path in chan_analysis.complete_paths:
        if path.status == "CURRENT":
            return path
    return chan_analysis.complete_paths[0] if chan_analysis.complete_paths else None
