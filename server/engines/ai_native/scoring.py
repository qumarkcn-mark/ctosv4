"""Path scoring for AI Native Radar."""

from __future__ import annotations

from server.engines.ai_native.schemas import PathScore, StructureTranscript
from server.engines.ai_native.score_calibration import load_score_calibration


PATH_NAMES = {
    "A": "延续观察",
    "B": "回撤震荡",
    "C": "转弱防守",
    "D": "停止推演",
}


def score_paths(
    transcript: StructureTranscript,
    *,
    current_hypothesis: str = "UNKNOWN",
    gate_status: str = "PASS",
    calibration: dict | None = None,
    use_memory: bool = False,
) -> list[PathScore]:
    """把结构事实转成 A/B/C/D 概率型评分。

    评分不是交易命令，只表达“哪条推演路径更值得优先观察”。
    """
    scores = {"A": 25, "B": 40, "C": 25, "D": 10}
    reasons: dict[str, list[str]] = {path: [] for path in scores}
    context = transcript.divergence_context

    if context.alignment == "NO_DIVERGENCE":
        _add(scores, "A", -6)
        _add(scores, "B", 8)
        _add(scores, "C", 4)
        reasons["B"].append("没有明确背驰联动，优先等结构确认")
        reasons["C"].append("无背驰时保留转弱路径")
    elif context.alignment == "LOW_LEVEL_ONLY":
        _add(scores, "A", -8)
        _add(scores, "B", 10)
        _add(scores, "C", 5)
        reasons["B"].append("小级别背驰还没有收回高级别中枢边界")
        reasons["C"].append("背驰只在线索级别，仍要防失效")
    elif context.alignment == "ALIGNING":
        _add(scores, "A", 8)
        _add(scores, "B", 6)
        _add(scores, "C", -4)
        reasons["A"].append("小级别背驰正在靠近高级别边界")
        reasons["B"].append("联动未完成，仍需要市场表态")
    elif context.alignment == "CONFIRMED_SUPPORT":
        _add(scores, "A", 18)
        _add(scores, "B", -4)
        _add(scores, "C", -8)
        reasons["A"].append("背驰线索已回到高级别中枢边界内")
    elif context.alignment == "FAILED_DIVERGENCE":
        _add(scores, "A", -12)
        _add(scores, "B", -8)
        _add(scores, "C", 28)
        reasons["C"].append("背驰线索已经失效")
    elif context.alignment == "COUNTER_TREND_RISK":
        _add(scores, "A", -10)
        _add(scores, "B", 8)
        _add(scores, "C", 12)
        reasons["C"].append("压力区出现反向背驰风险")
        reasons["B"].append("上方压力未化解，先按震荡/转弱观察")

    if transcript.stale:
        _add(scores, "D", 42)
        _add(scores, "A", -10)
        _add(scores, "B", -18)
        _add(scores, "C", -10)
        reasons["D"].append("结构数据过期，停止强推演")

    warnings = transcript.structure_snapshot.consistency_warnings
    if warnings:
        _add(scores, "D", min(24, len(warnings) * 8))
        _add(scores, "A", -6)
        reasons["D"].append("结构事实包存在缺口")

    chart_alignment = transcript.structure_snapshot.chart_alignment
    if chart_alignment.status == "PARTIAL":
        _add(scores, "A", -10)
        _add(scores, "B", 8)
        _add(scores, "C", -6)
        _add(scores, "D", min(16, 8 + len(chart_alignment.warnings) * 2))
        reasons["B"].append("Kline图层对账不完整，优先等结构确认")
        reasons["D"].append("Kline图层对账不完整，降低方向推演")
    elif chart_alignment.status == "MISSING":
        _add(scores, "A", -16)
        _add(scores, "B", 4)
        _add(scores, "C", -12)
        _add(scores, "D", 40)
        reasons["B"].append("缺少Kline图层对账，先按观察处理")
        reasons["D"].append("缺少Kline图层对账，停止强方向推演")

    _apply_buy_sell_candidate_scores(scores, reasons, transcript)
    _apply_operative_context_scores(scores, reasons, transcript)
    _apply_position_scores(scores, reasons, transcript)
    if calibration is None and use_memory:
        calibration = load_score_calibration(transcript)
    _apply_calibration_scores(scores, reasons, calibration or {})

    if gate_status == "PASS" and current_hypothesis in scores:
        _add(scores, current_hypothesis, 6)
        reasons[current_hypothesis].append("AI 主假设通过门禁")
    elif gate_status == "FALLBACK":
        _add(scores, "B", 3)
        _add(scores, "D", 3)
        reasons["B"].append("AI 输出未通过门禁，先降低方向性")
        reasons["D"].append("AI 输出未通过门禁，保留停止推演")

    normalized = _normalize(scores)
    return [
        PathScore(
            id=path,  # type: ignore[arg-type]
            name=PATH_NAMES[path],
            score=normalized[path],
            reason="；".join(reasons[path]) or f"{PATH_NAMES[path]} 暂无强证据，只保留为完全分类路径",
        )
        for path in ("A", "B", "C", "D")
    ]


