"""AI Native V5 deterministic structure chat.

This service answers from saved AI Structure Context only. It does not call
CZSC, old radar, or any heavy structure path.
"""

from __future__ import annotations

import json
import asyncio
import uuid
from typing import Any

from server.config import AI_NATIVE_LLM_TIMEOUT
from server.db.database import get_connection
from server.domain.symbols import normalize_symbol
from server.engines.ai_native.czsc_snapshot_service import DEFAULT_LEVELS, now_text, stable_hash
from server.engines.ai_native.structure_context_service import (
    _has_configured_ai_native_key,
    get_ai_structure_context_status,
    get_latest_ai_structure_context,
    get_reasoning_run_for_context,
    reasoning_availability,
)
from server.engines.ai_native.unified_reasoning_service import ALL_UNIFIED_FULL_TEXT_VERSIONS
from server.engines.ai_native.ai_trigger_service import (
    MODE_SHORT_ANSWER,
    TRIGGER_USER_QUESTION,
    insert_ai_trigger_log,
)
from server.engines.ai_native.reasoning_continuity_service import build_reasoning_continuity_context
from server.engines.ai_native.structure_evidence_service import (
    chart_focus_for_intent,
    ensure_evidence_ids_belong_to_context,
)
from server.services.intraday_observation_service import get_intraday_observation, get_intraday_observation_snapshot
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
        return _answer_without_context(
            user_id=user_id,
            symbol=canonical,
            question=question,
            session_id=session_id,
        )
    if not _is_llm_reasoning_ready(context):
        return _answer_reasoning_unavailable(
            user_id=user_id,
            symbol=canonical,
            question=question,
            session_id=session_id,
            context=context,
        )
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
    runtime_context = _chat_runtime_context(context=context, data_status=data_status, chart_focus=chart_focus)
    intraday_observation = _chat_intraday_observation(canonical)
    chat_current_price = _chat_current_price(runtime_context, intraday_observation)
    reasoning_continuity_context = build_reasoning_continuity_context(
        user_id=user_id,
        symbol=canonical,
        current_price=chat_current_price,
        intraday_observation=intraday_observation,
        prompt_versions=ALL_UNIFIED_FULL_TEXT_VERSIONS,
    )
    runtime_context["chat_current_price"] = chat_current_price
    runtime_context["chat_current_price_source"] = "intraday_quote" if _num((intraday_observation.get("quote") or {}).get("price")) > 0 else runtime_context.get("price_source")
    memory_context = get_memory_context_for_chat(user_id=user_id, symbol=canonical)
    review_context = (
        list_symbol_outcome_reviews(user_id=user_id, symbol=canonical, limit=5)
        if intent_type == "review"
        else None
    )
    answer = _build_ai_answer_from_full_reasoning(
        user_id=user_id,
        symbol=canonical,
        question=question,
        intent_type=intent_type,
        context=context,
        chart_focus=chart_focus,
        runtime_context=runtime_context,
        intraday_observation=intraday_observation,
        reasoning_continuity_context=reasoning_continuity_context,
        data_status=data_status,
        memory_context=memory_context,
        review_context=review_context,
        conversation_context=conversation_context,
    ) or _build_answer(
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
        "runtime_context": runtime_context,
        "intraday_observation": intraday_observation,
        "reasoning_continuity_context": reasoning_continuity_context,
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


def _answer_reasoning_unavailable(
    *,
    user_id: int,
    symbol: str,
    question: str,
    session_id: str | None = None,
    context: dict[str, Any],
) -> dict[str, Any] | None:
    session = upsert_chat_session(
        user_id=user_id,
        symbol=symbol,
        context_id=context.get("context_id") or "",
        session_id=session_id,
    )
    if not session:
        return None
    conversation_context = get_recent_conversation_context(
        user_id=user_id,
        session_id=session["session_id"],
    )
    intent_type = classify_intent(question, conversation_context=conversation_context)
    data_status = _context_data_status(user_id=user_id, symbol=symbol, context=context)
    answer = _build_reasoning_unavailable_answer(context)
    payload = {
        "session_id": session["session_id"],
        "context_id": context.get("context_id") or "",
        "answer": answer,
        "coach_answer": answer,
        "intent_type": intent_type,
        "referenced_boundaries": [],
        "chart_focus": {
            "level": "",
            "snapshot_id": "",
            "evidence_ids": [],
            "prices": [],
        },
        "suggested_reminders": [],
        "data_status": data_status,
        "memory_context": get_memory_context_for_chat(user_id=user_id, symbol=symbol),
        "review_context": None,
        "conversation_context": conversation_context,
        "risk_disclaimer": RISK_DISCLAIMER,
    }
    message = save_chat_message(
        user_id=user_id,
        symbol=symbol,
        session_id=session["session_id"],
        context_id=context.get("context_id") or "",
        question_text=question,
        intent_type=intent_type,
        answer_payload=payload,
        evidence_refs=[],
        reminder_candidates=[],
    )
    payload["message_id"] = message["message_id"]
    return payload


def _answer_without_context(
    *,
    user_id: int,
    symbol: str,
    question: str,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    session = upsert_chat_session(
        user_id=user_id,
        symbol=symbol,
        context_id="",
        session_id=session_id,
    )
    if not session:
        return None
    conversation_context = get_recent_conversation_context(
        user_id=user_id,
        session_id=session["session_id"],
    )
    intent_type = classify_intent(question, conversation_context=conversation_context)
    data_status = _context_data_status(user_id=user_id, symbol=symbol, context={})
    answer = _build_unavailable_context_answer(
        symbol=symbol,
        intent_type=intent_type,
        data_status=data_status,
    )
    payload = {
        "session_id": session["session_id"],
        "context_id": "",
        "answer": answer,
        "coach_answer": answer,
        "intent_type": intent_type,
        "referenced_boundaries": [],
        "chart_focus": {
            "level": "",
            "snapshot_id": "",
            "evidence_ids": [],
            "prices": [],
        },
        "suggested_reminders": [],
        "data_status": data_status,
        "memory_context": get_memory_context_for_chat(user_id=user_id, symbol=symbol),
        "review_context": None,
        "conversation_context": conversation_context,
        "risk_disclaimer": RISK_DISCLAIMER,
    }
    message = save_chat_message(
        user_id=user_id,
        symbol=symbol,
        session_id=session["session_id"],
        context_id="",
        question_text=question,
        intent_type=intent_type,
        answer_payload=payload,
        evidence_refs=[],
        reminder_candidates=[],
    )
    payload["message_id"] = message["message_id"]
    return payload


def _is_llm_reasoning_ready(context: dict[str, Any]) -> bool:
    return bool(reasoning_availability(context).get("ready"))


def _build_reasoning_unavailable_answer(context: dict[str, Any]) -> str:
    status = reasoning_availability(context)
    return status.get("message") or "AI 推演暂未完成，当前不展示本地算法边界。系统会在下一次刷新时重新生成完整推演。"


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
    if any(token in text for token in ("背驰", "背离")):
        return "divergence"
    if any(token in text for token in ("共振", "级别", "大级别", "小级别", "a+小b", "a＋小b")):
        return "resonance"
    if any(token in text for token in ("走势", "生长", "怎么走", "演化", "推演", "发展")):
        return "trend_growth"
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
            "answer_excerpt": _clip_text(str((item.get("answer") or {}).get("coach_answer") or ""), 220),
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


def _build_ai_answer_from_full_reasoning(
    *,
    user_id: int,
    symbol: str,
    question: str,
    intent_type: str,
    context: dict[str, Any],
    chart_focus: dict[str, Any],
    runtime_context: dict[str, Any] | None = None,
    intraday_observation: dict[str, Any] | None = None,
    reasoning_continuity_context: dict[str, Any] | None = None,
    data_status: dict[str, Any] | None = None,
    memory_context: dict[str, Any] | None = None,
    review_context: dict[str, Any] | None = None,
    conversation_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not _has_configured_ai_native_key(user_id):
        return None
    run = get_reasoning_run_for_context(
        user_id=user_id,
        symbol=symbol,
        context_id=context.get("context_id") or "",
        source_snapshot_ids=context.get("source_snapshot_ids") or [],
    )
    full_text = str((run or {}).get("full_reasoning_text") or "").strip()
    if not full_text:
        return None
    is_unified = str((run or {}).get("prompt_version") or "") in ALL_UNIFIED_FULL_TEXT_VERSIONS
    trigger_started_at = now_text()
    trigger_metadata = {
        "intent_type": intent_type,
        "context_id": context.get("context_id") or "",
        "question_chars": len(question or ""),
        "answer_source": "unified" if is_unified else "legacy_context",
    }
    if is_unified:
        wants_detail = _wants_detailed_chat_answer(question)
        chat_context = _build_chat_context_pack(
            question=question,
            intent_type=intent_type,
            intraday_observation=intraday_observation or {},
            reasoning_continuity_context=reasoning_continuity_context or {},
            conversation_context=conversation_context or {},
            runtime_context=runtime_context or {},
        )
        prompt = {
            "version": "unified_reasoning_chat.v1",
            "chat_style": "intraday_companion",
            "symbol": symbol,
            "question": question,
            "chat_context": chat_context,
            "full_reasoning_excerpt": _clip_text(full_text, 3200),
            "position_context": (context.get("raw_context") or {}).get("position_context") or {},
            "intraday_observation": intraday_observation or {},
            "reasoning_continuity_context": reasoning_continuity_context or {},
            "runtime_context": runtime_context or {},
            "data_status": data_status or {},
            "memory_context": memory_context or {},
            "conversation_context": conversation_context or {},
            "chart_focus": chart_focus,
            "answer_contract": {
                "mode": "detailed" if wants_detail else "concise",
                "preference": "像盘中搭档一样回答当前这句话；默认短，用户要求详细再展开。",
                "context_priority": "chat_context 是当前事实摘要；完整推演只是背景锚点。",
                "risk_disclaimer": RISK_DISCLAIMER,
            },
        }
        system_prompt = (
            "你是用户的盘中盯盘搭档。像正常对话一样，先回答用户此刻问的事。"
            "chat_context、盘中观察、连续性上下文和完整推演都是事实材料，不是固定模板。"
            "如果盘中新价、MACD或触发状态改变了上一轮看法，直接说变化在哪里。"
            "默认短答；用户要求详细时再解释逻辑。"
            f"{RISK_DISCLAIMER}。"
        )
    else:
        prompt = {
            "version": "ai_structure_chat_from_full_reasoning.v1",
            "symbol": symbol,
            "question": question,
            "intent_type": intent_type,
            "full_reasoning_text": full_text,
            "summary": {
                "coach_summary": context.get("coach_summary") or "",
                "reasoning": context.get("reasoning") or {},
            },
            "position_context": (context.get("raw_context") or {}).get("position_context") or {},
            "memory_context": memory_context or {},
            "review_context": review_context or {},
            "conversation_context": conversation_context or {},
            "data_status": data_status or {},
            "runtime_context": runtime_context or {},
            "chart_focus": chart_focus,
        }
        prompt["rules"] = {
            "answer_from_full_reasoning_only": True,
            "do_not_recalculate_structure": True,
            "do_not_add_new_price_levels": True,
            "no_direct_trade_instruction": True,
            "required_risk_disclaimer": RISK_DISCLAIMER,
        }
        system_prompt = (
            "你是 CT-OS AI Native V5 的问答解释层。"
            "你只能根据已保存的完整推演全文、摘要、持仓和历史记忆回答。"
            "不要重新计算中枢、笔、背驰或级别结构，不要引入新价格。"
            "回答用户真实问题，给条件化观察、风险边界、提醒/复盘建议。"
            "不要直接给买入、卖出、满仓、清仓指令。"
            f"结尾必须包含：{RISK_DISCLAIMER}"
        )
    try:
        from server.services.llm_service import AIModelRoute, LLMService
        try:
            asyncio.get_running_loop()
            return None
        except RuntimeError:
            pass
        answer_text = asyncio.run(
            LLMService().infer_ai_native_markdown(
                system_prompt,
                _json(prompt),
                user_id=user_id,
                model_route=AIModelRoute(
                    model_name="deepseek-v4-flash" if is_unified else "",
                    thinking_enabled=False if is_unified else True,
                    reasoning_effort="high",
                    timeout_seconds=45 if is_unified else max(float(AI_NATIVE_LLM_TIMEOUT), 150),
                    max_tokens=700 if is_unified else 2400,
                ),
            )
        )
    except Exception as exc:
        insert_ai_trigger_log(
            user_id=user_id,
            symbol=symbol,
            mode=MODE_SHORT_ANSWER,
            trigger_reason=TRIGGER_USER_QUESTION,
            decision="error",
            error_message=str(exc)[:500],
            metadata=trigger_metadata,
            started_at=trigger_started_at,
        )
        return None
    answer_text = str(answer_text or "").strip()
    if not answer_text:
        insert_ai_trigger_log(
            user_id=user_id,
            symbol=symbol,
            mode=MODE_SHORT_ANSWER,
            trigger_reason=TRIGGER_USER_QUESTION,
            decision="skipped",
            skip_reason="EMPTY_ANSWER",
            metadata=trigger_metadata,
            started_at=trigger_started_at,
        )
        return None
    if RISK_DISCLAIMER not in answer_text:
        answer_text = f"{answer_text.rstrip('。')}。{RISK_DISCLAIMER}"
    answer_text = _apply_memory_warning(answer_text, memory_context)
    trigger_log = insert_ai_trigger_log(
        user_id=user_id,
        symbol=symbol,
        mode=MODE_SHORT_ANSWER,
        trigger_reason=TRIGGER_USER_QUESTION,
        decision="generated",
        context_id=str(context.get("context_id") or ""),
        metadata=trigger_metadata,
        started_at=trigger_started_at,
    )
    return {
        "coach_answer": answer_text,
        "ai_trigger": trigger_log,
        "referenced_boundaries": _referenced_boundaries(
            chart_focus.get("level") or "",
            _num(((((context.get("boundary") or {}).get("levels") or {}).get(chart_focus.get("level") or "") or {}).get("active_center") or {}).get("zg")),
            _num(((((context.get("boundary") or {}).get("levels") or {}).get(chart_focus.get("level") or "") or {}).get("active_center") or {}).get("zd")),
        ),
    }


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
    reasoning = context.get("reasoning") or {}
    trend_growth = reasoning.get("trend_growth") or {}
    divergence_view = reasoning.get("divergence_view") or {}
    resonance_view = reasoning.get("resonance_view") or {}
    coach_summary = str(context.get("coach_summary") or reasoning.get("coach_summary") or "").strip()
    reasoning_intro = _reasoning_intro(reasoning)
    position = (context.get("raw_context") or {}).get("position_context") or {}
    holding_text = "你现在有持仓，先把防守线看清楚" if position.get("has_position") else "你现在是空仓，重点是等触发条件而不是追问结论"
    if intent_type == "out_of_scope":
        boundary_text = (
            f"当前只能回到 {level} 级别结构：站上 {zg:.2f} 才是观察增强，跌破 {zd:.2f} 就先按分支失效复核。"
            if zg > 0 and zd > 0
            else "当前 CZSC 边界不足，只能先等待结构快照刷新出有效中枢。"
        )
        coach = (
            f"{freshness_note}这个问题超出当前结构教练边界，我不能给目标价、荐股、基本面买卖结论或收益预测。"
            f"{boundary_text}{background_note}{RISK_DISCLAIMER}"
        )
        return {"coach_answer": coach, "referenced_boundaries": _referenced_boundaries(level, zg, zd)}
    if zg <= 0 or zd <= 0:
        coach = f"{freshness_note}{symbol} 目前结构边界不足，无法判断。先等 CZSC 快照刷新出有效中枢，再讨论观察窗口。{RISK_DISCLAIMER}"
        return {"coach_answer": coach, "referenced_boundaries": []}
    if intent_type == "invalidation":
        failure_path = str(trend_growth.get("failure_path") or "").strip()
        coach = (
            f"{freshness_note}这只票先看 {level} 级别下沿 {zd:.2f}。如果有效跌破这里，当前观察分支就要降级；"
            f"重新站回 {zg:.2f} 上方，弱化信号才算缓和。"
            f"{failure_path}{RISK_DISCLAIMER}"
        )
    elif intent_type == "hold_or_exit":
        coach = (
            f"{freshness_note}{holding_text}：{reasoning_intro}{level} 级别 {zd:.2f} 是当前防守边界，{zg:.2f} 是重新转强观察线。"
            f"我不能替你下卖出结论，但可以把跌破 {zd:.2f} 设成复核提醒。{RISK_DISCLAIMER}"
        )
    elif intent_type == "reminder":
        coach = (
            f"{freshness_note}可以围绕两个条件设提醒：站上 {zg:.2f} 后观察是否回踩不破，或跌破 {zd:.2f} 后复核结构失效。"
            f"提醒只帮助你复核，不代表交易指令。{RISK_DISCLAIMER}"
        )
    elif intent_type == "explain_structure":
        summary = str(reasoning.get("structure_summary") or coach_summary or "").strip()
        coach = (
            f"{freshness_note}{summary}当前回答只引用 {level} 级别中枢：上沿 {zg:.2f}、下沿 {zd:.2f}。"
            f"站上上沿是观察增强，跌破下沿是观察失效；中间区域不适合给确定性判断。"
            f"{background_note}{RISK_DISCLAIMER}"
        )
    elif intent_type == "trend_growth":
        coach = (
            f"{freshness_note}{reasoning_intro}"
            f"走势生长路径：{trend_growth.get('growth_path') or '当前推演里还没有足够清晰的生长路径。'}"
            f"下一步确认：{trend_growth.get('next_confirmation') or '等待触发级别收线确认。'}"
            f"失败路径：{trend_growth.get('failure_path') or f'跌破 {zd:.2f} 后复核分支失效。'}"
            f"{RISK_DISCLAIMER}"
        )
    elif intent_type == "divergence":
        coach = (
            f"{freshness_note}{reasoning_intro}"
            f"背驰观察：{divergence_view.get('status') or 'unclear'}，级别 {divergence_view.get('level') or level}。"
            f"{divergence_view.get('evidence') or '当前推演没有确认背驰，只能继续观察离开段和回拉段的力度。'}"
            f"{divergence_view.get('risk_note') or '若离开后不能延续，需要在触发级别复核潜在背驰。'}"
            f"{RISK_DISCLAIMER}"
        )
    elif intent_type == "resonance":
        coach = (
            f"{freshness_note}{reasoning_intro}"
            f"级别关系：{resonance_view.get('higher_level_context') or f'{level} 级别中枢边界'}；"
            f"触发观察：{resonance_view.get('lower_level_trigger') or f'{level} 级别承接'}。"
            f"共振类型：{resonance_view.get('resonance_type') or 'unclear'}。"
            f"{resonance_view.get('conflict_note') or '若背景和结构冲突，仍以触发线和失败线为纪律边界。'}"
            f"{RISK_DISCLAIMER}"
        )
    elif intent_type == "review":
        coach = _review_answer(
            symbol=symbol,
            review_context=review_context,
            memory_context=memory_context,
            freshness_note=freshness_note,
        )
    else:
        branch_text = _branch_answer_text(reasoning)
        coach = (
            f"{freshness_note}不能直接回答“现在买”。{reasoning_intro}{branch_text}"
            f"更稳的说法是：只有站上 {level} 级别上沿 {zg:.2f}，"
            f"并且回踩不跌回 {zd:.2f} 下方，才进入观察窗口；跌破 {zd:.2f} 就先不看这条分支。"
            f"{background_note}{RISK_DISCLAIMER}"
        )
    if intent_type != "review":
        coach = _apply_memory_warning(coach, memory_context)
    return {
        "coach_answer": coach,
        "referenced_boundaries": _referenced_boundaries(level, zg, zd),
    }


def _build_unavailable_context_answer(
    *,
    symbol: str,
    intent_type: str,
    data_status: dict[str, Any],
) -> str:
    status = data_status.get("status") or "unknown"
    reason = data_status.get("stale_reason") or ""
    missing = data_status.get("missing_levels") or []
    missing_text = f"，缺少 {','.join(missing)} 级别数据" if missing else ""
    if status == "pending":
        lead = f"{symbol} 的 CZSC 结构上下文正在生成{missing_text}。"
    elif status == "failed":
        lead = f"{symbol} 的结构刷新失败{missing_text}，原因是 {reason or 'UNKNOWN'}。"
    elif status == "no_snapshot":
        lead = f"{symbol} 还没有可用的 CZSC 结构快照{missing_text}。"
    else:
        lead = f"{symbol} 还没有可用的 CZSC 结构上下文{missing_text}。"

    if intent_type == "out_of_scope":
        return (
            f"{lead}这个问题也超出结构教练边界，我不能给目标价、荐股、收益预测或基本面买卖结论。"
            f"先等 K 线事实层生成结构快照，再只围绕触发线、失败线和提醒条件复核。{RISK_DISCLAIMER}"
        )
    if intent_type == "review":
        return (
            f"{lead}现在不足以做结构复盘，因为还没有可引用的上下文和证据线。"
            f"先等结构分支和 outcome 生成后，再看纪律偏差。{RISK_DISCLAIMER}"
        )
    return (
        f"{lead}我现在不能回答“能不能买/要不要卖”这类结论。"
        f"先刷新或等待后台生成结构上下文；有了触发线和失败线后，我只能给条件化观察、风险边界和提醒建议。"
        f"{RISK_DISCLAIMER}"
    )


def _review_answer(
    *,
    symbol: str,
    review_context: dict[str, Any] | None,
    memory_context: dict[str, Any] | None,
    freshness_note: str = "",
) -> str:
    items = (review_context or {}).get("items") or []
    warnings = (memory_context or {}).get("active_warnings") or []
    if not items:
        return f"{freshness_note}{symbol} 还没有可复盘的结构分支结果。先等提醒触发或 outcome worker 生成复盘记录，再看纪律偏差。{RISK_DISCLAIMER}"
    latest = items[0]
    mistake_items = [item for item in items if item.get("is_mistake")]
    latest_text = _review_item_text(latest)
    if mistake_items:
        mistake = mistake_items[0]
        warning = str((warnings[0] if warnings else {}).get("text") or "").strip()
        warning_text = f"历史纪律提示：{warning}。" if warning else ""
        return (
            f"{freshness_note}{symbol} 最近最需要看的问题是：{_review_item_text(mistake)}。"
            f"{warning_text}最新一条复盘是 {latest_text}。"
            f"这不是交易指令，只用于复核你的执行纪律。{RISK_DISCLAIMER}"
        )
    return (
        f"{freshness_note}{symbol} 最近 {len(items)} 条结构复盘里，还没有记录到“结构失效后未处理”的纪律错误。"
        f"最新一条是 {latest_text}。继续看触发线和失败线是否被实际验证。{RISK_DISCLAIMER}"
    )


def _referenced_boundaries(level: str, zg: float, zd: float) -> list[dict[str, Any]]:
    items = []
    if zg > 0:
        items.append({"role": "trigger", "level": level, "price": zg})
    if zd > 0:
        items.append({"role": "invalidation", "level": level, "price": zd})
    return items


def _reasoning_intro(reasoning: dict[str, Any]) -> str:
    main_level = str(reasoning.get("main_level") or "")
    trigger_level = str(reasoning.get("trigger_level") or "")
    summary = str(reasoning.get("structure_summary") or "").strip()
    parts = []
    if main_level or trigger_level:
        parts.append(f"当前推演主级别 {main_level or '未知'}，触发级别 {trigger_level or main_level or '未知'}。")
    if summary:
        parts.append(summary)
    return "".join(parts)


def _branch_answer_text(reasoning: dict[str, Any]) -> str:
    branches = reasoning.get("scenario_branches") or []
    if not isinstance(branches, list) or not branches:
        return ""
    first = next((item for item in branches if isinstance(item, dict)), None)
    if not first:
        return ""
    title = str(first.get("title") or "").strip()
    trigger = (first.get("trigger_condition") or {}) if isinstance(first.get("trigger_condition"), dict) else {}
    invalidate = (first.get("invalidate_condition") or {}) if isinstance(first.get("invalidate_condition"), dict) else {}
    trigger_label = str(trigger.get("label") or "").strip()
    invalidate_label = str(invalidate.get("label") or "").strip()
    if not (title or trigger_label or invalidate_label):
        return ""
    return f"当前优先分支：{title or '结构观察'}。{trigger_label}{invalidate_label}"


def _context_data_status(*, user_id: int, symbol: str, context: dict[str, Any]) -> dict[str, Any]:
    levels = list(DEFAULT_LEVELS)
    status = get_ai_structure_context_status(user_id=user_id, symbol=symbol, levels=levels or None)
    return {
        "status": status.get("status") or "unknown",
        "stale_reason": status.get("stale_reason") or "",
        "missing_levels": status.get("missing_levels") or [],
        "reasoning_status": status.get("reasoning_status") or reasoning_availability(context or None),
        "context_id": context.get("context_id") or "",
    }


def _chat_runtime_context(
    *,
    context: dict[str, Any],
    data_status: dict[str, Any],
    chart_focus: dict[str, Any],
) -> dict[str, Any]:
    level = chart_focus.get("level") or ((context.get("boundary") or {}).get("primary_level") or "")
    levels = (context.get("boundary") or {}).get("levels") or {}
    level_item = levels.get(level) or {}
    position = (context.get("raw_context") or {}).get("position_context") or {}
    meta = ((context.get("reasoning") or {}).get("reasoning_meta") or context.get("reasoning_meta") or {})
    current_price = _num(level_item.get("current_price"))
    price_source = f"snapshot:{level}" if current_price > 0 else ""
    if current_price <= 0:
        current_price = _num(position.get("current_price"))
        price_source = "position_context" if current_price > 0 else ""
    if current_price <= 0:
        current_price = _num(meta.get("price"))
        price_source = "reasoning_meta" if current_price > 0 else ""
    return {
        "current_price": current_price,
        "price_source": price_source,
        "focus_level": level,
        "context_id": context.get("context_id") or "",
        "context_updated_at": context.get("updated_at") or "",
        "reasoning_status": data_status.get("reasoning_status") or reasoning_availability(context or None),
        "data_status": {
            "status": data_status.get("status") or "unknown",
            "stale_reason": data_status.get("stale_reason") or "",
            "missing_levels": data_status.get("missing_levels") or [],
        },
        "think": {
            "ready": bool((data_status.get("reasoning_status") or {}).get("ready")),
            "provider": str(meta.get("provider") or ""),
            "llm_status": str(meta.get("llm_status") or ""),
            "full_reasoning_available": bool(meta.get("full_reasoning_available")),
            "full_reasoning_run_id": str(meta.get("full_reasoning_run_id") or ""),
        },
    }


def _chat_intraday_observation(symbol: str) -> dict[str, Any]:
    """Best-effort intraday preview for chat; never blocks the fallback answer."""
    try:
        asyncio.get_running_loop()
        return get_intraday_observation_snapshot(symbol)
    except RuntimeError:
        pass
    try:
        return asyncio.run(get_intraday_observation(symbol))
    except Exception:
        return {}


def _chat_current_price(runtime_context: dict[str, Any], intraday_observation: dict[str, Any]) -> float:
    quote_price = _num((intraday_observation.get("quote") or {}).get("price"))
    if quote_price > 0:
        return quote_price
    for key in ("chat_current_price", "current_price"):
        value = _num(runtime_context.get(key))
        if value > 0:
            return value
    return 0.0


def _build_chat_context_pack(
    *,
    question: str,
    intent_type: str,
    intraday_observation: dict[str, Any],
    reasoning_continuity_context: dict[str, Any],
    conversation_context: dict[str, Any],
    runtime_context: dict[str, Any],
) -> dict[str, Any]:
    """Compact facts for the short-answer LLM, shaped for conversation."""
    triggers = reasoning_continuity_context.get("trigger_status_since_last_run") or []
    return {
        "version": "ai_structure_chat_context.v1",
        "question": {
            "text": question,
            "intent_type": intent_type,
            "wants_detail": _wants_detailed_chat_answer(question),
        },
        "live_tape": _chat_live_tape(intraday_observation, runtime_context),
        "trigger_state": _chat_trigger_state(triggers),
        "recent_dialogue": _chat_recent_dialogue(conversation_context),
    }


def _chat_live_tape(intraday_observation: dict[str, Any], runtime_context: dict[str, Any]) -> dict[str, Any]:
    quote = intraday_observation.get("quote") or {}
    levels = intraday_observation.get("levels") or {}
    return {
        "as_of": intraday_observation.get("as_of") or "",
        "source": intraday_observation.get("source") or "",
        "usage": intraday_observation.get("usage") or "",
        "coverage": intraday_observation.get("coverage") or {},
        "price": _chat_current_price(runtime_context, intraday_observation),
        "price_source": "intraday_quote" if _num(quote.get("price")) > 0 else runtime_context.get("price_source") or "",
        "change_pct": quote.get("change_pct"),
        "levels": {
            key: _chat_level_tape(value)
            for key, value in levels.items()
            if key in {"1m", "5m", "30m"} and isinstance(value, dict)
        },
    }


def _chat_level_tape(level: dict[str, Any]) -> dict[str, Any]:
    closed = level.get("macd_closed_only") or {}
    forming = level.get("macd_with_forming") or {}
    return {
        "last_bar_at": level.get("last_bar_at") or "",
        "last_bar_status": level.get("last_bar_status") or "",
        "last_close": _num(level.get("last_close")),
        "intraday_bar_count": int(_num(level.get("intraday_bar_count"))),
        "closed_only": {
            "basis": closed.get("basis") or "closed_only",
            "macd_state": closed.get("macd_state") or "unknown",
            "macd_momentum": closed.get("macd_momentum") or "unknown",
            "volume_state": closed.get("volume_state") or "unknown",
            "ma_posture": closed.get("ma_posture") or "unknown",
        },
        "with_forming": {
            "basis": forming.get("basis") or "with_forming",
            "macd_state": forming.get("macd_state") or "unknown",
            "macd_momentum": forming.get("macd_momentum") or "unknown",
            "volume_state": forming.get("volume_state") or "unknown",
            "ma_posture": forming.get("ma_posture") or "unknown",
        },
    }


def _chat_trigger_state(triggers: list[dict[str, Any]]) -> dict[str, Any]:
    items = [item for item in triggers if isinstance(item, dict)]
    crossed = [item for item in items if item.get("status") == "crossed"]
    nearest = sorted(
        items,
        key=lambda item: abs(_num(item.get("distance_pct"))) if item.get("distance_pct") is not None else 9999,
    )
    return {
        "crossed": crossed[:3],
        "nearest": nearest[:4],
    }


def _chat_recent_dialogue(conversation_context: dict[str, Any]) -> list[dict[str, Any]]:
    turns = conversation_context.get("recent_turns") or []
    return [
        {
            "question_text": item.get("question_text") or "",
            "intent_type": item.get("intent_type") or "",
            "answer_excerpt": item.get("answer_excerpt") or "",
        }
        for item in turns[-3:]
        if item.get("question_text")
    ]


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


def _wants_detailed_chat_answer(question: str) -> bool:
    text = (question or "").strip().lower()
    if not text:
        return False
    detail_tokens = (
        "详细",
        "展开",
        "完整",
        "仔细",
        "讲清楚",
        "解释下",
        "为什么",
        "逻辑",
        "推演过程",
        "分级别",
        "step by step",
    )
    return any(token in text for token in detail_tokens)


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
    current_price = _num(level_item.get("current_price"))
    if intent_type in {"buy_window", "reminder", "explain_structure"} and zg > 0 and (current_price <= 0 or current_price < zg):
        candidates.append({
            "type": "price_cross",
            "direction": "ABOVE",
            "trigger_price": zg,
            "level": level,
            "evidence_id": evidence.get("trigger_line"),
            "message": f"站上 {level} 级别中枢上沿后复核观察窗口",
            "risk_disclaimer": RISK_DISCLAIMER,
        })
    if intent_type in {"invalidation", "hold_or_exit", "reminder", "buy_window"} and zd > 0 and (current_price <= 0 or current_price > zd):
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


def _clip_text(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return f"{value[:limit].rstrip()}\n...[已截断，完整推演仅作背景锚点]"


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
