"""CT-OS V4.5 多元宇宙日志 API"""

import logging
from fastapi import APIRouter, Query
from pydantic import BaseModel

from server.services.multiverse_service import (
    take_daily_snapshot, settle_previous, get_timeline,
    get_scorecard, ai_daily_review, auto_daily_run
)

logger = logging.getLogger(__name__)
router = APIRouter()


class SnapshotRequest(BaseModel):
    symbol: str


@router.post("/snapshot/{symbol}")
async def create_snapshot(symbol: str):
    """手动拍快照"""
    result = await take_daily_snapshot(symbol)
    return {"status": "success", "data": result}


@router.post("/settle/{symbol}")
async def settle(symbol: str):
    """手动结算昨天的分类"""
    result = await settle_previous(symbol)
    return {"status": "success", "data": result}


@router.get("/timeline/{symbol}")
async def timeline(symbol: str, days: int = Query(30, ge=1, le=90)):
    """获取时间线（最近N天）"""
    data = get_timeline(symbol, days=days)
    return {"status": "success", "data": data}


@router.get("/scorecard/{symbol}")
async def scorecard(symbol: str, days: int = Query(30, ge=1, le=90)):
    """获取记分卡统计"""
    data = get_scorecard(symbol, days=days)
    return {"status": "success", "data": data}


@router.post("/review/{symbol}")
async def review(symbol: str):
    """AI 复盘解读"""
    result = await ai_daily_review(symbol)
    return {"status": "success", "data": result}


@router.post("/auto_run")
async def auto_run():
    """手动触发一次自动运行（调试用）"""
    await auto_daily_run()
    return {"status": "success", "message": "auto_run completed"}
