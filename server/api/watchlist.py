from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import sqlite3
from datetime import datetime
from server import config
from server.api.auth import get_current_user_id
from server.db.kline_lake import query_klines as query_lake_klines
from server.db.database import get_connection
from server.domain.symbols import normalize_symbol, symbol_aliases, to_tencent_symbol
from server.engines.ai_native.czsc_snapshot_service import DEFAULT_LEVELS, get_snapshot_status_batch
from server.workers.kline_sync_worker import ALL_FREQS, sync_new_watchlist_symbol

router = APIRouter()

# ── Models ──
class WatchlistItem(BaseModel):
    symbol: str
    name: Optional[str] = None
    sort_order: int = 0

class WatchlistGroupBase(BaseModel):
    name: str

class WatchlistGroupCreate(WatchlistGroupBase):
    pass

class WatchlistGroupRename(WatchlistGroupBase):
    pass

class WatchlistGroupResponse(BaseModel):
    id: int
    name: str
    stocks: List[WatchlistItem]

# ── Endpoints ──

@router.get("", response_model=List[WatchlistGroupResponse])
def get_watchlist(current_user_id: int = Depends(get_current_user_id)):
    """获取用户所有分组及其包含的股票"""
    user_id = current_user_id
    conn = get_connection()
    try:
        # 获取所有分组
        groups = conn.execute(
            "SELECT id, name FROM watchlist_groups WHERE user_id=? ORDER BY sort_order, id",
            (user_id,)
        ).fetchall()
        
        # 默认分组兜底逻辑（如果从来没创建过）
        if not groups:
            conn.execute(
                "INSERT INTO watchlist_groups (user_id, name, sort_order) VALUES (?, ?, ?), (?, ?, ?), (?, ?, ?)",
                (user_id, "观察", 0, user_id, "重仓", 1, user_id, "短线", 2)
            )
            conn.commit()
            groups = conn.execute(
                "SELECT id, name FROM watchlist_groups WHERE user_id=? ORDER BY sort_order, id",
                (user_id,)
            ).fetchall()

        result = []
        for g in groups:
            g_id = g["id"]
            items = conn.execute(
                "SELECT symbol, name, sort_order FROM watchlist_items WHERE group_id=? ORDER BY sort_order, id",
                (g_id,)
            ).fetchall()
            result.append({
                "id": g_id,
                "name": g["name"],
                "stocks": [dict(item) for item in items]
            })
            
        return result
    finally:
        conn.close()

@router.post("/groups")
def create_group(group: WatchlistGroupCreate, current_user_id: int = Depends(get_current_user_id)):
    """创建新分组"""
    user_id = current_user_id
    conn = get_connection()
    try:
        # 获取当前最大的 sort_order
        row = conn.execute("SELECT MAX(sort_order) as m FROM watchlist_groups WHERE user_id=?", (user_id,)).fetchone()
        next_order = (row["m"] or 0) + 1
        
        cursor = conn.execute(
            "INSERT INTO watchlist_groups (user_id, name, sort_order) VALUES (?, ?, ?)",
            (user_id, group.name, next_order)
        )
        conn.commit()
        return {"status": "ok", "id": cursor.lastrowid}
    except sqlite3.IntegrityError:
        raise HTTPException(400, "分组名已存在")
    finally:
        conn.close()

@router.put("/groups/{name}")
def rename_group(name: str, group: WatchlistGroupRename, current_user_id: int = Depends(get_current_user_id)):
    """重命名分组"""
    user_id = current_user_id
    conn = get_connection()
    try:
        row = conn.execute("SELECT id FROM watchlist_groups WHERE user_id=? AND name=?", (user_id, name)).fetchone()
        if not row:
            raise HTTPException(404, "分组不存在")
            
        conn.execute(
            "UPDATE watchlist_groups SET name=? WHERE id=?",
            (group.name, row["id"])
        )
        conn.commit()
        return {"status": "ok"}
    except sqlite3.IntegrityError:
        raise HTTPException(400, "目标分组名已存在")
    finally:
        conn.close()

@router.delete("/groups/{name}")
def delete_group(name: str, current_user_id: int = Depends(get_current_user_id)):
    """删除分组 (级联删除其下股票)"""
    user_id = current_user_id
    conn = get_connection()
    try:
        conn.execute("DELETE FROM watchlist_groups WHERE user_id=? AND name=?", (user_id, name))
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()

