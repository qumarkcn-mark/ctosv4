"""AI Native V5 structure reasoning prompt and local fallback helpers."""

from __future__ import annotations

from typing import Any


AI_STRUCTURE_REASONING_PROMPT_VERSION = "ai_structure_reasoning.e1_dynamic_growth"


AI_STRUCTURE_REASONING_SYSTEM_PROMPT = """你是 CT-OS AI Native V5 的缠论结构推演层。

你的任务不是给买卖指令，而是基于 CZSC 已经计算完成的结构事实，解释当前走势可能如何生长。

你只能使用输入中的 structure_facts、position_context、symbol_memory 和 background_context。
CZSC snapshot 是唯一结构来源；你不能重新计算结构，不能引入旧 radar、旧 chan.py、旧 matrix 或任何其他结构引擎。

请用纯净缠论语言推演：
- 当前主推演级别是什么，触发级别是什么。
- 当前笔、中枢、离开段、回拉段可能如何继续生长。
- 是否存在背驰、不背驰延伸、潜在背驰或背驰尚不清楚。
- 高级别背景和低级别触发之间是否存在级别共振或冲突。
- 如果是 A+小b，请解释“大级别中枢上沿/历史关键位置 + 小级别震荡承接”的含义。
- 给出若干条自然分支，数量由结构决定，不强制三条。
- 每条分支都要给触发条件、失效条件、观察级别和可对应到图表证据的 focus。

边界：
- 不要使用 Commander、战星、绝对分类等叙事。
- 不要强制输出 right_side_major_wave / zhongshu_oscillation / structural_breakdown 三分类。
- 不要直接说买入、卖出、满仓、清仓。
- 允许回答“进入观察窗口”“分支失效”“等待确认”“提醒复核”。
- 必须保留“仅供参考，不构成投资建议”的风险边界。

只返回 JSON 对象，不要 Markdown 代码块。"""


def build_reasoning_input(
    *,
    symbol: str,
    source_snapshot_ids: list[str],
    raw_context: dict[str, Any],
    boundary: dict[str, Any],
    background: dict[str, Any],
    symbol_memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "version": AI_STRUCTURE_REASONING_PROMPT_VERSION,
        "symbol": symbol,
        "source_snapshot_ids": source_snapshot_ids,
        "structure_facts": {
            "boundary": boundary,
            "snapshots": raw_context.get("snapshots") or [],
        },
        "position_context": raw_context.get("position_context") or {},
        "background_context": background,
        "symbol_memory": symbol_memory or {"status": "not_loaded"},
        "output_contract": {
            "main_level": "string",
            "trigger_level": "string",
            "structure_summary": "string",
            "trend_growth": "object",
            "divergence_view": "object",
            "resonance_view": "object",
            "scenario_branches": "array",
            "key_boundaries": "array",
            "coach_summary": "string",
            "risk_notes": "array",
        },
    }


