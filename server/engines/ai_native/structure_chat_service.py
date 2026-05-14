"""AI Native V5 deterministic structure chat.

This service answers from saved AI Structure Context only. It does not call
CZSC, old radar, or any heavy structure path.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from server.db.database import get_connection
from server.domain.symbols import normalize_symbol
from server.engines.ai_native.czsc_snapshot_service import now_text, stable_hash
from server.engines.ai_native.structure_context_service import (
    get_ai_structure_context_status,
    get_latest_ai_structure_context,
)
from server.engines.ai_native.structure_evidence_service import (
    chart_focus_for_intent,
    ensure_evidence_ids_belong_to_context,
)
from server.engines.ai_native.scenario_outcome_service import get_memory_context_for_chat, list_symbol_outcome_reviews


RISK_DISCLAIMER = "仅供参考，不构成投资建议"


def answer_structure_question(
    *,
    user_id: int,
    symbol: str,
    question: str,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    canonical = normalize_symbol(symbol)
    context = get_latest_ai_structure_context(user_id=user_id, symbol=canonical)
    if not context:
        return None
    session = upsert_chat_session(
        user_id=user_id,
        symbol=canonical,
        context_id=context["context_id"],
        session_id=session_id,
    )
    if not session:
        return None
    conversation_context = get_recent_conversation_context(
        user_id=user_id,
        session_id=session["session_id"],
    )
    intent_type = classify_intent(question, conversation_context=conversation_context)
    data_status = _context_data_status(user_id=user_id, symbol=canonical, context=context)
    chart_focus = chart_focus_for_intent(context, intent_type)
    if not ensure_evidence_ids_belong_to_context(context, chart_focus["evidence_ids"]):
        raise ValueError("evidence ids do not belong to context")
    memory_context = get_memory_context_for_chat(user_id=user_id, symbol=canonical)
    review_context = (
        list_symbol_outcome_reviews(user_id=user_id, symbol=canonical, limit=5)
        if intent_type == "review"
        else None
    )
    answer = _build_answer(
        question=question,
        intent_type=intent_type,
        context=context,
        chart_focus=chart_focus,
        data_status=data_status,
        memory_context=memory_context,
        review_context=review_context,
    )
    reminder_candidates = _reminder_candidates(intent_type=intent_type, context=context, chart_focus=chart_focus)
    payload = {
        "session_id": session["session_id"],
        "context_id": context["context_id"],
        "answer": answer["coach_answer"],
        "coach_answer": answer["coach_answer"],
        "intent_type": intent_type,
        "referenced_boundaries": answer["referenced_boundaries"],
        "chart_focus": chart_focus,
        "suggested_reminders": reminder_candidates,
        "data_status": data_status,
        "memory_context": memory_context,
        "review_context": review_context,
        "conversation_context": conversation_context,
        "risk_disclaimer": RISK_DISCLAIMER,
    }
    message = save_chat_message(
        user_id=user_id,
        symbol=canonical,
        session_id=session["session_id"],
        context_id=context["context_id"],
        question_text=question,
        intent_type=intent_type,
        answer_payload=payload,
        evidence_refs=chart_focus["evidence_ids"],
        reminder_candidates=reminder_candidates,
    )
    payload["message_id"] = message["message_id"]
    return payload


def classify_intent(question: str, conversation_context: dict[str, Any] | None = None) -> str:
    text = (question or "").strip().lower()
    if _is_out_of_scope_question(text):
        return "out_of_scope"
    if any(token in text for token in ("复盘", "回顾", "错在哪里", "错哪", "纪律", "上次错", "最近错")):
        return "review"
    if any(token in text for token in ("跌破", "不看", "失效", "哪里止", "防守", "破位")):
        return "invalidation"
    if any(token in text for token in ("拿着", "还能持", "要走", "卖", "减仓", "清仓")):
        return "hold_or_exit"
    if any(token in text for token in ("提醒", "盯", "到了叫", "到价")):
        return "reminder"
    if any(token in text for token in ("为什么", "解释", "结构", "中枢")):
        return "explain_structure"
    followup_intent = _followup_intent(text, conversation_context)
    if followup_intent:
        return followup_intent
    return "buy_window"


def upsert_chat_session(
    *,
    user_id: int,
    symbol: str,
    context_id: str,
    session_id: str | None = None,
) -> dict[str, Any]:
    canonical = normalize_symbol(symbol)
    conn = get_connection()
    try:
        now = now_text()
        row = None
        if session_id:
            row = conn.execute(
                "SELECT * FROM ai_structure_chat_sessions WHERE session_id = ? AND user_id = ? AND symbol = ?",
                (session_id, int(user_id), canonical),
            ).fetchone()
            if not row:
                return {}
        if not row:
            row = conn.execute(
                """
                SELECT *
                  FROM ai_structure_chat_sessions
                 WHERE user_id = ? AND symbol = ? AND status = 'ACTIVE'
                 ORDER BY updated_at DESC, id DESC
                 LIMIT 1
                """,
                (int(user_id), canonical),
            ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE ai_structure_chat_sessions
                   SET latest_context_id = ?,
                       updated_at = ?
                 WHERE id = ?
                """,
                (context_id, now, row["id"]),
            )
            conn.commit()
            updated = conn.execute("SELECT * FROM ai_structure_chat_sessions WHERE id = ?", (row["id"],)).fetchone()
            return dict(updated)
        new_session_id = _new_session_id(user_id=user_id, symbol=canonical)
        conn.execute(
            """
            INSERT INTO ai_structure_chat_sessions (
                session_id, user_id, symbol, latest_context_id, status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?)
            """,
            (new_session_id, int(user_id), canonical, context_id, now, now),
        )
        conn.commit()
        created = conn.execute("SELECT * FROM ai_structure_chat_sessions WHERE session_id = ?", (new_session_id,)).fetchone()
        return dict(created)
    finally:
        conn.close()


