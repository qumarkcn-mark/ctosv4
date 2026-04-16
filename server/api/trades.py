"""交易记录 CRUD API"""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from server.db.database import get_connection
from server.services.position_calc import recalculate_position

router = APIRouter()


# ── 请求/响应模型 ──

class TradeCreate(BaseModel):
    """创建交易请求"""
    symbol: str = Field(..., description="股票代码, 如 sh600519")
    name: Optional[str] = Field(None, description="股票名称")
    direction: str = Field(..., description="BUY 或 SELL")
    price: float = Field(..., gt=0, description="成交价格")
    quantity: int = Field(..., gt=0, description="成交数量")
    stop_loss_price: Optional[float] = Field(None, description="止损价")
    reason_text: Optional[str] = Field(None, description="交易原因")
    reason_category: Optional[str] = Field(None, description="原因分类")
    trend_direction: Optional[str] = Field(None, description="大级别方向")
    source: str = Field("MANUAL", description="来源: VOICE/MANUAL/CSV_IMPORT")
    traded_at: Optional[str] = Field(None, description="成交时间, ISO格式")


class TradeFromText(BaseModel):
    """从文本提取交易请求 (语音录入)"""
    text: str = Field(..., description="原始文本, 如 '买了300股茅台1800'")


class TradeResponse(BaseModel):
    """交易记录响应"""
    id: int
    user_id: int
    symbol: str
    name: Optional[str]
    direction: str
    price: float
    quantity: int
    amount: float
    stop_loss_price: Optional[float]
    reason_text: Optional[str]
    reason_category: Optional[str]
    trend_direction: Optional[str]
    source: str
    traded_at: str
    created_at: str


# ── 路由 ──

