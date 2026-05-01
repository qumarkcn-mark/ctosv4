"""Deterministic agent observations for AI Native Radar.

第一版多 Agent 不直接多次调用 LLM。先用可测试的观察者把结构事实切成职责边界，
后续可以逐个替换为真正的 AI Agent。
"""

from __future__ import annotations

from server.engines.ai_native.scoring import primary_path, score_paths
from server.engines.ai_native.schemas import AgentObservation, StructureTranscript


def build_agent_observations(transcript: StructureTranscript) -> list[AgentObservation]:
    return [
        _structure_agent(transcript),
        _divergence_agent(transcript),
        _key_level_agent(transcript),
        _path_scorer_agent(transcript),
        _coach_agent(transcript),
    ]


def _structure_agent(transcript: StructureTranscript) -> AgentObservation:
    snapshot = transcript.structure_snapshot
    warnings = snapshot.consistency_warnings + snapshot.chart_alignment.warnings
    level_count = len(snapshot.levels)
    if warnings:
        return AgentObservation(
            agent_id="structure_agent",
            verdict="结构事实不完整",
            confidence=0.45,
            evidence=warnings,
            next_focus="先补齐缺失级别或等待结构刷新",
            blocks=warnings,
        )
    pivot = _level_by_role(transcript, {"pivot", "pivot_alt"})
    trigger = _level_by_role(transcript, {"trigger", "trigger_alt"})
    evidence = []
    if pivot:
        evidence.extend(pivot.evidence[:3])
    if trigger:
        evidence.extend(trigger.evidence[:2])
    return AgentObservation(
        agent_id="structure_agent",
        verdict="结构事实可用于推演",
        confidence=min(0.9, 0.55 + level_count * 0.06),
        evidence=evidence,
        next_focus="围绕主中枢边界和小级别触发层验证",
    )


def _divergence_agent(transcript: StructureTranscript) -> AgentObservation:
    context = transcript.divergence_context
    signals = context.lower_level_signals + context.pivot_signals
    confidence = 0.42 + min(0.28, len(signals) * 0.1)
    if context.alignment in {"CONFIRMED_SUPPORT", "FAILED_DIVERGENCE", "COUNTER_TREND_RISK"}:
        confidence += 0.14
    candidate = context.buy_sell_candidate
    return AgentObservation(
        agent_id="divergence_agent",
        verdict=_divergence_verdict(context),
        confidence=round(min(confidence, 0.88), 2),
        evidence=[
            _candidate_summary(candidate),
            _chain_summary(context),
            *(signal.evidence[0] for signal in signals if signal.evidence),
            context.upgrade_condition,
            context.failure_condition,
        ],
        next_focus=_divergence_focus(context),
        blocks=[] if signals else ["no_divergence_signal"],
    )


def _key_level_agent(transcript: StructureTranscript) -> AgentObservation:
    boundaries = transcript.reasoning_boundaries
    confirm = _first_price(boundaries.confirm)
    observe = _first_price(boundaries.observe or boundaries.support)
    invalidate = _first_price(boundaries.invalidate)
    evidence = []
    if confirm:
        evidence.append(f"确认位 {confirm.label} {confirm.value:.2f}")
    if observe:
        evidence.append(f"观察/支撑 {observe.label} {observe.value:.2f}")
    if invalidate:
        evidence.append(f"失效位 {invalidate.label} {invalidate.value:.2f}")
    return AgentObservation(
        agent_id="key_level_agent",
        verdict="关键价位已锁定" if evidence else "关键价位不足",
        confidence=0.78 if evidence else 0.35,
        evidence=evidence,
        next_focus="只围绕确认位、观察位、失效位验证路径",
        blocks=[] if evidence else ["missing_key_boundaries"],
    )


