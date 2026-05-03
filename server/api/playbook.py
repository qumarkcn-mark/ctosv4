"""今日作战台 API。

把 Radar 的单票计划组织成盘前/盘中的纪律清单。这里不执行交易，
只记录计划、触发响应和复盘所需事实。
"""

import json
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from server.api import radar
from server.db.database import get_connection
from server.engines.ai_native.scoring import primary_path, score_paths
from server.engines.ai_native.transcript_compiler import compile_structure_transcript
from server.engines.coach.event_log import log_user_action

router = APIRouter()

DISCLAIMER = "仅供参考，不构成投资建议"
VALID_RESPONSES = {
    "ACKNOWLEDGED",
    "EXECUTED",
    "IGNORED",
    "CONTINUE_WATCHING",
    "INVALIDATED",
}
VALID_RELATIONSHIPS = {
    "PLANNED",
    "UNPLANNED",
    "EMOTIONAL",
    "AFTER_ALERT",
    "IGNORED_ALERT",
    "UNKNOWN",
}


class GeneratePlaybookRequest(BaseModel):
    """生成今日作战计划请求。"""

    user_id: int = 1
    sources: list[str] = Field(default_factory=lambda: ["positions", "scanner", "watchlist"])
    max_items: int = Field(default=8, ge=1, le=20)


class PlaybookResponseRequest(BaseModel):
    """用户对某个计划触发/观察项的响应。"""

    response: str
    note: Optional[str] = None


class TradePlanClassifyRequest(BaseModel):
    """给已录入交易补充计划关系。"""

    plan_relationship: str
    discipline_tag: Optional[str] = None
    playbook_item_id: Optional[int] = None


