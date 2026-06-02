"""Unified AI Native reasoning service.

This service turns the tested one-call reasoning script into a reusable V5
runtime path. It consumes persisted CZSC snapshots, nearby pressure/support
clusters, and user position context, then stores the full LLM answer as the
single source for panel summaries and chat context.
"""

from __future__ import annotations

import json
import re
from typing import Any

from server import config
from server.db.database import get_connection
from server.domain.symbols import normalize_symbol, symbol_aliases
from server.engines.ai_native.chan_signal_digest import build_chan_signal_digest
from server.engines.ai_native.czsc_snapshot_service import (
    DEFAULT_COMPUTE_PROFILE,
    DEFAULT_LEVELS,
    now_text,
    stable_hash,
)
from server.engines.ai_native.dynamics_hydrator import hydrate_dynamics
from server.engines.ai_native.index_sector_context_hydrator import hydrate_index_sector_context
from server.engines.ai_native.market_task_context_hydrator import hydrate_market_task_context
from server.engines.ai_native.practical_evidence_hydrator import hydrate_practical_evidence
from server.engines.ai_native.reasoning_continuity_service import build_reasoning_continuity_context
from server.engines.ai_native.structure_context_service import (
    _boundary_payload,
    save_ai_structure_context,
    save_reasoning_run,
)
from server.engines.structure.structure_key import normalize_freq
from server.engines.structure.canonical_structure_service import get_latest_structure
from server.services.intraday_observation_service import get_intraday_observation, get_intraday_observation_snapshot
from server.services.llm_service import AIModelRoute, LLMService


UNIFIED_REASONING_VERSION = "unified_reasoning.v2"
UNIFIED_FULL_TEXT_VERSION = f"{UNIFIED_REASONING_VERSION}.full_text"
LEGACY_UNIFIED_REASONING_VERSIONS = {"unified_reasoning.v1"}
LEGACY_UNIFIED_FULL_TEXT_VERSIONS = {f"{version}.full_text" for version in LEGACY_UNIFIED_REASONING_VERSIONS}
ALL_UNIFIED_REASONING_VERSIONS = {UNIFIED_REASONING_VERSION, *LEGACY_UNIFIED_REASONING_VERSIONS}
ALL_UNIFIED_FULL_TEXT_VERSIONS = {UNIFIED_FULL_TEXT_VERSION, *LEGACY_UNIFIED_FULL_TEXT_VERSIONS}
RESONANCE_OVERLAP_THRESHOLD = 0.015
CHAN_SIGNAL_MARKERS = (
    "五笔",
    "三笔",
    "七笔",
    "九笔",
    "十一笔",
    "背驰",
    "分型",
    "三买",
    "三卖",
    "第二买卖点",
    "BS3",
    "BUY",
    "SELL",
    "BE辅助",
)

SYSTEM_PROMPT = """你是用户的盘中盯盘搭档，熟悉缠论、多级别联立、买卖点转化和实盘节奏。

请基于第一阶段完整推演、当前盘中数据、压力支撑、动力学指标、市场/板块背景和用户持仓，重新推演当下走势。

不要用 JSON，不要机械分条，也不要只罗列支撑压力。请用自然短段落，像盘中高手在解释走势一样，把以下内容自然融入推演中：
大盘、板块、概念是否形成背景共振；
当前这一笔在什么级别、什么方向，可能先走到哪里；
到目标区后，回调或反抽要看哪里；
当前是否已经形成可参与的买点或卖点；
如果还没有，差哪个低级别买卖点确认；
低级别买卖点对上一级别结构意味着什么；
买卖点成立后会推进成什么结构，失败后会转化成什么结构；
结合用户持仓成本，说明此刻最该盯什么。

如果引用中枢、压力、支撑或目标点位，必须说明这个点位属于哪个级别、什么角色；不要混用旧支撑和当前主战场中枢。
卡片文案要具体到关键转化条件，不要只写“失败则震荡或再探支撑”。

最后单独输出两行卡片文案：
第一行：现在在验证什么。
第二行：成立怎样，失败怎样。

chan_signal_digest 是 CZSC 原生辅助证据，不是最终裁决；若它与笔序列、动力状态、压力支撑冲突，需要说明冲突点。
intraday_observation 是盘中观察层，不是正式结构确认；数据里的 source、as_of、coverage、bar_status 表示事实来源和新鲜度，请自行权衡。
reasoning_continuity_context 是上一轮推演、触发状态、用户近期观察和历史结果的事实集合，不是规则；请结合当前结构与盘中观察自行判断原推演是延续、触发、增强、减弱还是失效。
market_task_context 是走势任务、压力语义、量能阶段和小转大观察事实，不是规则；请用它理解当前走势正在完成什么任务，但不要被它替代你的综合判断。
market_task_context.macro_phase 是大级别阶段背景；practical_evidence.divergence_evidence.impulse_exhaustion_context 是当前同向笔的动能释放上下文。二者都是事实参考，不是结论规则。
index_sector_context 是市场指数和主板块相对强弱背景，不是规则；请只把它作为判断个股走势是否有市场/板块共振的证据。
index_sector_context.concept_context 是题材概念背景，只用于判断市场活跃方向和合力来源，不要把概念标签当作结构结论或交易依据。

仅供参考，不构成投资建议。"""

WATCHBOARD_EXTRACT_PROMPT = """从完整推演中提取盯盘状态机，返回 JSON：
{
  "card_summary": "优先使用完整推演最后两行卡片文案的第一行，不超过28个中文字符",
  "card_secondary": "优先使用完整推演最后两行卡片文案的第二行，不超过46个中文字符",
  "card_action": "结合持仓状态的短标签，不超过6个中文字符",
  "watch_state_machine": {
    "version": "watch_state_machine.v1",
    "current_state": {
      "name": "当前结构状态，不超过18个中文字符",
      "level": "主要观察级别，例如5分钟/30分钟",
      "range": [下沿价格, 上沿价格] 或 null,
      "display": "卡片默认显示，不超过28个中文字符"
    },
    "transitions": [
      {
        "id": "up_break|down_break|pressure_test|support_test|pullback|rebound",
        "trigger": {"type": "price_above|price_below", "level": 价格数字},
        "next_state": "触发后进入的状态，不超过18个中文字符",
        "observe": "触发后当下看什么，不超过30个中文字符",
        "success": "如果这个连续动作成功，下一步看什么，不超过34个中文字符",
        "failure": "如果这个连续动作失败，下一步看什么，不超过34个中文字符",
        "next_watch": "后续继续盯什么，不超过34个中文字符"
      }
    ]
  }
}
要求：
- 只抽完整推演已经明确给出的结构、价格和连续动作，不要编价格。
- 优先从完整推演最后“两行卡片文案”抽 card_summary、当前状态和关键转化条件。
- transitions 最多 6 个；状态不限制为三买/三卖，也可以是一买、二买、类买卖点、中枢震荡、离开段、回拉、反抽等。
- trigger.level 必须是价格，不是级别数字；严禁把“5分钟/30分钟”里的 5 或 30 当成价格。
- card_summary 和 transitions 都要围绕“触发什么后显示什么、成功/失败后继续看什么”，不要写“关注”这种空话。
- 如果完整推演说明某个价位属于旧支撑、当前主战场中枢、目标区、回调确认位或失败位，抽取时必须保留这个角色语义，不能改写成泛泛的“压力/支撑”。
- 不要输出买入、卖出、加仓、减仓、清仓、止损等下单命令；可以使用观察、确认、转弱、失效、防守等描述。
- 只返回 JSON。"""

