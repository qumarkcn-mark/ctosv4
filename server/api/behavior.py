"""投资行为体检 API"""

from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool
from server.db.database import get_connection
from server.services.behavior_engine import analyze
from server.services.behavior_coach import generate_diagnosis

router = APIRouter()


def _fetch_trades_and_alerts(user_id: int) -> tuple[list, int]:
    """线程池内同步获取交易记录和止损预警计数"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM trades WHERE user_id = ? ORDER BY traded_at ASC",
            (user_id,)
        ).fetchall()
        trades = [dict(r) for r in rows] if rows else []

        alert_row = conn.execute(
            "SELECT COUNT(*) as cnt FROM alerts WHERE user_id = ? AND alert_type IN ('STOP_LOSS_BROKEN', 'STOP_LOSS_WARNING')",
            (user_id,)
        ).fetchone()
        alert_count = alert_row["cnt"] if alert_row else 0

        return trades, alert_count
    finally:
        conn.close()


def _save_stats(user_id: int, report, period: str):
    """将分析结果缓存到 behavior_stats 表"""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO behavior_stats
               (user_id, period, total_trades, win_rate, profit_loss_ratio,
                avg_hold_days, max_drawdown, stop_loss_execution_rate,
                early_exit_count, counter_trend_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, period, report.total_pairs, report.win_rate,
             report.profit_loss_ratio, report.avg_hold_days,
             report.max_drawdown, report.stop_loss_execution_rate,
             report.early_exit_count,
             int(report.counter_trend_rate * report.total_pairs / 100) if report.total_pairs > 0 else 0)
        )
        conn.commit()
    finally:
        conn.close()


def _fetch_previous(user_id: int):
    """获取上一次分析结果用于纵向对比"""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT win_rate, profit_loss_ratio, avg_hold_days, total_trades
               FROM behavior_stats
               WHERE user_id = ?
               ORDER BY calculated_at DESC LIMIT 1 OFFSET 1""",
            (user_id,)
        ).fetchone()
        if row:
            from server.services.behavior_engine import BehaviorReport
            prev = BehaviorReport()
            prev.win_rate = row["win_rate"] or 0
            prev.profit_loss_ratio = row["profit_loss_ratio"] or 0
            prev.avg_hold_days = row["avg_hold_days"] or 0
            prev.total_pairs = row["total_trades"] or 0
            return prev
        return None
    finally:
        conn.close()


@router.get("/report")
async def get_behavior_report(user_id: int = 1, period: str = Query("ALL_TIME")):
    """生成投资行为体检报告"""
    trades, alert_count = await run_in_threadpool(_fetch_trades_and_alerts, user_id)

    report = analyze(trades, alert_count)

    # 缓存本次结果
    await run_in_threadpool(_save_stats, user_id, report, period)

    # 获取上次结果用于纵向对比
    previous = await run_in_threadpool(_fetch_previous, user_id)

    diagnosis = generate_diagnosis(report, previous)

    return {
        "status": "success",
        "data": {
            "discipline_score": report.discipline_score,
            "metrics": {
                "total_pairs": report.total_pairs,
                "win_rate": report.win_rate,
                "profit_loss_ratio": report.profit_loss_ratio,
                "avg_hold_days": report.avg_hold_days,
                "max_drawdown": report.max_drawdown,
                "stop_loss_execution_rate": report.stop_loss_execution_rate,
                "counter_trend_rate": report.counter_trend_rate,
                "impulse_trade_rate": report.impulse_trade_rate,
                "early_exit_count": report.early_exit_count,
            },
            "diagnosis": diagnosis,
        }
    }


@router.get("/history")
def get_behavior_history(user_id: int = 1, limit: int = Query(10, ge=1, le=50)):
    """查询历史体检记录，用于纪律趋势追踪"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT * FROM behavior_stats
               WHERE user_id = ?
               ORDER BY calculated_at DESC
               LIMIT ?""",
            (user_id, limit)
        ).fetchall()
        return {"status": "success", "data": [dict(r) for r in rows]}
    finally:
        conn.close()
