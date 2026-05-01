"""Model routing for AI Native Radar."""

from __future__ import annotations

from server import config
from server.engines.ai_native.schemas import ModelRoute, StructureTranscript
from server.engines.ai_native.scoring import score_paths


def choose_model_route(transcript: StructureTranscript, *, calibration: bool = False) -> ModelRoute:
    """Choose the cheapest model that should be good enough for this structure.

    路由只看已经生成的结构事实，不重新判定走势，避免形成第三套缠论真相。
    """
    if calibration:
        return ModelRoute(
            tier="calibration",
            difficulty_score=100,
            model_name=_pro_model(),
            thinking_enabled=True,
            reasoning_effort="max",
            max_tokens=max(config.AI_NATIVE_RADAR_MAX_TOKENS, 4096),
            timeout_seconds=max(config.AI_NATIVE_RADAR_LLM_TIMEOUT, 120),
            reasons=["离线校准样本，使用最高推理强度"],
        )

    score, reasons = _difficulty_score(transcript)
    if score >= 40:
        return ModelRoute(
            tier="hard",
            difficulty_score=score,
            model_name=_pro_model(),
            thinking_enabled=True,
            reasoning_effort="high",
            max_tokens=config.AI_NATIVE_RADAR_MAX_TOKENS,
            timeout_seconds=config.AI_NATIVE_RADAR_LLM_TIMEOUT,
            reasons=reasons,
        )
    return ModelRoute(
        tier="simple",
        difficulty_score=score,
        model_name=_pro_model(),
        thinking_enabled=False,
        reasoning_effort="high",
        max_tokens=config.AI_NATIVE_RADAR_MAX_TOKENS,
        timeout_seconds=min(config.AI_NATIVE_RADAR_LLM_TIMEOUT, 45),
        reasons=reasons or ["结构清晰，使用 Pro 普通模式保持教练质量"],
    )


def _difficulty_score(transcript: StructureTranscript) -> tuple[int, list[str]]:
    points = 0
    reasons: list[str] = []
    context = transcript.divergence_context
    candidate = context.buy_sell_candidate

    if candidate.side != "NONE" and candidate.status in {"SIGNAL_ONLY", "WAITING_CONFIRM"}:
        points += 25
        reasons.append("低级别买卖点候选等待高级别确认")

    if context.chain_status in {"LOWER_ONLY", "ALIGNING"}:
        points += 20
        reasons.append("背驰链条尚未完成，存在背了又背风险")
    elif context.chain_status == "COUNTER_RISK":
        points += 22
        reasons.append("压力区出现反向风险候选")

    if context.pivot_position in {"NEAR_ZD", "NEAR_DD", "BELOW_ZD", "BELOW_DD", "ABOVE_ZG_WITHIN_GG"}:
        points += 12
        reasons.append(f"{context.pivot_level}分钟枢纽边界附近或边界外")

    scores = score_paths(transcript, current_hypothesis="UNKNOWN", gate_status="PASS", use_memory=True)
    ordered = sorted((item.score for item in scores), reverse=True)
    if len(ordered) >= 2 and ordered[0] - ordered[1] <= 10:
        points += 15
        reasons.append("主路径和第二路径分差小")

    if _has_level_conflict(transcript):
        points += 12
        reasons.append("大级别和小级别方向冲突")

    position_points, position_reasons = _position_difficulty(transcript)
    points += position_points
    reasons.extend(position_reasons)

    chart = transcript.structure_snapshot.chart_alignment
    if chart.status == "PARTIAL":
        points += 10
        reasons.append("Kline图层对账部分缺失")
    elif chart.status == "MISSING":
        points += 20
        reasons.append("Kline图层对账缺失")

    return min(points, 100), reasons[:6]


def _position_difficulty(transcript: StructureTranscript) -> tuple[int, list[str]]:
    position = transcript.position_context
    if not position or not position.is_holding:
        return 0, []

    points = 8
    reasons = ["持仓状态需要联动风控边界"]
    flags = set(position.risk_flags or [])
    if "STRUCTURE_AGAINST_POSITION" in flags:
        points += 18
        reasons.append("持仓方向和结构状态冲突")
    if position.pnl_percentage is not None and position.pnl_percentage <= -5:
        points += 10
        reasons.append("持仓浮亏扩大，需提高推理强度")
    nearest = position.nearest_risk_line or {}
    distance_pct = _optional_float(nearest.get("distance_pct")) if isinstance(nearest, dict) else None
    if distance_pct is not None and abs(distance_pct) <= 2:
        points += 12
        reasons.append("当前价贴近持仓风险线")
    return points, reasons[:3]


def _has_level_conflict(transcript: StructureTranscript) -> bool:
    states = {
        item.level: str(item.raw_state or "").upper()
        for item in transcript.levels
        if item.level in {"week", "day", "30", "60", "15", "5"}
    }
    macro = " ".join(states.get(level, "") for level in ("week", "day"))
    trigger = " ".join(states.get(level, "") for level in ("15", "5"))
    return ("UP" in macro and "DOWN" in trigger) or ("DOWN" in macro and "UP" in trigger)


def _pro_model() -> str:
    return config.AI_NATIVE_RADAR_MODEL or "deepseek-v4-pro"


def _optional_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
