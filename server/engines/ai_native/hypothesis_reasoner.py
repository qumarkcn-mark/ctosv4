"""LLM bridge for AI Native Radar commander reasoning."""

from __future__ import annotations

import json

from server.engines.ai_native.schemas import SimilarCaseSummary, StructureTranscript
from server.engines.ai_native.schemas import ModelRoute
from server.prompts.ai_native_radar_prompt import FREE_REASONING_PROMPT
from server.services.llm_service import LLMService


async def infer_ai_hypotheses(
    *,
    user_id: int,
    transcript: StructureTranscript,
    similar_cases: SimilarCaseSummary,
    rewrite_feedback: list[str] | None = None,
    llm_service: LLMService | None = None,
    model_route: ModelRoute | None = None,
) -> dict:
    """Run single-pass commander reasoning and return the Markdown contract.

    Semantic Coach Filter 已移除（2026-05-01）：
    - 第二次 LLM 调用砍掉，延迟减半。
    - 教练用语（买入/卖出/止损/清仓/接飞刀）允许出现在推演中。
    - 安全性由 deterministic verifier 保证（价格校验 + 危险词拦截）。
    """
    service = llm_service or LLMService()
    context = {
        "evidence_pack": _commander_evidence_payload(transcript),
        "similar_cases": similar_cases.model_dump(),
        "rewrite_feedback": rewrite_feedback or [],
    }
    context_json = json.dumps(context, ensure_ascii=False, indent=2)
    raw_reasoning_md = await service.infer_ai_native_markdown(
        FREE_REASONING_PROMPT,
        f"EVIDENCE PACK:\n{context_json}",
        user_id=user_id,
        model_route=model_route,
    )
    return {
        "raw_reasoning_md": raw_reasoning_md,
        "coach_filtered_md": raw_reasoning_md,
        "semantic_filter_status": "PASS",
        "semantic_filter_violations": [],
        "disclaimer": transcript.disclaimer,
    }


def _commander_evidence_payload(transcript: StructureTranscript) -> dict:
    pack = transcript.reasoning_evidence_pack or {}
    operative = pack.get("operative_context") or {}
    commander = pack.get("commander_context") or {}
    return {
        "symbol": transcript.symbol,
        "mode": transcript.mode,
        "generated_at": transcript.generated_at,
        "primary_context": commander.get("primary_context") or {},
        "must_use_levels": commander.get("must_use_levels") or {},
        "semantic_assertions": commander.get("semantic_assertions") or pack.get("semantic_assertions") or [],
        "tactical_levels": commander.get("tactical_levels") or {},
        "secondary_risks": commander.get("secondary_risks") or [],
        "commander_instruction": commander.get("instruction") or "",
        "basic_anchors": {
            "current_price": pack.get("current_price"),
            "quote_anchors": pack.get("quote_anchors") or {},
            "position_context": _position_context_payload(transcript),
        },
        "operative_context": {
            "current_zone": operative.get("current_zone"),
            "structure_gap": operative.get("structure_gap"),
            "nearest_known_level": operative.get("nearest_known_level"),
            "immediate_supports": operative.get("immediate_supports") or [],
            "immediate_resistances": operative.get("immediate_resistances") or [],
            "deep_references": operative.get("deep_references") or [],
        },
        "levels": _compact_levels(pack.get("levels") or {}, commander),
        "data_quality": {
            "stale": transcript.stale,
            "consistency_warnings": transcript.structure_snapshot.consistency_warnings,
            "chart_alignment": transcript.structure_snapshot.chart_alignment.status,
        },
        "disclaimer": transcript.disclaimer,
    }


def _compact_levels(levels: dict, commander: dict) -> dict:
    """Keep enough structure for audit without letting macro noise dominate."""
    primary = commander.get("primary_context") or {}
    code = primary.get("code")
    if code in {"MACRO_BREAKOUT_EDGE"}:
        keep = {"day", "60", "30", "5"}
    elif code in {"RETRACE_TESTING_3RD_BUY", "REBOUND_INTO_30M_ZHONGSHU"}:
        keep = {"30", "15", "5"}
    elif code in {"EXTREME_ABOVE_ALL_STRUCTURES"}:
        keep = {"day", "30", "15", "5"}
    elif code in {"BREAKDOWN_BELOW_30M"}:
        keep = {"day", "60", "30", "15", "5"}
    else:
        keep = {"day", "60", "30", "15", "5"}
    result = {}
    for key, value in levels.items():
        if key not in keep:
            continue
        if not isinstance(value, dict):
            continue
        result[key] = {
            "level": value.get("level"),
            "price": value.get("price"),
            "center": value.get("center"),
            "recent_centers": (value.get("recent_centers") or [])[-3:],
            "price_vs_center": value.get("price_vs_center"),
            "last_bi_dir": value.get("last_bi_dir"),
            "recent_bis": (value.get("recent_bis") or [])[-3:],
            "recent_bsp_events": (value.get("recent_bsp_events") or [])[-4:],
        }
    return result


def _position_context_payload(transcript: StructureTranscript) -> dict | None:
    if not transcript.position_context:
        return None
    position = transcript.position_context
    return {
        "is_holding": position.is_holding,
        "state": position.state,
        "label": position.label,
        "avg_cost": position.avg_cost,
        "quantity": position.quantity,
        "current_price": position.current_price,
        "pnl_percentage": position.pnl_percentage,
        "position_value": position.position_value,
        "weight_pct": position.weight_pct,
        "risk_flags": position.risk_flags,
        "coach_summary": position.coach_summary,
        "coach_focus": position.coach_focus,
    }


def _llm_transcript_payload(transcript: StructureTranscript) -> dict:
    """Legacy helper retained for older tests/imports; new model calls use commander evidence."""
    return _commander_evidence_payload(transcript)
