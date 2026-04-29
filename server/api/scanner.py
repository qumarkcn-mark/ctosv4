"""选股扫描器 API — 今日机会列表与手动补跑。"""

import json
import threading
from datetime import date
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query

from server.config import SCANNER_ADMIN_TOKEN
from server.db.database import get_connection
from server.engines.coach.event_log import log_user_action
from server.engines.decision.strategy_definitions import build_strategy_contract, get_strategy_definition

router = APIRouter()

_SCAN_JOB_LOCK = threading.Lock()
_SCAN_JOB = {
    "running": False,
    "last_status": "idle",
    "last_scan_date": None,
    "last_candidate_count": 0,
    "last_error": None,
}


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def require_scanner_admin(
    x_scanner_admin_token: Optional[str] = Header(default=None),
) -> None:
    """生产可启用的 scanner mutation 保护；未配置 token 时保持本地开发兼容。"""
    if SCANNER_ADMIN_TOKEN and x_scanner_admin_token != SCANNER_ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="scanner admin token required")


def _json_or_empty(value: Optional[str], fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _strategy_status_from_scan_status(scan_status: str) -> str:
    if scan_status == "ready":
        return "TRIGGERED"
    if scan_status == "failed":
        return "BLOCKED"
    return "WATCHING"


def _attach_strategy_contract(item: dict) -> dict:
    raw_strategy = item.get("strategy")
    item["strategy_code"] = raw_strategy
    if not raw_strategy:
        return item

    try:
        definition = get_strategy_definition(raw_strategy)
        contract = build_strategy_contract(
            definition.strategy_id,
            _strategy_status_from_scan_status(item.get("status")),
        )
    except ValueError:
        item["strategy_id"] = raw_strategy
        return item

    item["strategy_id"] = definition.strategy_id
    item["strategy_version"] = definition.strategy_version
    item["strategy_name"] = definition.name
    item["strategy_type"] = definition.strategy_type
    item["strategy_contract"] = contract
    return item


def _row_to_candidate(row) -> dict:
    item = dict(row)
    item["fundamental"] = _json_or_empty(item.get("fundamental"), {})
    item["llm_pros"] = _json_or_empty(item.get("llm_pros"), [])
    item["llm_cons"] = _json_or_empty(item.get("llm_cons"), [])
    item["llm_red_flags"] = _json_or_empty(item.get("llm_red_flags"), [])
    item = _attach_strategy_contract(item)
    return item


def _scan_job_snapshot() -> dict:
    with _SCAN_JOB_LOCK:
        return dict(_SCAN_JOB)


def _update_scan_job(**values):
    with _SCAN_JOB_LOCK:
        _SCAN_JOB.update(values)


def _run_scan_job(force: bool):
    """后台执行完整扫描，避免手动触发接口阻塞前端请求。"""
    from server.workers.scanner import get_today, run_scan as run_worker_scan
    from server.workers.scanner import trigger_fundamental_analysis

    scan_date = get_today()
    _update_scan_job(
        running=True,
        last_status="running",
        last_scan_date=scan_date,
        last_candidate_count=0,
        last_error=None,
    )
    try:
        count = run_worker_scan(force)
        if count > 0:
            import asyncio

            asyncio.run(trigger_fundamental_analysis(scan_date))
        _update_scan_job(
            running=False,
            last_status="completed",
            last_scan_date=scan_date,
            last_candidate_count=count,
            last_error=None,
        )
    except Exception as exc:
        _update_scan_job(
            running=False,
            last_status="failed",
            last_scan_date=scan_date,
            last_error=str(exc),
        )


@router.get("/results")
def list_scan_results(
    scan_date: Optional[str] = Query(None),
    status: str = Query("ready", pattern="^(pending|analyzing|ready|failed|all)$"),
    limit: int = Query(50, ge=1, le=200),
):
    """返回扫描候选股，默认只展示 ready 结果。"""
    scan_date = scan_date or _today()
    conn = get_connection()
    try:
        params: list = [scan_date]
        where = "scan_date = ?"
        if status != "all":
            where += " AND status = ?"
            params.append(status)

        rows = conn.execute(
            f"""
            SELECT id, scan_date, symbol, strategy, status, score,
                   close, stop_loss, target, rr_ratio, atr_pct,
                   volume_ratio, chan_desc, fundamental,
                   llm_verdict, llm_summary, llm_pros, llm_cons,
                   llm_red_flags, fundamental_at, retry_count, created_at
              FROM scan_results
             WHERE {where}
             ORDER BY score DESC, created_at DESC
             LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        return {
            "scan_date": scan_date,
            "status": status,
            "count": len(rows),
            "results": [_row_to_candidate(r) for r in rows],
        }
    finally:
        conn.close()


@router.get("/status")
def scan_status(scan_date: Optional[str] = Query(None)):
    """返回某日扫描结果状态统计。"""
    scan_date = scan_date or _today()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT status, COUNT(*) AS count
              FROM scan_results
             WHERE scan_date = ?
             GROUP BY status
            """,
            (scan_date,),
        ).fetchall()
        counts = {"pending": 0, "analyzing": 0, "ready": 0, "failed": 0}
        for row in rows:
            counts[row["status"]] = row["count"]
        return {
            "scan_date": scan_date,
            "counts": counts,
            "total": sum(counts.values()),
            "job": _scan_job_snapshot(),
        }
    finally:
        conn.close()


@router.post("/run")
def run_scan(
    background_tasks: BackgroundTasks,
    force: bool = False,
    _: None = Depends(require_scanner_admin),
):
    """手动触发后台扫描，接口立即返回，页面通过 /status 轮询进度。"""
    from server.workers.scanner import get_today

    scan_date = get_today()
    snapshot = _scan_job_snapshot()
    if snapshot["running"]:
        return {
            "status": "running",
            "scan_date": snapshot["last_scan_date"] or scan_date,
            "candidate_count": snapshot["last_candidate_count"],
        }

    _update_scan_job(
        running=True,
        last_status="queued",
        last_scan_date=scan_date,
        last_candidate_count=0,
        last_error=None,
    )
    background_tasks.add_task(_run_scan_job, force)
    return {"status": "queued", "scan_date": scan_date, "candidate_count": 0}


@router.delete("/results/{result_id}")
def delete_scan_result(result_id: int, _: None = Depends(require_scanner_admin)):
    """删除单条候选结果。用户删除或加入观察库后调用。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, symbol, strategy FROM scan_results WHERE id = ?",
            (result_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "扫描结果不存在")
        cur = conn.execute("DELETE FROM scan_results WHERE id = ?", (result_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "扫描结果不存在")
        log_user_action(
            conn,
            user_id=1,
            symbol=row["symbol"],
            action_type="SCAN_RESULT_DELETED",
            source="scanner_api",
            dedupe_key=f"scan_delete:1:{result_id}",
            evidence={"result_id": result_id, "strategy": row["strategy"]},
            message={"title": "删除扫描候选", "body": f"{row['symbol']} 已从今日机会中删除。"},
        )
        conn.commit()
        return {"status": "ok", "deleted_id": result_id}
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@router.post("/results/{result_id}/observe")
def observe_scan_result(
    result_id: int,
    group_name: str = Query("观察", min_length=1, max_length=20),
    user_id: int = 1,
    _: None = Depends(require_scanner_admin),
):
    """加入观察库并移除候选结果，保证前端单次操作是原子的。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id, symbol, strategy FROM scan_results WHERE id = ?",
            (result_id,),
        ).fetchone()
        if not row:
            raise HTTPException(404, "扫描结果不存在")

        symbol = row["symbol"]
        conn.execute("BEGIN")

        group = conn.execute(
            "SELECT id FROM watchlist_groups WHERE user_id = ? AND name = ?",
            (user_id, group_name),
        ).fetchone()
        if group:
            group_id = group["id"]
        else:
            order_row = conn.execute(
                "SELECT MAX(sort_order) AS m FROM watchlist_groups WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            next_order = (order_row["m"] or 0) + 1
            cur = conn.execute(
                "INSERT INTO watchlist_groups (user_id, name, sort_order) VALUES (?, ?, ?)",
                (user_id, group_name, next_order),
            )
            group_id = cur.lastrowid

        # 自选股全局唯一：加入观察前先从该用户所有分组移除。
        conn.execute(
            """
            DELETE FROM watchlist_items
             WHERE symbol = ?
               AND group_id IN (SELECT id FROM watchlist_groups WHERE user_id = ?)
            """,
            (symbol, user_id),
        )

        item_order_row = conn.execute(
            "SELECT MAX(sort_order) AS m FROM watchlist_items WHERE group_id = ?",
            (group_id,),
        ).fetchone()
        next_item_order = (item_order_row["m"] or 0) + 1
        conn.execute(
            """
            INSERT INTO watchlist_items (group_id, symbol, name, sort_order)
            VALUES (?, ?, ?, ?)
            """,
            (group_id, symbol, symbol, next_item_order),
        )
        conn.execute("DELETE FROM scan_results WHERE id = ?", (result_id,))
        log_user_action(
            conn,
            user_id=user_id,
            symbol=symbol,
            action_type="SCAN_RESULT_OBSERVED",
            source="scanner_api",
            dedupe_key=f"scan_observe:{user_id}:{result_id}",
            evidence={
                "result_id": result_id,
                "strategy": row["strategy"],
                "group_name": group_name,
                "group_id": group_id,
            },
            message={"title": "加入观察库", "body": f"{symbol} 已加入 {group_name}。"},
        )
        conn.commit()
        return {
            "status": "ok",
            "symbol": symbol,
            "group_name": group_name,
            "deleted_id": result_id,
        }
    except HTTPException:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
