"""AI Native V5 CZSC-only structure APIs."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from server import config
from server.api.auth import get_current_user_id
from server.db.database import get_connection
from server.domain.symbols import normalize_symbol, to_tencent_symbol
from server.engines.ai_native.structure_chat_service import (
    answer_structure_question,
    list_chat_messages,
    list_chat_sessions,
    stream_structure_question,
)
from server.engines.ai_native.czsc_snapshot_service import (
    DEFAULT_COMPUTE_PROFILE,
    DEFAULT_LEVELS,
    get_snapshot_status,
    prewarm_structure_snapshots,
)
from server.engines.ai_native.scenario_branch_service import list_scenario_branches
from server.engines.ai_native.scenario_outcome_service import (
    get_symbol_memory_profile,
    list_user_outcome_review_feed,
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
from server.engines.ai_native.momentum_context_service import get_momentum_context
from server.engines.ai_native.structure_preview_service import get_structure_preview
from server.engines.ai_native.structure_view_service import get_structure_view
from server.engines.ai_native.structure_reminder_service import (
    ack_structure_reminder,
    create_reminder_from_chat_evidence,
    list_structure_reminders,
)
from server.engines.ai_native.intraday_snapshot_hydrator import hydrate_intraday_snapshot
from server.engines.ai_native.unified_reasoning_service import (
    ALL_UNIFIED_FULL_TEXT_VERSIONS,
    ALL_UNIFIED_REASONING_VERSIONS,
    UNIFIED_FULL_TEXT_VERSION,
    UNIFIED_REASONING_VERSION,
    get_latest_unified_reasoning,
    normalize_monitor_conditions,
)
from server.engines.ai_native.ai_trigger_service import TRIGGER_MANUAL_FULL_REASONING, request_ai_reasoning
from server.engines.ai_native.universe_resolver import resolve_ai_native_universe
from server.engines.ai_native.workspace_bootstrap_service import bootstrap_ai_structure_workspace
from server.engines.structure.structure_key import COMPUTE_PROFILES, FREQ_ALIASES, normalize_freq
from server.engines.structure.canonical_structure_service import get_latest_structure
from server.services.price_service import get_batch_prices


router = APIRouter()


class SnapshotPrewarmRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=50)
    levels: list[str] = Field(default_factory=lambda: list(DEFAULT_LEVELS), max_length=6)
    compute_profile: str = DEFAULT_COMPUTE_PROFILE
    priority: int = Field(default=80, ge=1, le=100)
    reason: str = "manual_prewarm"
    force_rebuild: bool = False


class ContextPrewarmRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=50)
    levels: list[str] = Field(default_factory=lambda: list(DEFAULT_LEVELS), max_length=6)
    compute_profile: str = DEFAULT_COMPUTE_PROFILE
    priority: int = Field(default=70, ge=1, le=100)
    reason: str = "manual_context_prewarm"
    force_rebuild: bool = False


class PipelineEnsureRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list, min_length=1, max_length=20)
    levels: list[str] = Field(default_factory=lambda: list(DEFAULT_LEVELS), max_length=6)
    compute_profile: str = DEFAULT_COMPUTE_PROFILE
    priority: int = Field(default=85, ge=1, le=100)
    reason: str = "web_ai_structure_workspace"
    allow_context_enqueue: bool = False


class WorkspaceBootstrapRequest(BaseModel):
    sources: list[str] = Field(default_factory=lambda: ["positions", "recent_chat", "watchlist"], max_length=5)
    focus_symbols: list[str] = Field(default_factory=list, max_length=5)
    levels: list[str] = Field(default_factory=lambda: list(DEFAULT_LEVELS), max_length=6)
    compute_profile: str = DEFAULT_COMPUTE_PROFILE
    limit: int = Field(default=20, ge=1, le=50)
    ensure_pipeline: bool = False
    priority: int = Field(default=85, ge=1, le=100)
    reason: str = "workspace_bootstrap"
    client: str = Field(default="web", pattern="^(web|miniprogram|worker|reminder)$")
    include: Optional[list[str]] = Field(default=None, max_length=5)


class StructureChatRequest(BaseModel):
    symbol: str
    question: str = Field(min_length=1, max_length=500)
    session_id: Optional[str] = None
    current_price: Optional[float] = None
    quote_time: Optional[str] = None
    change_pct: Optional[float] = None
    price_source: Optional[str] = None
    thinking_enabled: bool = False  # 深度推演模式（DeepSeek-R1 + thinking）


class UnifiedReasoningRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list, min_length=1, max_length=10)
    levels: list[str] = Field(default_factory=lambda: list(DEFAULT_LEVELS), max_length=6)
    compute_profile: str = DEFAULT_COMPUTE_PROFILE
    trigger_reason: str = TRIGGER_MANUAL_FULL_REASONING
    force: bool = True


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
    sources: str = Query(default="positions,recent_chat,watchlist"),
    current_user_id: int = Depends(get_current_user_id),
):
    source_list = [item.strip() for item in sources.split(",") if item.strip()]
    return {
        "status": "success",
        "data": {
            "symbols": resolve_ai_native_universe(current_user_id, source_list),
        },
    }


@router.get("/watchboard")
async def watchboard(current_user_id: int = Depends(get_current_user_id)):
    """Return the V5 intraday watchboard data in one user-scoped payload."""
    groups = _load_watchboard_groups(current_user_id)
    symbols = _unique_symbols(item["symbol"] for group in groups for item in group["items"])
    prices = await get_batch_prices(symbols)
    for group in groups:
        for item in group["items"]:
            price = _price_for_symbol(prices, item["symbol"])
            if price:
                item["price"] = price.get("price") or item.get("price") or 0
                item["change_pct"] = price.get("change_pct") or 0
                item["price_data"] = price
                if not item.get("name"):
                    item["name"] = price.get("name") or item["symbol"]
            if item.get("position") and item.get("price"):
                cost = _num(item["position"].get("cost"))
                if cost > 0:
                    item["position"]["pnl_pct"] = round((_num(item["price"]) - cost) / cost * 100, 2)
    return {
        "status": "success",
        "data": {
            "groups": groups,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    }


@router.post("/workspace/bootstrap")
async def workspace_bootstrap(
    request: WorkspaceBootstrapRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    """Return a client-ready V5 workspace state without inline CZSC computation."""
    _validate_compute_profile(request.compute_profile)
    levels = [_validate_level(level) for level in request.levels]
    focus_symbols = [_validate_symbol(symbol) for symbol in request.focus_symbols]
    include_sections = [_validate_workspace_include(item) for item in request.include] if request.include is not None else None
    result = await bootstrap_ai_structure_workspace(
        user_id=current_user_id,
        sources=request.sources,
        focus_symbols=focus_symbols,
        levels=levels,
        compute_profile=request.compute_profile,
        limit=request.limit,
        ensure_pipeline=request.ensure_pipeline,
        priority=request.priority,
        reason=request.reason,
        client=request.client,
        include_sections=include_sections,
    )
    return {"status": "success", "data": result}


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
            force_rebuild=request.force_rebuild,
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
        allow_context_enqueue=request.allow_context_enqueue,
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
    snapshot = get_latest_structure(
        symbol=normalize_symbol(symbol),
        level=normalized_level,
        min_profile=compute_profile,
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
        force_rebuild=request.force_rebuild,
    )
    return {"status": "success", "data": result}


@router.post("/contexts/regenerate")
async def regenerate_contexts(
    request: ContextPrewarmRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    _validate_compute_profile(request.compute_profile)
    levels = [_validate_level(level) for level in request.levels]
    result = await run_in_threadpool(
        _regenerate_context_chain,
        user_id=current_user_id,
        symbols=request.symbols,
        levels=levels,
        compute_profile=request.compute_profile,
        priority=max(request.priority, 95),
        reason=request.reason or "manual_context_regenerate",
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


@router.post("/unified-reasoning/trigger")
async def unified_reasoning_trigger(
    request: UnifiedReasoningRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    _validate_compute_profile(request.compute_profile)
    levels = [_validate_level(level) for level in request.levels]
    items = []
    for raw_symbol in request.symbols:
        try:
            items.append(await request_ai_reasoning(
                user_id=current_user_id,
                symbol=normalize_symbol(raw_symbol),
                trigger_reason=request.trigger_reason or TRIGGER_MANUAL_FULL_REASONING,
                force=request.force,
                levels=levels,
                compute_profile=request.compute_profile,
            ))
        except ValueError as exc:
            items.append({"symbol": str(raw_symbol), "status": "skipped", "error": str(exc)})
    return {"status": "success", "data": {"count": len(items), "items": items}}


@router.get("/unified-reasoning/full/{symbol}")
def unified_reasoning_full(symbol: str, current_user_id: int = Depends(get_current_user_id)):
    canonical = normalize_symbol(symbol)
    run = get_latest_unified_reasoning(user_id=current_user_id, symbol=canonical)
    if not run:
        raise HTTPException(status_code=404, detail="unified reasoning not found")
    source_snapshot_ids = run.get("source_snapshot_ids") or []
    conn = get_connection()
    try:
        source_snapshots = _snapshot_lineage_by_ids(conn, source_snapshot_ids)
        latest_as_of = _latest_snapshot_as_of_by_level(conn, canonical)
        source_as_of = {item["level"]: item["data_as_of"] for item in source_snapshots}
    finally:
        conn.close()
    return {
        "status": "success",
        "data": {
            "symbol": run["symbol"],
            "context_id": run.get("context_id") or "",
            "run_id": run["run_id"],
            "source_snapshot_ids": source_snapshot_ids,
            "source_snapshots": source_snapshots,
            "source_snapshot_as_of_by_level": source_as_of,
            "latest_snapshot_as_of_by_level": latest_as_of,
            "full_text": run.get("full_reasoning_text") or "",
            "summary": run.get("summary") or {},
            "updated_at": run.get("updated_at") or "",
        },
    }


@router.get("/unified-reasoning/summary/{symbol}")
def unified_reasoning_summary(symbol: str, current_user_id: int = Depends(get_current_user_id)):
    canonical = normalize_symbol(symbol)
    run = get_latest_unified_reasoning(user_id=current_user_id, symbol=canonical)
    if not run:
        raise HTTPException(status_code=404, detail="unified reasoning not found")
    summary = run.get("summary") or {}
    source_snapshot_ids = run.get("source_snapshot_ids") or []
    conn = get_connection()
    try:
        source_snapshots = _snapshot_lineage_by_ids(conn, source_snapshot_ids)
        latest_as_of = _latest_snapshot_as_of_by_level(conn, canonical)
    finally:
        conn.close()
    return {
        "status": "success",
        "data": {
            "symbol": run["symbol"],
            "context_id": run.get("context_id") or "",
            "run_id": run["run_id"],
            "source_snapshot_ids": source_snapshot_ids,
            "source_snapshots": source_snapshots,
            "latest_snapshot_as_of_by_level": latest_as_of,
            "summary": summary.get("coach_summary") or "",
            "updated_at": run.get("updated_at") or "",
        },
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
            current_price=request.current_price,
            quote_time=request.quote_time,
            change_pct=request.change_pct,
            price_source=request.price_source,
            thinking_enabled=request.thinking_enabled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=404, detail="context not found")
    return {"status": "success", "data": result}


@router.post("/chat/stream")
async def chat_stream(request: StructureChatRequest, current_user_id: int = Depends(get_current_user_id)):
    async def event_stream():
        try:
            async for event in stream_structure_question(
                user_id=current_user_id,
                symbol=normalize_symbol(request.symbol),
                question=request.question,
                session_id=request.session_id,
                current_price=request.current_price,
                quote_time=request.quote_time,
                change_pct=request.change_pct,
                price_source=request.price_source,
                thinking_enabled=request.thinking_enabled,
            ):
                event_name = str(event.get("event") or "message")
                data = {key: value for key, value in event.items() if key != "event"}
                yield f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        except ValueError as exc:
            yield f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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


@router.get("/structure-view/{symbol}")
def structure_view(
    symbol: str,
    level: str = Query(default="day"),
    compute_profile: str = Query(default=DEFAULT_COMPUTE_PROFILE),
    count: int = Query(default=1200, ge=10, le=2000),
):
    _validate_compute_profile(compute_profile)
    normalized_level = _validate_level(level)
    result = get_structure_view(
        symbol=normalize_symbol(symbol),
        level=normalized_level,
        compute_profile=compute_profile,
        count=count,
    )
    if not result:
        raise HTTPException(status_code=404, detail="structure view not found")
    return {"status": "success", "data": result}


@router.get("/structure-preview/{symbol}")
def structure_preview(
    symbol: str,
    level: str = Query(default="day"),
    compute_profile: str = Query(default=DEFAULT_COMPUTE_PROFILE),
    count: int = Query(default=1200, ge=10, le=2000),
):
    _validate_compute_profile(compute_profile)
    normalized_level = _validate_level(level)
    result = get_structure_preview(
        symbol=normalize_symbol(symbol),
        level=normalized_level,
        compute_profile=compute_profile,
        count=count,
    )
    if not result:
        raise HTTPException(status_code=404, detail="structure preview not found")
    return {"status": "success", "data": result}


@router.get("/momentum-context/{symbol}")
def momentum_context(
    symbol: str,
    level: str = Query(default="day"),
    compute_profile: str = Query(default=DEFAULT_COMPUTE_PROFILE),
    count: int = Query(default=1200, ge=10, le=2000),
):
    _validate_compute_profile(compute_profile)
    normalized_level = _validate_level(level)
    result = get_momentum_context(
        symbol=normalize_symbol(symbol),
        level=normalized_level,
        compute_profile=compute_profile,
        count=count,
    )
    if not result:
        raise HTTPException(status_code=404, detail="momentum context not found")
    return {"status": "success", "data": result}


@router.get("/intraday-snapshot/{symbol}")
def intraday_snapshot(
    symbol: str,
    trade_date: Optional[str] = Query(default=None, description="YYYY-MM-DD；不填则使用今天"),
    recent_bar_count: int = Query(default=80, ge=0, le=120),
):
    """Return today's read-only 1m intraday facts for plan validation."""
    result = hydrate_intraday_snapshot(
        normalize_symbol(symbol),
        trade_date=trade_date,
        include_recent_bars=recent_bar_count > 0,
        recent_bar_count=recent_bar_count,
    )
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


