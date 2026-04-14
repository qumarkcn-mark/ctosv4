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

# 全局内存中的沙盘任务状态
# 格式: { task_id: {"symbol": str, "freq": str, "klines": list, "current_index": int, "created_at": float} }
TASKS = {}
_TASK_TTL = 3600  # 1小时过期


def _cleanup_stale_tasks():
    """清理超过 TTL 的过期沙盘任务，防止内存泄漏"""
    now = time.time()
    stale = [k for k, v in TASKS.items() if now - v.get("created_at", 0) > _TASK_TTL]
    for k in stale:
        del TASKS[k]
    if stale:
        logger.info("清理 %d 个过期沙盘任务", len(stale))


def _normalize_symbol(symbol: str) -> str:
    """统一股票代码格式为 sh.600519 形式"""
    symbol = symbol.strip()
    if "." not in symbol and len(symbol) >= 7:
        return f"{symbol[:2]}.{symbol[2:]}"
    return symbol

class StartRequest(BaseModel):
    pool_symbols: List[str] = []
    pool: str = "custom"  # "custom" 或 "all"
    freq: str = "day"
    window_count: int = 80

@router.post("/start")
async def start_training(req: StartRequest):
    """启动一次新的推演沙盘训练任务"""
    _cleanup_stale_tasks()
    
    # 1. 确定最终股池
    symbols = req.pool_symbols
    if req.pool == "all" or not symbols:
        # 简单策略：如果你想要"全量"，在这里暂时用几个默认好票，
        # 等未来有一个全部股票缓存表再放开随机。为了保证能查出数据，暂硬编码一些高活跃股。
        symbols = [
            "sh.600519", "sz.000001", "sh.600000", "sz.002594", 
            "sh.601318", "sz.000858", "sh.600036", "sz.002714"
        ]
    
    # 2. 随机选中一只股票，尝试获取包含足够 K 线的数据
    max_retries = 5
    selected_klines = []
    symbol = ""
    
    for _ in range(max_retries):
        symbol = _normalize_symbol(random.choice(symbols))
            
        count = count_klines(symbol, req.freq)
        if count < 200:
            # 不要同步阻塞！直接调用 async 工具函数加载
            try:
                from server.services.baostock_service import ensure_klines_cached
                success = await ensure_klines_cached(symbol, req.freq, min_count=200)
                if not success:
                    continue
            except Exception as e:
                logger.error(f"Try fetch {symbol} klines failed: {e}")
                continue
                
        klines = query_klines(symbol, req.freq, limit=1000)
        # 需要至少有 window_count + 50 根用来推演
        if len(klines) > req.window_count + 50:
            selected_klines = klines
            break

    if not selected_klines:
        raise HTTPException(status_code=400, detail="所选股池数据不足，请换一批或重试")

    # 3. 随机选择起始点
    # current_index 代表我们要发给前端的"最后"一根初始 K 线位置
    # 范围：从 window_count，到 max_idx - 50 (预留50根来推演)
    max_idx = len(selected_klines) - 1
    max_start_idx = max_idx - 30 # 最少留30根推演
    if max_start_idx <= req.window_count:
        max_start_idx = req.window_count + 1
        
    start_index = random.randint(req.window_count, max_start_idx)
    
    # 4. 注册 Task（附带创建时间用于过期清理）
    task_id = str(uuid.uuid4())
    TASKS[task_id] = {
        "symbol": symbol,
        "freq": req.freq,
        "klines": selected_klines,
        "current_index": start_index,
        "created_at": time.time()
    }
    
    # 5. 准备初始数据
    initial_klines = selected_klines[start_index - req.window_count + 1 : start_index + 1]
    
    return {
        "task_id": task_id,
        "symbol": symbol,
        "start_date": initial_klines[0]["date"],
        "end_date_initial": initial_klines[-1]["date"],
        "klines": initial_klines,
        "total_count": len(selected_klines),
        "start_index": start_index
    }


class AdvanceRequest(BaseModel):
    task_id: str

@router.post("/advance")
async def advance_training(req: AdvanceRequest):
    """向后推进一天"""
    task = TASKS.get(req.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found or expired")
    
    klines = task["klines"]
    current_index = task["current_index"]
    
    next_index = current_index + 1
    
    if next_index >= len(klines):
        return {"is_end": True}
        
    # 更新内存中的游标
    task["current_index"] = next_index
    
    next_kline = klines[next_index]
    
    return {
        "is_end": next_index == len(klines) - 1,
        "next_kline": next_kline
    }