def build_local_reasoning_fallback(*, symbol: str, reasoning_input: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic reasoning object when the LLM layer is not available.

    这是 P0 的兜底推演契约：它不伪造模型能力，只把 CZSC 边界组织成
    V5 后续层可以消费的 reasoning_json，保证主链路和测试先回到正确形状。
    """

    facts = reasoning_input.get("structure_facts") or {}
    boundary = facts.get("boundary") or {}
    levels = boundary.get("levels") or {}
    primary = _primary_level(levels) or boundary.get("primary_level") or ""
    primary_item = levels.get(primary) or {}
    center = primary_item.get("active_center") or {}
    zg = _num(center.get("zg"))
    zd = _num(center.get("zd"))
    price = _num(primary_item.get("current_price"))
    evidence = primary_item.get("evidence") or {}
    position = reasoning_input.get("position_context") or {}
    holding_text = "持仓" if position.get("has_position") else "空仓"
    trigger_level = _trigger_level(levels, primary)

    if primary and zg > 0 and zd > 0:
        structure_summary = (
            f"{symbol} 当前以 {primary} 级别中枢为主要观察边界，"
            f"上沿 {zg:.2f}、下沿 {zd:.2f}，用户状态为{holding_text}。"
        )
        growth_path = (
            f"走势若要继续向上生长，需要先离开 {primary} 级别中枢上沿 {zg:.2f}，"
            f"再在 {trigger_level or primary} 级别形成回拉不破或小中枢承接。"
        )
        failure_path = f"若重新跌破 {zd:.2f}，当前离开/突破观察分支先按失效复核。"
    else:
        structure_summary = f"{symbol} 当前 CZSC 快照还没有形成足够清晰的有效中枢边界。"
        growth_path = "先等待 CZSC 快照补齐有效中枢、笔方向和关键边界，再做走势生长推演。"
        failure_path = "结构事实不足时不建立进攻分支，只保留刷新观察。"

    branches = []
    key_boundaries = []
    if primary and zg > 0 and zd > 0:
        key_boundaries = [
            {"role": "trigger", "level": primary, "price": zg, "evidence_id": evidence.get("trigger_line") or ""},
            {"role": "invalidation", "level": primary, "price": zd, "evidence_id": evidence.get("invalidation_line") or ""},
        ]
        branches.append({
            "branch_type": "observe_breakout",
            "title": "中枢上沿离开后的承接观察",
            "main_level": primary,
            "trigger_level": trigger_level or primary,
            "trigger_condition": {
                "type": "price_above",
                "price": zg,
                "level": primary,
                "label": f"站上 {primary} 级别中枢上沿后观察承接",
            },
            "invalidate_condition": {
                "type": "price_below",
                "price": zd,
                "level": primary,
                "label": f"跌破 {primary} 级别中枢下沿则当前观察分支失效",
            },
            "next_recheck": "下一根触发级别 K 线收线后复核",
            "chart_focus": _chart_focus(evidence, ["active_center", "trigger_line", "invalidation_line"]),
        })
        branches.append({
            "branch_type": "invalidation_watch",
            "title": "跌回中枢后的结构失效观察",
            "main_level": primary,
            "trigger_level": trigger_level or primary,
            "trigger_condition": {
                "type": "price_below",
                "price": zd,
                "level": primary,
                "label": f"跌破 {primary} 级别中枢下沿后复核转弱",
            },
            "invalidate_condition": {
                "type": "price_above",
                "price": zg,
                "level": primary,
                "label": f"重新站回 {primary} 级别中枢上沿后弱化信号撤销",
            },
            "next_recheck": "触发后检查是否能快速收回中枢",
            "chart_focus": _chart_focus(evidence, ["active_center", "invalidation_line", "trigger_line"]),
        })
        if position.get("has_position"):
            branches.append({
                "branch_type": "holding_defense",
                "title": "持仓防守线复核",
                "main_level": primary,
                "trigger_level": trigger_level or primary,
                "trigger_condition": {
                    "type": "price_below",
                    "price": zd,
                    "level": primary,
                    "label": f"持仓跌破 {zd:.2f} 后提醒复核纪律",
                },
                "invalidate_condition": {
                    "type": "price_above",
                    "price": zg,
                    "level": primary,
                    "label": f"重新站回 {zg:.2f} 后防守压力缓和",
                },
                "next_recheck": "触发提醒后进入复盘队列",
                "chart_focus": _chart_focus(evidence, ["active_center", "invalidation_line"]),
            })

    return {
        "version": AI_STRUCTURE_REASONING_PROMPT_VERSION,
        "symbol": symbol,
        "data_as_of": _data_as_of(reasoning_input),
        "main_level": primary,
        "trigger_level": trigger_level or primary,
        "structure_summary": structure_summary,
        "trend_growth": {
            "current_state": "active_center_observation" if primary and zg > 0 and zd > 0 else "insufficient_structure",
            "growth_path": growth_path,
            "next_confirmation": (
                f"观察 {trigger_level or primary} 级别是否形成回踩不破或小中枢承接。"
                if primary and zg > 0 and zd > 0
                else "等待有效中枢边界生成。"
            ),
            "failure_path": failure_path,
        },
        "divergence_view": {
            "status": "unclear",
            "level": primary,
            "evidence": "当前 fallback 只读取 CZSC 边界，不替代后续 LLM 对背驰细节的推演。",
            "risk_note": "若离开段放大后不能延续，需要在触发级别复核潜在背驰。",
        },
        "resonance_view": {
            "higher_level_context": f"{primary} 级别中枢边界" if primary else "高级别结构不足",
            "lower_level_trigger": f"{trigger_level or primary} 级别承接" if primary else "触发级别不足",
            "resonance_type": "boundary_with_lower_trigger" if primary else "unclear",
            "conflict_note": "背景信息只作解释，不覆盖结构触发线和失败线。",
        },
        "scenario_branches": branches,
        "key_boundaries": key_boundaries,
        "coach_summary": (
            f"{structure_summary}{growth_path}{failure_path}仅供参考，不构成投资建议"
        ),
        "risk_notes": ["仅供参考，不构成投资建议"],
        "reasoning_meta": {
            "provider": "local_fallback",
            "llm_status": "not_invoked",
            "price": price,
        },
    }


def normalize_reasoning_payload(payload: dict[str, Any], *, symbol: str, reasoning_input: dict[str, Any]) -> dict[str, Any]:
    fallback = build_local_reasoning_fallback(symbol=symbol, reasoning_input=reasoning_input)
    if not isinstance(payload, dict):
        return fallback
    normalized = {**fallback, **payload}
    normalized["version"] = str(normalized.get("version") or AI_STRUCTURE_REASONING_PROMPT_VERSION)
    normalized["symbol"] = symbol
    normalized["scenario_branches"] = payload.get("scenario_branches") if isinstance(payload.get("scenario_branches"), list) else fallback["scenario_branches"]
    normalized["key_boundaries"] = payload.get("key_boundaries") if isinstance(payload.get("key_boundaries"), list) else fallback["key_boundaries"]
    normalized["risk_notes"] = payload.get("risk_notes") if isinstance(payload.get("risk_notes"), list) else fallback["risk_notes"]
    return normalized


def _primary_level(levels: dict[str, Any]) -> str:
    for level in ("5", "30", "day", "week"):
        center = (levels.get(level) or {}).get("active_center") or {}
        if _num(center.get("zg")) > 0 and _num(center.get("zd")) > 0:
            return level
    return next(iter(levels.keys()), "")


def _trigger_level(levels: dict[str, Any], primary: str) -> str:
    order = ["week", "day", "30", "5"]
    if primary not in order:
        return "5" if "5" in levels else primary
    lower = order[order.index(primary) + 1 :]
    for level in reversed(lower):
        if level in levels:
            return level
    return primary


def _chart_focus(evidence: dict[str, Any], keys: list[str]) -> list[str]:
    return [str(evidence[key]) for key in keys if evidence.get(key)]


def _data_as_of(reasoning_input: dict[str, Any]) -> str:
    snapshots = ((reasoning_input.get("structure_facts") or {}).get("snapshots") or [])
    dates = [str(item.get("data_as_of") or "") for item in snapshots if item.get("data_as_of")]
    return max(dates) if dates else ""


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