@router.get("/outcomes")
def outcome_review_feed(
    sources: str = Query(default="positions,recent_chat,watchlist"),
    symbols: str = Query(default=""),
    client: str = Query(default="web", pattern="^(web|miniprogram|worker|reminder)$"),
    limit: int = Query(default=30, ge=1, le=100),
    symbol_limit: int = Query(default=20, ge=1, le=50),
    current_user_id: int = Depends(get_current_user_id),
):
    requested_symbols = [_validate_symbol(item.strip()) for item in symbols.split(",") if item.strip()]
    source_list = [item.strip() for item in sources.split(",") if item.strip()]
    if requested_symbols:
        symbol_items = _focused_symbol_items(
            user_id=current_user_id,
            symbols=requested_symbols[:symbol_limit],
            sources=source_list,
        )
    else:
        symbol_items = resolve_ai_native_universe(current_user_id, source_list)[:symbol_limit]
    return {
        "status": "success",
        "data": list_user_outcome_review_feed(
            user_id=current_user_id,
            symbol_items=symbol_items,
            limit=limit,
            compact=client != "web",
        ),
    }


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


def _validate_symbol(symbol: str) -> str:
    try:
        return normalize_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _load_watchboard_groups(user_id: int) -> list[dict]:
    conn = get_connection()
    try:
        _ensure_watchboard_groups(conn, user_id)
        positions = _load_watchboard_positions(conn, user_id)
        reasoning_by_symbol = {
            item["symbol"]: _load_watchboard_reasoning(conn, user_id, item["symbol"])
            for item in positions
        }
        watch_groups = []
        for name, group_type in (("自选", "watchlist"), ("备选", "candidate")):
            items = _load_watchlist_group_items(conn, user_id, name)
            for item in items:
                if item["symbol"] not in reasoning_by_symbol:
                    reasoning_by_symbol[item["symbol"]] = _load_watchboard_reasoning(conn, user_id, item["symbol"])
            watch_groups.append({
                "name": name,
                "type": group_type,
                "items": [_attach_watchboard_reasoning(item, reasoning_by_symbol.get(item["symbol"]), conn=conn) for item in items],
            })
        return [
            {
                "name": "持仓",
                "type": "position",
                "items": [_attach_watchboard_reasoning(item, reasoning_by_symbol.get(item["symbol"]), conn=conn) for item in positions],
            },
            *watch_groups,
        ]
    finally:
        conn.close()