def _apply_buy_sell_candidate_scores(
    scores: dict[str, int],
    reasons: dict[str, list[str]],
    transcript: StructureTranscript,
) -> None:
    candidate = transcript.divergence_context.buy_sell_candidate
    if candidate.side == "BUY":
        if candidate.status == "SIGNAL_ONLY":
            _add(scores, "A", -10)
            _add(scores, "B", 12)
            _add(scores, "C", 4)
            reasons["B"].append("买点候选仍是低级别线索，解释为背了又背的观察态")
            reasons["C"].append("候选未升级前仍要防继续失效")
        elif candidate.status == "WAITING_CONFIRM":
            _add(scores, "A", 6)
            _add(scores, "B", 6)
            _add(scores, "C", -4)
            reasons["A"].append("买点候选等待高级别结构确认")
            reasons["B"].append("候选未完成转换，仍需观察确认")
        elif candidate.status == "CONFIRMED":
            boost = 18 if candidate.kind == "THIRD_CONFIRM" else 12
            _add(scores, "A", boost)
            _add(scores, "B", -4)
            _add(scores, "C", -8)
            reasons["A"].append(f"{candidate.kind} 已由结构事实确认")
        elif candidate.status == "INVALID":
            _add(scores, "A", -14)
            _add(scores, "B", -4)
            _add(scores, "C", 22)
            reasons["C"].append("买点候选失效，转弱路径升权")
    elif candidate.side == "SELL":
        if candidate.status in {"WAITING_CONFIRM", "CONFIRMED"}:
            _add(scores, "A", -12)
            _add(scores, "B", 6)
            _add(scores, "C", 14)
            reasons["C"].append(f"{candidate.kind} 风险候选压制向上路径")
            reasons["B"].append("卖点候选未完全处理前先按风险观察")
        elif candidate.status == "INVALID":
            _add(scores, "B", 5)
            reasons["B"].append("卖点候选失效后等待结构重新分类")


def _apply_operative_context_scores(
    scores: dict[str, int],
    reasons: dict[str, list[str]],
    transcript: StructureTranscript,
) -> None:
    operative = (transcript.reasoning_evidence_pack or {}).get("operative_context") or {}
    if operative.get("structure_gap") or operative.get("current_zone") == "price_structure_gap":
        _add(scores, "A", -24)
        _add(scores, "B", -4)
        _add(scores, "C", -6)
        _add(scores, "D", 58)
        reasons["D"].append("当前价已脱离可用分钟结构包，不能用旧中枢边界强推演")
        reasons["B"].append("近端结构缺失时先等新笔/新中枢补齐")


def _apply_position_scores(
    scores: dict[str, int],
    reasons: dict[str, list[str]],
    transcript: StructureTranscript,
) -> None:
    position = transcript.position_context
    if not position or not position.is_holding:
        return

    flags = set(position.risk_flags or [])
    if "STRUCTURE_AGAINST_POSITION" in flags:
        _add(scores, "A", -10)
        _add(scores, "C", 18)
        _add(scores, "D", 4)
        reasons["C"].append("持仓结构风险升高，失效路径需要升权")

    if position.pnl_percentage is not None and position.pnl_percentage <= -5:
        _add(scores, "A", -6)
        _add(scores, "C", 10)
        reasons["C"].append("持仓浮亏扩大，优先盯防守线是否失效")
    elif position.pnl_percentage is not None and position.pnl_percentage >= 8:
        _add(scores, "B", 5)
        reasons["B"].append("持仓已有浮盈，压力与回撤边界需要同步观察")

    nearest = position.nearest_risk_line or {}
    distance_pct = _optional_float(nearest.get("distance_pct")) if isinstance(nearest, dict) else None
    if distance_pct is not None and abs(distance_pct) <= 2:
        _add(scores, "A", -5)
        _add(scores, "B", 8)
        _add(scores, "C", 8)
        reasons["B"].append("当前价贴近持仓风险线，先等边界选择方向")
        reasons["C"].append("贴近持仓风险线，失效路径不能忽略")


def _apply_calibration_scores(
    scores: dict[str, int],
    reasons: dict[str, list[str]],
    calibration: dict,
) -> None:
    sample_count = int(calibration.get("sample_count") or 0)
    if sample_count < 3:
        return
    outcome_counts = calibration.get("outcome_counts") or {}
    tag_counts = calibration.get("tag_counts") or {}
    total = sum(_optional_float(value) or 0 for value in outcome_counts.values()) or 1
    capped_strength = min(10, 4 + sample_count // 3)
    for path in ("A", "B", "C", "D"):
        ratio = (_optional_float(outcome_counts.get(path)) or 0) / total
        if ratio >= 0.55:
            boost = min(capped_strength, int(round(ratio * capped_strength)))
            _add(scores, path, boost)
            reasons[path].append(f"历史复盘同结构多次兑现为 {PATH_NAMES[path]}")

    if (_optional_float(tag_counts.get("OVER_OPTIMISTIC")) or 0) >= 1.5:
        _add(scores, "A", -5)
        _add(scores, "B", 3)
        _add(scores, "C", 4)
        reasons["C"].append("历史复盘提示同结构偏乐观，失效路径升权")
    if (_optional_float(tag_counts.get("OVER_PESSIMISTIC")) or 0) >= 1.5:
        _add(scores, "C", -4)
        _add(scores, "B", 3)
        reasons["B"].append("历史复盘提示同结构偏悲观，先按观察处理")
    if (_optional_float(tag_counts.get("REPEATED_DIVERGENCE_RISK")) or 0) >= 1.5:
        _add(scores, "A", -4)
        _add(scores, "B", 4)
        _add(scores, "C", 5)
        reasons["C"].append("历史复盘多次出现背了又背风险")


def primary_path(path_scores: list[PathScore]) -> str:
    if not path_scores:
        return "UNKNOWN"
    return max(path_scores, key=lambda item: item.score).id


def _add(scores: dict[str, int], path: str, delta: int) -> None:
    scores[path] = scores.get(path, 0) + delta


def _normalize(scores: dict[str, int]) -> dict[str, int]:
    clamped = {path: max(5, value) for path, value in scores.items()}
    total = sum(clamped.values()) or 1
    normalized = {path: int(round(value * 100 / total)) for path, value in clamped.items()}
    drift = 100 - sum(normalized.values())
    if drift:
        top = max(normalized, key=normalized.get)
        normalized[top] += drift
    return normalized


def _optional_float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