WATCH_STATE_MACHINE_VERSION = "watch_state_machine.v1"
WATCH_STATE_TRADING_WORDS = ("买入", "卖出", "加仓", "减仓", "清仓", "止损", "满仓", "重仓")


async def trigger_unified_reasoning(
    *,
    user_id: int,
    symbol: str,
    levels: list[str] | None = None,
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
) -> dict[str, Any]:
    """Run the single-call reasoning path and persist its full answer."""
    canonical = normalize_symbol(symbol)
    payload = build_unified_reasoning_input(
        user_id=user_id,
        symbol=canonical,
        levels=levels or list(DEFAULT_LEVELS),
        compute_profile=compute_profile,
    )
    service = LLMService()
    route = AIModelRoute(
        thinking_enabled=getattr(config, "AI_NATIVE_THINKING_ENABLED", True),
        reasoning_effort=getattr(config, "AI_NATIVE_REASONING_EFFORT", "high"),
        timeout_seconds=max(float(getattr(config, "AI_NATIVE_LLM_TIMEOUT", 150)), 150),
        max_tokens=max(int(getattr(config, "AI_NATIVE_MAX_TOKENS", 4096)), 4096),
    )
    user_message = (
        f"以下是 {canonical} 的完整数据，请给出第二阶段综合推演：\n\n"
        f"{json.dumps(payload['input'], ensure_ascii=False, indent=2)}"
    )
    full_text = await service.infer_ai_native_markdown(
        SYSTEM_PROMPT,
        user_message,
        user_id=user_id,
        model_route=route,
    )
    full_text = str(full_text or "").strip()
    if not full_text:
        raise RuntimeError("Unified reasoning returned empty content")
    watchboard_payload = await extract_watchboard_payload(
        full_text,
        user_id=user_id,
        position_context=(payload.get("input") or {}).get("position_context") or {},
    )
    return save_unified_reasoning_result(
        user_id=user_id,
        symbol=canonical,
        payload=payload,
        full_text=full_text,
        model_name=route.model_name,
        watchboard_payload=watchboard_payload,
    )