def _ensure_watchboard_groups(conn, user_id: int) -> None:
    rows = conn.execute(
        "SELECT name FROM watchlist_groups WHERE user_id = ?",
        (int(user_id),),
    ).fetchall()
    names = {row["name"] for row in rows}
    next_order = len(names)
    changed = False
    for name in ("自选", "备选"):
        if name in names:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO watchlist_groups (user_id, name, sort_order) VALUES (?, ?, ?)",
            (int(user_id), name, next_order),
        )
        next_order += 1
        changed = True
    if changed:
        conn.commit()


def _load_watchboard_positions(conn, user_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT symbol, name, quantity, avg_cost, current_price, updated_at
          FROM positions
         WHERE user_id = ? AND quantity > 0
         ORDER BY updated_at DESC, id DESC
        """,
        (int(user_id),),
    ).fetchall()
    items = []
    seen = set()
    for row in rows:
        try:
            symbol = normalize_symbol(row["symbol"])
        except ValueError:
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        current_price = _num(row["current_price"])
        cost = _num(row["avg_cost"])
        position = {
            "shares": int(_num(row["quantity"])),
            "cost": cost,
            "pnl_pct": round((current_price - cost) / cost * 100, 2) if current_price > 0 and cost > 0 else None,
        }
        items.append({
            "symbol": symbol,
            "name": row["name"] or "",
            "price": current_price,
            "change_pct": 0,
            "position": position,
        })
    return items


def _load_watchlist_group_items(conn, user_id: int, group_name: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT wi.symbol, wi.name, wi.sort_order
          FROM watchlist_items wi
          JOIN watchlist_groups wg ON wg.id = wi.group_id
         WHERE wg.user_id = ? AND wg.name = ?
         ORDER BY wi.sort_order, wi.id
        """,
        (int(user_id), group_name),
    ).fetchall()
    items = []
    seen = set()
    for row in rows:
        try:
            symbol = normalize_symbol(row["symbol"])
        except ValueError:
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        items.append({
            "symbol": symbol,
            "name": row["name"] or "",
            "price": 0,
            "change_pct": 0,
            "position": None,
        })
    return items