def list_chat_sessions(*, user_id: int, symbol: str) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
              FROM ai_structure_chat_sessions
             WHERE user_id = ? AND symbol = ?
             ORDER BY updated_at DESC, id DESC
            """,
            (int(user_id), normalize_symbol(symbol)),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def list_chat_messages(*, user_id: int, session_id: str) -> list[dict[str, Any]] | None:
    conn = get_connection()
    try:
        session = conn.execute(
            "SELECT * FROM ai_structure_chat_sessions WHERE user_id = ? AND session_id = ?",
            (int(user_id), session_id),
        ).fetchone()
        if not session:
            return None
        rows = conn.execute(
            """
            SELECT *
              FROM ai_structure_chat_messages
             WHERE user_id = ? AND session_id = ?
             ORDER BY created_at ASC, id ASC
            """,
            (int(user_id), session_id),
        ).fetchall()
        return [_message_row(row) for row in rows]
    finally:
        conn.close()


def get_recent_conversation_context(*, user_id: int, session_id: str, limit: int = 4) -> dict[str, Any]:
    messages = list_chat_messages(user_id=user_id, session_id=session_id) or []
    recent = messages[-max(1, limit):]
    turns = [
        {
            "question_text": item.get("question_text") or "",
            "intent_type": item.get("intent_type") or "",
            "context_id": item.get("context_id") or "",
            "message_id": item.get("message_id") or "",
            "evidence_refs": item.get("evidence_refs") or [],
        }
        for item in recent
    ]
    last = turns[-1] if turns else {}
    return {
        "version": "ai_structure_conversation_context.v1",
        "turn_count": len(messages),
        "last_intent_type": last.get("intent_type") or None,
        "last_context_id": last.get("context_id") or None,
        "last_message_id": last.get("message_id") or None,
        "recent_turns": turns,
    }


def save_chat_message(
    *,
    user_id: int,
    symbol: str,
    session_id: str,
    context_id: str,
    question_text: str,
    intent_type: str,
    answer_payload: dict[str, Any],
    evidence_refs: list[str],
    reminder_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    message_key = stable_hash({
        "user_id": int(user_id),
        "session_id": session_id,
        "context_id": context_id,
        "question": question_text,
        "intent_type": intent_type,
        "nonce": uuid.uuid4().hex,
    })
    message_id = f"v5msg_{message_key[:16]}"
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO ai_structure_chat_messages (
                message_id, session_id, user_id, symbol, context_id, role,
                question_text, intent_type, answer_json, evidence_refs_json,
                reminder_candidates_json, risk_disclaimer, created_at
            )
            VALUES (?, ?, ?, ?, ?, 'assistant', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                session_id,
                int(user_id),
                normalize_symbol(symbol),
                context_id,
                question_text,
                intent_type,
                _json(answer_payload),
                _json(evidence_refs),
                _json(reminder_candidates),
                RISK_DISCLAIMER,
                now_text(),
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM ai_structure_chat_messages WHERE message_id = ?", (message_id,)).fetchone()
        return _message_row(row)
    finally:
        conn.close()


def _build_answer(
    *,
    question: str,
    intent_type: str,
    context: dict[str, Any],
    chart_focus: dict[str, Any],
    data_status: dict[str, Any] | None = None,
    memory_context: dict[str, Any] | None = None,
    review_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    symbol = context["symbol"]
    boundary = context.get("boundary") or {}
    level = chart_focus.get("level") or ""
    center = (((boundary.get("levels") or {}).get(level) or {}).get("active_center") or {})
    zg = _num(center.get("zg"))
    zd = _num(center.get("zd"))
    background_note = _background_note(context)
    freshness_note = _freshness_note(data_status)
    position = (context.get("raw_context") or {}).get("position_context") or {}
    holding_text = "你现在有持仓，先把防守线看清楚" if position.get("has_position") else "你现在是空仓，重点是等触发条件而不是追问结论"
    if zg <= 0 or zd <= 0:
        coach = f"{freshness_note}{symbol} 目前结构边界不足，无法判断。先等 CZSC 快照刷新出有效中枢，再讨论观察窗口。{RISK_DISCLAIMER}"
        return {"coach_answer": coach, "referenced_boundaries": []}
    if intent_type == "invalidation":
        coach = (
            f"{freshness_note}这只票先看 {level} 级别下沿 {zd:.2f}。如果有效跌破这里，当前观察分支就要降级；"
            f"重新站回 {zg:.2f} 上方，弱化信号才算缓和。{RISK_DISCLAIMER}"
        )
    elif intent_type == "hold_or_exit":
        coach = (
            f"{freshness_note}{holding_text}：{level} 级别 {zd:.2f} 是当前防守边界，{zg:.2f} 是重新转强观察线。"
            f"我不能替你下卖出结论，但可以把跌破 {zd:.2f} 设成复核提醒。{RISK_DISCLAIMER}"
        )
    elif intent_type == "reminder":
        coach = (
            f"{freshness_note}可以围绕两个条件设提醒：站上 {zg:.2f} 后观察是否回踩不破，或跌破 {zd:.2f} 后复核结构失效。"
            f"提醒只帮助你复核，不代表交易指令。{RISK_DISCLAIMER}"
        )
    elif intent_type == "explain_structure":
        coach = (
            f"{freshness_note}当前回答只引用 {level} 级别中枢：上沿 {zg:.2f}、下沿 {zd:.2f}。"
            f"站上上沿是观察增强，跌破下沿是观察失效；中间区域不适合给确定性判断。"
            f"{background_note}{RISK_DISCLAIMER}"
        )
    elif intent_type == "review":
        coach = _review_answer(symbol=symbol, review_context=review_context, memory_context=memory_context)
    elif intent_type == "out_of_scope":
        coach = (
            f"{freshness_note}这个问题超出当前结构教练边界，我不能给目标价、荐股、基本面买卖结论或收益预测。"
            f"当前只能回到 {level} 级别结构：站上 {zg:.2f} 才是观察增强，跌破 {zd:.2f} 就先按分支失效复核。"
            f"{background_note}{RISK_DISCLAIMER}"
        )
    else:
        coach = (
            f"{freshness_note}不能直接回答“现在买”。更稳的说法是：只有站上 {level} 级别上沿 {zg:.2f}，"
            f"并且回踩不跌回 {zd:.2f} 下方，才进入观察窗口；跌破 {zd:.2f} 就先不看这条分支。"
            f"{background_note}{RISK_DISCLAIMER}"
        )
    if intent_type != "review":
        coach = _apply_memory_warning(coach, memory_context)
    return {
        "coach_answer": coach,
        "referenced_boundaries": [
            {"role": "trigger", "level": level, "price": zg},
            {"role": "invalidation", "level": level, "price": zd},
        ],
    }


def _review_answer(
    *,
    symbol: str,
    review_context: dict[str, Any] | None,
    memory_context: dict[str, Any] | None,
) -> str:
    items = (review_context or {}).get("items") or []
    warnings = (memory_context or {}).get("active_warnings") or []
    if not items:
        return f"{symbol} 还没有可复盘的结构分支结果。先等提醒触发或 outcome worker 生成复盘记录，再看纪律偏差。{RISK_DISCLAIMER}"
    latest = items[0]
    mistake_items = [item for item in items if item.get("is_mistake")]
    latest_text = _review_item_text(latest)
    if mistake_items:
        mistake = mistake_items[0]
        warning = str((warnings[0] if warnings else {}).get("text") or "").strip()
        warning_text = f"历史纪律提示：{warning}。" if warning else ""
        return (
            f"{symbol} 最近最需要看的问题是：{_review_item_text(mistake)}。"
            f"{warning_text}最新一条复盘是 {latest_text}。"
            f"这不是交易指令，只用于复核你的执行纪律。{RISK_DISCLAIMER}"
        )
    return (
        f"{symbol} 最近 {len(items)} 条结构复盘里，还没有记录到“结构失效后未处理”的纪律错误。"
        f"最新一条是 {latest_text}。继续看触发线和失败线是否被实际验证。{RISK_DISCLAIMER}"
    )


def _context_data_status(*, user_id: int, symbol: str, context: dict[str, Any]) -> dict[str, Any]:
    levels = list(((context.get("boundary") or {}).get("levels") or {}).keys())
    status = get_ai_structure_context_status(user_id=user_id, symbol=symbol, levels=levels or None)
    return {
        "status": status.get("status") or "unknown",
        "stale_reason": status.get("stale_reason") or "",
        "missing_levels": status.get("missing_levels") or [],
        "context_id": context.get("context_id") or "",
    }


def _freshness_note(data_status: dict[str, Any] | None) -> str:
    status = (data_status or {}).get("status")
    reason = (data_status or {}).get("stale_reason") or ""
    missing = (data_status or {}).get("missing_levels") or []
    if status == "stale":
        if missing:
            return f"结构快照待刷新，当前基于上一版数据，缺少 {','.join(missing)} 级别。"
        return "结构快照待刷新，当前基于上一版数据。"
    if status == "failed":
        return f"结构刷新失败，当前只能基于上一版结构复核，原因是 {reason or 'UNKNOWN'}。"
    return ""


def _is_out_of_scope_question(text: str) -> bool:
    if not text:
        return False
    direct_tokens = ("目标价", "能涨到", "涨多少", "收益", "荐股", "推荐股票", "推荐一只", "基本面能买吗", "财报能买吗")
    if any(token in text for token in direct_tokens):
        return True
    if any(token in text for token in ("基本面", "财报", "业绩", "估值", "消息面")) and any(
        token in text for token in ("买", "卖", "目标", "涨", "推荐", "仓位")
    ):
        return True
    return False


def _review_item_text(item: dict[str, Any]) -> str:
    outcome = item.get("outcome") or "pending"
    branch_type = ((item.get("branch") or {}).get("branch_type") or "结构分支")
    price = item.get("invalidated_price") or item.get("triggered_price") or item.get("trigger_price")
    price_text = f"{_num(price):.2f}" if _num(price) > 0 else "边界价"
    if item.get("is_mistake"):
        return f"{_branch_type_text(branch_type)}在 {price_text} 附近失效后没有按计划处理"
    if outcome == "invalidated":
        return f"{_branch_type_text(branch_type)}在 {price_text} 附近失效"
    if outcome == "triggered":
        return f"{_branch_type_text(branch_type)}在 {price_text} 附近触发"
    if outcome == "expired":
        return f"{_branch_type_text(branch_type)}到期未触发"
    return f"{_branch_type_text(branch_type)}仍在观察"


def _branch_type_text(branch_type: str) -> str:
    return {
        "observe_breakout": "突破观察分支",
        "invalidation_watch": "失效观察分支",
        "holding_defense": "持仓防守分支",
    }.get(branch_type, branch_type or "结构分支")


def _background_note(context: dict[str, Any]) -> str:
    background = context.get("background") or {}
    fundamental = background.get("fundamental") or {}
    if fundamental.get("status") != "available":
        return ""
    summary = str(fundamental.get("summary") or "").strip()
    verdict = str(fundamental.get("verdict") or "").strip()
    parts = []
    if verdict:
        parts.append(f"基本面背景为{verdict}")
    if summary:
        parts.append(summary)
    if not parts:
        return ""
    text = "，".join(parts)
    return f"背景层只作观察背景：{text}；它不能替代 CZSC 触发线和失败线。"


def _followup_intent(text: str, conversation_context: dict[str, Any] | None) -> str | None:
    last_intent = (conversation_context or {}).get("last_intent_type")
    if not last_intent:
        return None
    if any(token in text for token in ("那", "如果", "这个", "它", "继续", "还有", "呢", "然后")):
        if any(token in text for token in ("跌", "破", "失效", "不看", "防守")):
            return "invalidation"
        if any(token in text for token in ("提醒", "盯", "到价", "叫我")):
            return "reminder"
        if any(token in text for token in ("为什么", "结构", "中枢")):
            return "explain_structure"
        if any(token in text for token in ("复盘", "错", "纪律")):
            return "review"
        if last_intent in {"buy_window", "invalidation", "hold_or_exit", "reminder", "explain_structure", "review"}:
            return last_intent
    return None


def _apply_memory_warning(coach: str, memory_context: dict[str, Any] | None) -> str:
    warnings = (memory_context or {}).get("active_warnings") or []
    if not warnings:
        return coach
    text = str(warnings[0].get("text") or "").strip()
    if not text:
        return coach
    warning = f"历史纪律提示：{text}"
    if coach.endswith(RISK_DISCLAIMER):
        return f"{coach[:-len(RISK_DISCLAIMER)]}{warning}。{RISK_DISCLAIMER}"
    return f"{coach}\n\n{warning}"


def _reminder_candidates(*, intent_type: str, context: dict[str, Any], chart_focus: dict[str, Any]) -> list[dict[str, Any]]:
    if intent_type in {"review", "out_of_scope"}:
        return []
    boundary = context.get("boundary") or {}
    level = chart_focus.get("level") or ""
    level_item = ((boundary.get("levels") or {}).get(level) or {})
    center = level_item.get("active_center") or {}
    evidence = level_item.get("evidence") or {}
    candidates = []
    zg = _num(center.get("zg"))
    zd = _num(center.get("zd"))
    if intent_type in {"buy_window", "reminder", "explain_structure"} and zg > 0:
        candidates.append({
            "type": "price_cross",
            "direction": "ABOVE",
            "trigger_price": zg,
            "level": level,
            "evidence_id": evidence.get("trigger_line"),
            "message": f"站上 {level} 级别中枢上沿后复核观察窗口",
            "risk_disclaimer": RISK_DISCLAIMER,
        })
    if intent_type in {"invalidation", "hold_or_exit", "reminder", "buy_window"} and zd > 0:
        candidates.append({
            "type": "price_cross",
            "direction": "BELOW",
            "trigger_price": zd,
            "level": level,
            "evidence_id": evidence.get("invalidation_line"),
            "message": f"跌破 {level} 级别中枢下沿后复核结构失效",
            "risk_disclaimer": RISK_DISCLAIMER,
        })
    return [item for item in candidates if item.get("evidence_id")]


def _message_row(row) -> dict[str, Any]:
    data = dict(row)
    data["answer"] = json.loads(data.pop("answer_json") or "{}")
    data["evidence_refs"] = json.loads(data.pop("evidence_refs_json") or "[]")
    data["reminder_candidates"] = json.loads(data.pop("reminder_candidates_json") or "[]")
    return data


def _new_session_id(*, user_id: int, symbol: str) -> str:
    digest = stable_hash({"user_id": int(user_id), "symbol": symbol, "nonce": uuid.uuid4().hex})
    return f"v5chat_{digest[:16]}"


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
