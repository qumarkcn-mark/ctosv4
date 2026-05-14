"""AI Native V5 CZSC-only structure APIs."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from server.api.auth import get_current_user_id
from server.domain.symbols import normalize_symbol
from server.engines.ai_native.structure_chat_service import (
    answer_structure_question,
    list_chat_messages,
    list_chat_sessions,
)
from server.engines.ai_native.czsc_snapshot_service import (
    DEFAULT_COMPUTE_PROFILE,
    DEFAULT_LEVELS,
    get_latest_snapshot,
    get_snapshot_status,
    prewarm_structure_snapshots,
)
from server.engines.ai_native.scenario_branch_service import list_scenario_branches
from server.engines.ai_native.scenario_outcome_service import (
    get_symbol_memory_profile,
    list_symbol_outcome_reviews,
    settle_scenario_branch,
)
from server.engines.ai_native.pipeline_ensure_service import ensure_ai_structure_pipeline
from server.engines.ai_native.structure_context_service import (
    get_ai_structure_context_status,
    get_latest_ai_structure_context,
    prewarm_ai_structure_contexts,
)
from server.engines.ai_native.structure_evidence_service import get_chart_context
from server.engines.ai_native.structure_reminder_service import (
    ack_structure_reminder,
    create_reminder_from_chat_evidence,
    list_structure_reminders,
)
from server.engines.ai_native.universe_resolver import resolve_ai_native_universe
from server.engines.structure.structure_key import COMPUTE_PROFILES, FREQ_ALIASES, normalize_freq


router = APIRouter()


class SnapshotPrewarmRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=50)
    levels: list[str] = Field(default_factory=lambda: list(DEFAULT_LEVELS), max_length=6)
    compute_profile: str = DEFAULT_COMPUTE_PROFILE
    priority: int = Field(default=80, ge=1, le=100)
    reason: str = "manual_prewarm"


class ContextPrewarmRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=50)
    levels: list[str] = Field(default_factory=lambda: list(DEFAULT_LEVELS), max_length=6)
    compute_profile: str = DEFAULT_COMPUTE_PROFILE
    priority: int = Field(default=70, ge=1, le=100)
    reason: str = "manual_context_prewarm"


class PipelineEnsureRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list, min_length=1, max_length=20)
    levels: list[str] = Field(default_factory=lambda: list(DEFAULT_LEVELS), max_length=6)
    compute_profile: str = DEFAULT_COMPUTE_PROFILE
    priority: int = Field(default=85, ge=1, le=100)
    reason: str = "web_ai_structure_workspace"


class StructureChatRequest(BaseModel):
    symbol: str
    question: str = Field(min_length=1, max_length=500)
    session_id: Optional[str] = None


class ReminderCreateRequest(BaseModel):
    session_id: str
    message_id: str
    evidence_id: str


class ReminderAckRequest(BaseModel):
    action: str = Field(pattern="^(handled|continue_watch|ignored)$")


class OutcomeSettleRequest(BaseModel):
    branch_id: str
    current_price: Optional[float] = None
    settlement_window: str = "manual"
    checked_at: Optional[str] = None
    user_followed_plan: Optional[bool] = None
    expired: bool = False


@router.get("/universe")
def get_ai_native_universe(
    sources: str = Query(default="positions,watchlist"),
    current_user_id: int = Depends(get_current_user_id),
):
    source_list = [item.strip() for item in sources.split(",") if item.strip()]
    return {
        "status": "success",
        "data": {
            "symbols": resolve_ai_native_universe(current_user_id, source_list),
        },
    }


@router.post("/snapshots/prewarm")
def prewarm_snapshots(
    request: SnapshotPrewarmRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    _validate_compute_profile(request.compute_profile)
    levels = [_validate_level(level) for level in request.levels]
    try:
        result = prewarm_structure_snapshots(
            symbols=request.symbols,
            levels=levels,
            compute_profile=request.compute_profile,
            priority=request.priority,
            reason=request.reason,
            requested_by_user_id=current_user_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "data": result}


@router.post("/pipeline/ensure")
async def ensure_pipeline(
    request: PipelineEnsureRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    """Ensure the V5 data path is warm without running CZSC inline."""
    _validate_compute_profile(request.compute_profile)
    levels = [_validate_level(level) for level in request.levels]
    try:
        result = await ensure_ai_structure_pipeline(
            user_id=current_user_id,
            symbols=request.symbols,
            levels=levels,
            compute_profile=request.compute_profile,
            priority=request.priority,
            reason=request.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "data": result}


@router.get("/snapshots/latest/{symbol}")
def latest_snapshot(
    symbol: str,
    level: str = Query(default="day"),
    compute_profile: str = Query(default=DEFAULT_COMPUTE_PROFILE),
):
    _validate_compute_profile(compute_profile)
    normalized_level = _validate_level(level)
    snapshot = get_latest_snapshot(
        symbol=normalize_symbol(symbol),
        level=normalized_level,
        compute_profile=compute_profile,
    )
    if not snapshot:
        raise HTTPException(status_code=404, detail="snapshot not found")
    return {"status": "success", "data": snapshot}


@router.get("/snapshots/status/{symbol}")
def snapshot_status(
    symbol: str,
    level: str = Query(default="day"),
    compute_profile: str = Query(default=DEFAULT_COMPUTE_PROFILE),
):
    _validate_compute_profile(compute_profile)
    normalized_level = _validate_level(level)
    return {
        "status": "success",
        "data": get_snapshot_status(
            symbol=normalize_symbol(symbol),
            level=normalized_level,
            compute_profile=compute_profile,
        ),
    }


@router.post("/contexts/prewarm")
def prewarm_contexts(
    request: ContextPrewarmRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    _validate_compute_profile(request.compute_profile)
    levels = [_validate_level(level) for level in request.levels]
    result = prewarm_ai_structure_contexts(
        user_id=current_user_id,
        symbols=request.symbols,
        levels=levels,
        compute_profile=request.compute_profile,
        priority=request.priority,
        reason=request.reason,
    )
    return {"status": "success", "data": result}


@router.get("/contexts/latest/{symbol}")
def latest_context(symbol: str, current_user_id: int = Depends(get_current_user_id)):
    context = get_latest_ai_structure_context(user_id=current_user_id, symbol=normalize_symbol(symbol))
    if not context:
        raise HTTPException(status_code=404, detail="context not found")
    return {"status": "success", "data": context}


@router.get("/contexts/status/{symbol}")
def context_status(
    symbol: str,
    levels: str = Query(default="week,day,30,5"),
    compute_profile: str = Query(default=DEFAULT_COMPUTE_PROFILE),
    current_user_id: int = Depends(get_current_user_id),
):
    _validate_compute_profile(compute_profile)
    normalized_levels = [_validate_level(level.strip()) for level in levels.split(",") if level.strip()]
    return {
        "status": "success",
        "data": get_ai_structure_context_status(
            user_id=current_user_id,
            symbol=normalize_symbol(symbol),
            levels=normalized_levels,
            compute_profile=compute_profile,
        ),
    }


@router.get("/branches/{symbol}")
def branches(
    symbol: str,
    context_id: Optional[str] = Query(default=None),
    current_user_id: int = Depends(get_current_user_id),
):
    items = list_scenario_branches(
        user_id=current_user_id,
        symbol=normalize_symbol(symbol),
        context_id=context_id,
    )
    if context_id and not items:
        latest = get_latest_ai_structure_context(user_id=current_user_id, symbol=normalize_symbol(symbol))
        if not latest or latest["context_id"] != context_id:
            raise HTTPException(status_code=404, detail="context not found")
    return {"status": "success", "data": {"branches": items}}


@router.post("/chat")
def chat(request: StructureChatRequest, current_user_id: int = Depends(get_current_user_id)):
    try:
        result = answer_structure_question(
            user_id=current_user_id,
            symbol=normalize_symbol(request.symbol),
            question=request.question,
            session_id=request.session_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="context not found")
    return {"status": "success", "data": result}


@router.get("/chat/sessions/{symbol}")
def chat_sessions(symbol: str, current_user_id: int = Depends(get_current_user_id)):
    return {
        "status": "success",
        "data": {
            "sessions": list_chat_sessions(user_id=current_user_id, symbol=normalize_symbol(symbol)),
        },
    }


@router.get("/chat/messages")
def chat_messages(
    session_id: str = Query(...),
    current_user_id: int = Depends(get_current_user_id),
):
    messages = list_chat_messages(user_id=current_user_id, session_id=session_id)
    if messages is None:
        raise HTTPException(status_code=404, detail="session not found")
    return {"status": "success", "data": {"messages": messages}}


@router.get("/chart-context/{symbol}")
def chart_context(
    symbol: str,
    context_id: str = Query(...),
    level: str = Query(default="5"),
    evidence_ids: str = Query(default=""),
    current_user_id: int = Depends(get_current_user_id),
):
    normalized_level = _validate_level(level)
    requested_ids = [item.strip() for item in evidence_ids.split(",") if item.strip()]
    result = get_chart_context(
        user_id=current_user_id,
        symbol=normalize_symbol(symbol),
        context_id=context_id,
        level=normalized_level,
        evidence_ids=requested_ids,
    )
    if not result:
        raise HTTPException(status_code=404, detail="context not found")
    return {"status": "success", "data": result}


@router.post("/reminders")
def create_reminder(
    request: ReminderCreateRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    try:
        reminder = create_reminder_from_chat_evidence(
            user_id=current_user_id,
            session_id=request.session_id,
            message_id=request.message_id,
            evidence_id=request.evidence_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not reminder:
        raise HTTPException(status_code=404, detail="message not found")
    return {"status": "success", "data": reminder}


@router.get("/reminders/{symbol}")
def list_reminders(symbol: str, current_user_id: int = Depends(get_current_user_id)):
    return {
        "status": "success",
        "data": list_structure_reminders(
            user_id=current_user_id,
            symbol=normalize_symbol(symbol),
        ),
    }


@router.post("/reminders/{reminder_id}/ack")
def ack_reminder(
    reminder_id: int,
    request: ReminderAckRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    try:
        result = ack_structure_reminder(
            user_id=current_user_id,
            reminder_id=reminder_id,
            action=request.action,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="reminder not found")
    return {"status": "success", "data": result}


@router.post("/branches/settle")
def settle_branch(
    request: OutcomeSettleRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    outcome = settle_scenario_branch(
        user_id=current_user_id,
        branch_id=request.branch_id,
        current_price=request.current_price,
        settlement_window=request.settlement_window,
        checked_at=request.checked_at,
        user_followed_plan=request.user_followed_plan,
        expired=request.expired,
    )
    if not outcome:
        raise HTTPException(status_code=404, detail="branch not found")
    return {"status": "success", "data": outcome}


@router.get("/memory/{symbol}")
def memory_profile(symbol: str, current_user_id: int = Depends(get_current_user_id)):
    profile = get_symbol_memory_profile(user_id=current_user_id, symbol=normalize_symbol(symbol))
    if not profile:
        raise HTTPException(status_code=404, detail="memory profile not found")
    return {"status": "success", "data": profile}


@router.get("/outcomes/{symbol}")
def outcome_reviews(
    symbol: str,
    limit: int = Query(default=50, ge=1, le=100),
    current_user_id: int = Depends(get_current_user_id),
):
    return {
        "status": "success",
        "data": list_symbol_outcome_reviews(
            user_id=current_user_id,
            symbol=normalize_symbol(symbol),
            limit=limit,
        ),
    }


def _validate_compute_profile(compute_profile: str) -> None:
    if compute_profile not in COMPUTE_PROFILES:
        raise HTTPException(status_code=400, detail=f"unsupported compute profile: {compute_profile}")


def _validate_level(level: str) -> str:
    if str(level or "").strip().lower() not in FREQ_ALIASES:
        raise HTTPException(status_code=400, detail=f"unsupported level: {level}")
    return normalize_freq(level)