class PlaybookReportRequest(BaseModel):
    """生成盘后作战报告。"""

    user_id: int = 1
    trade_date: Optional[str] = None


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def _json(value) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(value, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _item_status_from_response(response: str) -> str:
    if response == "EXECUTED":
        return "EXECUTED"
    if response == "IGNORED":
        return "IGNORED"
    if response == "INVALIDATED":
        return "INVALIDATED"
    return "WATCHING"


async def _load_radar_contract(symbol: str, user_id: int) -> dict:
    """Small wrapper so tests can monkeypatch the Radar dependency."""
    result = await radar.get_radar(symbol, user_id=user_id)
    data = result.get("data", {})
    return {"status": result.get("status"), "data": data}


def _candidate_key(candidate: dict) -> str:
    return str(candidate.get("symbol", "")).strip()


def _collect_candidates(conn, user_id: int, sources: list[str], max_items: int) -> list[dict]:
    """按持仓、扫描器、自选股顺序收集今日作战候选。"""
    candidates: list[dict] = []
    seen: set[str] = set()

    def add(candidate: dict):
        symbol = _candidate_key(candidate)
        if not symbol or symbol in seen or len(candidates) >= max_items:
            return
        seen.add(symbol)
        candidates.append(candidate)

    if "positions" in sources:
        rows = conn.execute(
            """
            SELECT symbol, name, quantity, avg_cost
              FROM positions
             WHERE user_id = ? AND quantity > 0
             ORDER BY updated_at DESC, id DESC
            """,
            (user_id,),
        ).fetchall()
        for row in rows:
            add({
                "symbol": row["symbol"],
                "name": row["name"],
                "source": "positions",
                "position": {"quantity": row["quantity"], "avg_cost": row["avg_cost"]},
            })

    if "scanner" in sources and len(candidates) < max_items:
        rows = conn.execute(
            """
            SELECT symbol, strategy, score, close
              FROM scan_results
             WHERE scan_date = ? AND status = 'ready'
             ORDER BY score DESC, created_at DESC
             LIMIT ?
            """,
            (_today(), max_items * 2),
        ).fetchall()
        for row in rows:
            add({
                "symbol": row["symbol"],
                "name": None,
                "source": "scanner",
                "scanner": {
                    "strategy": row["strategy"],
                    "score": row["score"],
                    "close": row["close"],
                },
            })

    if "watchlist" in sources and len(candidates) < max_items:
        rows = conn.execute(
            """
            SELECT wi.symbol, wi.name, wg.name AS group_name
              FROM watchlist_items wi
              JOIN watchlist_groups wg ON wg.id = wi.group_id
             WHERE wg.user_id = ?
             ORDER BY wg.sort_order, wi.sort_order, wi.id
            """,
            (user_id,),
        ).fetchall()
        for row in rows:
            add({
                "symbol": row["symbol"],
                "name": row["name"],
                "source": "watchlist",
                "watchlist": {"group_name": row["group_name"]},
            })

    return candidates


def _ensure_playbook(conn, user_id: int, trade_date: str, sources: list[str]) -> int:
    row = conn.execute(
        "SELECT id FROM daily_playbooks WHERE user_id = ? AND trade_date = ?",
        (user_id, trade_date),
    ).fetchone()
    if row:
        return row["id"]

    cursor = conn.execute(
        """
        INSERT INTO daily_playbooks (user_id, trade_date, status, source_json)
        VALUES (?, ?, 'OPEN', ?)
        """,
        (user_id, trade_date, _json({"sources": sources})),
    )
    return cursor.lastrowid


def _plan_payload_from_radar(candidate: dict, radar_result: dict) -> dict:
    data = radar_result.get("data") or {}
    success = radar_result.get("status") == "success"
    mode = data.get("mode") or ("HOLDING" if candidate.get("position") else "EMPTY")
    primary_plan = data.get("holding_plan") if mode == "HOLDING" else data.get("entry_plan")
    primary_plan = primary_plan or {}
    freshness = data.get("freshness") or {}
    strategy = data.get("strategy") or {}

    status = primary_plan.get("status") or "WATCHING"
    if not success:
        status = "ENGINE_ERROR"
    elif freshness.get("is_stale"):
        status = "STALE"

    trigger = {
        "plan_title": primary_plan.get("title") or primary_plan.get("plan_id"),
        "conditions": primary_plan.get("conditions") or [],
        "stop_reference": (primary_plan.get("risk") or {}).get("stop_reference"),
        "targets": primary_plan.get("targets") or [],
    }
    invalidation = {
        "invalid_if": (primary_plan.get("risk") or {}).get("invalid_if"),
        "freshness": freshness,
    }
    ai_native = _ai_native_battle_plan(data) if success else {}
    if ai_native:
        trigger["ai_native"] = ai_native
        if mode == "HOLDING" and ai_native.get("priority") == "HIGH" and status == "WATCHING":
            status = "TRIGGERED"

    return {
        "mode": mode,
        "plan_id": primary_plan.get("plan_id"),
        "strategy_id": strategy.get("strategy_id"),
        "status": status,
        "trigger": trigger,
        "invalidation": invalidation,
        "radar_snapshot": data,
    }


def _ai_native_battle_plan(radar_data: dict) -> dict:
    """把 AI Native 结构评分压成今日作战卡。

    这里不调用 LLM，只复用 transcript/scoring 的确定性事实，保证生成今日作战足够快。
    """
    try:
        transcript = compile_structure_transcript(radar_data)
        algorithm = radar_data.get("algorithm_v2") or {}
        scores = score_paths(
            transcript,
            current_hypothesis=str(algorithm.get("current_scenario_id") or "UNKNOWN"),
            gate_status="PASS",
            use_memory=True,
        )
    except Exception:
        return {}
    primary = next((item for item in scores if item.id == primary_path(scores)), scores[0] if scores else None)
    position = transcript.position_context
    coach = radar_data.get("coach_action") or {}
    nearest = position.nearest_risk_line if position else None
    priority = _ai_native_priority(primary.id if primary else "UNKNOWN", position, coach)
    return {
        "version": "ai_native_playbook.v1",
        "generated_at": transcript.generated_at,
        "mode": transcript.mode,
        "primary_path": primary.id if primary else "UNKNOWN",
        "primary_name": primary.name if primary else "等待",
        "primary_score": primary.score if primary else 0,
        "primary_reason": primary.reason if primary else "",
        "path_scores": [item.model_dump() for item in scores],
        "next_focus": _ai_native_next_focus(primary, position, coach),
        "position_label": position.label if position else "",
        "pnl_percentage": position.pnl_percentage if position else None,
        "nearest_risk_line": nearest,
        "priority": priority,
        "disclaimer": DISCLAIMER,
    }


def _ai_native_priority(path: str, position, coach: dict) -> str:
    if coach.get("priority") == "HIGH":
        return "HIGH"
    if position and position.is_holding:
        nearest = position.nearest_risk_line or {}
        distance_pct = _optional_float(nearest.get("distance_pct")) if isinstance(nearest, dict) else None
        if path == "C" or "STRUCTURE_AGAINST_POSITION" in set(position.risk_flags or []):
            return "HIGH"
        if distance_pct is not None and abs(distance_pct) <= 2:
            return "HIGH"
        if position.pnl_percentage is not None and position.pnl_percentage <= -5:
            return "MEDIUM_HIGH"
    return "MEDIUM"


def _ai_native_next_focus(primary, position, coach: dict) -> str:
    if position and position.is_holding:
        nearest = position.nearest_risk_line or {}
        if isinstance(nearest, dict) and nearest.get("price"):
            label = nearest.get("label") or nearest.get("type") or "风险边界"
            return f"持仓先看 {label} {float(nearest['price']):.2f} 是否被守住或重新收回"
        if coach.get("focus"):
            return str(coach["focus"])
    if primary and primary.reason:
        return primary.reason
    return "等待市场在关键边界给出确认"


def _optional_float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _insert_item(conn, playbook_id: int, user_id: int, candidate: dict, payload: dict) -> int:
    source = candidate.get("source") or "unknown"
    source_payload = {
        key: candidate.get(key)
        for key in ("position", "scanner", "watchlist")
        if candidate.get(key) is not None
    }
    cursor = conn.execute(
        """
        INSERT INTO daily_playbook_items (
            playbook_id, user_id, symbol, name, source, source_json, mode, plan_id, strategy_id,
            status, trigger_json, invalidation_json, radar_snapshot_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            playbook_id,
            user_id,
            candidate["symbol"],
            candidate.get("name"),
            source,
            _json(source_payload),
            payload["mode"],
            payload.get("plan_id"),
            payload.get("strategy_id"),
            payload["status"],
            _json(payload.get("trigger")),
            _json(payload.get("invalidation")),
            _json(payload.get("radar_snapshot")),
        ),
    )
    return cursor.lastrowid


def _row_to_item(row) -> dict:
    item = dict(row)
    item["source_json"] = _loads(item.get("source_json"), {})
    item["trigger"] = _loads(item.pop("trigger_json", None), {})
    item["invalidation"] = _loads(item.pop("invalidation_json", None), {})
    item["radar_snapshot"] = _loads(item.pop("radar_snapshot_json", None), {})
    item["response"] = _loads(item.pop("response_json", None), None)
    return item


def _infer_missing_source(conn, user_id: int, trade_date: str, item: dict) -> dict:
    """Backfill source for playbook rows created before source tracking existed."""
    if item.get("source"):
        return item

    symbol = item.get("symbol")
    source = "unknown"
    source_payload = {}

    position = conn.execute(
        """
        SELECT quantity, avg_cost
          FROM positions
         WHERE user_id = ? AND symbol = ? AND quantity > 0
        """,
        (user_id, symbol),
    ).fetchone()
    if position:
        source = "positions"
        source_payload = {"position": {"quantity": position["quantity"], "avg_cost": position["avg_cost"]}}
    else:
        scan = conn.execute(
            """
            SELECT strategy, score, close
              FROM scan_results
             WHERE scan_date = ? AND symbol = ? AND status = 'ready'
             ORDER BY score DESC, created_at DESC
             LIMIT 1
            """,
            (trade_date, symbol),
        ).fetchone()
        if scan:
            source = "scanner"
            source_payload = {"scanner": {"strategy": scan["strategy"], "score": scan["score"], "close": scan["close"]}}
        else:
            watch = conn.execute(
                """
                SELECT wg.name AS group_name
                  FROM watchlist_items wi
                  JOIN watchlist_groups wg ON wg.id = wi.group_id
                 WHERE wg.user_id = ? AND wi.symbol = ?
                 ORDER BY wg.sort_order, wi.sort_order, wi.id
                 LIMIT 1
                """,
                (user_id, symbol),
            ).fetchone()
            if watch:
                source = "watchlist"
                source_payload = {"watchlist": {"group_name": watch["group_name"]}}

    item["source"] = source
    item["source_json"] = source_payload
    if source != "unknown":
        conn.execute(
            """
            UPDATE daily_playbook_items
               SET source = ?, source_json = ?, updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (source, _json(source_payload), item["id"]),
        )
    return item


def _playbook_payload(conn, user_id: int, trade_date: str) -> dict:
    playbook = conn.execute(
        "SELECT * FROM daily_playbooks WHERE user_id = ? AND trade_date = ?",
        (user_id, trade_date),
    ).fetchone()
    if not playbook:
        return {
            "trade_date": trade_date,
            "status": "EMPTY",
            "freshness": {},
            "items": [],
            "metrics": _metrics(conn, user_id, trade_date, None),
            "disclaimer": DISCLAIMER,
        }

    rows = conn.execute(
        """
        SELECT * FROM daily_playbook_items
         WHERE playbook_id = ?
         ORDER BY
           CASE mode WHEN 'HOLDING' THEN 0 ELSE 1 END,
           CASE status WHEN 'TRIGGERED' THEN 0 WHEN 'WATCHING' THEN 1 ELSE 2 END,
           id
        """,
        (playbook["id"],),
    ).fetchall()
    items = [_infer_missing_source(conn, user_id, trade_date, _row_to_item(row)) for row in rows]
    metrics = _metrics(conn, user_id, trade_date, playbook["id"])
    stored_summary = _loads(playbook["summary_json"], {})
    conn.commit()
    return {
        "id": playbook["id"],
        "trade_date": playbook["trade_date"],
        "status": playbook["status"],
        "freshness": _aggregate_freshness(items),
        "items": items,
        "metrics": metrics,
        "report": stored_summary or _build_playbook_report(
            items,
            metrics,
            trade_date=trade_date,
            persisted=False,
            ai_settlement=_ai_settlement_summary(conn, user_id, trade_date, items),
        ),
        "disclaimer": DISCLAIMER,
    }


def _aggregate_freshness(items: list[dict]) -> dict:
    stale_count = 0
    for item in items:
        freshness = (item.get("radar_snapshot") or {}).get("freshness") or {}
        if freshness.get("is_stale") or item.get("status") in ("STALE", "ENGINE_ERROR"):
            stale_count += 1
    return {
        "is_stale": stale_count > 0,
        "stale_items": stale_count,
        "total_items": len(items),
    }


def _metrics(conn, user_id: int, trade_date: str, playbook_id: Optional[int]) -> dict:
    trade_rows = conn.execute(
        """
        SELECT plan_relationship, COUNT(*) AS count
          FROM trades
         WHERE user_id = ? AND date(traded_at) = ?
         GROUP BY plan_relationship
        """,
        (user_id, trade_date),
    ).fetchall()
    by_relationship = {row["plan_relationship"] or "UNKNOWN": row["count"] for row in trade_rows}
    if playbook_id:
        item_rows = conn.execute(
            """
            SELECT status, COUNT(*) AS count
              FROM daily_playbook_items
             WHERE playbook_id = ?
             GROUP BY status
            """,
            (playbook_id,),
        ).fetchall()
    else:
        item_rows = []
    by_status = {row["status"]: row["count"] for row in item_rows}
    return {
        "planned_trades": by_relationship.get("PLANNED", 0) + by_relationship.get("AFTER_ALERT", 0),
        "unplanned_trades": by_relationship.get("UNPLANNED", 0) + by_relationship.get("EMOTIONAL", 0),
        "emotional_trades": by_relationship.get("EMOTIONAL", 0),
        "triggered_items": by_status.get("TRIGGERED", 0),
        "ignored_triggers": by_status.get("IGNORED", 0),
        "executed_items": by_status.get("EXECUTED", 0),
    }


def _build_playbook_report(
    items: list[dict],
    metrics: dict,
    *,
    trade_date: str,
    persisted: bool = True,
    ai_settlement: Optional[dict] = None,
) -> dict:
    total = len(items)
    triggered = [item for item in items if item.get("status") == "TRIGGERED"]
    responded = [item for item in items if item.get("response") or item.get("status") in {"EXECUTED", "IGNORED", "INVALIDATED"}]
    ai_items = [item for item in items if (item.get("trigger") or {}).get("ai_native")]
    high_priority = [
        item for item in ai_items
        if ((item.get("trigger") or {}).get("ai_native") or {}).get("priority") == "HIGH"
    ]
    ignored_high = [
        item for item in high_priority
        if ((item.get("response") or {}).get("response") == "IGNORED") or item.get("status") == "IGNORED"
    ]
    path_counts: dict[str, int] = {}
    risk_symbols = []
    for item in ai_items:
        ai_native = (item.get("trigger") or {}).get("ai_native") or {}
        path = ai_native.get("primary_path") or "UNKNOWN"
        path_counts[path] = path_counts.get(path, 0) + 1
        nearest = ai_native.get("nearest_risk_line")
        if nearest:
            risk_symbols.append({
                "symbol": item.get("symbol"),
                "name": item.get("name"),
                "line": nearest,
                "priority": ai_native.get("priority"),
                "path": path,
            })
    discipline_flags = []
    if ignored_high:
        discipline_flags.append("高优先级风险项被忽略")
    if metrics.get("unplanned_trades", 0) > 0:
        discipline_flags.append("出现计划外交易")
    if total and len(responded) < len(triggered):
        discipline_flags.append("部分触发项未响应")
    if ai_settlement and ai_settlement.get("wrong_runs", 0) > 0:
        discipline_flags.append("存在 AI 推演未兑现")

    focus = "今天没有生成作战项。"
    if ai_settlement and ai_settlement.get("wrong_runs", 0) > 0:
        focus = "先复盘 AI 推演未兑现的结构原因。"
    elif ignored_high:
        focus = "先复盘高优先级风险项为何被忽略。"
    elif metrics.get("unplanned_trades", 0) > 0:
        focus = "先复盘计划外交易的触发场景。"
    elif high_priority:
        focus = "重点复盘高优先级风险边界是否按计划处理。"
    elif ai_items:
        focus = "复盘 AI 路径评分和盘中实际走势是否一致。"

    return {
        "version": "daily_playbook_report.v1",
        "trade_date": trade_date,
        "persisted": persisted,
        "summary": {
            "total_items": total,
            "triggered_items": len(triggered),
            "responded_items": len(responded),
            "ai_items": len(ai_items),
            "high_priority_items": len(high_priority),
            "unplanned_trades": metrics.get("unplanned_trades", 0),
            "ai_reviewed_runs": (ai_settlement or {}).get("reviewed_runs", 0),
            "ai_wrong_runs": (ai_settlement or {}).get("wrong_runs", 0),
        },
        "ai_path_distribution": path_counts,
        "ai_settlement": ai_settlement or _empty_ai_settlement(),
        "risk_symbols": risk_symbols[:8],
        "discipline_flags": discipline_flags,
        "review_focus": focus,
        "disclaimer": DISCLAIMER,
    }


def _persist_playbook_report(conn, user_id: int, trade_date: str) -> dict:
    payload = _playbook_payload(conn, user_id, trade_date)
    if payload.get("status") == "EMPTY":
        raise LookupError("今日作战计划不存在")
    report = _build_playbook_report(
        payload.get("items") or [],
        payload.get("metrics") or {},
        trade_date=trade_date,
        persisted=True,
        ai_settlement=_ai_settlement_summary(conn, user_id, trade_date, payload.get("items") or []),
    )
    conn.execute(
        """
        UPDATE daily_playbooks
           SET status = 'REVIEWED',
               summary_json = ?
         WHERE id = ?
        """,
        (_json(report), payload["id"]),
    )
    conn.commit()
    return report


def _ai_settlement_summary(conn, user_id: int, trade_date: str, items: list[dict]) -> dict:
    if not _table_exists(conn, "ai_reasoning_runs"):
        return _empty_ai_settlement()

    item_symbols = {item.get("symbol") for item in items if item.get("symbol")}
    rows = conn.execute(
        """
        SELECT id, symbol, created_at, replay_score, outcome_json
          FROM ai_reasoning_runs
         WHERE user_id = ?
           AND replay_status = 'REVIEWED'
           AND substr(created_at, 1, 10) = ?
         ORDER BY created_at DESC, id DESC
        """,
        (user_id, trade_date),
    ).fetchall()

    reviewed = 0
    matched = 0
    wrong = 0
    unknown = 0
    tag_counts: dict[str, int] = {}
    quality_counts: dict[str, int] = {}
    path_counts: dict[str, int] = {}
    runs = []
    scores = []
    for row in rows:
        if item_symbols and row["symbol"] not in item_symbols:
            continue
        outcome = _loads(row["outcome_json"], {})
        reviewed += 1
        if outcome.get("matched") is True:
            matched += 1
        elif outcome.get("matched") is False:
            wrong += 1
        else:
            unknown += 1
        path = outcome.get("actual_hypothesis") or outcome.get("path") or "UNKNOWN"
        path_counts[path] = path_counts.get(path, 0) + 1
        sample_quality = str(outcome.get("sample_quality") or "UNKNOWN")
        quality_counts[sample_quality] = quality_counts.get(sample_quality, 0) + 1
        for tag in outcome.get("tags") or []:
            tag = str(tag)
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
        if row["replay_score"] is not None:
            scores.append(float(row["replay_score"]))
        runs.append({
            "id": row["id"],
            "symbol": row["symbol"],
            "predicted": outcome.get("predicted_hypothesis") or "UNKNOWN",
            "actual": outcome.get("actual_hypothesis") or "UNKNOWN",
            "matched": outcome.get("matched"),
            "replay_score": row["replay_score"],
            "tags": outcome.get("tags") or [],
            "sample_quality": outcome.get("sample_quality") or "UNKNOWN",
            "learning_weight": outcome.get("learning_weight"),
            "notes": outcome.get("notes") or "",
        })

    match_rate = round(matched / reviewed, 4) if reviewed else 0.0
    average_score = round(sum(scores) / len(scores), 2) if scores else 0.0
    return {
        "reviewed_runs": reviewed,
        "matched_runs": matched,
        "wrong_runs": wrong,
        "unknown_runs": unknown,
        "match_rate": match_rate,
        "average_replay_score": average_score,
        "actual_path_distribution": path_counts,
        "tag_counts": tag_counts,
        "quality_counts": quality_counts,
        "runs": runs[:12],
    }


def _empty_ai_settlement() -> dict:
    return {
        "reviewed_runs": 0,
        "matched_runs": 0,
        "wrong_runs": 0,
        "unknown_runs": 0,
        "match_rate": 0.0,
        "average_replay_score": 0.0,
        "actual_path_distribution": {},
        "tag_counts": {},
        "quality_counts": {},
        "runs": [],
    }


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


@router.get("/today")
def get_today_playbook(user_id: int = 1, trade_date: Optional[str] = Query(None)):
    """读取今日作战计划。没有生成时返回空状态。"""
    trade_date = trade_date or _today()
    conn = get_connection()
    try:
        return {"status": "success", "data": _playbook_payload(conn, user_id, trade_date)}
    finally:
        conn.close()


@router.post("/today/generate")
async def generate_today_playbook(request: GeneratePlaybookRequest):
    """生成今日作战计划。重复调用保持幂等，不覆盖已有手动响应。"""
    trade_date = _today()

    def _prepare():
        conn = get_connection()
        try:
            playbook_id = _ensure_playbook(conn, request.user_id, trade_date, request.sources)
            existing_count = conn.execute(
                "SELECT COUNT(*) AS count FROM daily_playbook_items WHERE playbook_id = ?",
                (playbook_id,),
            ).fetchone()["count"]
            if existing_count:
                payload = _playbook_payload(conn, request.user_id, trade_date)
                conn.commit()
                return playbook_id, [], payload

            candidates = _collect_candidates(conn, request.user_id, request.sources, request.max_items)
            conn.commit()
            return playbook_id, candidates, None
        finally:
            conn.close()

    playbook_id, candidates, existing_payload = await run_in_threadpool(_prepare)
    if existing_payload is not None:
        return {"status": "success", "data": existing_payload}

    inserted = []
    for candidate in candidates:
        radar_result = await _load_radar_contract(candidate["symbol"], request.user_id)
        payload = _plan_payload_from_radar(candidate, radar_result)

        def _insert():
            conn = get_connection()
            try:
                item_id = _insert_item(conn, playbook_id, request.user_id, candidate, payload)
                log_user_action(
                    conn,
                    user_id=request.user_id,
                    symbol=candidate["symbol"],
                    source="playbook_api",
                    action_type="PLAYBOOK_ITEM_CREATED",
                    dedupe_key=f"playbook_item:{request.user_id}:{trade_date}:{candidate['symbol']}",
                    evidence={
                        "playbook_id": playbook_id,
                        "item_id": item_id,
                        "source": candidate.get("source"),
                        "status": payload["status"],
                    },
                    message={"title": "加入今日作战", "body": f"{candidate['symbol']} 已加入今日作战。"},
                )
                conn.commit()
                return item_id
            finally:
                conn.close()

        inserted.append(await run_in_threadpool(_insert))

    conn = get_connection()
    try:
        return {"status": "success", "data": _playbook_payload(conn, request.user_id, trade_date)}
    finally:
        conn.close()


@router.post("/today/report")
def generate_today_report(request: PlaybookReportRequest):
    """生成并保存今日作战盘后报告。"""
    trade_date = request.trade_date or _today()
    conn = get_connection()
    try:
        report = _persist_playbook_report(conn, request.user_id, trade_date)
        return {"status": "success", "data": report}
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    finally:
        conn.close()


@router.post("/items/{item_id}/response")
def record_item_response(item_id: int, request: PlaybookResponseRequest, user_id: int = 1):
    """记录用户对作战项的响应，用于盘后复盘。"""
    response = request.response.upper()
    if response not in VALID_RESPONSES:
        raise HTTPException(400, f"response 必须是 {sorted(VALID_RESPONSES)}")

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM daily_playbook_items WHERE id = ? AND user_id = ?",
            (item_id, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "作战项不存在")

        response_payload = {
            "response": response,
            "note": request.note,
            "recorded_at": date.today().isoformat(),
        }
        status = _item_status_from_response(response)
        conn.execute(
            """
            UPDATE daily_playbook_items
               SET status = ?, response_json = ?, updated_at = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (status, _json(response_payload), item_id),
        )
        event_id = log_user_action(
            conn,
            user_id=user_id,
            symbol=row["symbol"],
            source="playbook_api",
            action_type=f"PLAYBOOK_{response}",
            dedupe_key=f"playbook_response:{user_id}:{item_id}:{response}:{date.today().isoformat()}",
            evidence={"playbook_item_id": item_id, "response": response},
            message={"title": "作战响应", "body": f"{row['symbol']} 标记为 {response}。"},
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM daily_playbook_items WHERE id = ?", (item_id,)).fetchone()
        return {"status": "success", "data": {"event_id": event_id, "item": _row_to_item(updated)}}
    finally:
        conn.close()


@router.post("/trades/{trade_id}/classify")
def classify_trade_plan(trade_id: int, request: TradePlanClassifyRequest, user_id: int = 1):
    """给已录入交易补充计划关系。"""
    relationship = request.plan_relationship.upper()
    if relationship not in VALID_RELATIONSHIPS:
        raise HTTPException(400, f"plan_relationship 必须是 {sorted(VALID_RELATIONSHIPS)}")

    conn = get_connection()
    try:
        trade = conn.execute(
            "SELECT * FROM trades WHERE id = ? AND user_id = ?",
            (trade_id, user_id),
        ).fetchone()
        if not trade:
            raise HTTPException(404, "交易不存在")

        event_id = log_user_action(
            conn,
            user_id=user_id,
            symbol=trade["symbol"],
            source="playbook_api",
            action_type="TRADE_PLAN_CLASSIFIED",
            dedupe_key=f"trade_plan_classified:{user_id}:{trade_id}:{relationship}",
            evidence={
                "trade_id": trade_id,
                "playbook_item_id": request.playbook_item_id,
                "plan_relationship": relationship,
                "discipline_tag": request.discipline_tag,
            },
            message={"title": "交易计划关系", "body": f"交易 #{trade_id} 已标记为 {relationship}。"},
        )
        conn.execute(
            """
            UPDATE trades
               SET playbook_item_id = ?, plan_relationship = ?,
                   discipline_tag = ?, coach_event_id = ?
             WHERE id = ?
            """,
            (request.playbook_item_id, relationship, request.discipline_tag, event_id, trade_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        return {"status": "success", "data": dict(row)}
    finally:
        conn.close()
