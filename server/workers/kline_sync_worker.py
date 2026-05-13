"""CT-OS V4.0 K线数据自动同步 Worker

启动时自动更新所有自选股（kline_lake 中的全部 symbol + 持仓 symbol）的 K 线数据。
之后每 30 分钟检查一次是否有需要更新的数据。

BaoStock 更新时间表：
  - 日线：每个交易日 17:30 完成入库
  - 分钟线 (5/15/30/60)：每个交易日 20:30 完成入库
  - 周线：每周最后一个交易日更新
"""

import asyncio
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi.concurrency import run_in_threadpool
from server.domain.symbols import normalize_symbol

logger = logging.getLogger(__name__)

# 同步的频率列表。周线是 AI 推演和 CZSC 结构快照的宏观背景，不能只在展示层支持。
ALL_FREQS = ["week", "day", "60", "30", "15", "5"]

# 检查间隔（秒）：30 分钟
CHECK_INTERVAL = 30 * 60

# 启动后延迟（秒）：等待其他服务就绪
STARTUP_DELAY = 5

# V5 结构快照只消费这几个轻量级别。60/15 分钟线保留给 K 线展示和未来扩展，
# 但不会在第一版 AI Native 数据闭环里触发额外结构计算。
FREQ_TO_STRUCTURE_LEVEL = {
    "week": "week",
    "day": "day",
    "30": "30",
    "5": "5",
}

MAX_UNIVERSE_USERS_PER_PASS = 50
MAX_UNIVERSE_SYMBOLS_PER_USER = 80


