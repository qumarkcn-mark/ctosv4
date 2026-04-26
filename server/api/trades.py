"""交易记录 CRUD API"""

from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from server.db.database import get_connection
from server.services.entry_thesis import build_entry_thesis_from_trade, persist_entry_thesis
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
    # 入场战法（BUY 时可传）：战法一 / 战法二 / 未知
    strategy_type: Optional[str] = Field(None, description="入场战法：战法一/战法二/未知")
    # 入场时5分中枢ZG（BUY 时可传，用于结构失效判断）
    m5_entry_zg: Optional[float] = Field(None, description="入场时5分中枢ZG价格")
    entry_level: Optional[str] = Field(None, description="入场级别，如 5m/30m/day")
    entry_zg: Optional[float] = Field(None, description="入场中枢 ZG")
    entry_zd: Optional[float] = Field(None, description="入场中枢 ZD")
    initial_target: Optional[float] = Field(None, description="入场初始目标价")
    trigger_conditions: Optional[list] = Field(None, description="入场触发条件列表")


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

def _calc_fee(price: float, quantity: int, direction: str, symbol: str) -> dict:
    """计算A股交易费用（佣金万5 + 过户费0.001% + 卖出印花税0.1%）"""
    amount = price * quantity
    # 佣金：万分之5，最低5元
    commission = max(amount * 0.0005, 5.0)
    # 过户费：万分之1（沪市收，深市免；此处统一收取）
    transfer_fee = amount * 0.00001
    # 印花税：仅卖出收0.1%
    stamp_duty = amount * 0.001 if direction == "SELL" else 0.0
    total = commission + transfer_fee + stamp_duty
    return {
        "commission": round(commission, 2),
        "transfer_fee": round(transfer_fee, 2),
        "stamp_duty": round(stamp_duty, 2),
        "total_fee": round(total, 2),
    }


def _is_astock_special(symbol: str) -> bool:
    """科创板(688xx)或北交所(8xxxxx)，最小交易单位非100股"""
    code = symbol.lower().removeprefix("sh").removeprefix("sz")
    return code.startswith("688") or (code.startswith("8") and not code.startswith("68"))


@router.post("", response_model=TradeResponse)
async def create_trade(trade: TradeCreate, user_id: int = 1):
    """创建一笔交易记录（含A股合规校验）"""
    from fastapi.concurrency import run_in_threadpool

    # ── 校验方向 ──
    if trade.direction not in ("BUY", "SELL"):
        raise HTTPException(400, "direction 必须是 BUY 或 SELL")

    # ── 校验原因分类 ──
    valid_categories = ("CHAN_SIGNAL", "FRIEND_TIP", "FEELING", "OTHER", None)
    if trade.reason_category not in valid_categories:
        raise HTTPException(400, f"reason_category 必须是 {valid_categories}")

    # ── A股：数量100手校验（科创板/北交所放开）──
    if not _is_astock_special(trade.symbol):
        if trade.quantity % 100 != 0:
            raise HTTPException(
                400,
                f"A股最小交易单位为100股（1手），当前数量 {trade.quantity} 不符合规格"
            )

    # ── SELL 校验：持仓必须存在且不超量 ──
    if trade.direction == "SELL":
        def _check_position():
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT quantity FROM positions WHERE user_id=? AND symbol=? AND quantity>0",
                    (user_id, trade.symbol),
                ).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

        pos = await run_in_threadpool(_check_position)
        stock_label = trade.name or trade.symbol
        if pos is None:
            raise HTTPException(400, f"您未持有 {stock_label}，无法卖出")
        if trade.quantity > pos["quantity"]:
            raise HTTPException(
                400,
                f"持仓仅 {pos['quantity']} 股，卖出数量 {trade.quantity} 超出持仓"
            )

    # ── 计算金额 ──
    amount = trade.price * trade.quantity

    # ── 成交时间 ──
    traded_at = trade.traded_at or datetime.now().isoformat()

    # ── T+1 软校验：查当日是否已买入该标的 ──
    today_str = datetime.now().strftime("%Y-%m-%d")

    def _check_t1():
        conn = get_connection()
        try:
            row = conn.execute(
                """SELECT COUNT(*) as cnt FROM trades
                   WHERE user_id=? AND symbol=? AND direction='BUY'
                   AND date(traded_at)=?""",
                (user_id, trade.symbol, today_str),
            ).fetchone()
            return row["cnt"] > 0
        finally:
            conn.close()

    t1_warning = False
    if trade.direction == "SELL":
        t1_warning = await run_in_threadpool(_check_t1)

    # ── 手续费计算 ──
    fee_info = _calc_fee(trade.price, trade.quantity, trade.direction, trade.symbol)

    # ── 自动计算止损价（ATR看门狗）──
    stop_loss_price = trade.stop_loss_price
    if stop_loss_price is None:
        from server.services.atr_service import calculate_stop_loss
        stop_loss_price = await calculate_stop_loss(trade.symbol, trade.price, trade.direction)

    # ── DB 写入（线程池）──
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

            # ── 入场战法持久化（仅 BUY，且只在首次建仓或战法未知时写入）──
            if trade.direction == "BUY":
                _st = trade.strategy_type
                _zg = trade.m5_entry_zg
                _entry_date = traded_at[:10]
                thesis = build_entry_thesis_from_trade(
                    trade_id=trade_id,
                    symbol=trade.symbol,
                    source=trade.source,
                    traded_at=traded_at,
                    strategy_type=trade.strategy_type,
                    entry_level=trade.entry_level,
                    entry_zg=trade.entry_zg,
                    entry_zd=trade.entry_zd,
                    m5_entry_zg=trade.m5_entry_zg,
                    original_stop_loss=stop_loss_price,
                    initial_target=trade.initial_target,
                    reason_text=trade.reason_text,
                    reason_category=trade.reason_category,
                    trend_direction=trade.trend_direction,
                    trigger_conditions=trade.trigger_conditions,
                )
                persist_entry_thesis(
                    conn,
                    user_id=user_id,
                    symbol=trade.symbol,
                    thesis=thesis,
                    strategy_type=_st,
                    entry_date=_entry_date,
                    m5_entry_zg=_zg,
                )

            conn.commit()
            row = conn.execute(
                "SELECT * FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()
            result = dict(row)
            result["fee_info"] = fee_info
            result["t1_warning"] = t1_warning
            return result
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
    """从自然语言/语音文本提取交易信息（调用 DeepSeek V3 解析）"""
    from server.services.llm_service import LLMService
    svc = LLMService()
    result = await svc.parse_trade_from_text(req.text)
    return {
        "status": "ok",
        "parsed": result,
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
