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
from server.engines.ai_native.czsc_snapshot_service import (
    DEFAULT_COMPUTE_PROFILE,
    DEFAULT_LEVELS,
    get_latest_snapshot,
    now_text,
    stable_hash,
)
from server.engines.ai_native.dynamics_hydrator import hydrate_dynamics
from server.engines.ai_native.structure_context_service import (
    _boundary_payload,
    save_ai_structure_context,
    save_reasoning_run,
)
from server.engines.structure.structure_key import normalize_freq
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

SYSTEM_PROMPT = """你是用户的缠论盯盘搭档。

先理解第一阶段结构推演，再结合多级别结构几何、动力状态、附近压力支撑和持仓背景，做第二阶段综合推演。

重点说明：
当前走势在做什么；
第一阶段主线是否被动力和关口支持；
哪些价格和结构变化会让推演切换；
接下来最需要盯住什么。

仅供参考，不构成投资建议。"""

WATCHBOARD_EXTRACT_PROMPT = """从完整推演中提取盯盘卡片展示信息，返回 JSON：
{
  "card_summary": "一句话盯盘摘要，不超过28个中文字符",
  "card_action": "结合当前持仓状态给出的短标签，不超过6个中文字符",
  "triggers": [
    {
      "type": "price_below|price_above",
      "level": 数字,
      "message_on_trigger": "触发时显示的一句话（15字以内）",
      "action_on_trigger": "触发时应关注的动作"
    }
  ]
}
要求：
- card_summary 只说当前最核心的结构动作，适合盯盘卡片扫一眼，不写解释。
- card_action 必须结合当前持仓状态；空仓看建仓/观望，持仓看加仓/减仓/止损/锁利等管理动作；只输出短标签，不带价格。
- triggers 最多 4 个，只取推演中明确提到的关键价格。
- 不要输出买入、卖出这类下单命令，用盯盘建议语气。
- 只返回 JSON。"""


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
        row = get_latest_snapshot(symbol=canonical, level=level, compute_profile=compute_profile)
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
    current_price = _current_price(snapshots)
    source_snapshot_ids = [item["snapshot_id"] for item in rows]
    pressure_support = _compute_pressure_support(snapshots)
    nearby_pressure_support = _add_pressure_support_semantics(pressure_support, structure_geometry)
    resonance_evidence = _compute_resonance_evidence(
        current_price=current_price,
        structure_geometry=structure_geometry,
        pressure_support=nearby_pressure_support,
    )
    chan_signals = _collect_chan_signals(snapshots, level_names)
    position_context = _position_context(user_id=user_id, symbol=canonical, current_price=current_price)
    full_input = {
        "symbol": canonical,
        "current_price": current_price,
        "data_as_of": _data_as_of(snapshots),
        "first_stage_reasoning": structure,
        "structure_geometry": structure_geometry,
        "momentum_dynamics": momentum_dynamics,
        "nearby_pressure_support": nearby_pressure_support,
        "resonance_evidence": resonance_evidence,
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
    summary_payload = {
        "coach_summary": summary,
        "card_summary": normalized_watchboard["card_summary"],
        "card_action": normalized_watchboard["card_action"],
        "version": UNIFIED_REASONING_VERSION,
        "monitor_conditions": normalized_watchboard["monitor_conditions"],
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
        "card_action": normalized_watchboard["card_action"],
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
        "card_action": normalized_watchboard["card_action"],
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
        max_tokens=900,
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
    except Exception:
        return {"card_summary": "", "card_action": "", "triggers": []}
    return payload if isinstance(payload, dict) else {"card_summary": "", "card_action": "", "triggers": []}


async def extract_monitor_conditions(full_reasoning_text: str, *, user_id: int) -> dict[str, Any]:
    """Backward-compatible trigger extraction API."""
    payload = await extract_watchboard_payload(full_reasoning_text, user_id=user_id, position_context={})
    return normalize_monitor_conditions(payload)


def normalize_watchboard_payload(payload: dict[str, Any] | None, *, fallback_summary: str = "") -> dict[str, Any]:
    """Normalize AI-extracted card fields without turning the card into a rule engine."""
    raw = payload or {}
    card_summary = re.sub(r"\s+", "", str(raw.get("card_summary") or "")).strip()
    if not card_summary:
        card_summary = str(fallback_summary or "").strip()
    card_action = re.sub(r"\s+", "", str(raw.get("card_action") or "")).strip()
    card_action = _normalize_watchboard_action(card_action)
    return {
        "card_summary": card_summary[:42],
        "card_action": card_action[:8],
        "monitor_conditions": normalize_monitor_conditions(raw),
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
    raw_triggers = (payload or {}).get("triggers") or []
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
    action = _normalize_watchboard_action(action)
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
    if action in {"买入", "开仓", "建仓"}:
        return "考虑建仓"
    if action == "卖出":
        return "考虑减仓"
    if action == "清仓":
        return "考虑止损"
    if action in {"持有", "继续持有"}:
        return "继续持有"
    if action in {"持仓观望", "持仓观察"}:
        return "持仓观望"
    return action


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


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
