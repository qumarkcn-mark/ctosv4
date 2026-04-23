"""CT-OS V4.0 — 调仓罗盘 API

提供一个接口：将所有持仓 + watchlist 候选股，
通过同一套缠论评分引擎打分，排序，并生成"砍/加/换"建议。

GET /api/rotation/compass?user_id=1
"""

import asyncio
import logging
from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from server.db.database import get_connection
from server.services.chan_service import analyze_matrix_state
from server.services.rotation_scorer import score_symbol

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════════════════════
# 工具：分析单只 + 打分
# ═══════════════════════════════════════════════════════════════

async def _analyze_one(row: dict, is_holding: bool) -> dict:
    """拉取 analyze_matrix_state → 打分 → 合并持仓基础字段"""
    symbol = row["symbol"]
    base = {
        "symbol": symbol,
        "name": row.get("name"),
        "quantity": row.get("quantity"),
        "avg_cost": row.get("avg_cost"),
        "current_price": row.get("current_price"),
        "pnl_pct": row.get("pnl_pct"),
        "category": "持仓" if is_holding else "候选",
    }
    try:
        matrix = await analyze_matrix_state(symbol)
        score_pkg = score_symbol(matrix, is_holding=is_holding)
        return {**base, **score_pkg, "error": None}
    except Exception as e:
        logger.warning("rotation score failed for %s: %s", symbol, e)
        return {
            **base,
            "sort_score": 0,
            "state_emoji": "⚪",
            "state_label": "接口异常",
            "lifecycle_node": "",
            "distance_pct": None,
            "stop_loss": None,
            "price": None,
            "main_action": "",
            "error": str(e)[:120],
        }


# ═══════════════════════════════════════════════════════════════
# 主接口
# ═══════════════════════════════════════════════════════════════

@router.get("/compass")
async def rotation_compass(user_id: int = 1):
    """调仓罗盘主接口 — 返回 holdings / candidates / suggestions。"""
    positions, watchlist = await run_in_threadpool(_fetch_rows, user_id)

    # 并行打分（chan_service 内部已有数据湖缓存，批量不会很慢）
    tasks = [
        *[_analyze_one(p, is_holding=True) for p in positions],
        *[_analyze_one(w, is_holding=False) for w in watchlist],
    ]
    rows = await asyncio.gather(*tasks)

    holdings = sorted(
        [r for r in rows if r["category"] == "持仓"],
        key=lambda r: r.get("sort_score", 0),
        reverse=True,
    )
    candidates = sorted(
        [r for r in rows if r["category"] == "候选"],
        key=lambda r: r.get("sort_score", 0),
        reverse=True,
    )

    suggestions = _build_suggestions(holdings, candidates)

    return {
        "status": "success",
        "data": {
            "holdings": holdings,
            "candidates": candidates,
            "suggestions": suggestions,
            "summary": _build_summary(holdings, candidates, suggestions),
        },
    }


# ═══════════════════════════════════════════════════════════════
# 辅助：数据库查询
# ═══════════════════════════════════════════════════════════════

def _fetch_rows(user_id: int):
    conn = get_connection()
    try:
        pos_rows = conn.execute(
            """
            SELECT symbol, name, quantity, avg_cost, current_price,
                   CASE WHEN current_price IS NOT NULL AND avg_cost > 0
                        THEN round((current_price - avg_cost) / avg_cost * 100, 2)
                        ELSE 0 END as pnl_pct
              FROM positions
             WHERE user_id = ? AND quantity > 0
             ORDER BY (quantity * avg_cost) DESC
            """,
            (user_id,),
        ).fetchall()

        # 候选 = watchlist 里未被持仓的股票
        watch_rows = conn.execute(
            """
            SELECT w.symbol, w.name
              FROM watchlist w
             WHERE w.user_id = ?
               AND w.symbol NOT IN (
                   SELECT symbol FROM positions
                    WHERE user_id = ? AND quantity > 0
               )
             ORDER BY w.added_at DESC
            """,
            (user_id, user_id),
        ).fetchall()

        return [dict(r) for r in pos_rows], [dict(r) for r in watch_rows]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# 辅助：调仓建议生成
# ═══════════════════════════════════════════════════════════════

def _build_suggestions(holdings: list, candidates: list) -> dict:
    """基于 sort_score 和持仓现状，给出 "砍/加/换" 建议。

    阈值不变，但 reason 改为用 lifecycle_node 代替 action 文字。
    """
    out = {"cut": [], "add": [], "rotate": []}

    for h in holdings:
        score = h.get("sort_score", 0)
        mv = (h.get("current_price") or h.get("avg_cost") or 0) * (h.get("quantity") or 0)
        node = h.get("lifecycle_node") or h.get("state_label", "")
        if score < 30:
            out["cut"].append({
                "symbol": h["symbol"], "name": h.get("name"),
                "sort_score": score, "action": "结构破坏，止损出场",
                "reason": f"当前节点：{node}，结构评分偏低",
                "freed": round(mv, 2),
            })
        elif score < 45:
            out["cut"].append({
                "symbol": h["symbol"], "name": h.get("name"),
                "sort_score": score, "action": "减仓 50%",
                "reason": f"当前节点：{node}，结构偏弱建议减半",
                "freed": round(mv * 0.5, 2),
            })

    for h in holdings:
        if h.get("sort_score", 0) >= 75:
            node = h.get("lifecycle_node") or h.get("state_label", "")
            out["add"].append({
                "symbol": h["symbol"], "name": h.get("name"),
                "sort_score": h["sort_score"], "action": "结构强势，可回踩加仓",
                "reason": f"当前节点：{node}",
            })

    for c in candidates:
        if c.get("sort_score", 0) >= 70:
            node = c.get("lifecycle_node") or c.get("state_label", "")
            out["rotate"].append({
                "symbol": c["symbol"], "name": c.get("name"),
                "sort_score": c["sort_score"], "action": "择机开仓",
                "reason": f"当前节点：{node}",
            })

    return out


def _build_summary(holdings: list, candidates: list, suggestions: dict) -> dict:
    """顶部一行战报摘要"""
    total_cut_freed = sum(s.get("freed", 0) or 0 for s in suggestions["cut"])
    return {
        "holdings_count":   len(holdings),
        "candidates_count": len(candidates),
        "cut_count":        len(suggestions["cut"]),
        "add_count":        len(suggestions["add"]),
        "rotate_count":     len(suggestions["rotate"]),
        "freed_cash_estimate": round(total_cut_freed, 2),
        # sort_score 仅内部用，摘要条不再显示分数数字
        "top_holding_score":    holdings[0]["sort_score"] if holdings else 0,
        "worst_holding_score":  holdings[-1]["sort_score"] if holdings else 0,
    }
