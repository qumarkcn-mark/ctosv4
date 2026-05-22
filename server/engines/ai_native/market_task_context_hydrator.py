"""Hydrate practical market-task facts for second-stage reasoning.

This layer does not decide direction. It translates existing structure,
momentum, pressure/support, intraday preview, and continuity facts into the
kind of observation vocabulary a human desk trader would naturally use.
"""

from __future__ import annotations

from typing import Any


def hydrate_market_task_context(
    *,
    current_price: float,
    structure_geometry: dict[str, dict[str, Any]],
    momentum_dynamics: dict[str, dict[str, Any]],
    intraday_observation: dict[str, Any] | None = None,
    nearby_pressure_support: list[dict[str, Any]] | None = None,
    reasoning_continuity_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a factual observation layer for the second-stage LLM."""
    return {
        "version": "market_task_context.v1",
        "task_candidates": _task_candidates(
            current_price=current_price,
            structure_geometry=structure_geometry,
            momentum_dynamics=momentum_dynamics,
        ),
        "small_to_large_turn": _small_to_large_turn(intraday_observation or {}),
        "pressure_semantics": _pressure_semantics(
            current_price=current_price,
            clusters=nearby_pressure_support or [],
        ),
        "volume_phase": _volume_phase(momentum_dynamics),
        "continuity_read": _continuity_read(reasoning_continuity_context or {}),
    }


def _task_candidates(
    *,
    current_price: float,
    structure_geometry: dict[str, dict[str, Any]],
    momentum_dynamics: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for level_name, geometry in structure_geometry.items():
        center = geometry.get("center") or {}
        if not center or center.get("relevance") == "distant_context":
            continue
        position = (geometry.get("price_position") or {}).get("position") or "unknown"
        unfinished = geometry.get("unfinished_bi") or {}
        dynamics = momentum_dynamics.get(level_name) or {}
        evidence = []
        against = []
        if unfinished:
            evidence.append(
                f"{level_name}当前未确认笔方向为{unfinished.get('direction') or 'unknown'}，仍需等待完成或延伸"
            )
        if dynamics.get("macd_momentum"):
            evidence.append(f"{level_name} MACD 动能为 {dynamics.get('macd_momentum')}")
        if dynamics.get("macd_state"):
            evidence.append(f"{level_name} MACD 状态为 {dynamics.get('macd_state')}")

        if position == "below_zd":
            task = "跌破/接近中枢下沿后的止跌与反抽确认"
            against.append("未重新回到中枢下沿上方前，反抽仍可能只是弱修复")
        elif position == "above_zg":
            task = "离开中枢后的压力消化与回拉承接确认"
            against.append("未在上方站稳前，仍可能回到中枢震荡")
        elif position == "in_center":
            task = "中枢内震荡后的方向选择"
            against.append("仍在中枢内部时，外部压力/支撑不应被当成已触发目标")
        else:
            task = "结构边界不足，等待更清晰的中枢任务"
            against.append("当前缺少明确中枢位置关系")

        candidates.append(
            {
                "level": level_name,
                "task": task,
                "price_position": position,
                "center": {
                    "zg": center.get("zg"),
                    "zd": center.get("zd"),
                    "maturity": center.get("maturity"),
                    "relevance": center.get("relevance"),
                },
                "evidence": evidence[:4],
                "against": against[:3],
            }
        )
        if len(candidates) >= 4:
            break
    return candidates


def _small_to_large_turn(payload: dict[str, Any]) -> dict[str, Any]:
    levels = payload.get("levels") or {}
    chain = []
    for level in ("1m", "5m", "30m"):
        item = levels.get(level) or {}
        macd = item.get("macd_with_forming") or item.get("macd_closed_only") or {}
        state = _turn_state(macd)
        chain.append(
            {
                "level": level,
                "state": state,
                "bar_status": item.get("last_bar_status") or "",
                "basis": macd.get("basis") or "",
                "macd_state": macd.get("macd_state") or "unknown",
                "macd_momentum": macd.get("macd_momentum") or "unknown",
            }
        )
    states = [item["state"] for item in chain]
    if states[:2] == ["turning_up", "turning_up"]:
        status = "forming_upward_chain"
    elif states[0] == "turning_up" and states[1] in {"neutral", "turning_up"}:
        status = "early_low_level_turn"
    elif "turning_down" in states[:2]:
        status = "low_level_pressure"
    else:
        status = "not_confirmed"
    return {
        "status": status,
        "as_of": payload.get("as_of") or "",
        "coverage": payload.get("coverage") or {},
        "chain": chain,
        "note": "FORMING bar 只表示盘中观察，不能当作正式结构确认",
    }


def _pressure_semantics(*, current_price: float, clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for cluster in sorted(clusters, key=lambda item: abs(_num(item.get("distance_pct"))))[:4]:
        center = _cluster_center(cluster)
        if center <= 0:
            continue
        cluster_type = cluster.get("type") or ("pressure" if center > current_price else "support")
        if cluster_type == "pressure":
            role = "risk_release_or_rejection_watch"
            after_break = "若有效站上并回踩不破，可转为下一段修复/上攻的支撑观察"
            if_rejected = "若冲高受阻，优先按压力消化或回到中枢震荡处理"
        else:
            role = "fallback_support_or_breakdown_watch"
            after_break = "若跌破后反抽不过，可能转为上方压力"
            if_rejected = "若在此止跌并低级别转强，可作为回拉承接观察"
        result.append(
            {
                "zone": cluster.get("zone") or [],
                "center": round(center, 4),
                "type": cluster_type,
                "role": role,
                "distance_pct": cluster.get("distance_pct"),
                "source_levels": cluster.get("source_levels") or [],
                "semantic": cluster.get("semantic") or "",
                "after_break": after_break,
                "if_rejected": if_rejected,
            }
        )
    return result


def _volume_phase(momentum_dynamics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    observations = []
    for level_name, dynamics in momentum_dynamics.items():
        volume_state = dynamics.get("volume_state") or "unknown"
        if volume_state == "unknown":
            continue
        observations.append(
            {
                "level": level_name,
                "volume_state": volume_state,
                "volume_ratio_5_20": dynamics.get("volume_ratio_5_20"),
                "macd_momentum": dynamics.get("macd_momentum") or "unknown",
                "atr_volatility": dynamics.get("atr_volatility") or "unknown",
            }
        )
    state = "mixed"
    if observations:
        latest = observations[0]
        state = str(latest.get("volume_state") or "mixed")
    return {
        "state": state,
        "observations": observations[:4],
        "possible_readings": [
            "放量杀跌后的缩量修复，若低级别转强，可能代表抛压减轻",
            "若冲击压力位失败，缩量仍可能代表反弹力度不足",
            "量能必须结合结构任务、压力位置和低级别转折一起看",
        ],
    }


def _continuity_read(payload: dict[str, Any]) -> dict[str, Any]:
    previous = payload.get("previous_reasoning") or {}
    trigger_statuses = payload.get("trigger_status_since_last_run") or []
    crossed = [item for item in trigger_statuses if item.get("status") == "crossed"]
    not_touched = [item for item in trigger_statuses if item.get("status") == "not_touched"]
    return {
        "previous_summary": previous.get("card_summary") or "",
        "previous_action": previous.get("card_action") or "",
        "crossed_triggers": crossed[:3],
        "not_touched_triggers": not_touched[:3],
        "read": "上一轮已有触发，需要判断是增强、失效还是进入新任务"
        if crossed
        else "上一轮关键触发尚未发生，需要判断原推演是延续还是减弱",
    }


def _turn_state(macd: dict[str, Any]) -> str:
    state = macd.get("macd_state") or "unknown"
    momentum = macd.get("macd_momentum") or "unknown"
    if state == "golden_cross" or (state == "below_zero" and momentum == "weakening"):
        return "turning_up"
    if state == "dead_cross" or (state == "above_zero" and momentum == "weakening"):
        return "turning_down"
    if state in {"above_zero", "crossing_zero"}:
        return "neutral"
    return "not_confirmed"


def _cluster_center(cluster: dict[str, Any]) -> float:
    zone = cluster.get("zone") or []
    if len(zone) != 2:
        return 0.0
    return (_num(zone[0]) + _num(zone[1])) / 2


def _num(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
