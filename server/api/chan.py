from fastapi import APIRouter, Query
from server.services.chan_service import analyze_matrix_state
from server.services.chan_detail_service import get_chan_detail

router = APIRouter()


@router.get("/matrix/{symbol}")
async def get_chan_matrix(symbol: str):
    """
    获取指定股票的双轴跨级别缠论状态矩阵。
    包含：
    - matrix_a: 日线 + 30分钟 + 5分钟
    - matrix_b: 日线 + 60分钟 + 15分钟
    """
    matrix_data = await analyze_matrix_state(symbol)
    return {"status": "success", "data": matrix_data}


@router.get("/detail/{symbol}")
async def get_chan_detail_api(
    symbol: str,
    freq: str = Query(default="day", description="K线级别: day/60/30/15/5"),
    count: int = Query(default=500, ge=50, le=2000, description="K线条数"),
):
    """
    获取指定股票的完整缠论几何解析数据，供 KlineChart 前端渲染。

    返回：
    - klines:    原始 K 线（OHLCV）
    - bis:       笔（折线几何坐标 x0/y0/x1/y1）
    - segs:      线段（TODO，待 chan_engine 升级后接入）
    - zhongshus: 中枢（矩形框 begin_date/end_date/zg/zd/gg/dd）
    - macd:      MACD 指标（dif/dea/hist/dates）
    - stats:     统计摘要（k线数/笔数/中枢数）
    """
    # 兼容多种股票代码格式：sh600519 / sh.600519 / sh-600519
    symbol_bs = symbol.replace("-", ".")
    if len(symbol_bs) > 2 and symbol_bs[2] != ".":
        symbol_bs = f"{symbol_bs[:2]}.{symbol_bs[2:]}"

    result = await get_chan_detail(symbol_bs, freq=freq, count=count)
    return {"status": "success", "data": result}
