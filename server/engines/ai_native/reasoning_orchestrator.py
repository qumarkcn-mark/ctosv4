"""Orchestrate the AI Native Radar commander flow."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from server import config
from server.api import radar as radar_api
from server.engines.ai_native.case_memory import find_similar_cases, save_reasoning_run
from server.engines.ai_native.hypothesis_reasoner import infer_ai_hypotheses
from server.engines.ai_native.model_router import choose_model_route
from server.engines.ai_native.schemas import (
    AIReasoningOutput,
    AIReasoningResponse,
    GateResult,
    GateViolation,
    SimilarCaseSummary,
    StructureTranscript,
)
from server.engines.ai_native.transcript_compiler import compile_structure_transcript
from server.engines.ai_native.verifier import verify_ai_reasoning
from server.services.llm_service import LLMService

logger = logging.getLogger(__name__)


async def build_ai_native_reasoning(
    *,
    symbol: str,
    user_id: int,
    mode: Optional[str] = None,
    llm_service: LLMService | None = None,
) -> AIReasoningResponse:
    """Run the AI-native commander reasoning loop.

    Radar contract 是只读输入。任何 AI 失败都降级，不影响确定性结构雷达。
    """
    radar_response = await radar_api.get_radar(symbol, user_id=user_id, include_structure=True)
    radar_contract = radar_response.get("data") or {}
    await _attach_tactical_structure(radar_contract)
    transcript = compile_structure_transcript(radar_contract)
    if mode in {"EMPTY", "HOLDING"} and transcript.mode != mode:
        transcript.mode = mode  # type: ignore[misc]
    model_route = choose_model_route(transcript)
    _apply_user_model_settings(model_route, user_id=user_id)

    memory = find_similar_cases(transcript)
    raw_output: dict | None = None
    output: AIReasoningOutput | None = None
    gate = _fallback_gate("LLM_NOT_CALLED", "AI 推理未运行")

    try:
        raw_output = await infer_ai_hypotheses(
            user_id=user_id,
            transcript=transcript,
            similar_cases=memory,
            llm_service=llm_service,
            model_route=model_route,
        )
        if config.AI_NATIVE_RADAR_GATE_ENABLED:
            output, gate = verify_ai_reasoning(raw_output, transcript)
        else:
            output, gate = _accept_without_gate(raw_output)
    except Exception as exc:
        logger.error("AI native reasoning failed, falling back: %s", exc)
        gate = _fallback_gate("LLM_ERROR", f"AI 推理异常: {str(exc)[:120]}")
        output = None

    if gate.status == "PASS" and output:
        response = _response_from_output(output, transcript, gate, model_route)
    else:
        response = _fallback_response(radar_contract, transcript, gate, model_route)

    run_id = save_reasoning_run(
        user_id=user_id,
        symbol=symbol,
        mode=transcript.mode,
        prompt_version=config.AI_NATIVE_RADAR_PROMPT_VERSION,
        model_name=model_route.model_name or config.AI_NATIVE_RADAR_MODEL,
        transcript=transcript,
        memory_context=memory,
        ai_output=output,
        gate_result=gate,
        model_route=model_route,
    )
    response.run_id = run_id
    _write_snapshot_if_enabled(run_id, transcript, memory, raw_output, gate)
    return response


async def _attach_tactical_structure(radar_contract: dict) -> None:
    """Attach the day/30/5 battlefield used by AI Native free reasoning.

    The legacy Radar UI keeps its full week/day/60/30/15/5 structure. AI Native
    needs the same short-horizon structure that produced the golden cases, so
    we compute it separately and mark it as the primary reasoning source.
    """
    symbol = str(radar_contract.get("symbol") or "")
    if not symbol:
        return
    try:
        tactical_adapter = await radar_api.analyze_structure(
            symbol,
            levels=["day", "30", "5"],
            count=800,
        )
        if not radar_api._adapter_structure_ready(tactical_adapter):
            return
        radar_contract["tactical_structure"] = radar_api._build_structure_from_adapter(tactical_adapter)
        radar_contract["tactical_freshness"] = radar_api._build_freshness_from_adapter(tactical_adapter)
        radar_contract["tactical_structure_config"] = radar_api._structure_config_from_adapter(tactical_adapter)
    except Exception as exc:
        logger.warning("AI Native tactical structure unavailable for %s: %s", symbol, exc)


def _response_from_output(output: AIReasoningOutput, transcript: StructureTranscript, gate: GateResult, model_route) -> AIReasoningResponse:
    return AIReasoningResponse(
        gate_status=gate.status,
        gate_score=gate.score,
        generated_at=transcript.generated_at,
        raw_reasoning_md=output.raw_reasoning_md,
        coach_filtered_md=output.coach_filtered_md,
        semantic_filter_status=output.semantic_filter_status,
        semantic_filter_violations=[
            GateViolation(**item) if isinstance(item, dict) else item
            for item in output.semantic_filter_violations
        ],
        agent_observations=transcript.agent_observations,
        key_boundaries=transcript.reasoning_boundaries,
        position_context=transcript.position_context,
        model_route=model_route,
        coach_talk=output.coach_filtered_md,
        disclaimer=output.disclaimer,
        fallback_reason=_gate_note(gate),
    )


def _fallback_response(radar_contract: dict, transcript: StructureTranscript, gate: GateResult, model_route) -> AIReasoningResponse:
    reason = "; ".join(violation.message for violation in gate.violations) or "AI 输出未通过门禁"
    deduction = radar_contract.get("deduction") or {}
    algorithm = radar_contract.get("algorithm_v2") or {}
    divergence_text = _divergence_boundary_text(transcript)
    structure_gap_text = _structure_gap_text(transcript)
    diagnosis = structure_gap_text or (
        algorithm.get("summary")
        or (deduction.get("path_thesis") or {}).get("title")
        or deduction.get("summary")
        or "沿用规则雷达推演"
    )
    coach_text = (
        "**1. 【全局语境定性】**\n"
        f"{diagnosis}\n\n"
        "**2. 【防守看门狗】**\n"
        "AI 推演本次未展示，先回到结构事实层核对近端边界。\n\n"
        "**3. 【推演与应对沙盘】**\n"
        "当前先停止强推演，等分钟级别新笔、新中枢或近端买卖点补齐后再判断。\n"
        "不要把远离当前价的旧中枢上沿当成眼前回踩位。仅供参考，不构成投资建议。"
        if structure_gap_text
        else (
            "**1. 【全局语境定性】**\n"
            f"{diagnosis}\n\n"
            "**2. 【防守看门狗】**\n"
            f"AI 推演未通过门禁，本次只保留结构边界事实。{divergence_text}\n\n"
            "**3. 【推演与应对沙盘】**\n"
            "本轮不展示自由推演文本，等待下一次结构刷新或模型输出通过语义过滤。"
            "仅供参考，不构成投资建议。"
        )
    )
    return AIReasoningResponse(
        gate_status="FALLBACK",
        gate_score=gate.score,
        generated_at=transcript.generated_at,
        raw_reasoning_md="",
        coach_filtered_md=coach_text,
        semantic_filter_status="FALLBACK",
        semantic_filter_violations=gate.violations,
        agent_observations=transcript.agent_observations,
        key_boundaries=transcript.reasoning_boundaries,
        position_context=transcript.position_context,
        model_route=model_route,
        coach_talk=coach_text,
        fallback_reason=reason,
        fallback_data={
            "source": "radar_contract",
            "diagnosis": diagnosis,
            "deduction": deduction,
            "algorithm_v2": algorithm,
            "divergence_context": transcript.divergence_context.model_dump(),
        },
    )


def _fallback_gate(code: str, message: str) -> GateResult:
    return GateResult(
        status="FALLBACK",
        score=0,
        violations=[
            GateViolation(
                code=code,
                message=message,
                severity="FALLBACK",
            )
        ],
    )


def _accept_without_gate(raw_output: dict | None) -> tuple[AIReasoningOutput | None, GateResult]:
    try:
        output = AIReasoningOutput(**(raw_output or {}))
    except ValidationError as exc:
        return None, GateResult(
            status="FALLBACK",
            score=0,
            violations=[
                GateViolation(
                    code="SCHEMA_INVALID",
                    message="AI 输出不符合前端展示 schema",
                    severity="FALLBACK",
                    evidence=[str(exc)[:500]],
                )
            ],
        )
    return output, GateResult(
        status="PASS",
        score=100,
        violations=[
            GateViolation(
                code="GATE_DISABLED",
                message="AI Native Radar 门禁已关闭，原始 AI 推演直接展示",
                severity="REWRITE",
            )
        ],
    )


def _gate_note(gate: GateResult) -> str | None:
    if gate.status == "PASS":
        return None
    reason = "; ".join(violation.message for violation in gate.violations)
    return f"门禁提示：{reason}" if reason else None


def _rewrite_feedback_text(violation: GateViolation) -> str:
    if violation.evidence:
        return f"{violation.message}：{', '.join(str(item) for item in violation.evidence)}"
    return violation.message


def _divergence_boundary_text(transcript: StructureTranscript) -> str:
    context = transcript.divergence_context
    if context.alignment == "NO_DIVERGENCE":
        return "背驰状态：当前没有明确多级别背驰联动，不把下跌放缓直接当成止跌。"
    if context.alignment == "LOW_LEVEL_ONLY":
        return f"背驰状态：只有小级别背驰线索，尚未和 {context.pivot_level} 分钟中枢完成联动；{context.upgrade_condition}"
    if context.alignment == "ALIGNING":
        return f"背驰状态：小级别背驰正在靠近 {context.pivot_level} 分钟关键边界；{context.upgrade_condition}"
    if context.alignment == "CONFIRMED_SUPPORT":
        return f"背驰状态：背驰线索已回到 {context.pivot_level} 分钟中枢边界内，支撑验证增强；{context.failure_condition}"
    if context.alignment == "FAILED_DIVERGENCE":
        return f"背驰状态：背驰线索失效；{context.failure_condition}"
    if context.alignment == "COUNTER_TREND_RISK":
        return f"背驰状态：压力区出现反向风险；{context.failure_condition}"
    return "背驰状态：等待更清晰的级别联动。"


def _structure_gap_text(transcript: StructureTranscript) -> str:
    operative = (transcript.reasoning_evidence_pack or {}).get("operative_context") or {}
    if not (operative.get("structure_gap") or operative.get("current_zone") == "price_structure_gap"):
        return ""
    current_price = operative.get("current_price")
    nearest = operative.get("nearest_known_level") or {}
    nearest_price = nearest.get("price")
    distance = nearest.get("distance_abs_pct")
    if current_price and nearest_price and distance is not None:
        return f"当前价 {current_price} 已明显脱离最近可用结构价 {nearest_price}（约 {distance}%），近端分钟结构缺失，停止强推演。"
    return "当前价已明显脱离最近可用分钟结构，近端结构缺失，停止强推演。"


def _write_snapshot_if_enabled(
    run_id: int | None,
    transcript: StructureTranscript,
    memory: SimilarCaseSummary,
    raw_output: dict | None,
    gate: GateResult,
) -> None:
    if not config.AI_NATIVE_RADAR_WRITE_SNAPSHOTS:
        return
    try:
        root = Path(config.AI_NATIVE_RADAR_DATA_DIR)
        date_part = transcript.generated_at[:10]
        target_dir = root / "runs" / date_part
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{transcript.symbol or 'unknown'}_{run_id or 'unsaved'}.json"
        payload = {
            "transcript": transcript.model_dump(),
            "memory_context": memory.model_dump(),
            "ai_output": raw_output,
            "gate_result": gate.model_dump(),
        }
        (target_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("AI native snapshot write failed: %s", exc)


def _apply_user_model_settings(model_route, *, user_id: int) -> None:
    """Apply user-configured hard-model settings to the selected route."""
    try:
        from server.db.database import get_connection

        conn = get_connection()
        try:
            row = conn.execute("SELECT settings_json FROM users WHERE id = ?", (user_id,)).fetchone()
        finally:
            conn.close()
        if not row or not row["settings_json"]:
            return
        settings = json.loads(row["settings_json"])
    except Exception:
        logger.debug("AI native model route settings lookup failed", exc_info=True)
        return

    provider = str(settings.get("ai_native_radar_provider") or "deepseek").lower()
    if provider == "gemini":
        gemini_model = str(settings.get("gemini_model") or config.GEMINI_MODEL or "gemini-2.5-pro").strip()
        model_route.model_name = gemini_model
        model_route.thinking_enabled = False
        model_route.reasoning_effort = "high"
        if "gemini" not in model_route.reasons:
            model_route.reasons.append("gemini")
        return

    if model_route.tier != "simple" and settings.get("ai_native_radar_model"):
        model_route.model_name = str(settings["ai_native_radar_model"])
    if model_route.tier != "simple" and isinstance(settings.get("ai_native_radar_thinking_enabled"), bool):
        model_route.thinking_enabled = settings["ai_native_radar_thinking_enabled"]
    effort = settings.get("ai_native_radar_reasoning_effort")
    if model_route.tier != "simple" and effort in {"high", "max"}:
        model_route.reasoning_effort = effort