def _load_watchboard_reasoning(conn, user_id: int, symbol: str) -> dict:
    full_text_versions = [UNIFIED_FULL_TEXT_VERSION, *sorted(ALL_UNIFIED_FULL_TEXT_VERSIONS - {UNIFIED_FULL_TEXT_VERSION})]
    context_versions = [UNIFIED_REASONING_VERSION, *sorted(ALL_UNIFIED_REASONING_VERSIONS - {UNIFIED_REASONING_VERSION})]
    accepted_versions = [*full_text_versions, *context_versions, "ai_structure_reasoning.e1_dynamic_growth.full_text"]
    placeholders = ",".join("?" for _ in accepted_versions)
    row = conn.execute(
        f"""
        SELECT run_id, context_id, prompt_version, source_snapshot_ids_json, full_reasoning_text, summary_json, updated_at
          FROM ai_structure_reasoning_runs
         WHERE user_id = ?
           AND symbol = ?
           AND status = 'SUCCESS'
           AND prompt_version IN ({placeholders})
         ORDER BY
           CASE
             WHEN prompt_version = ? THEN 0
             WHEN prompt_version IN ({",".join("?" for _ in full_text_versions[1:])}) THEN 1
             WHEN prompt_version = ? THEN 2
             WHEN prompt_version IN ({",".join("?" for _ in context_versions[1:])}) THEN 3
             ELSE 4
           END,
           updated_at DESC,
           id DESC
         LIMIT 1
        """,
        (
            int(user_id),
            normalize_symbol(symbol),
            *accepted_versions,
            UNIFIED_FULL_TEXT_VERSION,
            *full_text_versions[1:],
            UNIFIED_REASONING_VERSION,
            *context_versions[1:],
        ),
    ).fetchone()
    if not row:
        return {}
    summary = _loads_json(row["summary_json"])
    return {
        "run_id": row["run_id"],
        "context_id": row["context_id"] or "",
        "prompt_version": row["prompt_version"] or "",
        "full_reasoning_text": row["full_reasoning_text"] or "",
        "summary": summary,
        "source_snapshot_ids": _loads_json_list(row["source_snapshot_ids_json"]),
        "updated_at": row["updated_at"] or "",
    }


