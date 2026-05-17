"""AI Native V5 scenario branch persistence.

Branches are user-scoped and derived only from AI Structure Context rows.
"""

from __future__ import annotations

import json
from typing import Any

from server.db.database import get_connection
from server.domain.symbols import normalize_symbol
from server.engines.ai_native.czsc_snapshot_service import now_text, stable_hash


def upsert_scenario_branches_for_context(context: dict[str, Any]) -> list[dict[str, Any]]:
    branches = _derive_branches(context)
    saved = []
    for branch in branches:
        saved.append(save_scenario_branch(**branch))
    return saved


def list_scenario_branches(
    *,
    user_id: int,
    symbol: str,
    context_id: str | None = None,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    canonical = normalize_symbol(symbol)
    where = ["user_id = ?", "symbol = ?"]
    params: list[Any] = [int(user_id), canonical]
    if context_id:
        where.append("context_id = ?")
        params.append(context_id)
    if active_only:
        where.append("status = 'ACTIVE'")
    conn = get_connection()
    try:
        rows = conn.execute(
            f"""
            SELECT *
              FROM scenario_branches
             WHERE {" AND ".join(where)}
             ORDER BY updated_at DESC, id DESC
            """,
            params,
        ).fetchall()
        return [_branch_row(row) for row in rows]
    finally:
        conn.close()


def save_scenario_branch(
    *,
    user_id: int,
    context_id: str,
    symbol: str,
    branch_type: str,
    main_level: str,
    trigger_level: str,
    trigger_condition: dict[str, Any],
    invalidate_condition: dict[str, Any],
    evidence_refs: list[str],
    source_context_version: str,
    next_recheck: str = "",
    status: str = "ACTIVE",
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    trigger_json = _json(trigger_condition)
    invalidate_json = _json(invalidate_condition)
    evidence_json = _json(evidence_refs)
    key = stable_hash({
        "user_id": int(user_id),
        "context_id": context_id,
        "branch_type": branch_type,
        "trigger_condition": trigger_condition,
        "invalidate_condition": invalidate_condition,
    })
    branch_id = f"v5branch_{key[:16]}"
    now = now_text()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO scenario_branches (
                branch_id, idempotency_key, user_id, context_id, symbol,
                branch_type, main_level, trigger_level, trigger_condition_json,
                invalidate_condition_json, next_recheck, status,
                source_context_version, evidence_refs_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, context_id, branch_type, trigger_condition_json, invalidate_condition_json)
            DO UPDATE SET
                status = excluded.status,
                next_recheck = excluded.next_recheck,
                evidence_refs_json = excluded.evidence_refs_json,
                source_context_version = excluded.source_context_version,
                updated_at = excluded.updated_at
            """,
            (
                branch_id,
                key,
                int(user_id),
                context_id,
                canonical,
                branch_type,
                main_level,
                trigger_level,
                trigger_json,
                invalidate_json,
                next_recheck,
                status,
                source_context_version,
                evidence_json,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM scenario_branches WHERE branch_id = ?", (branch_id,)).fetchone()
        return _branch_row(row)
    finally:
        conn.close()


def _derive_branches(context: dict[str, Any]) -> list[dict[str, Any]]:
    reasoning_branches = _derive_reasoning_branches(context)
    if reasoning_branches:
        return reasoning_branches

    boundary = context.get("boundary") or {}
    position = (context.get("raw_context") or {}).get("position_context") or {}
    base = {
        "user_id": int(context["user_id"]),
        "context_id": context["context_id"],
        "symbol": context["symbol"],
        "source_context_version": context.get("prompt_version") or "",
    }
    branches: list[dict[str, Any]] = []
    primary = _primary_boundary(boundary)
    if primary:
        branches.append({
            **base,
            "branch_type": "observe_breakout",
            "main_level": primary["level"],
            "trigger_level": primary["level"],
            "trigger_condition": {
                "type": "price_above",
                "price": primary["zg"],
                "level": primary["level"],
                "label": "站上中枢上沿后进入观察窗口",
            },
            "invalidate_condition": {
                "type": "price_below",
                "price": primary["zd"],
                "level": primary["level"],
                "label": "跌回中枢下沿则突破观察失效",
            },
            "evidence_refs": _refs(primary, ["active_center", "trigger_line", "invalidation_line"]),
        })
        branches.append({
            **base,
            "branch_type": "invalidation_watch",
            "main_level": primary["level"],
            "trigger_level": primary["level"],
            "trigger_condition": {
                "type": "price_below",
                "price": primary["zd"],
                "level": primary["level"],
                "label": "跌破中枢下沿后结构转弱",
            },
            "invalidate_condition": {
                "type": "price_above",
                "price": primary["zg"],
                "level": primary["level"],
                "label": "重新站回中枢上沿则弱化信号撤销",
            },
            "evidence_refs": _refs(primary, ["active_center", "invalidation_line", "trigger_line"]),
        })
    if primary and position.get("has_position"):
        branches.append({
            **base,
            "branch_type": "holding_defense",
            "main_level": primary["level"],
            "trigger_level": primary["level"],
            "trigger_condition": {
                "type": "price_below",
                "price": primary["zd"],
                "level": primary["level"],
                "label": "持仓跌破结构防守线时提醒复核",
            },
            "invalidate_condition": {
                "type": "price_above",
                "price": primary["zg"],
                "level": primary["level"],
                "label": "重新站回上沿后防守压力缓和",
            },
            "evidence_refs": _refs(primary, ["active_center", "invalidation_line"]),
        })
    return branches


def _derive_reasoning_branches(context: dict[str, Any]) -> list[dict[str, Any]]:
    reasoning = context.get("reasoning") or {}
    raw_branches = reasoning.get("scenario_branches") or []
    if not isinstance(raw_branches, list):
        return []
    base = {
        "user_id": int(context["user_id"]),
        "context_id": context["context_id"],
        "symbol": context["symbol"],
        "source_context_version": reasoning.get("version") or context.get("prompt_version") or "",
    }
    branches = []
    for index, item in enumerate(raw_branches):
        if not isinstance(item, dict):
            continue
        trigger = item.get("trigger_condition") if isinstance(item.get("trigger_condition"), dict) else {}
        invalidate = item.get("invalidate_condition") if isinstance(item.get("invalidate_condition"), dict) else {}
        if not trigger or not invalidate:
            continue
        branch_type = str(item.get("branch_type") or f"reasoning_branch_{index + 1}")
        branches.append({
            **base,
            "branch_type": branch_type,
            "main_level": str(item.get("main_level") or reasoning.get("main_level") or ""),
            "trigger_level": str(item.get("trigger_level") or reasoning.get("trigger_level") or ""),
            "trigger_condition": trigger,
            "invalidate_condition": invalidate,
            "next_recheck": str(item.get("next_recheck") or ""),
            "evidence_refs": _reasoning_refs(item),
        })
    return branches


def _reasoning_refs(item: dict[str, Any]) -> list[str]:
    refs = item.get("evidence_refs")
    if isinstance(refs, list):
        return [str(ref) for ref in refs if ref]
    focus = item.get("chart_focus")
    if isinstance(focus, list):
        return [str(ref) for ref in focus if ref]
    return []


def _primary_boundary(boundary: dict[str, Any]) -> dict[str, Any] | None:
    levels = boundary.get("levels") or {}
    for level in ("5", "30", "day", "week"):
        item = levels.get(level) or {}
        center = item.get("active_center") or {}
        zg = _num(center.get("zg"))
        zd = _num(center.get("zd"))
        if zg > 0 and zd > 0:
            return {
                "level": level,
                "zg": zg,
                "zd": zd,
                "evidence": item.get("evidence") or {},
            }
    return None


def _refs(primary: dict[str, Any], keys: list[str]) -> list[str]:
    evidence = primary.get("evidence") or {}
    return [evidence[key] for key in keys if evidence.get(key)]


def _branch_row(row) -> dict[str, Any]:
    data = dict(row)
    data["trigger_condition"] = json.loads(data.pop("trigger_condition_json") or "{}")
    data["invalidate_condition"] = json.loads(data.pop("invalidate_condition_json") or "{}")
    data["evidence_refs"] = json.loads(data.pop("evidence_refs_json") or "[]")
    return data


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
