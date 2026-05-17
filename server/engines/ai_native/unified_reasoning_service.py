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
from server.domain.symbols import normalize_symbol
from server.engines.ai_native.czsc_snapshot_service import (
    DEFAULT_COMPUTE_PROFILE,
    DEFAULT_LEVELS,
    get_latest_snapshot,
    now_text,
    stable_hash,
)
from server.engines.ai_native.structure_context_service import (
    _boundary_payload,
    save_ai_structure_context,
    save_reasoning_run,
)
from server.engines.structure.structure_key import normalize_freq
from server.services.llm_service import AIModelRoute, LLMService


UNIFIED_REASONING_VERSION = "unified_reasoning.v1"
UNIFIED_FULL_TEXT_VERSION = f"{UNIFIED_REASONING_VERSION}.full_text"

SYSTEM_PROMPT = """你是缠中说禅，用户的盯盘搭档。

输入包含：多级别结构快照、历史压力支撑位、用户持仓。

看完数据，说清楚当下是什么、接下来怎么走、用户该怎么做。

仅供参考，不构成投资建议。"""


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
        f"以下是 {canonical} 的完整数据，请给出你的推演和操作建议：\n\n"
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
    return save_unified_reasoning_result(
        user_id=user_id,
        symbol=canonical,
        payload=payload,
        full_text=full_text,
        model_name=route.model_name,
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
    current_price = _current_price(snapshots)
    source_snapshot_ids = [item["snapshot_id"] for item in rows]
    full_input = {
        "symbol": canonical,
        "current_price": current_price,
        "data_as_of": _data_as_of(snapshots),
        "structure": structure,
        "pressure_support": _compute_pressure_support(snapshots),
        "my_position": _position_context(user_id=user_id, symbol=canonical, current_price=current_price),
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
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    source_snapshot_ids = payload.get("source_snapshot_ids") or []
    summary = summarize_unified_reasoning(full_text)
    run = save_reasoning_run(
        user_id=user_id,
        symbol=canonical,
        source_snapshot_ids=source_snapshot_ids,
        prompt_version=UNIFIED_FULL_TEXT_VERSION,
        think_model=model_name,
        summary_model="",
        status="SUCCESS",
        full_reasoning_text=full_text,
        summary={"coach_summary": summary, "version": UNIFIED_REASONING_VERSION},
        error_message="",
    )
    boundary = _boundary_payload(payload.get("snapshots") or [])
    reasoning = {
        "version": UNIFIED_REASONING_VERSION,
        "structure_summary": summary,
        "coach_summary": summary,
        "front_panel_text": summary,
        "pressure_support": (payload.get("input") or {}).get("pressure_support") or [],
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
        summary_model="",
        status="SUCCESS",
        full_reasoning_text=full_text,
        summary={"coach_summary": summary, "version": UNIFIED_REASONING_VERSION},
        error_message="",
        context_id=context["context_id"],
    )
    return {
        "symbol": canonical,
        "context_id": context["context_id"],
        "run_id": run["run_id"],
        "summary": summary,
        "full_text": full_text,
        "source_snapshot_ids": source_snapshot_ids,
        "data_as_of": (payload.get("input") or {}).get("data_as_of") or "",
        "updated_at": context.get("updated_at") or now_text(),
    }


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
    for line in str(text or "").splitlines():
        cleaned = re.sub(r"^[#>*\\-\\d\\.、\\s]+", "", line).strip()
        if cleaned:
            return cleaned[:max_length]
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
    bis = snap.get("bis") or []
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


def _compute_pressure_support(snapshots: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    swing_points: list[dict[str, Any]] = []
    current_price = _current_price(snapshots)
    if current_price <= 0:
        return []
    for level, snap_data in snapshots.items():
        snap = snap_data.get("snapshot") or {}
        price = _num(snap.get("price")) or current_price
        for bi in (snap.get("bis") or [])[-10:]:
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


def _position_context(*, user_id: int, symbol: str, current_price: float) -> dict[str, Any]:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT quantity, avg_cost, current_price
              FROM positions
             WHERE user_id = ? AND symbol = ?
             ORDER BY updated_at DESC LIMIT 1
            """,
            (int(user_id), normalize_symbol(symbol)),
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