def _attach_watchboard_reasoning(item: dict, reasoning: dict | None, *, conn=None) -> dict:
    enriched = dict(item)
    summary = (reasoning or {}).get("summary") or {}
    safe_summary = dict(summary)
    safe_summary["monitor_conditions"] = normalize_monitor_conditions(summary.get("monitor_conditions") or {})
    enriched["reasoning_summary"] = _watchboard_summary(safe_summary, reasoning or {})
    enriched["reasoning_freshness"] = _watchboard_reasoning_freshness(enriched, reasoning or {}, conn=conn)
    enriched["monitor_conditions"] = safe_summary["monitor_conditions"]
    prompt_version = (reasoning or {}).get("prompt_version") or ""
    enriched["reasoning_source"] = "unified" if prompt_version in (ALL_UNIFIED_FULL_TEXT_VERSIONS | ALL_UNIFIED_REASONING_VERSIONS) else "legacy"
    enriched["full_reasoning_available"] = bool((reasoning or {}).get("full_reasoning_text"))
    enriched["unified_reasoning_available"] = enriched["reasoning_source"] == "unified" and enriched["full_reasoning_available"]
    enriched["reasoning_run_id"] = (reasoning or {}).get("run_id") or ""
    enriched["context_id"] = (reasoning or {}).get("context_id") or ""
    return enriched


