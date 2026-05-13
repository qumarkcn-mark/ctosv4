"""投资行为体检 API"""

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from server.api.auth import get_current_user_id
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


def _fetch_plan_metrics(user_id: int) -> dict:
    """获取计划内/计划外纪律指标。老库缺列时返回中性空值。"""
    conn = get_connection()
    try:
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(trades)").fetchall()
        }
        if "plan_relationship" not in columns:
            return {
                "plan_adherence_rate": None,
                "planned_trades": 0,
                "unplanned_trades": 0,
                "emotional_trades": 0,
                "known_plan_trades": 0,
                "alert_follow_rate": None,
            }

        rows = conn.execute(
            """
            SELECT plan_relationship, COUNT(*) AS count
              FROM trades
             WHERE user_id = ?
             GROUP BY plan_relationship
            """,
            (user_id,),
        ).fetchall()
        counts = {row["plan_relationship"] or "UNKNOWN": row["count"] for row in rows}
        planned = counts.get("PLANNED", 0) + counts.get("AFTER_ALERT", 0)
        unplanned = counts.get("UNPLANNED", 0) + counts.get("EMOTIONAL", 0)
        known = planned + unplanned + counts.get("IGNORED_ALERT", 0)

        event_rows = conn.execute(
            """
            SELECT
                SUM(CASE WHEN evidence_json LIKE '%PLAYBOOK_EXECUTED%' OR evidence_json LIKE '%ACKNOWLEDGED%' THEN 1 ELSE 0 END) AS followed,
                COUNT(*) AS total
              FROM coach_events
             WHERE user_id = ? AND event_type = 'USER_MARKED_ACTION'
               AND evidence_json LIKE '%PLAYBOOK_%'
            """,
            (user_id,),
        ).fetchone()
        followed = event_rows["followed"] or 0 if event_rows else 0
        total = event_rows["total"] or 0 if event_rows else 0

        return {
            "plan_adherence_rate": round(planned / known * 100, 1) if known else None,
            "planned_trades": planned,
            "unplanned_trades": unplanned,
            "emotional_trades": counts.get("EMOTIONAL", 0),
            "ignored_alert_trades": counts.get("IGNORED_ALERT", 0),
            "known_plan_trades": known,
            "alert_follow_rate": round(followed / total * 100, 1) if total else None,
        }
    finally:
        conn.close()


@router.get("/report")
async def get_behavior_report(
    period: str = Query("ALL_TIME"),
    current_user_id: int = Depends(get_current_user_id),
):
    """生成投资行为体检报告"""
    user_id = current_user_id
    trades, alert_count = await run_in_threadpool(_fetch_trades_and_alerts, user_id)

    report = analyze(trades, alert_count)

    # 缓存本次结果
    await run_in_threadpool(_save_stats, user_id, report, period)

    # 获取上次结果用于纵向对比
    previous = await run_in_threadpool(_fetch_previous, user_id)

    diagnosis = generate_diagnosis(report, previous)
    plan_metrics = await run_in_threadpool(_fetch_plan_metrics, user_id)

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
                **plan_metrics,
            },
            "diagnosis": diagnosis,
        }
    }


@router.get("/history")
def get_behavior_history(
    limit: int = Query(10, ge=1, le=50),
    current_user_id: int = Depends(get_current_user_id),
):
    """查询历史体检记录，用于纪律趋势追踪"""
    user_id = current_user_id
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
