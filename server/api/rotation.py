"""CT-OS V4.0 — 调仓罗盘 API

提供一个接口：将所有持仓 + watchlist 候选股，
通过同一套缠论评分引擎排序，并生成甲乙丙结构预案。

GET /api/rotation/compass?user_id=1
"""

import asyncio
import logging
from typing import Optional
from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from server.db.database import get_connection
from server.engines.decision.strategy_definitions import build_strategy_contract
from server.services.chan_service import analyze_matrix_state
from server.services.rotation_planner import RISK_DISCLAIMER, build_rotation_item
from server.services.rotation_scorer import score_symbol

logger = logging.getLogger(__name__)
router = APIRouter()

ROTATION_ANALYSIS_TIMEOUT_SECONDS = 8
ROTATION_TOTAL_TIMEOUT_SECONDS = 5
ROTATION_ANALYSIS_CONCURRENCY = 3


# ═══════════════════════════════════════════════════════════════
# 工具：分析单只 + 打分
# ═══════════════════════════════════════════════════════════════

async def _analyze_one(
    row: dict,
    is_holding: bool,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> dict:
    """拉取 analyze_matrix_state → 排序评分 → 甲乙丙预案"""
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
        if semaphore is None:
            matrix = await asyncio.wait_for(
                analyze_matrix_state(symbol),
                timeout=ROTATION_ANALYSIS_TIMEOUT_SECONDS,
            )
        else:
            async with semaphore:
                matrix = await asyncio.wait_for(
                    analyze_matrix_state(symbol),
                    timeout=ROTATION_ANALYSIS_TIMEOUT_SECONDS,
                )
        score_pkg = score_symbol(matrix, is_holding=is_holding)
        return build_rotation_item({**base, **score_pkg, "error": None}, is_holding)
    except asyncio.TimeoutError:
        logger.warning("rotation score timed out for %s", symbol)
        return _fallback_rotation_item(base, is_holding, "analysis timeout")
    except Exception as e:
        logger.warning("rotation score failed for %s: %s", symbol, e)
        return _fallback_rotation_item(base, is_holding, str(e)[:120])


def _fallback_rotation_item(base: dict, is_holding: bool, error: str) -> dict:
    fallback = {
        **base,
        "sort_score": 0,
        "state_label": "结构待确认",
        "lifecycle_node": "分析超时" if error == "analysis timeout" else "接口异常",
        "distance_pct": None,
        "stop_loss": None,
        "price": None,
        "main_action": "当前结构分析未完成，请稍后回到 Radar 单票复核。",
        "error": error,
    }
    return build_rotation_item(fallback, is_holding)


# ═══════════════════════════════════════════════════════════════
# 主接口
# ═══════════════════════════════════════════════════════════════

@router.get("/compass")
async def rotation_compass(user_id: int = 1):
    """调仓罗盘主接口 — 返回 holdings / candidates 的横向预案。"""
    positions, watchlist = await run_in_threadpool(_fetch_rows, user_id)

    # 并行打分（chan_service 内部已有数据湖缓存，批量不会很慢）
    semaphore = asyncio.Semaphore(ROTATION_ANALYSIS_CONCURRENCY)
    task_specs = [
        *[(p, True) for p in positions],
        *[(w, False) for w in watchlist],
    ]
    task_by_handle = {
        asyncio.create_task(_analyze_one(row, is_holding=is_holding, semaphore=semaphore)): (row, is_holding)
        for row, is_holding in task_specs
    }
    done, pending = await asyncio.wait(
        task_by_handle.keys(),
        timeout=ROTATION_TOTAL_TIMEOUT_SECONDS,
    )
    for task in pending:
        task.cancel()

    rows = []
    for task in done:
        try:
            rows.append(task.result())
        except Exception as exc:
            row, is_holding = task_by_handle[task]
            rows.append(
                _fallback_rotation_item(
                    {
                        "symbol": row["symbol"],
                        "name": row.get("name"),
                        "quantity": row.get("quantity"),
                        "avg_cost": row.get("avg_cost"),
                        "current_price": row.get("current_price"),
                        "pnl_pct": row.get("pnl_pct"),
                        "category": "持仓" if is_holding else "候选",
                    },
                    is_holding,
                    str(exc)[:120],
                )
            )
    for task in pending:
        row, is_holding = task_by_handle[task]
        rows.append(
            _fallback_rotation_item(
                {
                    "symbol": row["symbol"],
                    "name": row.get("name"),
                    "quantity": row.get("quantity"),
                    "avg_cost": row.get("avg_cost"),
                    "current_price": row.get("current_price"),
                    "pnl_pct": row.get("pnl_pct"),
                    "category": "持仓" if is_holding else "候选",
                },
                is_holding,
                "analysis timeout",
            )
        )

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
            "strategy": _build_rotation_strategy(suggestions),
            "holdings": holdings,
            "candidates": candidates,
            "comparison": _build_comparison(holdings, candidates),
            # 兼容旧前端字段；新前端不再把它作为主视觉。
            "suggestions": suggestions,
            "summary": _build_summary(holdings, candidates, suggestions),
            "risk_disclaimer": RISK_DISCLAIMER,
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

        # 候选 = watchlist 分组里未被持仓的股票
        watch_rows = conn.execute(
            """
            SELECT wi.symbol, wi.name
              FROM watchlist_items wi
              JOIN watchlist_groups wg ON wg.id = wi.group_id
             WHERE wg.user_id = ?
               AND wi.symbol NOT IN (
                   SELECT symbol FROM positions
                    WHERE user_id = ? AND quantity > 0
               )
             ORDER BY wg.sort_order, wi.sort_order, wi.id
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


def _build_rotation_strategy(suggestions: dict) -> dict:
    """调仓罗盘是横向比较策略，只输出 plans，不执行交易。"""
    has_suggestion = any(suggestions.get(key) for key in ("cut", "add", "rotate"))
    return build_strategy_contract(
        "rotation_comparison",
        "TRIGGERED" if has_suggestion else "WATCHING",
        [
            {
                "condition_id": "rotation_candidates_available",
                "status": "PASS" if has_suggestion else "WATCHING",
            }
        ],
    )


def _build_comparison(holdings: list, candidates: list) -> dict:
    strongest_holding = holdings[0] if holdings else None
    weakest_holding = holdings[-1] if holdings else None
    strongest_candidate = candidates[0] if candidates else None
    return {
        "holdings_count": len(holdings),
        "candidates_count": len(candidates),
        "strongest_holding": _compact_symbol(strongest_holding),
        "weakest_holding": _compact_symbol(weakest_holding),
        "strongest_candidate": _compact_symbol(strongest_candidate),
        "focus": "比较结构清晰度、风险防线和触发条件；分数只用于排序。",
    }


def _compact_symbol(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    return {
        "symbol": row.get("symbol"),
        "name": row.get("name"),
        "state_label": row.get("state_label"),
        "lifecycle_node": row.get("lifecycle_node"),
    }


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
