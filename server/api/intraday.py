"""Intraday preview APIs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from server.domain.symbols import normalize_symbol
from server.services.intraday_observation_service import get_intraday_observation

router = APIRouter()


@router.get("/observation/{symbol}")
async def intraday_observation(symbol: str):
    """Return quote-aggregated intraday preview facts for one symbol."""
    try:
        payload = await get_intraday_observation(normalize_symbol(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "success", "data": payload}
