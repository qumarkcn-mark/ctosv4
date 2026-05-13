"""AI Native V5 chart evidence contract."""

from __future__ import annotations

import json
from typing import Any

from server.db.database import get_connection
from server.domain.symbols import normalize_symbol


def get_chart_context(
    *,
    user_id: int,
    symbol: str,
    context_id: str,
    level: str,
    evidence_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    context = _load_context(user_id=user_id, symbol=symbol, context_id=context_id)
    if not context:
        return None
    boundary_level = ((context.get("boundary") or {}).get("levels") or {}).get(level) or {}
    snapshot_id = boundary_level.get("snapshot_id") or ""
    snapshot = _load_snapshot(snapshot_id)
    if not snapshot:
        return {
            "symbol": normalize_symbol(symbol),
            "level": level,
            "context_id": context_id,
            "snapshot_id": snapshot_id,
            "stale": True,
            "stale_reason": "SNAPSHOT_NOT_FOUND",
            "klines": [],
            "overlays": {"active_center": None, "lines": []},
        }
    allowed = set(evidence_ids or [])
    overlays = _build_overlays(boundary_level, snapshot, allowed, context_id=context_id)
    return {
        "symbol": normalize_symbol(symbol),
        "level": level,
        "context_id": context_id,
        "snapshot_id": snapshot_id,
        "stale": context.get("status") != "fresh",
        "stale_reason": context.get("stale_reason") or "",
        "klines": (snapshot.get("snapshot") or {}).get("klines") or [],
        "overlays": overlays,
    }


def evidence_ids_for_intent(context: dict[str, Any], intent_type: str) -> list[str]:
    boundary = context.get("boundary") or {}
    primary = _primary_level(boundary)
    level_item = ((boundary.get("levels") or {}).get(primary) or {})
    evidence = level_item.get("evidence") or {}
    if intent_type == "invalidation":
        keys = ("active_center", "invalidation_line", "current_price_line")
    elif intent_type == "hold_or_exit":
        keys = ("active_center", "invalidation_line", "trigger_line", "current_price_line")
    else:
        keys = ("active_center", "trigger_line", "invalidation_line", "current_price_line")
    return [evidence[key] for key in keys if evidence.get(key)]


def chart_focus_for_intent(context: dict[str, Any], intent_type: str) -> dict[str, Any]:
    boundary = context.get("boundary") or {}
    level = _primary_level(boundary)
    level_item = ((boundary.get("levels") or {}).get(level) or {})
    center = level_item.get("active_center") or {}
    prices = []
    for key in ("zg", "zd"):
        if _num(center.get(key)) > 0:
            prices.append(_num(center.get(key)))
    current = _num(level_item.get("current_price"))
    if current > 0:
        prices.append(current)
    return {
        "level": level,
        "snapshot_id": level_item.get("snapshot_id") or "",
        "context_id": context["context_id"],
        "evidence_ids": evidence_ids_for_intent(context, intent_type),
        "prices": prices,
    }


def ensure_evidence_ids_belong_to_context(context: dict[str, Any], evidence_ids: list[str]) -> bool:
    all_ids = set()
    for item in ((context.get("boundary") or {}).get("levels") or {}).values():
        all_ids.update((item.get("evidence") or {}).values())
    return all(evidence_id in all_ids for evidence_id in evidence_ids)


def _build_overlays(boundary_level: dict[str, Any], snapshot: dict[str, Any], allowed: set[str], *, context_id: str) -> dict[str, Any]:
    evidence = boundary_level.get("evidence") or {}
    center = boundary_level.get("active_center") or {}
    overlays = {"active_center": None, "lines": []}
    center_id = evidence.get("active_center")
    if center and _include(center_id, allowed):
        overlays["active_center"] = {
            **center,
            "evidence_id": center_id,
            "source_snapshot_id": snapshot["snapshot_id"],
            "source_context_id": context_id,
        }
    for role, key, price_key, label in (
        ("trigger", "trigger_line", "zg", "突破观察"),
        ("invalidation", "invalidation_line", "zd", "失败线"),
    ):
        evidence_id = evidence.get(key)
        price = _num(center.get(price_key))
        if evidence_id and price > 0 and _include(evidence_id, allowed):
            overlays["lines"].append({
                "evidence_id": evidence_id,
                "source_snapshot_id": snapshot["snapshot_id"],
                "source_context_id": context_id,
                "price": price,
                "label": label,
                "role": role,
                "reason": "AI 回答引用的结构边界",
            })
    current_id = evidence.get("current_price_line")
    current_price = _num(boundary_level.get("current_price"))
    if current_id and current_price > 0 and _include(current_id, allowed):
        overlays["lines"].append({
            "evidence_id": current_id,
            "source_snapshot_id": snapshot["snapshot_id"],
            "source_context_id": context_id,
            "price": current_price,
            "label": "当前价",
            "role": "current_price",
            "reason": "AI 回答引用的当前价位置",
        })
    return overlays


def _load_context(*, user_id: int, symbol: str, context_id: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
              FROM ai_structure_contexts
             WHERE user_id = ? AND symbol = ? AND context_id = ?
             LIMIT 1
            """,
            (int(user_id), normalize_symbol(symbol), context_id),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["source_snapshot_ids"] = json.loads(data.pop("source_snapshot_ids_json") or "[]")
        data["raw_context"] = json.loads(data.pop("raw_context_json") or "{}")
        data["background"] = json.loads(data.pop("background_json") or "{}")
        data["boundary"] = json.loads(data.pop("boundary_json") or "{}")
        return data
    finally:
        conn.close()


def _load_snapshot(snapshot_id: str) -> dict[str, Any] | None:
    if not snapshot_id:
        return None
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM structure_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["snapshot"] = json.loads(data.pop("snapshot_json") or "{}")
        data["raw_bi_context"] = json.loads(data.pop("raw_bi_context_json") or "{}")
        return data
    finally:
        conn.close()


def _primary_level(boundary: dict[str, Any]) -> str:
    primary = boundary.get("primary_level") or ""
    if primary:
        return primary
    for level in ("5", "30", "day", "week"):
        if level in (boundary.get("levels") or {}):
            return level
    return ""


def _include(evidence_id: str | None, allowed: set[str]) -> bool:
    return bool(evidence_id) and (not allowed or evidence_id in allowed)


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
