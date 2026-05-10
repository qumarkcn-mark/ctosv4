"""Structure snapshot job queue management APIs."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from server.domain.symbols import normalize_symbol
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
