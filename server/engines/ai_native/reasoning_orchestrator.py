"""Orchestrate the isolated AI Native Radar shadow flow."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from server import config
from server.api import radar as radar_api
from server.engines.ai_native.case_memory import find_similar_cases, save_reasoning_run
from server.engines.ai_native.hypothesis_reasoner import infer_ai_hypotheses
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
    """Run the shadow AI-native reasoning loop.

    老 Radar contract 是只读输入。任何 AI 失败都降级，不影响老 Radar。
    """
    radar_response = await radar_api.get_radar(symbol, user_id=user_id)
    radar_contract = radar_response.get("data") or {}
    transcript = compile_structure_transcript(radar_contract)
    if mode in {"EMPTY", "HOLDING"} and transcript.mode != mode:
        transcript.mode = mode  # type: ignore[misc]

    memory = find_similar_cases(transcript)
    raw_output: dict | None = None
    output: AIReasoningOutput | None = None
    gate = _fallback_gate("LLM_NOT_CALLED", "AI 推理未运行")

    try:
        raw_output = await infer_ai_hypotheses(
            transcript=transcript,
            similar_cases=memory,
            llm_service=llm_service,
        )
        output, gate = verify_ai_reasoning(raw_output, transcript)
        if gate.status == "REWRITE" and config.AI_NATIVE_RADAR_MAX_REWRITE > 0:
            feedback = [violation.message for violation in gate.violations]
            raw_output = await infer_ai_hypotheses(
                transcript=transcript,
                similar_cases=memory,
                rewrite_feedback=feedback,
                llm_service=llm_service,
            )
            output, gate = verify_ai_reasoning(raw_output, transcript)
    except Exception as exc:
        logger.error("AI native reasoning failed, falling back: %s", exc)
        gate = _fallback_gate("LLM_ERROR", f"AI 推理异常: {str(exc)[:120]}")
        output = None

    if gate.status == "PASS" and output:
        response = _response_from_output(output, gate)
    else:
        response = _fallback_response(radar_contract, gate)

    run_id = save_reasoning_run(
        user_id=user_id,
        symbol=symbol,
        mode=transcript.mode,
        prompt_version=config.AI_NATIVE_RADAR_PROMPT_VERSION,
        model_name=config.AI_NATIVE_RADAR_MODEL,
        transcript=transcript,
        memory_context=memory,
        ai_output=output,
        gate_result=gate,
    )
    response.run_id = run_id
    _write_snapshot_if_enabled(run_id, transcript, memory, raw_output, gate)
    return response


def _response_from_output(output: AIReasoningOutput, gate: GateResult) -> AIReasoningResponse:
    return AIReasoningResponse(
        gate_status="PASS",
        gate_score=gate.score,
        diagnosis=output.diagnosis,
        current_hypothesis=output.current_hypothesis,
        reasoning_boundary=output.reasoning_boundary,
        hypotheses=output.hypotheses,
        coach_talk=output.coach_talk,
        disclaimer=output.disclaimer,
    )


def _fallback_response(radar_contract: dict, gate: GateResult) -> AIReasoningResponse:
    reason = "; ".join(violation.message for violation in gate.violations) or "AI 输出未通过门禁"
    deduction = radar_contract.get("deduction") or {}
    algorithm = radar_contract.get("algorithm_v2") or {}
    diagnosis = (
        algorithm.get("summary")
        or (deduction.get("path_thesis") or {}).get("title")
        or deduction.get("summary")
        or "沿用规则雷达推演"
    )
    return AIReasoningResponse(
        gate_status="FALLBACK",
        gate_score=gate.score,
        diagnosis=diagnosis,
        current_hypothesis=str(algorithm.get("current_scenario_id") or "UNKNOWN"),
        reasoning_boundary="AI Native 推理未展示，沿用规则雷达边界。",
        hypotheses=[],
        coach_talk="AI Native 推理未通过门禁，本次只展示规则雷达结果。仅供参考，不构成投资建议。",
        fallback_reason=reason,
        fallback_data={
            "source": "radar_contract",
            "diagnosis": diagnosis,
            "deduction": deduction,
            "algorithm_v2": algorithm,
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

