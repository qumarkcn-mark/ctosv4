"""Structure snapshot job queue management APIs."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from server.domain.symbols import normalize_symbol
from server.engines.structure.engine_contract import normalize_engine_mode
from server.engines.structure.engine_router import analyze_structure_with_engine
from server.engines.structure.snapshot_query import build_formal_structure_key
from server.engines.structure.structure_key import COMPUTE_PROFILES, FREQ_ALIASES
from server.engines.structure.structure_jobs import enqueue_structure_job, structure_job_stats


router = APIRouter()


class StructurePrewarmRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=50)
    freqs: list[str] = Field(default_factory=lambda: ["day", "30", "5"], max_length=6)
    cchan_preset: str = "live_tolerant"
    compute_profile: str = "chart_standard_v1"
    priority: int = Field(default=80, ge=1, le=100)


@router.get("/engine/{symbol}")
async def get_structure_by_engine(
    symbol: str,
    levels: str = Query(default="day,30,5", description="Comma-separated levels, e.g. day,30,5"),
    count: int = Query(default=800, ge=50, le=5000),
    structure_engine: str = Query(default="chan_py", description="chan_py | czsc | dual"),
    cchan_preset: str = Query(default="live_tolerant"),
    compute_profile: str = Query(default="radar_tactical_v1"),
):
    """Debug/internal structure engine endpoint.

    The default remains chan_py. Use structure_engine=dual to attach CZSC
    shadow output and comparison without changing the primary result.
    """
    if compute_profile not in COMPUTE_PROFILES:
        raise HTTPException(status_code=400, detail=f"unsupported compute profile: {compute_profile}")
    try:
        engine = normalize_engine_mode(structure_engine)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    freq_list = [item.strip() for item in levels.split(",") if item.strip()]
    invalid = [item for item in freq_list if item.lower() not in FREQ_ALIASES]
    if invalid:
        raise HTTPException(status_code=400, detail=f"unsupported levels: {', '.join(invalid)}")
    result = await analyze_structure_with_engine(
        symbol=normalize_symbol(symbol),
        levels=freq_list,
        count=count,
        structure_engine=engine,
        cchan_preset=cchan_preset,
        compute_profile=compute_profile,
    )
    return {"status": "success", "data": result}


@router.get("/jobs")
async def get_structure_jobs(limit: int = Query(50, ge=1, le=200)):
    return {"status": "success", "data": structure_job_stats(limit=limit)}


@router.post("/prewarm")
async def prewarm_structure_jobs(request: StructurePrewarmRequest):
    if request.compute_profile not in COMPUTE_PROFILES:
        raise HTTPException(status_code=400, detail=f"unsupported compute profile: {request.compute_profile}")
    items = []
    for raw_symbol in request.symbols:
        symbol = normalize_symbol(raw_symbol)
        for freq in request.freqs:
            if str(freq or "").strip().lower() not in FREQ_ALIASES:
                items.append({
                    "symbol": symbol,
                    "freq": freq,
                    "status": "skipped",
                    "reason": "INVALID_FREQ",
                })
                continue
            structure_key, context = build_formal_structure_key(
                symbol=symbol,
                freq=freq,
                cchan_preset=request.cchan_preset,
                compute_profile=request.compute_profile,
            )
            if structure_key is None:
                items.append({
                    "symbol": symbol,
                    "freq": freq,
                    "status": "skipped",
                    "reason": context.get("freshness", {}).get("stale_reason") or "NO_DATA",
                })
                continue
            job = enqueue_structure_job(
                structure_key,
                priority=request.priority,
                reason="manual_prewarm",
                retry_terminal=True,
            )
            items.append({
                "symbol": symbol,
                "freq": structure_key.freq,
                "status": job.get("status"),
                "job_id": job.get("job_id"),
                "structure_key_hash": structure_key.hash,
                "enqueued": job.get("enqueued"),
                "bumped": job.get("bumped"),
                "retried": job.get("retried"),
            })
    return {"status": "success", "data": {"items": items}}
