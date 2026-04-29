"""LLM bridge for AI Native Radar hypotheses."""

from __future__ import annotations

import json

from server.engines.ai_native.schemas import SimilarCaseSummary, StructureTranscript
from server.prompts.ai_native_radar_prompt import AI_NATIVE_RADAR_SYSTEM_PROMPT
from server.services.llm_service import LLMService


async def infer_ai_hypotheses(
    *,
    user_id: int,
    transcript: StructureTranscript,
    similar_cases: SimilarCaseSummary,
    rewrite_feedback: list[str] | None = None,
    llm_service: LLMService | None = None,
) -> dict:
    """Call the configured LLM and return raw JSON for verifier validation."""
    service = llm_service or LLMService()
    context = {
        "structure_transcript": transcript.model_dump(),
        "similar_cases": similar_cases.model_dump(),
        "rewrite_feedback": rewrite_feedback or [],
    }
    return await service.infer_ai_native_radar(
        AI_NATIVE_RADAR_SYSTEM_PROMPT,
        json.dumps(context, ensure_ascii=False),
        user_id=user_id,
    )