def _watchboard_summary(summary: dict, reasoning: dict) -> dict:
    coach_summary = str(summary.get("front_panel_text") or summary.get("coach_summary") or summary.get("one_liner") or "").strip()
    watch_plan = summary.get("watch_plan") if isinstance(summary.get("watch_plan"), dict) else {}
    watch_state_machine = summary.get("watch_state_machine") or watch_plan.get("watch_state_machine") or {}
    triggers = (summary.get("monitor_conditions") or {}).get("triggers") or []
    down_levels = [item for item in triggers if item.get("type") == "price_below"]
    up_levels = [item for item in triggers if item.get("type") == "price_above"]
    key_down = min((_num(item.get("level")) for item in down_levels if _num(item.get("level")) > 0), default=None)
    key_up = max((_num(item.get("level")) for item in up_levels if _num(item.get("level")) > 0), default=None)
    card_summary = str(summary.get("card_summary") or "").strip()
    one_liner = card_summary or summary.get("one_liner") or coach_summary
    return {
        "one_liner": card_summary[:42] if card_summary else _compact_watchboard_line(one_liner, (reasoning or {}).get("full_reasoning_text") or ""),
        "action": summary.get("card_action") or summary.get("action") or "",
        "action_detail": summary.get("action_detail") or "",
        "card_secondary": summary.get("card_secondary") or "",
        "extract_status": summary.get("extract_status") or "",
        "extract_error": summary.get("extract_error") or "",
        "watch_plan": watch_plan,
        "watch_state_machine": watch_state_machine,
        "key_level_down": key_down,
        "key_level_down_meaning": summary.get("key_level_down_meaning") or "下方关键位",
        "key_level_up": key_up,
        "key_level_up_meaning": summary.get("key_level_up_meaning") or "上方关键位",
        "stop_loss": summary.get("stop_loss"),
        "scenarios": summary.get("scenarios") or [],
        "generated_at": summary.get("generated_at") or reasoning.get("updated_at") or "",
        "data_as_of": summary.get("data_as_of") or "",
    }


def _watchboard_reasoning_freshness(item: dict, reasoning: dict, *, conn=None) -> dict:
    """盯盘页使用的推演新鲜度；实时价不直接判旧，结构快照更新才判旧。"""
    symbol = item.get("symbol") or ""
    summary = (reasoning or {}).get("summary") or {}
    generated_at = summary.get("generated_at") or (reasoning or {}).get("updated_at") or ""
    source_snapshot_ids = (reasoning or {}).get("source_snapshot_ids") or []
    owns_connection = conn is None
    active_conn = conn or get_connection()
    try:
        source_as_of = _snapshot_as_of_by_ids(active_conn, source_snapshot_ids)
        latest_as_of = _latest_snapshot_as_of_by_level(active_conn, symbol)
    finally:
        if owns_connection:
            active_conn.close()
    reasoning_as_of_by_level = source_as_of or {}
    reasoning_data_as_of = (
        _primary_level_as_of(reasoning_as_of_by_level)
        or summary.get("data_as_of")
        or ""
    )
    latest_snapshot_as_of = _primary_level_as_of(latest_as_of)
    is_stale = bool(
        reasoning_data_as_of
        and latest_snapshot_as_of
        and _timestamp_sort_key(latest_snapshot_as_of) > _timestamp_sort_key(reasoning_data_as_of)
    )
    if not reasoning:
        status = "missing"
        label = "无推演"
        detail = "尚未生成"
    elif is_stale:
        status = "stale"
        label = "旧推演"
        detail = "结构已更新，推演待刷新"
    else:
        status = "ready"
        label = "最新推演"
        detail = "结构与推演一致"
    return {
        "status": status,
        "phase": "",
        "label": label,
        "detail": detail,
        "generated_at": generated_at,
        "data_as_of": reasoning_data_as_of,
        "data_as_of_by_level": reasoning_as_of_by_level,
        "latest_snapshot_as_of": latest_snapshot_as_of,
        "latest_snapshot_as_of_by_level": latest_as_of,
        "quote_time": (item.get("price_data") or {}).get("quote_time") or "",
        "elapsed_seconds": 0,
        "is_stale": is_stale,
        "stale_reason": "latest_snapshot_newer" if is_stale else "",
    }


def _snapshot_as_of_by_ids(conn, snapshot_ids: list[str]) -> dict[str, str]:
    ids = [str(item) for item in snapshot_ids if item]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT level, data_as_of
          FROM structure_snapshots
         WHERE snapshot_id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    return {row["level"]: row["data_as_of"] or "" for row in rows}