@router.post("/groups/{group_name}/stocks")
def add_stock(
    group_name: str,
    item: WatchlistItem,
    background_tasks: BackgroundTasks,
    current_user_id: int = Depends(get_current_user_id),
):
    """添加股票到分组（保证全局唯一，从其他分组中移除）"""
    user_id = current_user_id
    import sqlite3
    canonical_symbol = normalize_symbol(item.symbol)
    stock_symbol = to_tencent_symbol(canonical_symbol)
    aliases = symbol_aliases(item.symbol)
    conn = get_connection()
    try:
        g_row = conn.execute("SELECT id FROM watchlist_groups WHERE user_id=? AND name=?", (user_id, group_name)).fetchone()
        if not g_row:
            raise HTTPException(404, "分组不存在")
        g_id = g_row["id"]
        
        # 删除在其他分组的这只股票
        conn.execute(
            "DELETE FROM watchlist_items WHERE symbol IN (?, ?, ?) AND group_id IN (SELECT id FROM watchlist_groups WHERE user_id=?)",
            (*aliases, user_id)
        )
        
        # 添加到新分组
        row = conn.execute("SELECT MAX(sort_order) as m FROM watchlist_items WHERE group_id=?", (g_id,)).fetchone()
        next_order = (row["m"] or 0) + 1
        
        conn.execute(
            "INSERT INTO watchlist_items (group_id, symbol, name, sort_order) VALUES (?, ?, ?, ?)",
            (g_id, stock_symbol, item.name, next_order)
        )
        conn.commit()
        background_tasks.add_task(sync_new_watchlist_symbol, canonical_symbol)
        return {
            "status": "ok",
            "symbol": stock_symbol,
            "data_sync": {
                "status": "queued",
                "source": "baostock" if config.BAOSTOCK_AUTO_SYNC_ENABLED else "tdx",
                "quick_freqs": ["day", "5"],
                "full_freqs": ALL_FREQS,
                "reason": "" if config.BAOSTOCK_AUTO_SYNC_ENABLED else "TDX_SINGLE_SYMBOL_INIT",
            },
        }
    except sqlite3.IntegrityError:
        raise HTTPException(400, "添加失败")
    finally:
        conn.close()


@router.get("/stocks/{symbol}/init-status")
def get_stock_init_status(symbol: str, current_user_id: int = Depends(get_current_user_id)):
    """查询新票初始化状态：TDX K线、CZSC snapshot、是否可推演。"""
    canonical_symbol = normalize_symbol(symbol)
    kline_items = []
    for freq in ALL_FREQS:
        qfq_rows = query_lake_klines(canonical_symbol, freq, limit=1, adjustflag="2", source="tdx")
        raw_rows = query_lake_klines(canonical_symbol, freq, limit=1, adjustflag="3", source="tdx")
        rows = qfq_rows
        adjustflag = "2"
        if qfq_rows and raw_rows and str(raw_rows[-1].get("date") or "") > str(qfq_rows[-1].get("date") or ""):
            rows = raw_rows
            adjustflag = "3"
        elif not rows:
            rows = raw_rows
            adjustflag = "3" if rows else "2"
        latest = rows[-1]["date"] if rows else ""
        kline_items.append({
            "freq": freq,
            "ready": bool(rows),
            "latest": latest,
            "source": "tdx",
            "adjustflag": adjustflag,
        })

    snapshots = get_snapshot_status_batch(
        symbols=[canonical_symbol],
        levels=list(DEFAULT_LEVELS),
    ).get(canonical_symbol, {})
    snapshot_items = [
        {
            "level": level,
            "status": (snapshots.get(level) or {}).get("status", "unknown"),
            "ready": (snapshots.get(level) or {}).get("status") == "fresh",
            "last_bar_at": ((snapshots.get(level) or {}).get("freshness") or {}).get("last_bar_at", ""),
            "job_status": (((snapshots.get(level) or {}).get("job") or {}).get("status") or ""),
        }
        for level in DEFAULT_LEVELS
    ]
    kline_ready = all(item["ready"] for item in kline_items if item["freq"] in {"week", "day", "30", "5"})
    snapshot_ready = all(item["ready"] for item in snapshot_items)
    if snapshot_ready:
        stage = "ready_for_reasoning"
    elif kline_ready:
        stage = "snapshot_pending"
    else:
        stage = "tdx_kline_pending"

    return {
        "symbol": canonical_symbol,
        "source": "tdx",
        "stage": stage,
        "ready_for_reasoning": snapshot_ready,
        "kline": {"ready": kline_ready, "items": kline_items},
        "snapshots": {"ready": snapshot_ready, "items": snapshot_items},
        "next_action": _init_next_action(stage),
    }


def _init_next_action(stage: str) -> str:
    if stage == "ready_for_reasoning":
        return "可以生成 AI 推演"
    if stage == "snapshot_pending":
        return "等待 CZSC snapshot worker 生成四级别结构"
    return "请先同步 TDX K 线或确认 Windows TDX 本地数据可用"

@router.delete("/groups/{group_name}/stocks/{symbol}")
def remove_stock(group_name: str, symbol: str, current_user_id: int = Depends(get_current_user_id)):
    """从分组中移除股票"""
    user_id = current_user_id
    aliases = symbol_aliases(symbol)
    conn = get_connection()
    try:
        g_row = conn.execute("SELECT id FROM watchlist_groups WHERE user_id=? AND name=?", (user_id, group_name)).fetchone()
        if not g_row:
            raise HTTPException(404, "分组不存在")
            
        conn.execute(
            "DELETE FROM watchlist_items WHERE group_id=? AND symbol IN (?, ?, ?)",
            (g_row["id"], *aliases),
        )
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()