def _path_scorer_agent(transcript: StructureTranscript) -> AgentObservation:
    scores = score_paths(transcript)
    main = primary_path(scores)
    primary = next((item for item in scores if item.id == main), None)
    confidence = 0.5
    if primary:
        confidence += max(0, primary.score - 35) / 100
    return AgentObservation(
        agent_id="path_scorer_agent",
        verdict=f"主路径 {main}",
        confidence=round(min(confidence, 0.85), 2),
        evidence=[f"{item.id}:{item.score}" for item in scores],
        next_focus=primary.reason if primary else "等待路径评分",
        blocks=[] if main != "D" else ["stop_deduction_primary"],
    )


def _coach_agent(transcript: StructureTranscript) -> AgentObservation:
    scorer = _path_scorer_agent(transcript)
    mode = transcript.mode
    candidate = transcript.divergence_context.buy_sell_candidate
    candidate_focus = _candidate_focus(candidate)
    if transcript.stale or "stop_deduction_primary" in scorer.blocks:
        verdict = "停止强推演"
        focus = "先等数据和结构确认"
    elif mode == "HOLDING":
        verdict = "持仓按边界管理"
        focus = candidate_focus or "先看失效线和结构确认线，不扩大动作"
    else:
        verdict = "空仓等待市场表态"
        focus = candidate_focus or "只在关键边界附近观察，不追逐中间波动"
    return AgentObservation(
        agent_id="coach_agent",
        verdict=verdict,
        confidence=0.74,
        evidence=[_candidate_summary(candidate), scorer.verdict, scorer.next_focus],
        next_focus=focus,
        blocks=scorer.blocks,
    )


def _level_by_role(transcript: StructureTranscript, roles: set[str]):
    return next((level for level in transcript.structure_snapshot.levels if level.role in roles), None)


def _first_price(items):
    return next((item for item in items if item.value > 0), None)


def _divergence_verdict(context) -> str:
    candidate = context.buy_sell_candidate
    if candidate.kind != "NONE":
        return f"{context.chain_status} · {candidate.kind} · {candidate.status}"
    return context.alignment


def _divergence_focus(context) -> str:
    candidate = context.buy_sell_candidate
    if candidate.status == "SIGNAL_ONLY":
        return "只当低级别线索观察，等待枢纽边界确认，防止背了又背"
    if candidate.status == "WAITING_CONFIRM":
        return "等待候选完成级别转换，不提前当成确认买卖点"
    if candidate.status == "CONFIRMED":
        return "候选已由结构事实确认，继续看触发边界和失效边界"
    if candidate.status == "INVALID":
        return "候选已失效，按转弱路径重新分类"
    return {
        "NO_DIVERGENCE": "等待小级别止跌结构，不把放缓当背驰",
        "LOW_LEVEL_ONLY": "看小级别背驰能否收回主中枢边界",
        "ALIGNING": "看背驰线索能否升级为支撑确认",
        "CONFIRMED_SUPPORT": "看支撑确认后能否继续收复上沿",
        "FAILED_DIVERGENCE": "按背驰失败后的转弱路径观察",
        "COUNTER_TREND_RISK": "压力区先防反向风险",
    }.get(context.alignment, "等待背驰联动状态清晰")


def _candidate_summary(candidate) -> str:
    if candidate.kind == "NONE":
        return "买卖点候选：暂无"
    return f"买卖点候选：{candidate.side} {candidate.kind} {candidate.status}。{candidate.note}"


def _candidate_focus(candidate) -> str:
    if candidate.kind == "NONE":
        return ""
    if candidate.status == "SIGNAL_ONLY":
        return "当前只是低级别背驰线索，等待重新站回枢纽边界"
    if candidate.status == "WAITING_CONFIRM":
        return "候选等待确认，只看触发边界和失效边界"
    if candidate.status == "CONFIRMED":
        return "候选已确认，围绕失效边界验证是否延续"
    if candidate.status == "INVALID":
        return "候选失效，等待新的结构重新分类"
    return ""


def _chain_summary(context) -> str:
    if not context.chain:
        return ""
    return "背驰链条：" + " / ".join(
        f"{step.role}:{step.status}"
        for step in context.chain
        if step.role in {"macro", "pivot", "trigger", "confirmation"}
    )
