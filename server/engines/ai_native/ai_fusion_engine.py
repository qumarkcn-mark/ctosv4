"""V4.5 AI Fusion orchestration."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from server import config
from server.engines.ai_native.fusion_schemas import (
    ActionBias,
    AIFusionInference,
    AIChanInference,
    AIChanPath,
    ChanAnalysisResult,
    ConflictCandidate,
    DataAlignmentSnapshot,
    FusionActionPlaybook,
    FusionPathInference,
    KronosForecastResult,
    MarketRawFacts,
)
from server.engines.ai_native.market_time import latest_closed_data_slice
from server.engines.ai_native.schemas import DISCLAIMER, ModelRoute, PositionContext
from server.prompts.ai_native_fusion_prompt import AI_NATIVE_FUSION_PROMPT
from server.services.llm_service import LLMService


logger = logging.getLogger(__name__)
SHANGHAI_TZ = timezone(timedelta(hours=8))


async def build_ai_fusion_inference(
    *,
    chan_analysis: ChanAnalysisResult,
    kronos_forecast: KronosForecastResult,
    position_context: PositionContext | None = None,

    ai_chan_inference: AIChanInference | None = None,
    first_stage_reasoning: dict[str, Any] | None = None,
    user_id: int = 1,
    llm_service: LLMService | None = None,
    model_route: ModelRoute | None = None,
) -> AIFusionInference:
    """Run single-pass Fusion reasoning.

    V4.5 Fusion does not apply a deterministic trading gate. Chan structure
    and AI Chan reasoning are inputs to AI Fusion; the AI output is preserved as the unified
    inference as long as it satisfies the response schema.
    """
    service = llm_service or LLMService()
    first_stage_generated_at = str((first_stage_reasoning or {}).get("generated_at") or "")
    data_alignment = build_data_alignment_snapshot(
        chan_analysis,
        kronos_forecast,
        ai_chan_inference,
        first_stage_generated_at=first_stage_generated_at,
    )
    context = _fusion_context(
        chan_analysis,
        kronos_forecast,
        position_context,

        ai_chan_inference,
        first_stage_reasoning=first_stage_reasoning,
        data_alignment=data_alignment,
    )
    context_json = json.dumps(context, ensure_ascii=False, indent=2)
    effective_route = model_route or _default_fusion_model_route()
    diagnostics_base = {
        "prompt_chars": len(context_json),
        "llm_timeout_seconds": effective_route.timeout_seconds,
        "model_name": effective_route.model_name,
        "thinking_enabled": effective_route.thinking_enabled,
        "reasoning_effort": effective_route.reasoning_effort,

        "has_ai_chan_inference": bool(ai_chan_inference),
        "has_first_stage_reasoning": bool(first_stage_reasoning),
        "data_alignment": data_alignment.model_dump(),
    }

    try:
        llm_started = time.perf_counter()
        raw_output = await asyncio.wait_for(
            service.infer_ai_native_radar(
                AI_NATIVE_FUSION_PROMPT,
                context_json,
                user_id=user_id,
                model_route=effective_route,
            ),
            timeout=effective_route.timeout_seconds,
        )
        output = _coerce_fusion_output(
            raw_output,
            chan_analysis=chan_analysis,
            ai_chan_inference=ai_chan_inference,
            kronos_forecast=kronos_forecast,
            position_context=position_context,

        )
        output.diagnostics = {
            **diagnostics_base,
            "llm_ms": _elapsed_ms(llm_started),
            "fallback_triggered": False,
        }
        return output
    except asyncio.TimeoutError:
        logger.error(
            "AI Fusion inference timed out after %.1fs: symbol=%s",
            effective_route.timeout_seconds,
            chan_analysis.symbol,
        )
        raise RuntimeError(f"AI 服务忙，请稍后重试：AI Fusion 推演超时 {effective_route.timeout_seconds:.0f}s") from None
    except Exception as exc:
        logger.error("AI Fusion inference failed: %s", exc)
        raise RuntimeError(f"AI 服务忙，请稍后重试：AI Fusion 推演异常 {str(exc)[:120]}") from exc


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.perf_counter() - started_at) * 1000))


def _default_fusion_model_route() -> ModelRoute:
    """Fusion has its own response SLA because it is consumed by UI and CLI."""
    effort = config.AI_NATIVE_RADAR_REASONING_EFFORT
    reasoning_effort = "max" if effort == "max" else "high"
    return ModelRoute(
        tier="simple",
        difficulty_score=0,
        model_name=config.AI_NATIVE_FUSION_MODEL,
        thinking_enabled=config.AI_NATIVE_FUSION_THINKING_ENABLED,
        reasoning_effort=reasoning_effort,
        max_tokens=config.AI_NATIVE_FUSION_MAX_TOKENS,
        timeout_seconds=config.AI_NATIVE_FUSION_LLM_TIMEOUT,
        reasons=["Fusion 单票推演使用独立超时，保证 UI/CLI 可降级返回。"],
    )


def _fusion_context(
    chan_analysis: ChanAnalysisResult,
    kronos_forecast: KronosForecastResult,
    position_context: PositionContext | None,

    ai_chan_inference: AIChanInference | None = None,
    first_stage_reasoning: dict[str, Any] | None = None,
    data_alignment: DataAlignmentSnapshot | None = None,
) -> dict:
    return {
        "version": "fusion_input.v45",
        "raw_facts": _raw_facts(chan_analysis).model_dump(),
        "chan_structure": _compact_chan_structure(chan_analysis),
        "first_stage_reasoning": _compact_first_stage_reasoning(first_stage_reasoning),
        "ai_chan_inference": ai_chan_inference.model_dump() if ai_chan_inference else None,
        "data_alignment": data_alignment.model_dump() if data_alignment else build_data_alignment_snapshot(
            chan_analysis,
            kronos_forecast,
            ai_chan_inference,
            first_stage_generated_at=str((first_stage_reasoning or {}).get("generated_at") or ""),
        ).model_dump(),
        "conflict_candidates": [item.model_dump() for item in _conflict_candidates(chan_analysis, kronos_forecast)],
        "position_context": position_context.model_dump() if position_context else None,
        "fusion_rules": {
            "principle": "AI Chan 给结构推演和纪律边界；Signal V2 只给确定性时间/价格参考；AI Fusion 统一推演概率和行动偏向。",
            "responsibilities": {
                "structure_facts": "ChanAnalysisResult",
                "structure_location": "first_stage_reasoning 优先，其次 AI Chan",
                "complete_classification": "first_stage_reasoning 优先，其次 AI Chan",
                "invalidation_and_defense": "first_stage_reasoning/AI Chan 的剧本条件",
                "timing_price_reference": "signals_v2.context.kronos_timeline / kronos_envelope 如存在，仅作时间和价格参考",
                "path_probability_and_final_judgement": "AI Fusion",
            },
            "conflict_policy": [
                "冲突候选只作为提示，不是裁决。",
                "Signal V2 的 Kronos 时间线或信封只用于描述观察窗口和价格区间，不用于路径概率裁决。",
                "路径概率必须来自 first_stage_reasoning/AI Chan 的结构剧本、data_alignment 和持仓上下文。",
                "结构未触发时，即使时间或价格参考接近，也必须降级为等待确认。",
                "所有动作都必须给出失效条件。",
            ],
            "disclaimer": DISCLAIMER,
        },
    }


def _compact_first_stage_reasoning(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or not payload:
        return None
    text = str(payload.get("coach_filtered_md") or payload.get("raw_reasoning_md") or "").strip()
    if not text:
        return None
    compact_text = _compact_reasoning_text(text)
    return {
        "source": "ai_native_radar",
        "run_id": payload.get("run_id"),
        "symbol": payload.get("symbol"),
        "mode": payload.get("mode"),
        "generated_at": payload.get("generated_at"),
        "gate_status": payload.get("gate_status"),
        "current_position_and_scripts_md": compact_text,
        "usage_instruction": "这是用户第一步点击 AI 推演得到的当前定位 + 完全分类。Fusion 必须优先消费它，再结合对齐状态和持仓上下文。",
    }


def _compact_reasoning_text(text: str, *, max_chars: int = 4200) -> str:
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    sections = []
    for title in ("当前定位", "完全分类", "三种剧本", "实战指引", "纪律", "不确定"):
        section = _extract_markdown_section(normalized, title)
        if section:
            sections.append(f"### 【{title}】\n{section}")
    compact = "\n\n".join(sections) if sections else normalized
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars].rstrip() + "\n...（已截断，只保留结构推演摘要）"


def _extract_markdown_section(text: str, title: str) -> str:
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        stripped = line.strip().strip("*")
        if f"【{title}】" in stripped:
            start = idx + 1
            break
    if start is None:
        return ""
    end = len(lines)
    for idx in range(start, len(lines)):
        stripped = lines[idx].strip()
        if idx > start and stripped.startswith("#") and "【" in stripped and "】" in stripped:
            end = idx
            break
    return "\n".join(lines[start:end]).strip()


def build_data_alignment_snapshot(
    chan_analysis: ChanAnalysisResult,
    kronos_forecast: KronosForecastResult,
    ai_chan_inference: AIChanInference | None = None,
    first_stage_generated_at: str = "",
) -> DataAlignmentSnapshot:
    chan_time = chan_analysis.generated_at or ""
    ai_chan_time = first_stage_generated_at or (ai_chan_inference.generated_at if ai_chan_inference else "")
    kronos_time = _kronos_primary_data_time(kronos_forecast)
    comparison_time = ai_chan_time or chan_time
    source_dt = _parse_time(comparison_time)
    chan_dt = _analysis_data_time(source_dt) if source_dt else None
    kronos_dt = _parse_time(kronos_time)
    delta_minutes = None
    status = "UNKNOWN"
    note = "缺少可比对时间戳，Fusion 只能标记来源时间。"
    if chan_dt and kronos_dt:
        delta_minutes = round(abs((chan_dt - kronos_dt).total_seconds()) / 60, 2)
        if delta_minutes <= 35:
            status = "ALIGNED"
            if source_dt and source_dt != chan_dt:
                note = "AI 推演生成时间已按 A 股交易时段映射到最近闭合数据切片，可与 Kronos 同段融合。"
            else:
                note = "Chan 与 Kronos 时间戳在一个 30 分钟切片附近，可作为同段数据融合。"
        elif kronos_dt < chan_dt:
            status = "STALE_KRONOS"
            note = "Kronos 数据早于缠论结构切片，Fusion 需要降低 Kronos 权重。"
        else:
            status = "STALE_CHAN"
            note = "缠论结构切片早于 Kronos 数据，Fusion 需要等待结构刷新确认。"
    return DataAlignmentSnapshot(
        status=status,  # type: ignore[arg-type]
        chan_generated_at=chan_time,
        ai_chan_generated_at=ai_chan_time,
        analysis_data_time=_format_local_time(chan_dt),
        kronos_generated_at=kronos_forecast.generated_at or "",
        primary_data_time=kronos_time,
        max_delta_minutes=delta_minutes,
        note=note,
    )


def _kronos_primary_data_time(kronos_forecast: KronosForecastResult) -> str:
    if kronos_forecast.generated_at:
        return kronos_forecast.generated_at
    for point in kronos_forecast.forecast_mean:
        if point.timestamp:
            return point.timestamp
    return ""


def _parse_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                dt = None
        if dt is None:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=SHANGHAI_TZ).astimezone(timezone.utc)
    return dt.astimezone(timezone.utc)


def _analysis_data_time(value: datetime) -> datetime:
    return latest_closed_data_slice(value).astimezone(timezone.utc)


def _format_local_time(value: datetime | None) -> str:
    if not value:
        return ""
    return value.astimezone(SHANGHAI_TZ).isoformat(timespec="seconds")


def _compact_chan_structure(chan_analysis: ChanAnalysisResult) -> dict[str, Any]:
    """Keep Fusion focused once AI Chan has already completed structure reasoning."""
    return {
        "version": chan_analysis.version,
        "symbol": chan_analysis.symbol,
        "generated_at": chan_analysis.generated_at,
        "primary_level": chan_analysis.primary_level,
        "current_position": chan_analysis.current_position,
        "structure_state": chan_analysis.structure_state,
        "trend_context": chan_analysis.trend_context,
        "center_state": chan_analysis.center_state,
        "buy_sell_candidates": chan_analysis.buy_sell_candidates[:3],
        "complete_paths": [
            {
                "id": path.id,
                "name": path.name,
                "level": path.level,
                "status": path.status,
                "structure_logic": path.structure_logic,
                "trigger_condition": path.trigger_condition,
                "invalidation_condition": path.invalidation_condition,
                "evidence": path.evidence[:4],
            }
            for path in chan_analysis.complete_paths[:5]
        ],
        "key_levels": [
            {
                "label": level.label,
                "price": level.price,
                "level": level.level,
                "role": level.role,
                "source": level.source,
            }
            for level in chan_analysis.key_levels[:12]
        ],
        "signal_context": _compact_signal_context(chan_analysis.signal_v2),
        "discipline_rules": chan_analysis.discipline_rules,
        "warnings": chan_analysis.warnings,
        "disclaimer": chan_analysis.disclaimer,
    }


def _compact_signal_context(signal_v2: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(signal_v2, dict) or not signal_v2:
        return {}
    primary = signal_v2.get("primary") if isinstance(signal_v2.get("primary"), dict) else {}
    context = signal_v2.get("context") if isinstance(signal_v2.get("context"), dict) else {}
    result = {
        "primary_code": primary.get("code"),
        "primary_action": primary.get("action"),
        "kronos_timeline": _compact_kronos_timeline(context.get("kronos_timeline")),
        "kronos_envelope": _compact_kronos_envelope(context.get("kronos_envelope")),
    }
    return {key: value for key, value in result.items() if value}


def _compact_kronos_timeline(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    fenxing = value.get("predicted_fenxing") if isinstance(value.get("predicted_fenxing"), dict) else {}
    result = {
        "level": value.get("level"),
        "estimated_confirmation_bars": value.get("estimated_confirmation_bars"),
        "estimated_confirmation_date": value.get("estimated_confirmation_date"),
        "predicted_fenxing": {
            "type": fenxing.get("type"),
            "step": fenxing.get("step"),
            "price": fenxing.get("price"),
        } if fenxing else None,
        "predicted_trend_summary": value.get("predicted_trend_summary"),
    }
    return {key: item for key, item in result.items() if item}


def _compact_kronos_envelope(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = {
        "target_day": value.get("target_day"),
        "envelope_high": value.get("envelope_high"),
        "envelope_low": value.get("envelope_low"),
        "bar_direction": value.get("bar_direction"),
        "ai_buy_point": value.get("ai_buy_point"),
        "validation": value.get("validation"),
        "parent_level": value.get("parent_level"),
        "child_level": value.get("child_level"),
        "alignment": value.get("alignment"),
    }
    return {key: item for key, item in result.items() if item not in (None, "", [])}


def _raw_facts(chan_analysis: ChanAnalysisResult) -> MarketRawFacts:
    prices = [level.price for level in chan_analysis.key_levels if level.price > 0]
    current_price = prices[0] if prices else None
    high = max(prices) if prices else None
    low = min(prices) if prices else None
    return MarketRawFacts(
        symbol=chan_analysis.symbol,
        generated_at=chan_analysis.generated_at,
        current_price=current_price,
        recent_change_pct=None,
        volume_status="UNKNOWN",
        distance_to_high=round(high - current_price, 4) if high is not None and current_price is not None else None,
        distance_to_low=round(current_price - low, 4) if low is not None and current_price is not None else None,
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
        source="chan_structure_snapshot",
    )


def _conflict_candidates(
    chan_analysis: ChanAnalysisResult,
    kronos_forecast: KronosForecastResult,
) -> list[ConflictCandidate]:
    if "kronos_unavailable" in kronos_forecast.warnings:
        return [
            ConflictCandidate(
                code="KRONOS_UNAVAILABLE",
                status="NEEDS_AI_JUDGEMENT",
                chan_fact=chan_analysis.current_position,
                kronos_fact="Kronos evidence unavailable",
                fusion_instruction="不要强行预测，只按缠论结构边界给观察和防守。",
            )
        ]

    return [
        ConflictCandidate(
            code="KRONOS_RAW_INPUT_WITHHELD",
            status="CONSISTENT",
            chan_fact=chan_analysis.current_position,
            kronos_fact="Kronos 原始预测不进入 Fusion LLM；仅保留 API 诊断展示和 Signal V2 确定性时间/价格参考。",
            fusion_instruction="按 first_stage_reasoning/AI Chan、data_alignment、持仓上下文生成路径概率和动作边界。",
        )
    ]


def _coerce_fusion_output(
    raw_output: dict[str, Any],
    *,
    chan_analysis: ChanAnalysisResult,
    ai_chan_inference: AIChanInference | None,
    kronos_forecast: KronosForecastResult,
    position_context: PositionContext | None,
) -> AIFusionInference:
    """Accept AI Fusion output with a wide schema and fill display defaults."""
    if not isinstance(raw_output, dict):
        raise ValueError("AI Fusion output is not a JSON object")

    paths = _coerce_paths(raw_output.get("path_inferences") or raw_output.get("paths"), chan_analysis, ai_chan_inference)
    primary_path_id = str(raw_output.get("primary_path_id") or raw_output.get("primaryPathId") or "")
    if not primary_path_id and paths:
        primary_path_id = paths[0].id

    payload = {
        "version": str(raw_output.get("version") or "ai_fusion_inference.v45"),
        "symbol": str(raw_output.get("symbol") or chan_analysis.symbol),
        "generated_at": str(raw_output.get("generated_at") or raw_output.get("generatedAt") or chan_analysis.generated_at or kronos_forecast.generated_at),
        "current_judgement": _text(
            raw_output,
            "current_judgement",
            "currentJudgement",
            "judgement",
            "current_analysis",
            default="AI Fusion 已生成推演，按缠论结构、对齐状态和持仓上下文共同观察。",
        ),
        "primary_path_id": primary_path_id,
        "path_inferences": [path.model_dump() for path in paths],
        "coach_message": _with_disclaimer(
            _text(raw_output, "coach_message", "coachMessage", "summary", "advice", default="按结构边界观察，等待确认后再行动。")
        ),
        "defense_line": _text(raw_output, "defense_line", "defenseLine", "risk_line", default=_default_invalidation(chan_analysis)),
        "wait_for": _string_list(raw_output.get("wait_for") or raw_output.get("waitFor") or [_default_trigger(chan_analysis)]),
        "invalidation": _string_list(raw_output.get("invalidation") or raw_output.get("invalidations") or [_default_invalidation(chan_analysis)]),
        "action_playbook": _coerce_action_playbook(raw_output, paths, chan_analysis).model_dump(),
        "position_sizing_note": _with_disclaimer(
            _text(
                raw_output,
                "position_sizing_note",
                "positionSizingNote",
                default="不输出自动交易指令；仓位仅按风险暴露做教练式建议。",
            )
        ),
        "position_context": position_context,
        "source_versions": _source_versions(raw_output, chan_analysis, kronos_forecast),
        "disclaimer": _with_disclaimer(str(raw_output.get("disclaimer") or "")),
    }
    return AIFusionInference(**payload)


def _coerce_action_playbook(
    raw_output: dict[str, Any],
    paths: list[FusionPathInference],
    chan_analysis: ChanAnalysisResult,

) -> FusionActionPlaybook:
    raw = raw_output.get("action_playbook") or raw_output.get("actionPlaybook") or {}
    if not isinstance(raw, dict):
        raw = {}

    action = _fusion_action(
        raw.get("action")
        or raw_output.get("action")
        or raw_output.get("recommended_action")
        or (paths[0].action_bias if paths else None)
    )
    wait_for = _string_list(raw_output.get("wait_for") or raw_output.get("waitFor") or [_default_trigger(chan_analysis)])
    invalidation = _string_list(raw_output.get("invalidation") or raw_output.get("invalidations") or [_default_invalidation(chan_analysis)])

    conditions = _normalize_playbook_conditions(
        action,
        test_conditions=_string_list(raw.get("test_conditions") or raw.get("testConditions") or (wait_for if action == "TEST" else [])),
        add_conditions=_string_list(raw.get("add_conditions") or raw.get("addConditions") or (wait_for if action == "ADD" else [])),
        reduce_conditions=_string_list(raw.get("reduce_conditions") or raw.get("reduceConditions") or (wait_for if action == "REDUCE" else [])),
        exit_conditions=_string_list(raw.get("exit_conditions") or raw.get("exitConditions") or (wait_for if action == "EXIT" else [])),
        hold_conditions=_string_list(raw.get("hold_conditions") or raw.get("holdConditions") or (wait_for if action == "HOLD" else [])),
    )

    return FusionActionPlaybook(
        action=action,
        action_label=_text(raw, "action_label", "actionLabel", default=_action_label(action)),
        primary_reason=_with_disclaimer(
            _text(
                raw,
                "primary_reason",
                "primaryReason",
                "reason",
                default="当前动作来自缠论结构、对齐状态和持仓上下文的 AI Fusion 统一推演。",
            )
        ),
        test_conditions=conditions["test"],
        add_conditions=conditions["add"],
        reduce_conditions=conditions["reduce"],
        exit_conditions=conditions["exit"],
        hold_conditions=conditions["hold"],
        max_position_weight_pct=_optional_float(raw.get("max_position_weight_pct") or raw.get("maxPositionWeightPct")),
        recheck_trigger=_recheck_trigger(
            raw.get("recheck_trigger") or raw.get("recheckTrigger"),
        ),
        risk_note=_with_disclaimer(
            _text(raw, "risk_note", "riskNote", default="动作只按条件触发，不构成自动交易指令。")
        ),
    )


def _normalize_playbook_conditions(
    action: str,
    *,
    test_conditions: list[str],
    add_conditions: list[str],
    reduce_conditions: list[str],
    exit_conditions: list[str],
    hold_conditions: list[str],
) -> dict[str, list[str]]:
    grouped = {
        "test": [],
        "add": [],
        "reduce": [],
        "exit": [],
        "hold": [],
    }
    for default_key, items in (
        ("test", test_conditions),
        ("add", add_conditions),
        ("reduce", reduce_conditions),
        ("exit", exit_conditions),
        ("hold", hold_conditions),
    ):
        for item in items:
            text = str(item or "").strip()
            if not text:
                continue
            grouped[_condition_group_for_text(text, default_key)].append(text)

    if action == "REDUCE" and grouped["test"] and not grouped["reduce"]:
        grouped["reduce"].extend(grouped["test"])
        grouped["test"] = []
    return {key: _dedupe_strings(value) for key, value in grouped.items()}


def _condition_group_for_text(text: str, default_key: str) -> str:
    if any(word in text for word in ("清仓", "离场", "退出", "止损出局")):
        return "exit"
    if "跌破" in text and any(word in text for word in ("无快速收回", "结构失效", "清仓", "离场")):
        return "exit"
    if any(word in text for word in ("减仓", "降低风险", "降低仓位", "风险暴露", "保护利润")):
        return "reduce"
    if any(word in text for word in ("加仓", "再加", "放量突破", "回踩确认")):
        return "add"
    if any(word in text for word in ("持有", "暂时持有", "继续持有")):
        return "hold"
    if default_key == "test" and any(word in text for word in ("试仓", "小仓", "轻仓", "计划入场")):
        return "test"
    return default_key


def _dedupe_strings(items: list[str]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _source_versions(
    raw_output: dict[str, Any],
    chan_analysis: ChanAnalysisResult,
    kronos_forecast: KronosForecastResult,
) -> dict:
    value = raw_output.get("source_versions") or raw_output.get("sourceVersions")
    if isinstance(value, dict):
        return value
    return {
            "chan": chan_analysis.version,
            "kronos": kronos_forecast.version,
            "prompt": "ai_native_fusion.v45",
    }


def _coerce_paths(
    raw_paths: Any,
    chan_analysis: ChanAnalysisResult,
    ai_chan_inference: AIChanInference | None = None,
) -> list[FusionPathInference]:
    items = raw_paths if isinstance(raw_paths, list) and raw_paths else [{}]
    paths: list[FusionPathInference] = []
    for index, item in enumerate(items[:4], start=1):
        raw = item if isinstance(item, dict) else {"name": str(item)}
        ai_chan_path = _match_ai_chan_path(raw, ai_chan_inference, index)
        chan_path = _match_chan_path(raw, chan_analysis, index)
        probability = _coerce_probability(raw.get("probability") or raw.get("prob") or raw.get("p"))
        if probability is None:
            probability = _structure_probability(index, ai_chan_path)
        paths.append(
            FusionPathInference(
                id=str(raw.get("id") or f"path-{chan_path.id}"),
                chan_path_id=str(raw.get("chan_path_id") or raw.get("chanPathId") or (ai_chan_path.id if ai_chan_path else chan_path.id)),
                rank=_rank(raw.get("rank"), index),
                name=_text(raw, "name", "title", default=ai_chan_path.name if ai_chan_path else chan_path.name),
                probability=probability,
                confidence=_confidence(raw.get("confidence")),
                chan_basis=_text(raw, "chan_basis", "chanBasis", default=ai_chan_path.chan_basis if ai_chan_path else chan_path.structure_logic),
                kronos_basis=_text(raw, "kronos_basis", "kronosBasis", default=_kronos_basis()),
                action_bias=_action_bias(raw.get("action_bias") or raw.get("actionBias")),
                wait_condition=_text(raw, "wait_condition", "waitCondition", default=ai_chan_path.entry_condition if ai_chan_path else chan_path.trigger_condition),
                trigger_condition=_text(raw, "trigger_condition", "triggerCondition", default=ai_chan_path.entry_condition if ai_chan_path else chan_path.trigger_condition),
                invalidation_condition=_text(raw, "invalidation_condition", "invalidationCondition", default=ai_chan_path.invalidation if ai_chan_path else chan_path.invalidation_condition),
                position_discipline=_text(raw, "position_discipline", "positionDiscipline", default=(ai_chan_inference.discipline if ai_chan_inference else "按结构边界控制风险暴露，不做自动交易。")),
                risk_note=_with_disclaimer(_text(raw, "risk_note", "riskNote", default="按结构边界观察。")),
            )
        )
    return paths


def _match_ai_chan_path(raw: dict[str, Any], ai_chan_inference: AIChanInference | None, index: int):
    if not ai_chan_inference or not ai_chan_inference.paths:
        return None
    chan_id = str(raw.get("chan_path_id") or raw.get("chanPathId") or raw.get("id") or "").strip()
    for path in ai_chan_inference.paths:
        if path.id == chan_id:
            return path
    if 0 < index <= len(ai_chan_inference.paths):
        return ai_chan_inference.paths[index - 1]
    return ai_chan_inference.paths[0]


def _match_chan_path(raw: dict[str, Any], chan_analysis: ChanAnalysisResult, index: int):
    chan_id = str(raw.get("chan_path_id") or raw.get("chanPathId") or "").strip()
    for path in chan_analysis.complete_paths:
        if path.id == chan_id:
            return path
    if 0 < index <= len(chan_analysis.complete_paths):
        return chan_analysis.complete_paths[index - 1]
    primary = _primary_chan_path(chan_analysis)
    if primary:
        return primary
    return type("FallbackPath", (), {
        "id": "UNKNOWN",
        "name": "结构观察路径",
        "structure_logic": chan_analysis.current_position,
        "trigger_condition": "等待结构边界补齐。",
        "invalidation_condition": "结构不清晰时不做强推演。",
    })()


def _text(data: dict[str, Any], *keys: str, default: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


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
    return f"{text} 仅供参考，不构成投资建议。".strip()


def _coerce_probability(value: Any) -> float | None:
    try:
        probability = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    if probability > 1:
        probability /= 100
    return max(0.0, min(1.0, probability))


def _rank(value: Any, default: int) -> int:
    try:
        rank = int(value)
    except (TypeError, ValueError):
        rank = default
    return max(1, rank)


def _optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _latest_forecast_price(kronos_forecast: KronosForecastResult) -> float | None:
    if not kronos_forecast.forecast_mean:
        return None
    first = kronos_forecast.forecast_mean[0]
    return first.open or first.close or first.high or first.low


def _volume_status(kronos_forecast: KronosForecastResult) -> str:
    volumes = [point.volume for point in kronos_forecast.forecast_mean if point.volume is not None]
    if len(volumes) < 2:
        return "UNKNOWN"
    if volumes[-1] > volumes[0] * 1.1:
        return "expanding"
    if volumes[-1] < volumes[0] * 0.9:
        return "contracting"
    return "flat"


def _confidence(value: Any) -> str:
    text = str(value or "").upper()
    if text in {"LOW", "MEDIUM", "HIGH"}:
        return text
    if "高" in text:
        return "HIGH"
    if "中" in text:
        return "MEDIUM"
    return "LOW"


def _action_bias(value: Any) -> ActionBias:
    text = str(value or "").upper()
    if text in {"WAIT", "OBSERVE", "DEFEND", "REDUCE_RISK", "PLAN_ENTRY", "PLAN_EXIT"}:
        return text  # type: ignore[return-value]
    if "减" in text or "风险" in text:
        return "REDUCE_RISK"
    if "防" in text or "守" in text:
        return "DEFEND"
    if "等" in text:
        return "WAIT"
    return "OBSERVE"


def _fusion_action(value: Any) -> str:
    text = str(value or "").upper()
    direct = {"EXIT", "REDUCE", "HOLD", "OBSERVE", "TEST", "ADD", "NO_ACTION"}
    if text in direct:
        return text
    if text == "PLAN_EXIT":
        return "EXIT"
    if text == "REDUCE_RISK":
        return "REDUCE"
    if text == "PLAN_ENTRY":
        return "TEST"
    if text == "DEFEND":
        return "HOLD"
    if text == "WAIT":
        return "OBSERVE"
    if "清" in text or "退" in text or "离" in text:
        return "EXIT"
    if "减" in text or "降" in text:
        return "REDUCE"
    if "持" in text or "守" in text:
        return "HOLD"
    if "试" in text:
        return "TEST"
    if "加" in text:
        return "ADD"
    return "OBSERVE"


def _action_label(action: str) -> str:
    return {
        "EXIT": "退出或降到极小观察仓",
        "REDUCE": "降低风险暴露",
        "HOLD": "持有但守防线",
        "OBSERVE": "观察等待确认",
        "TEST": "满足条件后试仓",
        "ADD": "确认后再加仓",
        "NO_ACTION": "无动作",
    }.get(action, "观察等待确认")


def _recheck_trigger(value: Any) -> str:
    text = str(value or "").upper()
    if text in {"NEXT_5M_CLOSE", "NEXT_30M_CLOSE", "NEXT_DAILY_CLOSE", "PRICE_TOUCH", "MANUAL_REFRESH"}:
        return text
    return "NEXT_30M_CLOSE"


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


def _structure_probability(index: int, ai_chan_path: AIChanPath | None) -> float:
    if ai_chan_path and ai_chan_path.confidence is not None:
        try:
            return max(0.0, min(1.0, float(ai_chan_path.confidence)))
        except (TypeError, ValueError):
            pass
    return max(0.1, round(0.55 - (index - 1) * 0.15, 2))


def _kronos_basis() -> str:
    return "Fusion 未消费 Kronos 原始预测；本条按结构推演和可选时间/价格参考生成。"
