"""CT-OS V4.0 — 股票搜索 API"""

from fastapi import APIRouter, Query

from server.services.stock_search import search_stocks as search_stocks_service

router = APIRouter()


@router.get("/search")
async def search_stocks(q: str = Query(..., min_length=1, description="搜索关键词")):
    """
    搜索股票，支持代码/名称/拼音。
    返回 [{symbol, name, market}, ...]
    """
    return {"results": await search_stocks_service(q, limit=20)}