def _snapshot_lineage_by_ids(conn, snapshot_ids: list[str]) -> list[dict[str, str]]:
    ids = [str(item) for item in snapshot_ids if item]
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT snapshot_id, level, compute_profile, data_signature, data_as_of, updated_at
          FROM structure_snapshots
         WHERE snapshot_id IN ({placeholders})
        """,
        ids,
    ).fetchall()
    by_id = {
        row["snapshot_id"]: {
            "snapshot_id": row["snapshot_id"] or "",
            "level": row["level"] or "",
            "compute_profile": row["compute_profile"] or "",
            "data_signature": row["data_signature"] or "",
            "data_as_of": row["data_as_of"] or "",
            "updated_at": row["updated_at"] or "",
        }
        for row in rows
    }
    return [by_id[item] for item in ids if item in by_id]


def _latest_snapshot_as_of_by_level(conn, symbol: str) -> dict[str, str]:
    try:
        canonical = normalize_symbol(symbol)
    except ValueError:
        return {}
    rows = conn.execute(
        """
        SELECT level, data_as_of
          FROM structure_snapshots
         WHERE symbol = ?
           AND compute_profile = ?
           AND level IN ('week', 'day', '30', '5')
         ORDER BY
           CASE
             WHEN json_extract(snapshot_json, '$.source.provider') = 'tdx'
              AND json_extract(snapshot_json, '$.source.adjustflag') = '2' THEN 0
             WHEN json_extract(snapshot_json, '$.source.provider') = 'tdx' THEN 1
             ELSE 2
           END,
           updated_at DESC,
           id DESC
        """,
        (canonical, DEFAULT_COMPUTE_PROFILE),
    ).fetchall()
    latest: dict[str, str] = {}
    for row in rows:
        latest.setdefault(row["level"], row["data_as_of"] or "")
    return latest


def _primary_level_as_of(items: dict[str, str]) -> str:
    for level in ("30", "5", "day", "week"):
        value = str((items or {}).get(level) or "")
        if value:
            return value
    return ""


def _timestamp_sort_key(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.replace("T", " ").replace("+08:00", "").replace("Z", "").strip()


def _fallback_monitor_conditions_from_key_boundaries(summary: dict) -> dict:
    """旧推演没有 monitor_conditions 时，从结构化关键边界生成临时盯盘位。"""
    triggers: list[dict] = []
    seen_levels: set[float] = set()
    for boundary in summary.get("key_boundaries") or []:
        if not isinstance(boundary, dict):
            continue
        price = _num(boundary.get("price"))
        if price <= 0:
            continue
        rounded_price = round(price, 4)
        if rounded_price in seen_levels:
            continue
        seen_levels.add(rounded_price)
        description = str(boundary.get("description") or "").strip()
        boundary_type = str(boundary.get("type") or "").strip()
        action = "观望"
        if boundary_type == "invalidation" or any(word in description for word in ("失效", "防守", "跌破", "下沿")):
            message = f"跌破{_format_level(price)}，结构转弱"
            if any(word in description for word in ("止损", "失效", "下沿")):
                action = "减仓"
        else:
            message = f"跌破{_format_level(price)}，回中枢观察"
        triggers.append({
            "type": "price_below",
            "level": rounded_price,
            "message_on_trigger": message,
            "action_on_trigger": action,
        })
        if len(triggers) >= 4:
            break
    return normalize_monitor_conditions({"triggers": triggers})


def _format_level(value: float) -> str:
    if value >= 100:
        return f"{value:.2f}"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _apply_nearest_watch_levels(item: dict) -> None:
    """按当前价返回最近的上下关键位，避免卡片显示最远支撑/压力。"""
    price = _num(item.get("price"))
    if price <= 0:
        return
    summary = item.get("reasoning_summary") or {}
    triggers = (item.get("monitor_conditions") or {}).get("triggers") or []
    below_levels = [
        _num(trigger.get("level"))
        for trigger in triggers
        if trigger.get("type") == "price_below" and 0 < _num(trigger.get("level")) < price
    ]
    above_levels = [
        _num(trigger.get("level"))
        for trigger in triggers
        if trigger.get("type") == "price_above" and _num(trigger.get("level")) > price
    ]
    summary["key_level_down"] = max(below_levels, default=None)
    summary["key_level_up"] = min(above_levels, default=None)
    item["reasoning_summary"] = summary


def _compact_watchboard_line(summary_text: str, full_text: str = "") -> str:
    """Turn long reasoning prose into one scan-friendly watchboard task line."""
    bad_markers = (
        "好的",
        "请坐",
        "收到数据",
        "数据已齐",
        "我们开始",
        "我的分析如下",
        "下面我",
        "开始这场",
        "记住",
        "不构成",
        "投资建议",
        "自行决定",
    )
    source = "\n".join([str(summary_text or ""), str(full_text or "")]).strip()
    if not source:
        return "待生成路径"
    parts = [part.strip(" \t\r\n#*-0123456789.、：:") for part in re.split(r"[。！？\n]", source) if part.strip()]
    preferred_markers = ("当前", "核心", "观察", "重点", "处于", "主线", "关键", "跌破", "守住", "回到", "中枢", "三买", "三卖")
    for part in parts:
        part = re.sub(r"[*_`]+", "", part).strip()
        if any(marker in part for marker in bad_markers):
            continue
        if any(marker in part for marker in preferred_markers):
            return part[:42]
    for part in parts:
        part = re.sub(r"[*_`]+", "", part).strip()
        if not any(marker in part for marker in bad_markers):
            return part[:42]
    return "待生成路径"


def _regenerate_context_chain(
    *,
    user_id: int,
    symbols: list[str],
    levels: list[str],
    compute_profile: str,
    priority: int,
    reason: str,
) -> dict:
    """重新生成的真实链路：K线刷新 -> snapshot 刷新 -> context 重跑。"""
    items = []
    for raw_symbol in symbols:
        symbol = normalize_symbol(raw_symbol)
        sync_result = _sync_regenerate_klines(symbol, levels)
        snapshot_result = prewarm_structure_snapshots(
            symbols=[symbol],
            levels=levels,
            compute_profile=compute_profile,
            priority=priority,
            reason=f"{reason}:snapshot_refresh",
            requested_by_user_id=user_id,
            force_rebuild=False,
        )
        level_statuses = _snapshot_level_statuses(
            symbol=symbol,
            levels=levels,
            compute_profile=compute_profile,
        )
        blocking_levels = [
            item["level"]
            for item in level_statuses
            if item["status"] in {"pending", "stale", "failed"}
        ]

        context_result = None
        if not blocking_levels:
            context_result = prewarm_ai_structure_contexts(
                user_id=user_id,
                symbols=[symbol],
                levels=levels,
                compute_profile=compute_profile,
                priority=priority,
                reason=reason,
                force_rebuild=True,
            )

        items.append({
            "symbol": symbol,
            "status": "snapshot_pending" if blocking_levels else "context_enqueued",
            "sync": sync_result,
            "snapshot_jobs": snapshot_result,
            "context_jobs": context_result,
            "stale_levels": blocking_levels,
            "level_freshness": level_statuses,
        })

    return {
        "count": len(items),
        "items": items,
        "refresh_chain": "kline_to_snapshot_to_context",
    }


def _sync_regenerate_klines(symbol: str, levels: list[str]) -> dict:
    if not config.BAOSTOCK_AUTO_SYNC_ENABLED:
        return {
            "status": "skipped",
            "source": "tdx",
            "total_written": 0,
            "errors": 0,
            "levels": [
                {"level": level, "written": 0, "status": "skipped", "reason": "BAOSTOCK_AUTO_SYNC_DISABLED"}
                for level in levels
            ],
        }

    from server.services.baostock_service import fetch_klines_sync

    results = []
    total_written = 0
    error_count = 0
    for level in levels:
        try:
            written = fetch_klines_sync(symbol, level)
            total_written += int(written or 0)
            results.append({"level": level, "written": int(written or 0), "status": "ok"})
        except Exception as exc:
            error_count += 1
            results.append({
                "level": level,
                "written": 0,
                "status": "error",
                "error": str(exc)[:200],
            })
    return {
        "status": "success" if error_count == 0 else "partial",
        "total_written": total_written,
        "errors": error_count,
        "levels": results,
    }


def _snapshot_level_statuses(*, symbol: str, levels: list[str], compute_profile: str) -> list[dict]:
    items = []
    for level in levels:
        status = get_snapshot_status(symbol=symbol, level=level, compute_profile=compute_profile)
        snapshot = status.get("snapshot") or {}
        freshness = status.get("freshness") or {}
        items.append({
            "level": level,
            "status": status.get("status") or "unknown",
            "data_as_of": snapshot.get("data_as_of") or "",
            "kline_last_bar_at": freshness.get("last_bar_at") or "",
            "kline_count": freshness.get("kline_count") or 0,
            "stale_reason": freshness.get("stale_reason") or "",
            "job": status.get("job"),
        })
    return items


def _unique_symbols(symbols) -> list[str]:
    result = []
    seen = set()
    for raw in symbols:
        try:
            symbol = normalize_symbol(raw)
        except ValueError:
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
    return result


def _price_for_symbol(prices: dict, symbol: str) -> dict:
    tencent = to_tencent_symbol(symbol)
    return prices.get(tencent) or prices.get(symbol) or {}


def _loads_json(value: str) -> dict:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _loads_json_list(value: str) -> list:
    try:
        parsed = json.loads(value or "[]")
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _validate_workspace_include(section: str) -> str:
    normalized = str(section or "").strip().lower()
    allowed = {"context_status", "latest_context", "branches", "reminders", "outcomes"}
    if normalized not in allowed:
        raise HTTPException(status_code=400, detail=f"unsupported workspace include section: {section}")
    return normalized


def _focused_symbol_items(*, user_id: int, symbols: list[str], sources: list[str]) -> list[dict]:
    universe_by_symbol = {
        normalize_symbol(item["symbol"]): item
        for item in resolve_ai_native_universe(user_id, sources)
    }
    items = []
    for symbol in symbols:
        item = dict(universe_by_symbol.get(symbol) or {})
        if item:
            item["sources"] = sorted(set(item.get("sources") or []) | {"focus"})
            item["priority"] = max(int(item.get("priority") or 0), 120)
        else:
            item = {
                "symbol": symbol,
                "name": symbol,
                "sources": ["focus"],
                "priority": 120,
                "has_position": False,
            }
        items.append(item)
    return items
