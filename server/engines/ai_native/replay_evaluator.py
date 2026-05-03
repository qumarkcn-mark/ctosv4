"""Automatic replay evaluator for AI Native Radar scoring."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from server.engines.ai_native.schemas import ReasoningBoundaries

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def replay_score_pending() -> dict:
    """第一阶段只记录待复盘状态，自动 replay 后续单独实现。"""
    return {"replay_status": "PENDING"}


def evaluate_reasoning_outcome(run_row, current_radar_data: dict, *, reviewer: str = "auto") -> dict:
    """Compare one saved AI Native run with the latest Radar facts.

    自动结算只做分类复盘，不生成新的交易建议。
    """
    output = _loads(run_row["ai_output_json"] if _has_key(run_row, "ai_output_json") else None)
    transcript = _loads(run_row["transcript_json"] if _has_key(run_row, "transcript_json") else None)
    predicted = str(output.get("current_hypothesis") or "UNKNOWN")
    actual, reason = infer_actual_hypothesis(current_radar_data, transcript)
    matched = predicted == actual if predicted != "UNKNOWN" and actual != "UNKNOWN" else None
    tags = _outcome_tags(predicted, actual, transcript, current_radar_data)
    sample_quality, quality_reason = _sample_quality(run_row, transcript, current_radar_data)
    learning_weight = _learning_weight(sample_quality, matched)
    quality_score = _quality_score(matched, actual, tags)
    replay_score = _replay_score(
        gate_score=_gate_score(run_row),
        quality_score=quality_score,
        matched=matched,
    )
    return {
        "actual_hypothesis": actual,
        "predicted_hypothesis": predicted,
        "matched": matched,
        "path": actual,
        "quality_score": quality_score,
        "notes": reason,
        "reviewer": reviewer,
        "settled_at": datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
        "settlement_source": "radar_contract",
        "tags": tags,
        "sample_quality": sample_quality,
        "sample_quality_reason": quality_reason,
        "learning_weight": learning_weight,
        "learning_tags": tags,
        "replay_score": replay_score,
    }


def infer_actual_hypothesis(current_radar_data: dict, transcript: dict) -> tuple[str, str]:
    freshness = current_radar_data.get("freshness") or {}
    if freshness.get("is_stale"):
        return "D", "当前 Radar 数据过期，停止强复盘"

    algorithm = current_radar_data.get("algorithm_v2") or {}
    scenario = str(algorithm.get("current_scenario_id") or "")
    if scenario in {"A", "B", "C", "D"}:
        return scenario, f"当前规则雷达场景为 {scenario}"

    price = _current_price(current_radar_data)
    boundaries = _transcript_boundaries(transcript)
    confirm = _first_price(boundaries.get("confirm"))
    invalidate = _first_price(boundaries.get("invalidate")) or _first_price(boundaries.get("support"))
    observe = _first_price(boundaries.get("observe"))

    if price <= 0:
        return "D", "缺少当前价格，无法自动结算"
    if confirm and price >= confirm:
        return "A", f"当前价 {price:.2f} 已到确认边界 {confirm:.2f}"
    if invalidate and price <= invalidate:
        return "C", f"当前价 {price:.2f} 已到失效边界 {invalidate:.2f}"
    if observe:
        return "B", f"当前价 {price:.2f} 仍在观察边界附近"
    return "B", f"当前价 {price:.2f} 未触发确认或失效边界"


def should_settle_created_at(created_at: str, *, today: str, force: bool = False) -> bool:
    if force:
        return True
    if not created_at:
        return False
    return str(created_at)[:10] < today


def _outcome_tags(predicted: str, actual: str, transcript: dict, current_radar_data: dict) -> list[str]:
    tags = []
    if predicted == actual and predicted != "UNKNOWN":
        tags.append("MATCHED")
    elif predicted == "A" and actual in {"B", "C"}:
        tags.append("OVER_OPTIMISTIC")
    elif predicted == "C" and actual in {"A", "B"}:
        tags.append("OVER_PESSIMISTIC")
    elif predicted == "B" and actual in {"A", "C"}:
        tags.append("RANGE_BROKEN")

    divergence = transcript.get("divergence_context") or {}
    if divergence.get("chain_status") in {"LOWER_ONLY", "ALIGNING"} and actual == "C":
        tags.append("REPEATED_DIVERGENCE_RISK")

    position = transcript.get("position_context") or {}
    nearest = position.get("nearest_risk_line") or {}
    price = _current_price(current_radar_data)
    risk_price = _num(nearest.get("price"))
    if position.get("is_holding") and risk_price > 0 and price > 0:
        if price <= risk_price:
            tags.append("RISK_LINE_EFFECTIVE")
        elif abs(price - risk_price) / risk_price <= 0.02:
            tags.append("RISK_LINE_TESTED")
    return tags


def _quality_score(matched: bool | None, actual: str, tags: list[str]) -> int:
    if actual == "D":
        return 5
    if matched is True:
        return 9
    if matched is False:
        if "RANGE_BROKEN" in tags:
            return 6
        return 5
    return 6


def _sample_quality(run_row, transcript: dict, current_radar_data: dict) -> tuple[str, str]:
    gate_score = _gate_score(run_row)
    gate_status = str(run_row["gate_status"] if _has_key(run_row, "gate_status") else "").upper()
    freshness = current_radar_data.get("freshness") or {}
    snapshot = transcript.get("structure_snapshot") or {}
    chart = snapshot.get("chart_alignment") or {}
    warnings = snapshot.get("consistency_warnings") or []

    if freshness.get("is_stale"):
        return "LOW", "当前 Radar 数据过期"
    if transcript.get("stale"):
        return "LOW", "原始推演结构数据过期"
    if gate_status == "FALLBACK" or gate_score < 50:
        return "LOW", "AI 门禁分低或 fallback"
    if chart.get("status") == "MISSING":
        return "LOW", "Kline 图层对账缺失"
    if len(warnings) >= 3:
        return "LOW", "结构事实包缺口较多"
    if chart.get("status") == "PARTIAL" or warnings or gate_score < 80:
        return "MEDIUM", "结构事实可用但存在缺口"
    return "HIGH", "结构事实完整且门禁通过"


def _learning_weight(sample_quality: str, matched: bool | None) -> float:
    base = {
        "HIGH": 1.0,
        "MEDIUM": 0.55,
        "LOW": 0.15,
    }.get(sample_quality, 0.25)
    if matched is False and sample_quality == "HIGH":
        return 1.2
    return base


def _replay_score(*, gate_score: int, quality_score: int, matched: bool | None) -> float:
    score = quality_score * 10 * 0.7 + gate_score * 0.3
    if matched is False:
        score -= 20
    return round(max(0.0, min(100.0, score)), 2)


def _gate_score(row) -> int:
    gate = _loads(row["gate_result_json"] if _has_key(row, "gate_result_json") else None)
    try:
        return int(gate.get("score") or 0)
    except (TypeError, ValueError):
        return 0


def _transcript_boundaries(transcript: dict) -> dict:
    raw = transcript.get("reasoning_boundaries") or (transcript.get("structure_snapshot") or {}).get("key_boundaries") or {}
    try:
        return ReasoningBoundaries(**raw).model_dump()
    except Exception:
        return raw if isinstance(raw, dict) else {}


def _current_price(radar_data: dict) -> float:
    for path in (
        ("position_context", "current_price"),
        ("quote", "price"),
    ):
        value = radar_data
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        num = _num(value)
        if num > 0:
            return num
    levels = ((radar_data.get("structure") or {}).get("levels") or {})
    for level in ("5", "15", "30", "60", "day", "week"):
        num = _num((levels.get(level) or {}).get("price"))
        if num > 0:
            return num
    return 0.0


def _first_price(items) -> float:
    if not isinstance(items, list):
        return 0.0
    for item in items:
        value = _num((item or {}).get("value") or (item or {}).get("price"))
        if value > 0:
            return value
    return 0.0


def _has_key(row, key: str) -> bool:
    try:
        return key in row.keys()
    except AttributeError:
        return isinstance(row, dict) and key in row


def _loads(value: object) -> dict:
    if not value:
        return {}
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
