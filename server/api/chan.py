from fastapi import APIRouter
from server.services.chan_service import analyze_matrix_state

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
