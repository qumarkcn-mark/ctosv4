"""仓位集中度与透视分析引擎"""

from typing import Dict, Any
from fastapi.concurrency import run_in_threadpool
from server.db.database import get_connection
from server.services.price_service import get_batch_prices

async def analyze_concentration(user_id: int, max_single_weight: float = 0.20, max_total_stocks: int = 5) -> Dict[str, Any]:
    """
    分析用户的仓位集中度（武器 1：仓位透视镜核心功能）
    - 检查单只标的是否超配
    - 检查持仓总数是否超标
    """
    def _fetch_positions():
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT symbol, name, quantity, avg_cost, current_price, stop_loss_price FROM positions WHERE user_id = ? AND quantity > 0",
                (user_id,)
            ).fetchall()
            return [dict(r) for r in rows] if rows else []
        finally:
            conn.close()

    positions = await run_in_threadpool(_fetch_positions)
    if not positions:
        return {"total_market_value": 0, "positions": [], "warnings": [], "health_score": 100}

    # 更新最新价格
    symbols = [p["symbol"] for p in positions]
    prices = await get_batch_prices(symbols)
    
    total_market_value = 0.0
    for p in positions:
        sym = p["symbol"]
        if sym in prices:
            p["current_price"] = prices[sym]["price"]
        
        # 使用当前现价还是成本计算市值？通常实盘预警使用当前市值
        p["market_value"] = p["quantity"] * (p["current_price"] or p["avg_cost"])
        total_market_value += p["market_value"]

    warnings = []
    health_score = 100
    
    if total_market_value > 0:
        for p in positions:
            weight = p["market_value"] / total_market_value
            p["weight"] = round(weight, 4)
            
            if weight > max_single_weight:
                warnings.append({
                    "symbol": p["symbol"],
                    "name": p["name"],
                    "type": "SINGLE_OVERWEIGHT",
                    "message": f"单票占据 {round(weight*100, 1)}% 资金，超配风险较高（建议上限 {round(max_single_weight*100)}%）"
                })
                health_score -= int((weight - max_single_weight) * 100) # 扣分
    else:
        for p in positions:
            p["weight"] = 0.0

    # 检查股票总数
    if len(positions) > max_total_stocks:
        warnings.append({
            "type": "TOO_MANY_STOCKS",
            "message": f"持仓分散在 {len(positions)} 只股票，精力极度分散（建议上限 {max_total_stocks} 只）"
        })
        health_score -= (len(positions) - max_total_stocks) * 5

    # 排序：按权重倒序
    positions.sort(key=lambda x: x["weight"], reverse=True)
    
    return {
        "total_market_value": round(total_market_value, 2),
        "positions": positions,
        "warnings": warnings,
        "health_score": max(health_score, 0)
    }
