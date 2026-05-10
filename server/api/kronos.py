import logging
from fastapi import APIRouter, HTTPException, Query
from server.domain.symbols import normalize_symbol
from server.services.kronos_service import KronosUnavailable, kronos_service

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/forecast/{symbol}")
async def get_kronos_forecast(
    symbol: str,
    lookback: int = Query(default=400, ge=100, le=1000, description="回顾K线条数"),
    pred_len: int = Query(default=10, ge=1, le=60, description="预测未来步数"),
):
    """
    获取基于 Kronos (AAAI 2026) 基础模型的动力学预测。

    返回:
    - force_score: 合力分 (-100 到 100)，正数为向上合力，负数为向下合力
    - verdict: 结论描述
    - current_price: 当前参考收盘价
    - predicted_price: 预测终点价格
    - change_pct: 预期涨跌幅
    - forecast_data: 未来 K 线的模拟序列
    """
    symbol_bs = normalize_symbol(symbol)

    try:
        result = await kronos_service.get_forecast(symbol_bs, lookback=lookback, pred_len=pred_len)
        if not result:
            raise HTTPException(status_code=404, detail=f"无法获取 {symbol} 的预测数据，请检查行情连接。")

        return {"status": "success", "data": result}
    except HTTPException:
        raise
    except KronosUnavailable as e:
        logger.warning(f"Kronos unavailable: {e}")
        raise HTTPException(status_code=503, detail=f"Kronos 模型暂不可用：{e}")
    except Exception as e:
        logger.error(f"Kronos API Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/resonance/{symbol}", deprecated=True)
async def get_kronos_resonance(symbol: str):
    """
    已废弃：获取多级别动力学共振分析 (日线 + 30分 + 5分)。

    保留该端点仅为兼容旧客户端；新链路应消费 /forecast 输出中的预测序列，
    并通过 Signal V2 的 kronos_timeline / kronos_envelope 展示时间和价格参考。

    返回:
    - resonance_score: 平均共振分
    - resonance_type: 共振类型 (多头共振/空头共振/小转大潜力)
    - levels: 各级别的详细预测数据
    """
    symbol_bs = normalize_symbol(symbol)
    try:
        result = await kronos_service.get_multi_level_analysis(symbol_bs)
        if not result:
            raise HTTPException(status_code=404, detail=f"无法获取 {symbol} 的多级别分析数据。")
        return {"status": "success", "data": result}
    except HTTPException:
        raise
    except KronosUnavailable as e:
        logger.warning(f"Kronos resonance unavailable: {e}")
        raise HTTPException(status_code=503, detail=f"Kronos 模型暂不可用：{e}")
    except Exception as e:
        logger.error(f"Kronos Resonance API Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/status")
async def get_kronos_status():
    """检查模型加载状态"""
    return kronos_service.status()
