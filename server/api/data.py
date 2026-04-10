"""CSV 导入 + 行情查询 API"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Query

from server.db.database import get_connection
from server.services.csv_importer import import_csv
from server.services.price_service import get_current_price, get_batch_prices

router = APIRouter()


# ── CSV 导入 ──

@router.post("/import/csv")
def import_csv_file(
    file: UploadFile = File(...),
    broker: str = Query("auto", description="eastmoney/ths/auto"),
    user_id: int = 1,
):
    """导入券商交割单 CSV"""
    content = await file.read()
    csv_text = content.decode("utf-8-sig")  # 东方财富 CSV 带 BOM

    conn = get_connection()
    try:
        result = import_csv(conn, user_id, csv_text, broker)
        return result
    finally:
        conn.close()


# ── 行情查询 ──

@router.get("/price/{symbol}")
async def query_price(symbol: str):
    """查询单只股票当前价格"""
    result = await get_current_price(symbol)
    if not result:
        raise HTTPException(404, f"行情查询失败: {symbol}")
    return result


@router.get("/prices")
async def query_batch_prices(symbols: str = Query(..., description="逗号分隔的代码")):
    """批量查询股票价格"""
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(400, "请提供至少一个股票代码")
    if len(symbol_list) > 30:
        raise HTTPException(400, "最多同时查询 30 只")

    results = await get_batch_prices(symbol_list)
    return {"count": len(results), "prices": results}