def _get_all_tracked_symbols() -> list[str]:
    """收集所有需要同步的股票代码（去重）

    来源：
    1. kline_sync_meta 表中所有已有记录的 symbol
    2. positions 表中的持仓 symbol（转换为 baostock 格式）
    3. watchlist_items 表中的自选 symbol（自选即进入长期数据维护队列）
    """
    symbols = set()

    # 来源 1: kline_lake 中已有的 symbol（读操作，复用线程本地连接）
    try:
        from server.db.kline_lake import get_lake_connection
        conn = get_lake_connection()
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM kline_sync_meta"
        ).fetchall()
        for row in rows:
            symbols.add(row["symbol"])
    except Exception as e:
        logger.warning("读取 kline_sync_meta 失败: %s", e)

    # 来源 2: 持仓表中的 symbol
    try:
        from server.db.database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM positions WHERE quantity > 0"
            ).fetchall()
            for row in rows:
                raw = row["symbol"]
                symbols.add(normalize_symbol(raw))
        finally:
            conn.close()
    except Exception as e:
        logger.warning("读取 positions 失败: %s", e)

    # 来源 3: 自选股表中的 symbol
    try:
        from server.db.database import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT wi.symbol
                  FROM watchlist_items wi
                  JOIN watchlist_groups wg ON wg.id = wi.group_id
                """
            ).fetchall()
            for row in rows:
                raw = row["symbol"]
                symbols.add(normalize_symbol(raw))
        finally:
            conn.close()
    except Exception as e:
        logger.warning("读取 watchlist_items 失败: %s", e)

    return sorted(symbols)


def sync_new_watchlist_symbol(symbol: str) -> dict:
    """
    新增自选后的单股数据闭环。

    先快速拉 day/5 让前台尽快可显示，再补齐 BaoStock 全级别历史。
    这是后台任务，不阻塞自选添加接口。
    """
    from server.services.baostock_service import fetch_klines_quick, fetch_klines_sync

    canonical_symbol = normalize_symbol(symbol)
    result = {
        "symbol": canonical_symbol,
        "quick": [],
        "full": [],
        "errors": [],
    }

    for freq in ("day", "5"):
        try:
            written = fetch_klines_quick(canonical_symbol, freq)
            result["quick"].append({"freq": freq, "written": written, "status": "ok"})
        except Exception as exc:
            logger.warning("自选快速拉取失败 %s/%s: %s", canonical_symbol, freq, exc)
            result["quick"].append({"freq": freq, "written": 0, "status": "error"})
            result["errors"].append({"stage": "quick", "freq": freq, "error": str(exc)})

    for freq in ALL_FREQS:
        try:
            written = fetch_klines_sync(canonical_symbol, freq)
            result["full"].append({"freq": freq, "written": written, "status": "ok"})
        except Exception as exc:
            logger.warning("自选全量补齐失败 %s/%s: %s", canonical_symbol, freq, exc)
            result["full"].append({"freq": freq, "written": 0, "status": "error"})
            result["errors"].append({"stage": "full", "freq": freq, "error": str(exc)})

    changed = [
        {"symbol": canonical_symbol, "freq": item["freq"], "written": item["written"]}
        for item in result["full"]
        if item.get("written", 0) > 0
    ]
    result["structure_jobs"] = enqueue_structure_jobs_for_changes(
        changed,
        priority=80,
        reason="watchlist_backfill",
    )

    logger.info(
        "自选数据闭环完成 %s: quick=%d full=%d errors=%d",
        canonical_symbol,
        sum(item["written"] for item in result["quick"]),
        sum(item["written"] for item in result["full"]),
        len(result["errors"]),
    )
    return result


def _is_trading_day(dt: datetime) -> bool:
    """简单判断是否为交易日（排除周末，不处理节假日）"""
    return dt.weekday() < 5  # 0=周一 ~ 4=周五


def _sync_all_symbols(symbols: list[str], freqs: list[str]) -> dict:
    """同步拉取所有 symbol 的 K 线数据（同步版本，在线程池中运行）

    Returns:
        {"total_symbols": int, "updated_symbols": int, "total_written": int, "errors": int}
    """
    from server.services.baostock_service import fetch_klines_sync
    from server.db.kline_lake import get_last_sync_date

    today = datetime.today().strftime("%Y-%m-%d")
    total_written = 0
    updated_symbols = 0
    errors = 0
    changed: list[dict] = []

    for symbol in symbols:
        symbol_updated = False
        for freq in freqs:
            try:
                # 检查是否已经是最新
                last_date = get_last_sync_date(symbol, freq)
                if last_date and last_date[:10] >= today:
                    continue  # 已经是最新，跳过

                written = fetch_klines_sync(symbol, freq)
                total_written += written
                if written > 0:
                    symbol_updated = True
                    changed.append({"symbol": symbol, "freq": freq, "written": written})
            except Exception as e:
                logger.error("同步失败 %s/%s: %s", symbol, freq, e)
                errors += 1

        if symbol_updated:
            updated_symbols += 1

    return {
        "total_symbols": len(symbols),
        "updated_symbols": updated_symbols,
        "total_written": total_written,
        "errors": errors,
        "changed": changed,
    }


def enqueue_structure_jobs_for_changes(
    changes: list[dict],
    *,
    priority: int = 80,
    holding_priority: int = 95,
    reason: str = "kline_sync",
) -> dict:
    """Enqueue CZSC V5 snapshot jobs for formal BaoStock bars that changed.

    This is the bridge from the K-line lake into AI Native V5. It never calls
    old radar/chan fallback code and never performs structure calculation in
    the page request path.
    """
    from server.engines.ai_native.czsc_snapshot_service import prewarm_structure_snapshots
    from server.engines.ai_native.universe_resolver import (
        has_active_position_for_symbol,
        list_interested_user_ids_for_symbol,
    )

    levels_by_symbol = _structure_levels_from_changes(changes)
    if not levels_by_symbol:
        return {"count": 0, "items": [], "skipped": True, "reason": "NO_V5_STRUCTURE_LEVEL_CHANGES"}

    items: list[dict[str, Any]] = []
    for symbol, levels in sorted(levels_by_symbol.items()):
        user_ids = list_interested_user_ids_for_symbol(symbol)
        requested_by_user_id = user_ids[0] if user_ids else None
        symbol_priority = holding_priority if has_active_position_for_symbol(symbol) else priority
        result = prewarm_structure_snapshots(
            symbols=[symbol],
            levels=levels,
            priority=symbol_priority,
            reason=reason,
            requested_by_user_id=requested_by_user_id,
        )
        for item in result.get("items", []):
            item["interested_user_ids"] = user_ids
            items.append(item)

    return {
        "count": _active_item_count(items),
        "items": items,
        "skipped": False,
        "engine": "czsc",
        "reason": reason,
    }


def prewarm_ai_structure_universe_for_tracked_users(
    *,
    priority: int = 70,
    reason: str = "kline_sync_universe",
    max_users: int = MAX_UNIVERSE_USERS_PER_PASS,
    max_symbols_per_user: int = MAX_UNIVERSE_SYMBOLS_PER_USER,
) -> dict:
    """Keep user-scoped AI Native V5 universes warm after K-line sync passes.

    Snapshot jobs are idempotent by data signature. Context jobs are user-scoped
    and are only enqueued when at least one snapshot already exists.
    """
    from server.engines.ai_native.czsc_snapshot_service import DEFAULT_LEVELS, prewarm_structure_snapshots
    from server.engines.ai_native.structure_context_service import prewarm_ai_structure_contexts
    from server.engines.ai_native.universe_resolver import list_ai_native_user_ids, resolve_ai_native_universe

    user_ids = list_ai_native_user_ids(limit=max_users)
    users: list[dict[str, Any]] = []
    total_snapshot_items = 0
    total_context_items = 0

    for user_id in user_ids:
        universe = resolve_ai_native_universe(user_id, ["positions", "watchlist"])
        symbols = [item["symbol"] for item in universe[:max_symbols_per_user]]
        if not symbols:
            continue

        snapshot_result = prewarm_structure_snapshots(
            symbols=symbols,
            levels=list(DEFAULT_LEVELS),
            priority=priority,
            reason=reason,
            requested_by_user_id=user_id,
        )
        context_result = prewarm_ai_structure_contexts(
            user_id=user_id,
            symbols=symbols,
            levels=list(DEFAULT_LEVELS),
            priority=max(1, priority - 10),
            reason=reason,
        )
        total_snapshot_items += _active_item_count(snapshot_result.get("items", []))
        total_context_items += _active_item_count(context_result.get("items", []))
        users.append({
            "user_id": user_id,
            "symbols": len(symbols),
            "snapshot_jobs": _active_item_count(snapshot_result.get("items", [])),
            "context_jobs": _active_item_count(context_result.get("items", [])),
        })

    return {
        "count": total_snapshot_items + total_context_items,
        "snapshot_jobs": total_snapshot_items,
        "context_jobs": total_context_items,
        "users": users,
        "skipped": not bool(users),
        "engine": "czsc",
        "reason": reason,
    }


def _structure_levels_from_changes(changes: list[dict]) -> dict[str, list[str]]:
    levels_by_symbol: dict[str, set[str]] = {}
    for item in changes:
        if int(item.get("written") or 0) <= 0:
            continue
        level = FREQ_TO_STRUCTURE_LEVEL.get(str(item.get("freq") or "").strip())
        if not level:
            continue
        symbol = normalize_symbol(item["symbol"])
        levels_by_symbol.setdefault(symbol, set()).add(level)

    order = {level: idx for idx, level in enumerate(FREQ_TO_STRUCTURE_LEVEL.values())}
    return {
        symbol: sorted(levels, key=lambda level: order.get(level, 999))
        for symbol, levels in levels_by_symbol.items()
    }


def _active_item_count(items: list[dict]) -> int:
    return sum(
        1
        for item in items
        if item.get("enqueued") or item.get("bumped") or item.get("retried")
    )


def _get_holding_symbol_set() -> set[str]:
    try:
        from server.db.database import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM positions WHERE quantity > 0"
            ).fetchall()
        finally:
            conn.close()
        return {normalize_symbol(row["symbol"]) for row in rows}
    except Exception as exc:
        logger.warning("读取持仓优先级列表失败: %s", exc)
        return set()


class KlineSyncWorker:
    """K线数据自动同步后台任务"""

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_sync_time: Optional[datetime] = None

    def start(self):
        """启动后台同步任务"""
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._sync_loop())
            logger.info("📊 K线自动同步 Worker 已启动")

    def stop(self):
        """停止后台同步任务"""
        self._running = False
        if self._task:
            self._task.cancel()
            logger.info("📊 K线自动同步 Worker 已停止")

    async def _sync_loop(self):
        """主循环：启动后立即同步一次，之后定期检查"""
        # 启动延迟，等待其他服务就绪
        await asyncio.sleep(STARTUP_DELAY)

        # 启动时立即执行一次同步
        await self._do_sync("启动同步")

        while self._running:
            try:
                await asyncio.sleep(CHECK_INTERVAL)

                if not self._running:
                    break

                # 判断是否需要同步
                now = datetime.now()

                # 非交易日不同步
                if not _is_trading_day(now):
                    continue

                # 只在 17:30 之后同步（BaoStock 日线入库时间）
                if now.hour < 17 or (now.hour == 17 and now.minute < 30):
                    continue

                # 今天已经同步过就跳过
                if (self._last_sync_time
                        and self._last_sync_time.date() == now.date()):
                    continue

                await self._do_sync("定时同步")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("K线同步循环异常: %s", e)
                await asyncio.sleep(60)  # 异常后等 1 分钟再重试

    async def _do_sync(self, trigger: str):
        """执行一次完整同步"""
        try:
            symbols = await run_in_threadpool(_get_all_tracked_symbols)
            if not symbols:
                logger.info("📊 [%s] 无需同步：没有跟踪的股票", trigger)
                return

            now = datetime.now()

            # 决定同步哪些频率
            # 20:30 之后同步全部（含分钟线），之前只同步日线
            if now.hour >= 21 or (now.hour == 20 and now.minute >= 30):
                freqs = ALL_FREQS
                freq_desc = "全部级别"
            else:
                freqs = ["day"]
                freq_desc = "仅日线"

            logger.info(
                "📊 [%s] 开始同步 %d 只股票 (%s): %s",
                trigger, len(symbols), freq_desc,
                ", ".join(symbols[:5]) + ("..." if len(symbols) > 5 else ""),
            )

            result = await run_in_threadpool(_sync_all_symbols, symbols, freqs)
            structure_jobs = await run_in_threadpool(
                enqueue_structure_jobs_for_changes,
                result.get("changed", []),
                priority=80,
                reason="kline_sync",
            )
            universe_jobs = await run_in_threadpool(
                prewarm_ai_structure_universe_for_tracked_users,
                priority=70,
                reason="kline_sync_universe",
            )

            self._last_sync_time = datetime.now()

            logger.info(
                "📊 [%s] 同步完成: %d/%d 只股票已更新, 写入 %d 条, 错误 %d",
                trigger,
                result["updated_symbols"],
                result["total_symbols"],
                result["total_written"],
                result["errors"],
            )
            if structure_jobs["count"]:
                logger.info("📊 [%s] 结构任务入队: %d", trigger, structure_jobs["count"])
            if universe_jobs["count"]:
                logger.info(
                    "📊 [%s] AI Native 用户宇宙预热: snapshot=%d context=%d",
                    trigger,
                    universe_jobs["snapshot_jobs"],
                    universe_jobs["context_jobs"],
                )

            # 同步完成后执行 WAL checkpoint，防止 WAL 文件无限积累
            # 用 run_in_threadpool 包裹，避免同步 I/O 阻塞事件循环
            def _do_checkpoint():
                with sqlite3.connect(LAKE_PATH) as conn:
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

            try:
                from server.db.kline_lake import LAKE_PATH
                await run_in_threadpool(_do_checkpoint)
                logger.debug("📊 [%s] WAL checkpoint 完成", trigger)
            except Exception as wal_err:
                logger.warning("📊 [%s] WAL checkpoint 失败（非致命）: %s", trigger, wal_err)

        except Exception as e:
            logger.error("📊 [%s] 同步失败: %s", trigger, e)

    async def force_sync(self) -> dict:
        """手动触发一次立即同步（供 API 调用）"""
        symbols = await run_in_threadpool(_get_all_tracked_symbols)
        if not symbols:
            return {"message": "没有需要同步的股票", "total_written": 0}

        result = await run_in_threadpool(_sync_all_symbols, symbols, ALL_FREQS)
        result["structure_jobs"] = await run_in_threadpool(
            enqueue_structure_jobs_for_changes,
            result.get("changed", []),
            priority=80,
            reason="force_sync",
        )
        result["universe_jobs"] = await run_in_threadpool(
            prewarm_ai_structure_universe_for_tracked_users,
            priority=70,
            reason="force_sync_universe",
        )
        self._last_sync_time = datetime.now()
        return result

    @property
    def status(self) -> dict:
        """返回当前同步状态"""
        return {
            "running": self._running,
            "last_sync_time": self._last_sync_time.isoformat() if self._last_sync_time else None,
        }


# 单例实例
kline_sync = KlineSyncWorker()
