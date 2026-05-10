"""
scanner.py — 选股扫描器 Worker

调度顺序：
  1. screener_filter.batch_screen()   — 批量初筛（1次SQL）
  2. 逐只加载 kline_lake 数据
  3. chan_scanner.scan_symbol()       — 战法一/二检测
  4. 写入 scan_results（status=pending）
  5. 异步触发 fundamental_service     — LLM 基本面分析

LaunchAgent 触发：python -m server.workers.scanner
手动触发：       python -m server.workers.scanner --force
"""

import argparse
import asyncio
import json
import logging
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

# ── 路径修正（直接运行时需要）──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.db.database import get_connection, init_db
from server.db.kline_lake import get_lake_connection
from server.engines.coach.event_log import log_alert_candidate
from server.engines.decision.push_rules import (
    build_alert_message,
    build_alert_strategy_contract,
    evaluate_scanner_candidate_alert,
)
from server.engines.structure.snapshot_query import build_formal_structure_key
from server.engines.structure.structure_jobs import enqueue_structure_job
from server.services.screener_filter import batch_screen, load_all_symbols
from server.services.chan_scanner import scan_symbol

logger = logging.getLogger(__name__)

# ── 参数 ────────────────────────────────────────────────────────────────────
KLINE_LOOKBACK_DAYS   = 120    # 喂给缠论引擎的历史K线数（日历天 × 2）
KLINE_TRADING_DAYS    = 120    # 实际交易日数
ADJUSTFLAG            = "3"    # TDX不复权数据
CONCURRENT_LLM        = 5      # 基本面分析并发数
SCAN_STALE_DAYS       = 3      # 清理N天前的历史扫描结果
_LAST_EFFECTIVE_SCAN_DATE: Optional[str] = None


# ── 数据库操作 ────────────────────────────────────────────────────────────────

def get_today() -> str:
    return date.today().strftime("%Y-%m-%d")


def get_last_effective_scan_date(default: Optional[str] = None) -> str:
    """返回最近一次 run_scan 实际写入 scan_results 的日期。"""
    return _LAST_EFFECTIVE_SCAN_DATE or default or get_today()


def cleanup_stale_results(conn):
    """清理N天前的历史扫描结果，避免积压"""
    cutoff = (date.today() - timedelta(days=SCAN_STALE_DAYS)).strftime("%Y-%m-%d")
    r = conn.execute("DELETE FROM scan_results WHERE scan_date < ?", (cutoff,))
    conn.commit()
    if r.rowcount:
        logger.info("清理 %d 天前历史结果: 删除 %d 条", SCAN_STALE_DAYS, r.rowcount)


def check_lake_freshness(lake_conn) -> Optional[str]:
    """检查 TDX 日线数据日期，返回最新 bar 日期。"""
    row = lake_conn.execute(
        "SELECT MAX(date) FROM klines WHERE freq='day' AND adjustflag=?",
        (ADJUSTFLAG,)
    ).fetchone()
    latest = row[0] if row else None
    today  = get_today()
    if latest != today:
        logger.warning(
            "kline_lake 最新数据为 %s，今日 %s 的数据尚未同步，扫描结果可能滞后",
            latest, today
        )
    return latest


def upsert_scan_result(conn, today: str, result, force: bool = False) -> int:
    """写入一条扫描结果，返回 row id"""
    status_update = "'pending'" if force else "scan_results.status"
    cur = conn.execute(
        f"""
        INSERT INTO scan_results
            (scan_date, symbol, strategy, status, score,
             close, stop_loss, target, rr_ratio, atr_pct,
             volume_ratio, chan_desc)
        VALUES (?, ?, ?, 'pending', ?,
                ?, ?, ?, ?, ?,
                ?, ?)
        ON CONFLICT(scan_date, symbol, strategy) DO UPDATE SET
            status       = {status_update},
            score        = excluded.score,
            close        = excluded.close,
            stop_loss    = excluded.stop_loss,
            target       = excluded.target,
            rr_ratio     = excluded.rr_ratio,
            atr_pct      = excluded.atr_pct,
            volume_ratio = excluded.volume_ratio,
            chan_desc     = excluded.chan_desc,
            retry_count  = 0,
            created_at   = CURRENT_TIMESTAMP
        """,
        (today, result.symbol, result.strategy, result.score,
         result.close, result.stop_loss, result.target, result.rr_ratio, result.atr_pct,
         result.volume_ratio, result.chan_desc)
    )
    conn.commit()
    return cur.lastrowid