def build_unified_reasoning_input(
    *,
    user_id: int,
    symbol: str,
    levels: list[str] | None = None,
    compute_profile: str = DEFAULT_COMPUTE_PROFILE,
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    normalized_levels = [normalize_freq(level) for level in (levels or list(DEFAULT_LEVELS))]
    snapshots: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    missing_levels: list[str] = []
    for level in normalized_levels:
        row = get_latest_structure(
            symbol=canonical,
            level=level,
            min_profile=compute_profile,
            allow_bootstrap=False,
        )
        if not row:
            missing_levels.append(level)
            continue
        snapshots[level] = row
        rows.append(row)
    if not rows:
        raise ValueError("NO_SNAPSHOT")

    level_names = {"week": "周线", "day": "日线", "30": "30分钟", "5": "5分钟"}
    structure = {
        level_names.get(level, level): _extract_structure_for_llm(snapshots[level], level_names.get(level, level))
        for level in normalized_levels
        if level in snapshots
    }
    structure_geometry = {
        level_names.get(level, level): _hydrate_structure_geometry(snapshots[level])
        for level in normalized_levels
        if level in snapshots
    }
    momentum_dynamics = {
        level_names.get(level, level): hydrate_dynamics((snapshots[level].get("snapshot") or {}).get("klines") or [])
        for level in normalized_levels
        if level in snapshots
    }
    intraday_observation = _intraday_observation(canonical)
    snapshot_price = _current_price(snapshots)
    current_price = _intraday_price(intraday_observation) or snapshot_price
    source_snapshot_ids = [item["snapshot_id"] for item in rows]
    data_freshness = _data_freshness(
        snapshots=snapshots,
        intraday_observation=intraday_observation,
        missing_levels=missing_levels,
        current_price=current_price,
        snapshot_price=snapshot_price,
    )
    pressure_support = _compute_pressure_support(snapshots)
    nearby_pressure_support = _add_pressure_support_semantics(pressure_support, structure_geometry)
    resonance_evidence = _compute_resonance_evidence(
        current_price=current_price,
        structure_geometry=structure_geometry,
        pressure_support=nearby_pressure_support,
    )
    practical_evidence = hydrate_practical_evidence(
        snapshots,
        pressure_support=nearby_pressure_support,
        level_names=level_names,
    )
    reasoning_continuity_context = build_reasoning_continuity_context(
        user_id=user_id,
        symbol=canonical,
        current_price=current_price,
        intraday_observation=intraday_observation,
        prompt_versions=ALL_UNIFIED_FULL_TEXT_VERSIONS,
    )
    market_task_context = hydrate_market_task_context(
        current_price=current_price,
        structure_geometry=structure_geometry,
        momentum_dynamics=momentum_dynamics,
        intraday_observation=intraday_observation,
        nearby_pressure_support=nearby_pressure_support,
        reasoning_continuity_context=reasoning_continuity_context,
    )
    index_sector_context = _index_sector_context(canonical)
    chan_signal_digest = build_chan_signal_digest(snapshots, level_names=level_names)
    chan_signals = _collect_chan_signals(snapshots, level_names)
    position_context = _position_context(user_id=user_id, symbol=canonical, current_price=current_price)
    full_input = {
        "symbol": canonical,
        "current_price": current_price,
        "current_price_source": data_freshness["current_price_source"],
        "data_as_of": _data_as_of(snapshots),
        "data_freshness": data_freshness,
        "structure_snapshot": _structure_snapshot_manifest(snapshots),
        "first_stage_reasoning": structure,
        "structure_geometry": structure_geometry,
        "momentum_dynamics": momentum_dynamics,
        "nearby_pressure_support": nearby_pressure_support,
        "resonance_evidence": resonance_evidence,
        "practical_evidence": practical_evidence,
        "intraday_observation": intraday_observation,
        "reasoning_continuity_context": reasoning_continuity_context,
        "market_task_context": market_task_context,
        "index_sector_context": index_sector_context,
        "chan_signal_digest": chan_signal_digest,
        "chan_signals": chan_signals,
        "position_context": position_context,
        # 旧字段保留给前端、测试脚本和历史消费方，语义等同第一阶段结构参考。
        "structure": structure,
        "pressure_support": nearby_pressure_support,
        "my_position": position_context,
    }
    return {
        "version": UNIFIED_REASONING_VERSION,
        "symbol": canonical,
        "levels": normalized_levels,
        "missing_levels": missing_levels,
        "source_snapshot_ids": source_snapshot_ids,
        "snapshots": rows,
        "input": full_input,
    }


def _index_sector_context(symbol: str) -> dict[str, Any]:
    """Best-effort market / sector background; never required for formal reasoning."""
    try:
        return hydrate_index_sector_context(symbol)
    except Exception:
        return {}


def _intraday_observation(symbol: str) -> dict[str, Any]:
    """Best-effort intraday preview; never required for formal reasoning."""
    try:
        import asyncio

        asyncio.get_running_loop()
        return get_intraday_observation_snapshot(symbol)
    except RuntimeError:
        pass
    except Exception:
        return {}
    try:
        import asyncio

        return asyncio.run(get_intraday_observation(symbol))
    except Exception:
        return {}


def save_unified_reasoning_result(
    *,
    user_id: int,
    symbol: str,
    payload: dict[str, Any],
    full_text: str,
    model_name: str = "",
    monitor_conditions: dict[str, Any] | None = None,
    watchboard_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    source_snapshot_ids = payload.get("source_snapshot_ids") or []
    normalized_watchboard = normalize_watchboard_payload(
        watchboard_payload or {"triggers": (monitor_conditions or {}).get("triggers") or []},
        fallback_summary=summarize_unified_reasoning(full_text),
    )
    summary = summarize_unified_reasoning(full_text)
    generated_at = now_text()
    summary_payload = {
        "coach_summary": summary,
        "card_summary": normalized_watchboard["card_summary"],
        "card_secondary": normalized_watchboard["card_secondary"],
        "card_action": normalized_watchboard["card_action"],
        "extract_status": normalized_watchboard["extract_status"],
        "extract_error": normalized_watchboard["extract_error"],
        "watch_state_machine": normalized_watchboard["watch_state_machine"],
        "watch_plan": normalized_watchboard["watch_plan"],
        "version": UNIFIED_REASONING_VERSION,
        "monitor_conditions": normalized_watchboard["monitor_conditions"],
        "data_as_of": (payload.get("input") or {}).get("data_as_of") or "",
        "generated_at": generated_at,
    }
    run = save_reasoning_run(
        user_id=user_id,
        symbol=canonical,
        source_snapshot_ids=source_snapshot_ids,
        prompt_version=UNIFIED_FULL_TEXT_VERSION,
        think_model=model_name,
        summary_model=getattr(config, "LLM_MODEL", ""),
        status="SUCCESS",
        full_reasoning_text=full_text,
        summary=summary_payload,
        error_message="",
    )
    boundary = _boundary_payload(payload.get("snapshots") or [])
    reasoning = {
        "version": UNIFIED_REASONING_VERSION,
        "structure_summary": summary,
        "coach_summary": summary,
        "front_panel_text": normalized_watchboard["card_summary"] or summary,
        "card_summary": normalized_watchboard["card_summary"],
        "card_secondary": normalized_watchboard["card_secondary"],
        "card_action": normalized_watchboard["card_action"],
        "extract_status": normalized_watchboard["extract_status"],
        "extract_error": normalized_watchboard["extract_error"],
        "watch_state_machine": normalized_watchboard["watch_state_machine"],
        "watch_plan": normalized_watchboard["watch_plan"],
        "pressure_support": (payload.get("input") or {}).get("nearby_pressure_support")
        or (payload.get("input") or {}).get("pressure_support")
        or [],
        "reasoning_meta": {
            "provider": "llm",
            "llm_status": "success",
            "pipeline": "unified_single_llm",
            "full_reasoning_run_id": run["run_id"],
            "full_reasoning_available": True,
        },
    }
    fingerprint = stable_hash({
        "user_id": int(user_id),
        "symbol": canonical,
        "version": UNIFIED_REASONING_VERSION,
        "source_snapshot_ids": source_snapshot_ids,
    })
    context = save_ai_structure_context(
        user_id=user_id,
        symbol=canonical,
        prompt_version=UNIFIED_REASONING_VERSION,
        context_fingerprint=fingerprint,
        source_snapshot_ids=source_snapshot_ids,
        raw_context=payload.get("input") or {},
        reasoning=reasoning,
        background={"source": "unified_reasoning_service"},
        boundary=boundary,
        summary_text=summary,
        main_level=boundary.get("primary_level") or "",
        trigger_level=boundary.get("primary_level") or "",
        coach_summary=summary,
    )
    save_reasoning_run(
        user_id=user_id,
        symbol=canonical,
        source_snapshot_ids=source_snapshot_ids,
        prompt_version=UNIFIED_FULL_TEXT_VERSION,
        think_model=model_name,
        summary_model=getattr(config, "LLM_MODEL", ""),
        status="SUCCESS",
        full_reasoning_text=full_text,
        summary=summary_payload,
        error_message="",
        context_id=context["context_id"],
    )
    return {
        "symbol": canonical,
        "context_id": context["context_id"],
        "run_id": run["run_id"],
        "summary": summary,
        "card_summary": normalized_watchboard["card_summary"],
        "card_secondary": normalized_watchboard["card_secondary"],
        "card_action": normalized_watchboard["card_action"],
        "extract_status": normalized_watchboard["extract_status"],
        "extract_error": normalized_watchboard["extract_error"],
        "watch_state_machine": normalized_watchboard["watch_state_machine"],
        "watch_plan": normalized_watchboard["watch_plan"],
        "monitor_conditions": summary_payload["monitor_conditions"],
        "full_text": full_text,
        "source_snapshot_ids": source_snapshot_ids,
        "data_as_of": (payload.get("input") or {}).get("data_as_of") or "",
        "updated_at": context.get("updated_at") or now_text(),
    }


async def extract_watchboard_payload(
    full_reasoning_text: str,
    *,
    user_id: int,
    position_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract compact watchboard card fields from the full reasoning text."""
    text = str(full_reasoning_text or "").strip()
    if not text:
        return {"card_summary": "", "card_action": "", "triggers": []}
    service = LLMService()
    route = AIModelRoute(
        model_name=getattr(config, "LLM_MODEL", ""),
        thinking_enabled=False,
        reasoning_effort="",
        timeout_seconds=45,
        max_tokens=2200,
    )
    user_message = json.dumps(
        {
            "position_context": position_context or {},
            "full_reasoning_text": text[:12000],
        },
        ensure_ascii=False,
    )
    try:
        payload = await service.infer_ai_native_json(
            WATCHBOARD_EXTRACT_PROMPT,
            user_message,
            user_id=user_id,
            model_route=route,
        )
    except Exception as exc:
        return {
            "card_summary": "",
            "card_action": "",
            "triggers": [],
            "extract_status": "failed",
            "extract_error": str(exc)[:240],
        }
    if isinstance(payload, dict):
        payload.setdefault("extract_status", "success")
        return payload
    return {
        "card_summary": "",
        "card_action": "",
        "triggers": [],
        "extract_status": "failed",
        "extract_error": "LLM returned non-object watchboard payload",
    }


async def extract_monitor_conditions(full_reasoning_text: str, *, user_id: int) -> dict[str, Any]:
    """Backward-compatible trigger extraction API."""
    payload = await extract_watchboard_payload(full_reasoning_text, user_id=user_id, position_context={})
    return normalize_monitor_conditions(payload)


def normalize_watchboard_payload(payload: dict[str, Any] | None, *, fallback_summary: str = "") -> dict[str, Any]:
    """Normalize AI-extracted card fields without turning the card into a rule engine."""
    raw = payload or {}
    watch_state_machine = normalize_watch_state_machine(raw)
    extract_status = _normalize_extract_status(raw, watch_state_machine)
    watch_plan = normalize_watch_plan(
        raw,
        fallback_summary=fallback_summary,
        watch_state_machine=watch_state_machine,
        extract_status=extract_status,
    )
    card_summary = re.sub(
        r"\s+",
        "",
        str(raw.get("card_summary") or (watch_plan.get("card") or {}).get("summary") or ""),
    ).strip()
    if not card_summary:
        card_summary = str(fallback_summary or "").strip()
    card_secondary = re.sub(
        r"\s+",
        "",
        str(
            raw.get("card_secondary")
            or ((watch_plan.get("card") or {}).get("secondary") if isinstance(watch_plan.get("card"), dict) else "")
            or "",
        ),
    ).strip()
    card_action = re.sub(
        r"\s+",
        "",
        str(raw.get("card_action") or (watch_plan.get("card") or {}).get("action") or ""),
    ).strip()
    card_action = _normalize_watchboard_action(card_action)
    return {
        "card_summary": card_summary[:42],
        "card_secondary": card_secondary[:64],
        "card_action": card_action[:8],
        "extract_status": extract_status,
        "extract_error": str(raw.get("extract_error") or "").strip()[:240],
        "watch_state_machine": watch_state_machine,
        "monitor_conditions": normalize_monitor_conditions(raw),
        "watch_plan": watch_plan,
    }


def normalize_watch_state_machine(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize the compact AI state machine used by watch cards."""
    raw = payload or {}
    machine = raw.get("watch_state_machine") if isinstance(raw.get("watch_state_machine"), dict) else {}
    current = machine.get("current_state") if isinstance(machine.get("current_state"), dict) else {}
    raw_range = current.get("range") if isinstance(current.get("range"), list) else []
    state_range: list[float] | None = None
    if len(raw_range) >= 2:
        low = _num(raw_range[0])
        high = _num(raw_range[1])
        if low > 0 and high > 0 and low != high:
            state_range = [round(min(low, high), 4), round(max(low, high), 4)]

    transitions = []
    raw_transitions = machine.get("transitions") if isinstance(machine.get("transitions"), list) else []
    for item in raw_transitions:
        if not isinstance(item, dict):
            continue
        raw_trigger = item.get("trigger") if isinstance(item.get("trigger"), dict) else {}
        trigger_type = _watch_level_trigger_type(raw_trigger)
        level = _num(raw_trigger.get("level"))
        if trigger_type not in {"price_above", "price_below"} or level <= 0:
            continue
        normalized_item = {
            "id": re.sub(r"[^a-zA-Z0-9_-]", "", str(item.get("id") or f"s{len(transitions) + 1}"))[:24],
            "trigger": {"type": trigger_type, "level": round(level, 4)},
            "next_state": _clean_watch_state_text(item.get("next_state"), 18),
            "observe": _clean_watch_state_text(item.get("observe"), 30),
            "success": _clean_watch_state_text(item.get("success"), 34),
            "failure": _clean_watch_state_text(item.get("failure"), 34),
            "next_watch": _clean_watch_state_text(item.get("next_watch"), 34),
        }
        if not any(normalized_item[field] for field in ("next_state", "observe", "success", "failure", "next_watch")):
            continue
        transitions.append(normalized_item)
        if len(transitions) >= 6:
            break

    current_state = {
        "name": _clean_watch_state_text(current.get("name"), 18),
        "level": re.sub(r"\s+", "", str(current.get("level") or "")).strip()[:12],
        "range": state_range,
        "display": _clean_watch_state_text(current.get("display"), 48),
    }
    if not transitions and not any(value for value in current_state.values()):
        return {}
    return {
        "version": WATCH_STATE_MACHINE_VERSION,
        "current_state": current_state,
        "transitions": transitions,
    }


def _clean_watch_state_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", "", str(value or "")).strip()
    for word in WATCH_STATE_TRADING_WORDS:
        text = text.replace(word, "")
    return text[:limit]


def _normalize_extract_status(raw: dict[str, Any], watch_state_machine: dict[str, Any]) -> str:
    status = str(raw.get("extract_status") or "").strip().lower()
    if status in {"failed", "empty"}:
        return status
    if watch_state_machine:
        return "success"
    if raw:
        return "missing_state_machine"
    return "empty"


def normalize_watch_plan(
    payload: dict[str, Any] | None,
    *,
    fallback_summary: str = "",
    watch_state_machine: dict[str, Any] | None = None,
    extract_status: str = "",
) -> dict[str, Any]:
    """Normalize the AI watch plan while keeping it as observation data."""
    raw = payload or {}
    plan = raw.get("watch_plan") if isinstance(raw.get("watch_plan"), dict) else {}
    raw_card = plan.get("card") if isinstance(plan.get("card"), dict) else {}
    card_summary = re.sub(
        r"\s+",
        "",
        str(raw_card.get("summary") or raw.get("card_summary") or fallback_summary or ""),
    ).strip()[:42]
    card_secondary = re.sub(
        r"\s+",
        "",
        str(raw_card.get("secondary") or raw.get("card_secondary") or ""),
    ).strip()[:64]
    card_action = _normalize_watchboard_action(str(raw_card.get("action") or raw.get("card_action") or "").strip())[:8]
    key_levels = _normalize_watch_key_levels(plan, raw)
    return {
        "version": "watch_plan.v1",
        "main_task": re.sub(r"\s+", "", str(plan.get("main_task") or "")).strip()[:60],
        "card": {
            "summary": card_summary,
            "secondary": card_secondary,
            "action": card_action,
        },
        "watch_chain": _normalize_watch_chain(plan.get("watch_chain") if isinstance(plan.get("watch_chain"), dict) else {}),
        "watch_state_machine": watch_state_machine or normalize_watch_state_machine(raw),
        "extract_status": extract_status or _normalize_extract_status(raw, watch_state_machine or normalize_watch_state_machine(raw)),
        "key_levels": key_levels,
        "t_plan": _normalize_t_plan(plan.get("t_plan") if isinstance(plan.get("t_plan"), dict) else {}),
        "recheck_policy": _normalize_recheck_policy(
            plan.get("recheck_policy") if isinstance(plan.get("recheck_policy"), dict) else {}
        ),
    }


def _normalize_watch_chain(raw_chain: dict[str, Any]) -> dict[str, Any]:
    steps = []
    raw_steps = raw_chain.get("steps") if isinstance(raw_chain.get("steps"), list) else []
    allowed_points = {
        "一买",
        "二买",
        "三买",
        "类二买",
        "类三买",
        "一卖",
        "二卖",
        "三卖",
        "类二卖",
        "类三卖",
        "中枢震荡",
        "无",
    }
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        raw_trigger = item.get("trigger") if isinstance(item.get("trigger"), dict) else {}
        trigger_type = _watch_level_trigger_type(raw_trigger)
        level = _num(raw_trigger.get("level"))
        if trigger_type not in {"price_above", "price_below"} or level <= 0:
            continue
        buy_sell_point = re.sub(r"\s+", "", str(item.get("buy_sell_point") or "")).strip()
        if buy_sell_point not in allowed_points:
            buy_sell_point = ""
        steps.append(
            {
                "id": re.sub(r"[^a-zA-Z0-9_-]", "", str(item.get("id") or f"s{len(steps) + 1}"))[:24],
                "trigger": {"type": trigger_type, "level": round(level, 4)},
                "display": re.sub(r"\s+", "", str(item.get("display") or "")).strip()[:42],
                "then_watch": re.sub(r"\s+", "", str(item.get("then_watch") or "")).strip()[:48],
                "fail_watch": re.sub(r"\s+", "", str(item.get("fail_watch") or "")).strip()[:48],
                "buy_sell_point": buy_sell_point,
            }
        )
        if len(steps) >= 4:
            break
    return {
        "version": "watch_chain.v1",
        "level": re.sub(r"\s+", "", str(raw_chain.get("level") or "5分钟")).strip()[:12],
        "base_state": re.sub(r"\s+", "", str(raw_chain.get("base_state") or "")).strip()[:36],
        "base_display": re.sub(r"\s+", "", str(raw_chain.get("base_display") or "")).strip()[:42],
        "steps": steps,
    }


def normalize_monitor_conditions(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Keep monitor trigger JSON small, deterministic, and UI-safe."""
    allowed_actions = {
        "关注",
        "观望",
        "继续观望",
        "重点跟踪",
        "等待回踩",
        "考虑建仓",
        "继续持有",
        "考虑加仓",
        "考虑减仓",
        "考虑止损",
        "考虑锁利",
        "收紧防守",
    }
    normalized: list[dict[str, Any]] = []
    raw_triggers = _monitor_trigger_candidates(payload or {})
    if not isinstance(raw_triggers, list):
        return {"triggers": []}
    for raw in raw_triggers:
        if not isinstance(raw, dict):
            continue
        trigger_type = str(raw.get("type") or "").strip()
        if trigger_type not in {"price_below", "price_above"}:
            continue
        level = _num(raw.get("level"))
        if level <= 0:
            continue
        action = _normalize_monitor_action(str(raw.get("action_on_trigger") or "关注").strip())
        if action not in allowed_actions:
            action = "关注"
        message = re.sub(r"\s+", "", str(raw.get("message_on_trigger") or "")).strip()
        if not message:
            message = "触发关键位"
        if _is_semantically_invalid_monitor_trigger(trigger_type, action, message):
            continue
        normalized.append({
            "id": f"t{len(normalized) + 1}",
            "type": trigger_type,
            "level": round(level, 4),
            "message_on_trigger": message[:15],
            "action_on_trigger": action,
        })
        if len(normalized) >= 4:
            break
    return {"triggers": normalized}


def _monitor_trigger_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_triggers = payload.get("triggers")
    if isinstance(raw_triggers, list) and raw_triggers:
        return raw_triggers
    plan = payload.get("watch_plan") if isinstance(payload.get("watch_plan"), dict) else {}
    machine = payload.get("watch_state_machine") if isinstance(payload.get("watch_state_machine"), dict) else {}
    if not machine and isinstance(plan.get("watch_state_machine"), dict):
        machine = plan.get("watch_state_machine") or {}
    levels = plan.get("key_levels") if isinstance(plan.get("key_levels"), list) else []
    chain = plan.get("watch_chain") if isinstance(plan.get("watch_chain"), dict) else {}
    candidates = []
    for item in machine.get("transitions") or []:
        if not isinstance(item, dict):
            continue
        raw_trigger = item.get("trigger") if isinstance(item.get("trigger"), dict) else {}
        trigger_type = _watch_level_trigger_type(raw_trigger)
        price = _num(raw_trigger.get("level"))
        if not trigger_type or price <= 0:
            continue
        message = str(item.get("observe") or item.get("next_watch") or item.get("next_state") or "结构触发")
        candidates.append(
            {
                "type": trigger_type,
                "level": price,
                "message_on_trigger": message,
                "action_on_trigger": "关注",
            }
        )
    for item in chain.get("steps") or []:
        if not isinstance(item, dict):
            continue
        raw_trigger = item.get("trigger") if isinstance(item.get("trigger"), dict) else {}
        trigger_type = _watch_level_trigger_type(raw_trigger)
        price = _num(raw_trigger.get("level"))
        if not trigger_type or price <= 0:
            continue
        message = str(item.get("display") or item.get("then_watch") or "触发关键位")
        candidates.append(
            {
                "type": trigger_type,
                "level": price,
                "message_on_trigger": message,
                "action_on_trigger": item.get("action_on_trigger") or "关注",
            }
        )
    for item in levels:
        if not isinstance(item, dict):
            continue
        trigger_type = _watch_level_trigger_type(item)
        price = _num(item.get("price"))
        if not trigger_type or price <= 0:
            continue
        message = str(item.get("shape_to_watch") or item.get("meaning") or "触发关键位")
        candidates.append(
            {
                "type": trigger_type,
                "level": price,
                "message_on_trigger": message,
                "action_on_trigger": item.get("action_on_trigger") or "关注",
            }
        )
    return candidates


def _normalize_watch_key_levels(plan: dict[str, Any], raw: dict[str, Any]) -> list[dict[str, Any]]:
    raw_levels = plan.get("key_levels") if isinstance(plan.get("key_levels"), list) else []
    if not raw_levels and isinstance(raw.get("triggers"), list):
        raw_levels = [
            {
                "price": item.get("level"),
                "trigger": item.get("type"),
                "shape_to_watch": item.get("message_on_trigger"),
                "meaning": item.get("message_on_trigger"),
            }
            for item in raw["triggers"]
            if isinstance(item, dict)
        ]
    result = []
    for item in raw_levels:
        if not isinstance(item, dict):
            continue
        price = _num(item.get("price"))
        if price <= 0:
            continue
        trigger_type = _watch_level_trigger_type(item)
        if not trigger_type:
            continue
        side = "up" if trigger_type == "price_above" else "down"
        level_type = str(item.get("type") or "").strip()
        if level_type not in {"pressure", "support", "confirm", "invalidate", "t_watch", "reentry"}:
            level_type = "pressure" if trigger_type == "price_above" else "support"
        result.append(
            {
                "id": f"k{len(result) + 1}",
                "price": round(price, 4),
                "side": side,
                "type": level_type,
                "shape_to_watch": re.sub(r"\s+", "", str(item.get("shape_to_watch") or "")).strip()[:32],
                "meaning": re.sub(r"\s+", "", str(item.get("meaning") or "")).strip()[:40],
                "trigger": trigger_type,
                "ai_review_when": re.sub(r"\s+", "", str(item.get("ai_review_when") or "")).strip()[:48],
            }
        )
        if len(result) >= 4:
            break
    return result


def _watch_level_trigger_type(item: dict[str, Any]) -> str:
    trigger = str(item.get("trigger") or item.get("type") or "").strip()
    if trigger in {"price_above", "price_below"}:
        return trigger
    side = str(item.get("side") or "").strip()
    if side == "up":
        return "price_above"
    if side == "down":
        return "price_below"
    level_type = str(item.get("type") or "").strip()
    if level_type in {"pressure", "confirm", "t_watch"}:
        return "price_above"
    if level_type in {"support", "invalidate", "reentry"}:
        return "price_below"
    return ""


def _normalize_t_plan(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(raw.get("enabled")),
        "condition": str(raw.get("condition") or "").strip()[:80],
        "watch_price": round(_num(raw.get("watch_price")), 4) if _num(raw.get("watch_price")) > 0 else None,
        "reentry_area": str(raw.get("reentry_area") or "").strip()[:40],
        "risk": str(raw.get("risk") or "").strip()[:80],
    }


def _normalize_recheck_policy(raw: dict[str, Any]) -> dict[str, str]:
    return {
        "no_touch": str(raw.get("no_touch") or "不重推").strip()[:40],
        "near_key_level": str(raw.get("near_key_level") or "卡片轻量提示").strip()[:40],
        "touched_with_momentum_change": str(raw.get("touched_with_momentum_change") or "触发AI复核").strip()[:48],
    }


def _is_semantically_invalid_monitor_trigger(trigger_type: str, action: str, message: str) -> bool:
    """过滤把确认语义误提成单边价格触发的监控条件。"""
    action_kind = action.removeprefix("考虑")
    if trigger_type == "price_below":
        if action_kind == "加仓":
            return True
        if "突破失败" in message or "三买失败" in message:
            return False
        if any(marker in message for marker in ("不破", "三买确认", "确认三买", "站稳", "上破")):
            return True
        if "突破" in message and "跌破" not in message:
            return True
    if trigger_type == "price_above":
        if action_kind == "止损":
            return True
        if any(marker in message for marker in ("跌破", "失守", "破位", "转弱")):
            return True
    return False


def _normalize_monitor_action(action: str) -> str:
    """把交易动作统一成教练语气，避免前端显示成机械指令。"""
    raw_action = str(action or "").strip()
    action = _normalize_watchboard_action(raw_action)
    if action == "观望" and raw_action not in {"观望", "继续观望"}:
        return "关注"
    if action in {"加仓", "考虑加仓"}:
        return "考虑加仓"
    if action in {"减仓", "考虑减仓"}:
        return "考虑减仓"
    if action in {"止损", "考虑止损"}:
        return "考虑止损"
    return action


def _normalize_watchboard_action(action: str) -> str:
    action = str(action or "").strip()
    action = re.split(r"[，,。；;：:\\s]", action, maxsplit=1)[0].strip()
    if not action:
        return ""
    if re.search(r"\d|%|盈|亏|浮盈|浮亏|成本|仓位", action):
        return "持仓观望"
    if action in {"买入", "开仓", "建仓"}:
        return "考虑建仓"
    if action == "加仓":
        return "考虑加仓"
    if action == "减仓":
        return "考虑减仓"
    if action == "止损":
        return "考虑止损"
    if action == "卖出":
        return "考虑减仓"
    if action == "清仓":
        return "考虑止损"
    if action in {"持有", "继续持有"}:
        return "继续持有"
    if action in {"持仓观望", "持仓观察"}:
        return "持仓观望"
    allowed = {
        "观望",
        "观察",
        "关注",
        "继续观望",
        "重点跟踪",
        "等待确认",
        "等待回踩",
        "考虑建仓",
        "继续持有",
        "持仓观望",
        "考虑加仓",
        "考虑减仓",
        "考虑止损",
        "考虑做T",
        "等待接回",
        "风险收缩",
        "收紧防守",
    }
    return action if action in allowed else "观望"


def get_latest_unified_reasoning(*, user_id: int, symbol: str) -> dict[str, Any] | None:
    canonical = normalize_symbol(symbol)
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
              FROM ai_structure_reasoning_runs
             WHERE user_id = ? AND symbol = ? AND prompt_version = ? AND status = 'SUCCESS'
             ORDER BY updated_at DESC, id DESC
             LIMIT 1
            """,
            (int(user_id), canonical, UNIFIED_FULL_TEXT_VERSION),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["source_snapshot_ids"] = json.loads(data.pop("source_snapshot_ids_json") or "[]")
        data["summary"] = json.loads(data.pop("summary_json") or "{}")
        return data
    finally:
        conn.close()


def summarize_unified_reasoning(text: str, *, max_length: int = 96) -> str:
    source = re.sub(r"[*_`]+", "", str(text or "")).strip()
    bad_markers = ("收到数据", "看了数据", "开始", "请坐", "下面", "我的分析")
    heading_markers = (
        "当前走势在做什么",
        "第一阶段主线是否",
        "哪些价格和结构变化",
        "接下来最需要盯住",
        "第二阶段综合推演",
        "核心判断",
    )
    preferred_markers = ("当前", "核心", "结构", "走势", "中枢", "三买", "三卖", "回拉", "突破", "跌破", "观察")
    parts = [
        re.sub(r"^[#>*\\-\\d\\.、\\s📈🧭🔄👀【】]+", "", part).strip()
        for part in re.split(r"[。！？\n]", source)
        if part.strip()
    ]
    for part in parts:
        if any(marker in part for marker in bad_markers):
            continue
        if any(marker == part or marker in part[:24] for marker in heading_markers):
            continue
        if any(marker in part for marker in preferred_markers):
            return part[:max_length]
    for part in parts:
        if not any(marker in part for marker in bad_markers):
            if any(marker == part or marker in part[:24] for marker in heading_markers):
                continue
            return part[:max_length]
    return ""


def _extract_structure_for_llm(snapshot_data: dict[str, Any], level_name: str) -> dict[str, Any]:
    snap = snapshot_data.get("snapshot") or {}
    result = {
        "level": level_name,
        "data_as_of": snapshot_data.get("data_as_of") or "",
        "current_price": snap.get("price"),
        "last_bi_direction": snap.get("last_bi_dir"),
        "state_hint": snap.get("state_hint"),
    }
    active_zs = snap.get("active_zhongshu") or {}
    if active_zs:
        result["active_zhongshu"] = {
            "zg": active_zs.get("zg"),
            "zd": active_zs.get("zd"),
            "gg": active_zs.get("gg"),
            "dd": active_zs.get("dd"),
            "bi_count": active_zs.get("bi_count"),
            "begin_date": active_zs.get("begin_date"),
            "end_date": active_zs.get("end_date"),
        }
    if snap.get("price_vs_center"):
        result["price_vs_center"] = snap.get("price_vs_center")
    bis, unfinished_bi = _split_confirmed_and_unfinished_bis(snap)
    result["recent_bis"] = [
        {
            "direction": b.get("direction"),
            "start_price": b.get("start_price"),
            "end_price": b.get("end_price"),
            "high": b.get("high"),
            "low": b.get("low"),
            "bar_count": b.get("bar_count"),
            "is_sure": b.get("is_sure"),
        }
        for b in bis[-6:]
        if isinstance(b, dict)
    ]
    result["total_bi_count"] = len(bis)
    if isinstance(unfinished_bi, dict):
        result["current_unfinished_bi"] = {
            "direction": unfinished_bi.get("direction"),
            "start_price": unfinished_bi.get("start_price"),
            "end_price": unfinished_bi.get("end_price"),
            "high": unfinished_bi.get("high"),
            "low": unfinished_bi.get("low"),
            "bar_count": unfinished_bi.get("bar_count"),
            "is_sure": False,
            "status": unfinished_bi.get("status") or "ongoing",
        }
    zhongshus = snap.get("bi_zhongshus") or snap.get("zhongshus") or []
    if zhongshus:
        result["recent_zhongshus"] = [
            {
                "zg": z.get("zg"),
                "zd": z.get("zd"),
                "gg": z.get("gg"),
                "dd": z.get("dd"),
                "bi_count": z.get("bi_count"),
                "begin_date": z.get("begin_date"),
                "end_date": z.get("end_date"),
            }
            for z in zhongshus[-2:]
            if isinstance(z, dict)
        ]
    return result


def _hydrate_structure_geometry(snapshot_data: dict[str, Any]) -> dict[str, Any]:
    snap = snapshot_data.get("snapshot") or {}
    price = _num(snap.get("price"))
    active_zs = snap.get("active_zhongshu") or {}
    bis, unfinished_bi = _split_confirmed_and_unfinished_bis(snap)
    center = _center_fields(active_zs) if active_zs else {}
    if center:
        center["maturity"] = _center_maturity(center.get("bi_count"))
        center["maturity_note"] = _center_maturity_note(str(center["maturity"]))
        center["relevance"] = _center_relevance(price, center)
    return {
        "center": center,
        "price_position": _price_position(price, center.get("zg"), center.get("zd")) if center else {"position": "no_center"},
        "unfinished_bi": _bi_fields(unfinished_bi) if unfinished_bi else None,
        "recent_bis": [_bi_fields(item) for item in bis[-6:]],
        "total_confirmed_bi_count": len(bis),
    }


def _compute_pressure_support(snapshots: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    swing_points: list[dict[str, Any]] = []
    current_price = _current_price(snapshots)
    if current_price <= 0:
        return []
    for level, snap_data in snapshots.items():
        snap = snap_data.get("snapshot") or {}
        price = _num(snap.get("price")) or current_price
        bis, _unfinished_bi = _split_confirmed_and_unfinished_bis(snap)
        for bi in bis[-10:]:
            if not isinstance(bi, dict):
                continue
            high = _num(bi.get("high") or bi.get("end_price"))
            low = _num(bi.get("low") or bi.get("start_price"))
            if high > 0 and abs(high - price) / price < 0.15:
                swing_points.append({"price": high, "type": "high", "level": level})
            if low > 0 and abs(low - price) / price < 0.15:
                swing_points.append({"price": low, "type": "low", "level": level})
    if not swing_points:
        return []
    clusters: list[list[dict[str, Any]]] = []
    current = [sorted(swing_points, key=lambda item: item["price"])[0]]
    for point in sorted(swing_points, key=lambda item: item["price"])[1:]:
        if point["price"] / current[0]["price"] - 1 < 0.015:
            current.append(point)
        else:
            if len(current) >= 2:
                clusters.append(current)
            current = [point]
    if len(current) >= 2:
        clusters.append(current)
    result = []
    for cluster in clusters:
        prices = [item["price"] for item in cluster]
        zone_low = min(prices)
        zone_high = max(prices)
        center = (zone_low + zone_high) / 2
        distance_pct = round((center - current_price) / current_price * 100, 1)
        result.append({
            "zone": [round(zone_low, 4), round(zone_high, 4)],
            "type": "pressure" if center > current_price else "support",
            "status": "testing" if abs(distance_pct) < 1 else "holding",
            "source_levels": sorted({item["level"] for item in cluster}),
            "hit_count": len(cluster),
            "distance_pct": distance_pct,
        })
    return sorted(result, key=lambda item: abs(item["distance_pct"]))[:6]


def _add_pressure_support_semantics(
    clusters: list[dict[str, Any]],
    structure_geometry: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result = []
    for cluster in clusters:
        zone = cluster.get("zone") or []
        if len(zone) != 2:
            result.append(cluster)
            continue
        zone_center = (_num(zone[0]) + _num(zone[1])) / 2
        semantics = []
        for level_name, geometry in structure_geometry.items():
            center = geometry.get("center") or {}
            if center.get("relevance") == "distant_context":
                continue
            for key, label in (
                ("zg", "接近中枢上沿ZG，属于离开后回拉观察边界"),
                ("zd", "接近中枢下沿ZD，属于跌破后反抽观察边界"),
            ):
                value = _num(center.get(key))
                if value > 0 and abs(zone_center - value) / value < 0.01:
                    semantics.append(f"{level_name}:{label}")
        enriched = dict(cluster)
        if semantics:
            enriched["semantic"] = "；".join(semantics[:2])
        result.append(enriched)
    return result


def _compute_resonance_evidence(
    *,
    current_price: float,
    structure_geometry: dict[str, Any],
    pressure_support: list[dict[str, Any]],
) -> dict[str, Any]:
    """把结构边界与最近压力支撑的重叠翻译成低熵证据。"""
    if current_price <= 0:
        return {"score": 0, "grade": "LOW", "space_ratio": {}, "overlap_keys": [], "reasons": []}

    nearest_pressure = None
    nearest_support = None
    for cluster in pressure_support:
        center = _cluster_center(cluster)
        if center <= 0:
            continue
        if center > current_price and (nearest_pressure is None or center < nearest_pressure):
            nearest_pressure = center
        if center < current_price and (nearest_support is None or center > nearest_support):
            nearest_support = center

    upside_pct = round((nearest_pressure - current_price) / current_price * 100, 2) if nearest_pressure else None
    downside_pct = round((current_price - nearest_support) / current_price * 100, 2) if nearest_support else None
    risk_reward_ratio = (
        round(upside_pct / downside_pct, 2)
        if upside_pct is not None and downside_pct is not None and downside_pct > 0
        else None
    )

    score = 30
    overlap_keys: list[dict[str, Any]] = []
    reasons: list[str] = []
    for level_name, geometry in structure_geometry.items():
        center = geometry.get("center") or {}
        if center.get("relevance") == "distant_context":
            continue
        for boundary, boundary_label in (("zg", "中枢上沿ZG"), ("zd", "中枢下沿ZD")):
            boundary_price = _num(center.get(boundary))
            if boundary_price <= 0:
                continue
            for cluster in pressure_support:
                cluster_center = _cluster_center(cluster)
                if cluster_center <= 0:
                    continue
                distance_pct = abs(cluster_center - boundary_price) / boundary_price
                if distance_pct <= RESONANCE_OVERLAP_THRESHOLD:
                    overlap_keys.append({
                        "level": level_name,
                        "boundary": boundary,
                        "boundary_price": round(boundary_price, 4),
                        "cluster_center": round(cluster_center, 4),
                        "cluster_type": cluster.get("type"),
                        "distance_pct": round(distance_pct * 100, 2),
                        "source_levels": cluster.get("source_levels") or [],
                    })
                    reasons.append(f"{level_name}{boundary_label}接近历史{cluster.get('type') or 'cluster'}簇")
                    score += 12
                    break

    if risk_reward_ratio is not None:
        score += 8
        if risk_reward_ratio >= 1.5:
            score += 8
            reasons.append(f"上方空间/下方回撤约 {risk_reward_ratio}:1")
        elif risk_reward_ratio < 0.8:
            reasons.append(f"上方空间/下方回撤约 {risk_reward_ratio}:1，空间并不占优")

    score = min(score, 95)
    grade = "HIGH" if score >= 75 else "MEDIUM" if score >= 55 else "LOW"
    return {
        "score": score,
        "grade": grade,
        "space_ratio": {
            "nearest_pressure": round(nearest_pressure, 4) if nearest_pressure else None,
            "nearest_support": round(nearest_support, 4) if nearest_support else None,
            "upside_pct": upside_pct,
            "downside_pct": downside_pct,
            "risk_reward_ratio": risk_reward_ratio,
        },
        "overlap_keys": overlap_keys[:8],
        "reasons": reasons[:8],
    }


def _collect_chan_signals(
    snapshots: dict[str, dict[str, Any]],
    level_names: dict[str, str],
) -> dict[str, list[dict[str, str]]]:
    """收集 czsc 快照里已有的标准形态标签；没有就保持为空。"""
    result: dict[str, list[dict[str, str]]] = {}
    for level, row in snapshots.items():
        snap = row.get("snapshot") or {}
        raw = snap.get("chan_signals") or snap.get("signals") or {}
        if not isinstance(raw, dict):
            continue
        items: list[dict[str, str]] = []
        for key, value in raw.items():
            key_text = str(key or "")
            value_text = str(value or "")
            if not key_text or not value_text:
                continue
            if value_text in {"任意", "无", "None", "nan"} or value_text.startswith("其他"):
                continue
            if not any(marker in key_text for marker in CHAN_SIGNAL_MARKERS):
                continue
            items.append({"key": key_text[:80], "value": value_text[:80], "source": "czsc.signals"})
            if len(items) >= 8:
                break
        if items:
            result[level_names.get(level, level)] = items
    return result


def _cluster_center(cluster: dict[str, Any]) -> float:
    zone = cluster.get("zone") or []
    if len(zone) != 2:
        return 0.0
    return (_num(zone[0]) + _num(zone[1])) / 2


def _split_confirmed_and_unfinished_bis(snapshot: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    raw_bis = [item for item in (snapshot.get("bis") or []) if isinstance(item, dict)]
    raw_unfinished = snapshot.get("unfinished_bi") if isinstance(snapshot.get("unfinished_bi"), dict) else None
    if raw_unfinished:
        return raw_bis, raw_unfinished
    if raw_bis and _is_unfinished_bi(raw_bis[-1]):
        return raw_bis[:-1], raw_bis[-1]
    return raw_bis, None


def _is_unfinished_bi(item: dict[str, Any]) -> bool:
    return bool(item.get("is_sure") is False or item.get("source") == "czsc_ubi" or item.get("status") == "ongoing")


def _center_fields(center: dict[str, Any]) -> dict[str, Any]:
    return {
        "zg": center.get("zg"),
        "zd": center.get("zd"),
        "gg": center.get("gg"),
        "dd": center.get("dd"),
        "bi_count": center.get("bi_count"),
        "begin_date": center.get("begin_date"),
        "end_date": center.get("end_date"),
    }


def _bi_fields(bi: dict[str, Any] | None) -> dict[str, Any] | None:
    if not bi:
        return None
    return {
        "direction": bi.get("direction"),
        "start_price": bi.get("start_price"),
        "end_price": bi.get("end_price"),
        "high": bi.get("high"),
        "low": bi.get("low"),
        "bar_count": bi.get("bar_count"),
        "is_sure": bi.get("is_sure"),
        "status": bi.get("status"),
    }


def _center_maturity(bi_count: Any) -> str:
    count = int(_num(bi_count))
    if count <= 3:
        return "forming"
    if count <= 5:
        return "normal_extension"
    if count <= 8:
        return "late_extension"
    return "upgrade_watch"


def _center_maturity_note(maturity: str) -> str:
    return {
        "forming": "中枢刚形成，重点看是否继续延伸或快速离开",
        "normal_extension": "中枢正常延伸，方向仍需等待离开与回拉确认",
        "late_extension": "中枢延伸较充分，需关注离开确认或升级扩展",
        "upgrade_watch": "中枢延伸充分，需观察离开确认、三买三卖或升级扩展",
    }.get(maturity, "")


def _center_relevance(price: float, center: dict[str, Any]) -> str:
    zg = _num(center.get("zg"))
    zd = _num(center.get("zd"))
    if price <= 0 or zg <= 0 or zd <= 0:
        return "unknown"
    nearest = min(abs(price - zg) / zg, abs(price - zd) / zd)
    return "distant_context" if nearest > 0.2 else "active_boundary"


def _price_position(price: float, zg: Any, zd: Any) -> dict[str, Any]:
    upper = _num(zg)
    lower = _num(zd)
    if price <= 0 or upper <= 0 or lower <= 0:
        return {"position": "no_center"}
    position = "above_zg" if price > upper else "below_zd" if price < lower else "in_center"
    return {
        "position": position,
        "distance_to_zg_pct": round((price - upper) / upper * 100, 2),
        "distance_to_zd_pct": round((price - lower) / lower * 100, 2),
    }


def _position_context(*, user_id: int, symbol: str, current_price: float) -> dict[str, Any]:
    aliases = symbol_aliases(symbol)
    conn = get_connection()
    try:
        row = conn.execute(
            f"""
            SELECT quantity, avg_cost, current_price
              FROM positions
             WHERE user_id = ? AND symbol IN ({",".join("?" for _ in aliases)})
             ORDER BY updated_at DESC LIMIT 1
            """,
            (int(user_id), *aliases),
        ).fetchone()
    finally:
        conn.close()
    if row and _num(row["quantity"]) > 0:
        cost = _num(row["avg_cost"])
        price = current_price or _num(row["current_price"])
        result = {"holding": True, "shares": _num(row["quantity"]), "cost": cost, "source": "database"}
        if cost > 0 and price > 0:
            result["current_pnl_pct"] = round((price - cost) / cost * 100, 2)
        return result
    return {"holding": False, "shares": 0, "cost": 0, "source": "database", "note": "当前无持仓，观望中"}


def _current_price(snapshots: dict[str, dict[str, Any]]) -> float:
    for level in ("day", "5", "30", "week"):
        price = _num(((snapshots.get(level) or {}).get("snapshot") or {}).get("price"))
        if price > 0:
            return price
    return 0.0


def _data_as_of(snapshots: dict[str, dict[str, Any]]) -> str:
    for level in ("day", "5", "30", "week"):
        value = (snapshots.get(level) or {}).get("data_as_of")
        if value:
            return str(value)
    return ""


def _intraday_price(intraday_observation: dict[str, Any]) -> float:
    return _num((intraday_observation.get("quote") or {}).get("price"))


def _data_freshness(
    *,
    snapshots: dict[str, dict[str, Any]],
    intraday_observation: dict[str, Any],
    missing_levels: list[str],
    current_price: float,
    snapshot_price: float,
) -> dict[str, Any]:
    quote_price = _intraday_price(intraday_observation)
    levels = {
        level: {
            "snapshot_id": str(row.get("snapshot_id") or ""),
            "data_as_of": str(row.get("data_as_of") or ""),
            "updated_at": str(row.get("updated_at") or ""),
            "data_signature": str(row.get("data_signature") or ""),
            "source": ((row.get("snapshot") or {}).get("source") or {}),
        }
        for level, row in snapshots.items()
    }
    intraday_coverage = intraday_observation.get("coverage") or {}
    return {
        "version": "ai_native_data_freshness.v1",
        "structure_basis": "fresh_snapshot_read_only",
        "structure_levels": levels,
        "missing_structure_levels": list(missing_levels or []),
        "intraday_basis": {
            "source": intraday_observation.get("source") or "",
            "usage": intraday_observation.get("usage") or "",
            "as_of": intraday_observation.get("as_of") or "",
            "coverage": intraday_coverage,
        },
        "current_price": current_price,
        "current_price_source": "intraday_observation" if quote_price > 0 else ("structure_snapshot" if snapshot_price > 0 else ""),
        "snapshot_price": snapshot_price,
        "quote_price": quote_price,
    }


def _structure_snapshot_manifest(snapshots: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "source": "structure_snapshots",
        "usage": "formal_structure",
        "levels": {
            level: {
                "snapshot_id": str(row.get("snapshot_id") or ""),
                "data_as_of": str(row.get("data_as_of") or ""),
                "compute_profile": str(row.get("compute_profile") or ""),
                "data_signature": str(row.get("data_signature") or ""),
                "source": ((row.get("snapshot") or {}).get("source") or {}),
            }
            for level, row in snapshots.items()
        },
    }


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
