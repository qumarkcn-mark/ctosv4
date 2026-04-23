import uuid
import time
import random
import logging
from typing import List
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from server.db.kline_lake import query_klines, count_klines

router = APIRouter()
logger = logging.getLogger(__name__)

import uuid
import time
import random
import logging
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException

from server.db.kline_lake import query_klines, count_klines
from server.services.chan_detail_service import get_chan_detail

router = APIRouter()
logger = logging.getLogger(__name__)

TASKS = {}
_TASK_TTL = 3600 * 12  # 延长到 12 小时


def _cleanup_stale_tasks():
    now = time.time()
    stale = [k for k, v in TASKS.items() if now - v.get("created_at", 0) > _TASK_TTL]
    for k in stale:
        del TASKS[k]
    if stale:
        logger.info("清理 %d 个过期沙盘任务", len(stale))


def _normalize_symbol(symbol: str) -> str:
    symbol = symbol.strip()
    if "." not in symbol and len(symbol) >= 7:
        return f"{symbol[:2]}.{symbol[2:]}"
    return symbol


class StartRequest(BaseModel):
    pool_symbols: List[str] = []
    pool: str = "custom"


@router.post("/start")
async def start_training(req: StartRequest):
    """启动一次新的推演沙盘训练任务，锚定一个虚拟当前时间 (simulated_time)"""
    _cleanup_stale_tasks()

    symbols = req.pool_symbols
    if req.pool == "all" or not symbols:
        symbols = [
            "sh.600519", "sz.000001", "sh.600000", "sz.002594",
            "sh.601318", "sz.000858", "sh.600036", "sz.002714"
        ]

    max_retries = 5
    selected_klines = []
    symbol = ""

    # 使用 30 分钟级别来寻找锚点（保证锚点时间足够细）
    anchor_freq = "30"
    
    for _ in range(max_retries):
        symbol = _normalize_symbol(random.choice(symbols))

        count = count_klines(symbol, anchor_freq)
        if count < 1000:
            try:
                from server.services.baostock_service import ensure_klines_cached
                success = await ensure_klines_cached(symbol, anchor_freq, min_count=1000)
                if not success:
                    continue
            except Exception as e:
                logger.error(f"Try fetch {symbol} klines failed: {e}")
                continue

        klines = query_klines(symbol, anchor_freq, limit=3000)
        # 至少要留出 500 根给前端，且至少留 50 根用于未来推演
        if len(klines) > 550:
            selected_klines = klines
            break

    if not selected_klines:
        raise HTTPException(status_code=400, detail="所选股池数据不足，请换一批或重试")

    # 随机选起始点：从 500 到 len-50
    start_index = random.randint(500, len(selected_klines) - 50)
    simulated_time = selected_klines[start_index]["date"]

    task_id = str(uuid.uuid4())
    TASKS[task_id] = {
        "symbol": symbol,
        "simulated_time": simulated_time,
        "created_at": time.time(),
        "klines_30m": selected_klines, # 缓存一份用于步进推导
        "current_30m_idx": start_index
    }

    return {
        "task_id": task_id,
        "symbol": symbol,
        "simulated_time": simulated_time,
    }


class AdvanceRequest(BaseModel):
    task_id: str
    freq: str = "30"


@router.post("/advance")
async def advance_training(req: AdvanceRequest):
    """向后推进一根 K 线（按请求的级别）"""
    task = TASKS.get(req.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found or expired")

    # 使用 query_klines 找出紧接着 simulated_time 后的一根该级别的 K 线
    # 因为数据湖里的时间是正序的，我们可以重新查一下
    # 为了避免每次查库，其实最好的做法是直接查一次接下来 10 根，取第一根
    symbol = task["symbol"]
    simulated_time = task["simulated_time"]
    
    # 巧妙利用 query_klines 查 simulated_time 之后的数据
    # 注意：这里的 start_date 在 db 层包含等于，所以要过滤掉等于 simulated_time 的
    future_klines = query_klines(symbol, req.freq, start_date=simulated_time, limit=5)
    
    next_kline = None
    for k in future_klines:
        if k["date"] > simulated_time:
            next_kline = k
            break
            
    if not next_kline:
        return {"is_end": True, "simulated_time": simulated_time}

    # 推进虚拟时间
    new_simulated_time = next_kline["date"]
    task["simulated_time"] = new_simulated_time

    return {
        "is_end": False,
        "simulated_time": new_simulated_time,
    }


@router.get("/chan-detail")
async def sandbox_chan_detail(task_id: str, freq: str = "day"):
    """
    沙盘专属的缠论解析接口。
    强制附带 end_date=simulated_time 以防止未来数据泄露。
    返回 500 根数据。
    """
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found or expired")
        
    symbol = task["symbol"]
    simulated_time = task["simulated_time"]
    
    try:
        # 获取截至模拟时间点的缠论解析，取 500 根以保证性能和结构
        detail = await get_chan_detail(symbol, freq, count=500, end_date=simulated_time)
        return {"data": detail}
    except Exception as e:
        logger.error(f"Sandbox chan detail failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