def get_pending_ids(conn, today: str) -> list[tuple[int, str, str]]:
    """返回今日待分析的 (id, symbol, strategy) 列表"""
    rows = conn.execute(
        "SELECT id, symbol, strategy FROM scan_results WHERE scan_date=? AND status='pending'",
        (today,)
    ).fetchall()
    return [(r["id"], r["symbol"], r["strategy"]) for r in rows]


def notify_scanner_top_candidates(conn, today: str, user_id: int = 1, min_score: float = 80.0) -> int:
    """把 ready 高分候选写入提醒表和 Coach Event Log。

    scanner 本身不负责下单建议，只把重点候选推给交易教练闭环；用户仍需打开雷达复核。
    """
    rows = conn.execute(
        """
        SELECT id, symbol, strategy, status, score, close, stop_loss, target, rr_ratio, chan_desc
          FROM scan_results
         WHERE scan_date = ? AND status = 'ready'
         ORDER BY score DESC, created_at DESC
        """,
        (today,),
    ).fetchall()

    created = 0
    alert_columns = {row["name"] for row in conn.execute("PRAGMA table_info(alerts)").fetchall()}
    for row in rows:
        item = dict(row)
        candidate = evaluate_scanner_candidate_alert(item, min_score=min_score)
        if not candidate:
            continue

        exists = conn.execute(
            """
            SELECT id FROM alerts
             WHERE user_id = ? AND symbol = ? AND alert_type = ?
               AND date(created_at) = date('now')
            """,
            (user_id, item["symbol"], candidate.alert_type),
        ).fetchone()
        if exists:
            continue

        message = build_alert_message(
            candidate.alert_type,
            name=item["symbol"],
            current_price=candidate.trigger_price,
            score=candidate.extra.get("score", 0),
            signal_code=candidate.signal_code,
            signal_label=candidate.signal_context.get("label_plain") or "",
            signal_action=candidate.signal_context.get("action") or "",
        )
        strategy_contract = build_alert_strategy_contract(
            candidate.alert_type,
            candidate.extra.get("strategy", ""),
        )

        if strategy_contract and {
            "strategy_id",
            "strategy_version",
            "strategy_contract",
        }.issubset(alert_columns):
            cur = conn.execute(
                """
                INSERT INTO alerts (
                    user_id, symbol, alert_type, trigger_price, is_triggered,
                    message, strategy_id, strategy_version, strategy_contract
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    item["symbol"],
                    candidate.alert_type,
                    candidate.trigger_price,
                    1,
                    message,
                    strategy_contract["strategy_id"],
                    strategy_contract["strategy_version"],
                    json.dumps(strategy_contract, ensure_ascii=False),
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO alerts (user_id, symbol, alert_type, trigger_price, is_triggered, message)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, item["symbol"], candidate.alert_type, candidate.trigger_price, 1, message),
            )

        log_alert_candidate(
            conn,
            alert_id=cur.lastrowid,
            user_id=user_id,
            symbol=item["symbol"],
            alert_type=candidate.alert_type,
            message_text=message,
            source="scanner_worker",
            strategy_contract=strategy_contract,
            evidence={
                "scan_result_id": item["id"],
                "score": item.get("score"),
                "strategy": item.get("strategy"),
                "stop_loss": item.get("stop_loss"),
                "target": item.get("target"),
                "rr_ratio": item.get("rr_ratio"),
                "chan_desc": item.get("chan_desc"),
                "dedupe_node": candidate.dedupe_node,
                "signal_code": candidate.signal_code,
                "signal_context": candidate.signal_context,
            },
        )
        created += 1

    if created:
        logger.info("扫描器重点候选提醒已创建: %d 条", created)
    return created


def notify_today_ready_candidates(today: str, user_id: int = 1) -> int:
    conn = get_connection()
    try:
        count = notify_scanner_top_candidates(conn, today, user_id=user_id)
        conn.commit()
        return count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def enqueue_structure_jobs_for_scan_candidates(
    symbols,
    *,
    freqs: tuple[str, ...] = ("day", "30", "5"),
    priority: int = 30,
) -> dict:
    """低优先级预热扫描候选的正式结构快照。

    scanner 只负责发现候选，不直接计算结构；这里把候选交给后台队列，避免用户点开
    Radar/Kline 时才集中触发重计算。
    """
    items = []
    for symbol in sorted(set(symbols or [])):
        for freq in freqs:
            try:
                structure_key, _context = build_formal_structure_key(symbol=symbol, freq=freq)
                if structure_key is None:
                    items.append({"symbol": symbol, "freq": freq, "status": "skipped", "reason": "NO_DATA"})
                    continue
                job = enqueue_structure_job(
                    structure_key,
                    priority=priority,
                    reason="scanner_candidate",
                    retry_terminal=True,
                )
                items.append(
                    {
                        "symbol": structure_key.symbol,
                        "freq": structure_key.freq,
                        "status": job.get("status"),
                        "job_id": job.get("job_id"),
                        "enqueued": job.get("enqueued"),
                        "bumped": job.get("bumped"),
                    }
                )
            except Exception as exc:
                logger.warning("扫描候选结构任务入队失败 %s/%s: %s", symbol, freq, exc)
                items.append({"symbol": symbol, "freq": freq, "status": "error", "error": str(exc)})
    return {"count": len(items), "items": items}


# ── 批量加载 kline 数据（1次查询）──────────────────────────────────────────

def load_klines_batch(lake_conn, symbols: set[str]) -> dict[str, list]:
    """
    批量从 kline_lake 加载指定股票的日线数据。
    返回 {symbol: [rows...]}，行按日期升序。
    """
    if not symbols:
        return {}

    start_date = (
        date.today() - timedelta(days=KLINE_LOOKBACK_DAYS * 2)
    ).strftime("%Y-%m-%d")

    placeholders = ",".join("?" * len(symbols))
    sql = f"""
        SELECT symbol, date, open, high, low, close, volume, amount
        FROM klines
        WHERE freq = 'day'
          AND adjustflag = ?
          AND date >= ?
          AND symbol IN ({placeholders})
        ORDER BY symbol, date
    """
    params = [ADJUSTFLAG, start_date] + list(symbols)
    rows = lake_conn.execute(sql, params).fetchall()

    data: dict[str, list] = defaultdict(list)
    for row in rows:
        data[row["symbol"]].append(row)

    logger.info("批量加载 kline: %d 只股票，%d 条记录", len(data), len(rows))
    return data


# ── 主扫描流程 ────────────────────────────────────────────────────────────────

def run_scan(force: bool = False) -> int:
    """
    执行一次完整扫描。
    Returns: 候选股总数
    """
    global _LAST_EFFECTIVE_SCAN_DATE

    today      = get_today()
    _LAST_EFFECTIVE_SCAN_DATE = today
    ctos_conn  = get_connection()
    lake_conn  = get_lake_connection("tdx")

    try:
        logger.info("=" * 55)
        logger.info("选股扫描器启动  日期: %s", today)
        logger.info("=" * 55)

        # 1. 清理过期数据
        cleanup_stale_results(ctos_conn)

        # 2. 检查 kline_lake 数据新鲜度。非 force 模式不使用旧 K 线生成当日提醒。
        latest_bar_date = check_lake_freshness(lake_conn)
        if latest_bar_date != today and not force:
            logger.warning("TDX 数据未同步到今日，跳过扫描。force=True 可手动按最新交易日重跑。")
            return 0
        if latest_bar_date and latest_bar_date != today:
            today = latest_bar_date
            _LAST_EFFECTIVE_SCAN_DATE = today

        # 3. 初筛（1次批量 SQL）
        logger.info("Step 1/3: 批量初筛...")
        all_symbols = load_all_symbols(ADJUSTFLAG)
        passed      = batch_screen(all_symbols, ADJUSTFLAG)
        logger.info("初筛通过: %d / %d 只", len(passed), len(all_symbols))

        if not passed:
            logger.warning("初筛结果为空，退出")
            return 0

        # 4. 批量加载 kline 数据
        logger.info("Step 2/3: 批量加载 kline 数据...")
        klines_map = load_klines_batch(lake_conn, passed)

        # 5. 逐只扫描（战法一/二）
        logger.info("Step 3/3: 缠论扫描 %d 只股票...", len(klines_map))
        total_candidates = 0
        war1_count = 0
        war2_count = 0
        candidate_symbols: set[str] = set()

        for i, (symbol, rows) in enumerate(klines_map.items(), 1):
            if not rows:
                continue
            try:
                results = scan_symbol(symbol, rows)
            except Exception as e:
                logger.debug("扫描 %s 失败: %s", symbol, e)
                continue

            for result in results:
                upsert_scan_result(ctos_conn, today, result, force=force)
                candidate_symbols.add(result.symbol)
                total_candidates += 1
                if result.strategy == "war1":
                    war1_count += 1
                else:
                    war2_count += 1

            # 每200只打一次进度
            if i % 200 == 0:
                pct = i / len(klines_map) * 100
                logger.info("  进度 [%d/%d] %.0f%%  候选股: %d只",
                            i, len(klines_map), pct, total_candidates)

        logger.info(
            "扫描完成: 战法一 %d 只 / 战法二 %d 只 / 合计 %d 只",
            war1_count, war2_count, total_candidates
        )
        if candidate_symbols:
            structure_jobs = enqueue_structure_jobs_for_scan_candidates(candidate_symbols)
            logger.info("扫描候选结构任务低优先级入队: %d", structure_jobs["count"])

        return total_candidates
    finally:
        ctos_conn.close()
        lake_conn.close()


# ── 基本面分析触发（异步）────────────────────────────────────────────────────

async def trigger_fundamental_analysis(today: str):
    """
    对所有 status='pending' 的候选股发起 LLM 基本面分析。
    fundamental_service.py 实现后接入此处。
    """
    try:
        from server.services.fundamental_service import analyze_batch
        ctos_conn = get_connection()
        try:
            pending = get_pending_ids(ctos_conn, today)
        finally:
            ctos_conn.close()

        if not pending:
            logger.info("没有待分析的候选股")
            return

        logger.info("开始基本面分析: %d 只候选股 (并发=%d)", len(pending), CONCURRENT_LLM)
        await analyze_batch(pending, concurrency=CONCURRENT_LLM)
        logger.info("基本面分析完成")
        notify_today_ready_candidates(today)

    except ImportError:
        # fundamental_service 尚未实现时，跳过并将 pending → ready（仅展示技术面）
        logger.warning("fundamental_service 未实现，候选股直接置为 ready（仅技术面）")
        ctos_conn = get_connection()
        try:
            ctos_conn.execute(
                "UPDATE scan_results SET status='ready' WHERE scan_date=? AND status='pending'",
                (today,)
            )
            notify_scanner_top_candidates(ctos_conn, today)
            ctos_conn.commit()
        except Exception:
            ctos_conn.rollback()
            raise
        finally:
            ctos_conn.close()

    except Exception as e:
        logger.error("基本面分析异常: %s", e, exc_info=True)
        raise


# ── 入口 ─────────────────────────────────────────────────────────────────────

async def main(force: bool = False):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 确保数据库 schema 存在
    init_db()

    # 扫描
    count = run_scan(force=force)
    scan_date = get_last_effective_scan_date()

    if count == 0:
        logger.info("无候选股，跳过基本面分析")
        return

    # 基本面分析
    await trigger_fundamental_analysis(scan_date)

    logger.info("全流程完成，今日候选股: %d 只", count)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CT-OS 选股扫描器")
    parser.add_argument("--force", action="store_true", help="强制重跑（忽略今日已有结果）")
    args = parser.parse_args()

    asyncio.run(main(force=args.force))