@router.post("", response_model=TradeResponse)
async def create_trade(trade: TradeCreate, user_id: int = 1):
    """创建一笔交易记录"""
    from fastapi.concurrency import run_in_threadpool

    # 校验方向
    if trade.direction not in ("BUY", "SELL"):
        raise HTTPException(400, "direction 必须是 BUY 或 SELL")

    # 校验原因分类
    valid_categories = ("CHAN_SIGNAL", "FRIEND_TIP", "FEELING", "OTHER", None)
    if trade.reason_category not in valid_categories:
        raise HTTPException(400, f"reason_category 必须是 {valid_categories}")

    # 计算金额
    amount = trade.price * trade.quantity

    # 成交时间
    traded_at = trade.traded_at or datetime.now().isoformat()

    # 自动计算止损价 (武器2：止损看门狗) — 需要 await 异步网络请求
    stop_loss_price = trade.stop_loss_price
    if stop_loss_price is None:
        from server.services.atr_service import calculate_stop_loss
        stop_loss_price = await calculate_stop_loss(trade.symbol, trade.price, trade.direction)

    # 同步 DB 操作隔离到线程池
    def _db_insert():
        conn = get_connection()
        try:
            cursor = conn.execute(
                """
                INSERT INTO trades
                    (user_id, symbol, name, direction, price, quantity, amount,
                     stop_loss_price, reason_text, reason_category,
                     trend_direction, source, traded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, trade.symbol, trade.name, trade.direction,
                    trade.price, trade.quantity, amount,
                    stop_loss_price, trade.reason_text, trade.reason_category,
                    trade.trend_direction, trade.source, traded_at,
                ),
            )
            trade_id = cursor.lastrowid
            recalculate_position(conn, user_id, trade.symbol)
            conn.commit()
            row = conn.execute(
                "SELECT * FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()
            return dict(row)
        finally:
            conn.close()

    return await run_in_threadpool(_db_insert)


@router.get("")
def list_trades(
    user_id: int = 1,
    symbol: Optional[str] = None,
    direction: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """查询交易记录列表"""
    conn = get_connection()
    try:
        query = "SELECT * FROM trades WHERE user_id = ?"
        params: list = [user_id]

        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        if direction:
            if direction not in ("BUY", "SELL"):
                raise HTTPException(400, "direction 必须是 BUY 或 SELL")
            query += " AND direction = ?"
            params.append(direction)

        query += " ORDER BY traded_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()

        # 统计
        count_query = "SELECT COUNT(*) FROM trades WHERE user_id = ?"
        count_params: list = [user_id]
        if symbol:
            count_query += " AND symbol = ?"
            count_params.append(symbol)
        total = conn.execute(count_query, count_params).fetchone()[0]

        return {
            "total": total,
            "trades": [dict(r) for r in rows],
        }
    finally:
        conn.close()


# 注意: /from-text 必须在 /{trade_id} 之前注册，否则会被路径参数捕获
@router.post("/from-text")
async def create_trade_from_text(req: TradeFromText, user_id: int = 1):
    """从文本提取交易信息 (Phase 3: LLM 提取)"""
    # Phase 3 实现: 调用 LLM 提取结构化数据
    # 目前返回 placeholder
    return {
        "status": "not_implemented",
        "message": "语音提取功能将在 Phase 3 实现",
        "raw_text": req.text,
    }


@router.get("/{trade_id}")
def get_trade(trade_id: int, user_id: int = 1):
    """查询单笔交易"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM trades WHERE id = ? AND user_id = ?",
            (trade_id, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "交易记录不存在")
        return dict(row)
    finally:
        conn.close()


class TradeUpdate(BaseModel):
    """编辑交易请求（只传需要修改的字段）"""
    price: Optional[float] = None
    quantity: Optional[int] = None
    direction: Optional[str] = None
    name: Optional[str] = None
    stop_loss_price: Optional[float] = None
    reason_text: Optional[str] = None
    reason_category: Optional[str] = None
    traded_at: Optional[str] = None


@router.put("/{trade_id}")
def update_trade(trade_id: int, update: TradeUpdate, user_id: int = 1):
    """编辑交易记录（同时重算持仓）"""
    conn = get_connection()
    try:
        # 先查到原记录
        row = conn.execute(
            "SELECT * FROM trades WHERE id = ? AND user_id = ?",
            (trade_id, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "交易记录不存在")

        original = dict(row)

        # 合并更新字段
        new_price = update.price if update.price is not None else original["price"]
        new_qty = update.quantity if update.quantity is not None else original["quantity"]
        new_dir = update.direction if update.direction is not None else original["direction"]
        new_name = update.name if update.name is not None else original["name"]
        new_stop = update.stop_loss_price if update.stop_loss_price is not None else original["stop_loss_price"]
        new_reason = update.reason_text if update.reason_text is not None else original["reason_text"]
        new_cat = update.reason_category if update.reason_category is not None else original["reason_category"]
        new_time = update.traded_at if update.traded_at is not None else original["traded_at"]
        new_amount = new_price * new_qty

        conn.execute(
            """UPDATE trades
               SET price = ?, quantity = ?, amount = ?, direction = ?,
                   name = ?, stop_loss_price = ?, reason_text = ?,
                   reason_category = ?, traded_at = ?
               WHERE id = ?""",
            (new_price, new_qty, new_amount, new_dir,
             new_name, new_stop, new_reason, new_cat, new_time,
             trade_id),
        )

        # 重算持仓
        recalculate_position(conn, user_id, original["symbol"])
        conn.commit()

        updated = conn.execute(
            "SELECT * FROM trades WHERE id = ?", (trade_id,)
        ).fetchone()
        return dict(updated)
    finally:
        conn.close()



@router.delete("/{trade_id}")
def delete_trade(trade_id: int, user_id: int = 1):
    """删除交易记录 (同时重算持仓)"""
    conn = get_connection()
    try:
        # 先查到 symbol 用于重算持仓
        row = conn.execute(
            "SELECT symbol FROM trades WHERE id = ? AND user_id = ?",
            (trade_id, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "交易记录不存在")

        symbol = row["symbol"]
        conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))

        # 重算持仓
        recalculate_position(conn, user_id, symbol)
        conn.commit()

        return {"ok": True, "deleted_id": trade_id}
    finally:
        conn.close()
