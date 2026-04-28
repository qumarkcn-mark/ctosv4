from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
import sqlite3
from datetime import datetime
from server.db.database import get_connection
from server.domain.symbols import symbol_aliases, to_tencent_symbol

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
def get_watchlist(user_id: int = 1):
    """获取用户所有分组及其包含的股票"""
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
def create_group(group: WatchlistGroupCreate, user_id: int = 1):
    """创建新分组"""
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
def rename_group(name: str, group: WatchlistGroupRename, user_id: int = 1):
    """重命名分组"""
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
def delete_group(name: str, user_id: int = 1):
    """删除分组 (级联删除其下股票)"""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM watchlist_groups WHERE user_id=? AND name=?", (user_id, name))
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()

@router.post("/groups/{group_name}/stocks")
def add_stock(group_name: str, item: WatchlistItem, user_id: int = 1):
    """添加股票到分组（保证全局唯一，从其他分组中移除）"""
    import sqlite3
    stock_symbol = to_tencent_symbol(item.symbol)
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
        return {"status": "ok"}
    except sqlite3.IntegrityError:
        raise HTTPException(400, "添加失败")
    finally:
        conn.close()

@router.delete("/groups/{group_name}/stocks/{symbol}")
def remove_stock(group_name: str, symbol: str, user_id: int = 1):
    """从分组中移除股票"""
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
