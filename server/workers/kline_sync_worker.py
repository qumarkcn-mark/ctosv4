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
from datetime import date, datetime, timedelta
from typing import Any, Optional

from fastapi.concurrency import run_in_threadpool
from server import config
from server.domain.symbols import normalize_symbol
from server.engines.ai_native.universe_resolver import list_ai_native_user_ids, resolve_ai_native_universe

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

# LLM context 自动生成上限：只覆盖持仓 + 最近问过的票，纯自选不自动跑 LLM。
# CZSC snapshot 不消耗 token，保持全量覆盖。
MAX_AUTO_CONTEXT_SYMBOLS_PER_USER = 15

# 来源优先级阈值：priority >= 此值的票才自动生成 LLM context。
# positions=100, position_watchlist=110, recent_chat=80, watchlist=60
# 设为 80 则覆盖持仓和最近问过，排除纯自选。
AUTO_CONTEXT_PRIORITY_THRESHOLD = 80

SYNC_SCOPE_DAILY = "daily"
SYNC_SCOPE_FULL = "full"


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

    # CZSC 是纯本地计算，不消耗 token。无论 K 线是否有新写入，
    # 都无条件触发 snapshot prewarm，确保新自选股立即有结构快照。
    from server.engines.ai_native.czsc_snapshot_service import DEFAULT_LEVELS, prewarm_structure_snapshots

    result["snapshot_prewarm"] = prewarm_structure_snapshots(
        symbols=[canonical_symbol],
        levels=list(DEFAULT_LEVELS),
        priority=85,
        reason="watchlist_new_symbol",
    )

    logger.info(
        "自选数据闭环完成 %s: quick=%d full=%d errors=%d snapshot_jobs=%d",
        canonical_symbol,
        sum(item["written"] for item in result["quick"]),
        sum(item["written"] for item in result["full"]),
        len(result["errors"]),
        len(result["snapshot_prewarm"].get("items", [])),
    )
    return result


def _is_trading_day(dt: datetime) -> bool:
    """简单判断是否为交易日（排除周末，不处理节假日）"""
    return dt.weekday() < 5  # 0=周一 ~ 4=周五


def _scheduled_sync_scope(now: datetime) -> Optional[str]:
    """返回当前时间应该执行的自动同步窗口。

    BaoStock 日线和分钟线入库时间不同，必须分成两个独立窗口：
    - 17:30 后补日线
    - 20:30 后补全级别（含分钟线）
    """
    if not _is_trading_day(now):
        return None
    if now.hour > 20 or (now.hour == 20 and now.minute >= 30):
        return SYNC_SCOPE_FULL
    if now.hour > 17 or (now.hour == 17 and now.minute >= 30):
        return SYNC_SCOPE_DAILY
    return None


def _freqs_for_sync_scope(scope: str) -> list[str]:
    return ALL_FREQS if scope == SYNC_SCOPE_FULL else ["day"]


def _sync_all_symbols(symbols: list[str], freqs: list[str]) -> dict:
    """同步拉取所有 symbol 的 K 线数据（同步版本，在线程池中运行）

    Returns:
        {"total_symbols": int, "updated_symbols": int, "total_written": int, "errors": int}
    """
    from server.services.baostock_service import refresh_symbol_qfq
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

                written = refresh_symbol_qfq(symbol, freq)
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
    max_context_symbols_per_user: int = MAX_AUTO_CONTEXT_SYMBOLS_PER_USER,
    context_priority_threshold: int = AUTO_CONTEXT_PRIORITY_THRESHOLD,
) -> dict:
    """Keep user-scoped AI Native V5 universes warm after K-line sync passes.

    CZSC snapshot（本地计算，不消耗 token）覆盖全量 universe。
    LLM context（消耗 token）只覆盖高价值票：持仓 + 最近问过的票，
    纯自选股不自动跑 LLM，用户打开时手动触发即可。
    """
    from server.engines.ai_native.czsc_snapshot_service import DEFAULT_LEVELS, prewarm_structure_snapshots
    from server.engines.ai_native.structure_context_service import prewarm_ai_structure_contexts
    from server.engines.ai_native.universe_resolver import list_ai_native_user_ids, resolve_ai_native_universe

    user_ids = list_ai_native_user_ids(limit=max_users)
    users: list[dict[str, Any]] = []
    total_snapshot_items = 0
    total_context_items = 0

    for user_id in user_ids:
        universe = resolve_ai_native_universe(user_id, ["positions", "recent_chat", "watchlist"])
        # snapshot 全量覆盖（本地计算，零 token 成本）
        snapshot_items = universe[:max_symbols_per_user]
        # context 只覆盖高价值票（持仓/近期问过），纯自选不自动跑 LLM
        context_items = [
            item for item in snapshot_items
            if int(item.get("priority") or 0) >= context_priority_threshold
        ][:max_context_symbols_per_user]

        snapshot_symbols = [item["symbol"] for item in snapshot_items]
        context_symbols = [item["symbol"] for item in context_items]
        context_symbol_set = set(context_symbols)
        if not snapshot_symbols:
            continue

        snapshot_result = {"items": []}
        context_result = {"items": []}
        for item_priority, priority_items in _group_universe_by_priority(snapshot_items, default_priority=priority):
            priority_symbols = [item["symbol"] for item in priority_items]
            batch_snapshot = prewarm_structure_snapshots(
                symbols=priority_symbols,
                levels=list(DEFAULT_LEVELS),
                priority=item_priority,
                reason=reason,
                requested_by_user_id=user_id,
            )
            snapshot_result["items"].extend(batch_snapshot.get("items", []))
            # 只对 context_symbols 中的票入队 LLM context
            context_priority_symbols = [s for s in priority_symbols if s in context_symbol_set]
            if context_priority_symbols:
                batch_context = prewarm_ai_structure_contexts(
                    user_id=user_id,
                    symbols=context_priority_symbols,
                    levels=list(DEFAULT_LEVELS),
                    priority=max(1, item_priority - 10),
                    reason=reason,
                )
                context_result["items"].extend(batch_context.get("items", []))
        total_snapshot_items += _active_item_count(snapshot_result.get("items", []))
        total_context_items += _active_item_count(context_result.get("items", []))
        users.append({
            "user_id": user_id,
            "snapshot_symbols": len(snapshot_symbols),
            "context_symbols": len(context_symbols),
            "priority_buckets": [
                {"priority": bucket_priority, "symbols": [item["symbol"] for item in bucket_items]}
                for bucket_priority, bucket_items in _group_universe_by_priority(snapshot_items, default_priority=priority)
            ],
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


async def refresh_unified_reasoning_for_tracked_users(
    *,
    max_users: int = MAX_UNIVERSE_USERS_PER_PASS,
    max_symbols_per_user: int | None = None,
    reason: str = "after_kline_sync",
) -> dict:
    """K线同步成功后，基于最新 snapshot/context 生成次日盯盘推演。

    注意：此函数不再被 _do_sync / force_sync 自动调用。
    K 线同步后改为走 V5 snapshot → context 管线（prewarm_ai_structure_universe_for_tracked_users）。
    此函数保留供手动 API 调用。
    """
    from server.engines.ai_native.unified_reasoning_service import trigger_unified_reasoning

    if not getattr(config, "AI_UNIFIED_REASONING_AFTER_KLINE_SYNC_ENABLED", False):
        return {"generated": 0, "errors": [], "skipped": True, "reason": "DISABLED"}

    symbol_limit = max_symbols_per_user
    if symbol_limit is None:
        symbol_limit = int(getattr(config, "AI_UNIFIED_REASONING_AFTER_KLINE_SYNC_SYMBOLS_PER_USER", 30))
    symbol_limit = max(1, symbol_limit)

    users = list_ai_native_user_ids(limit=max_users)
    generated = 0
    errors: list[dict[str, str | int]] = []
    user_items: list[dict[str, Any]] = []
    for user_id in users:
        universe = resolve_ai_native_universe(user_id, ["positions", "watchlist"])
        symbols = [item["symbol"] for item in universe[:symbol_limit]]
        user_generated = 0
        for symbol in symbols:
            try:
                await trigger_unified_reasoning(user_id=user_id, symbol=symbol)
                generated += 1
                user_generated += 1
            except Exception as exc:
                errors.append({"user_id": int(user_id), "symbol": symbol, "error": str(exc)[:160]})
        user_items.append({"user_id": int(user_id), "symbols": len(symbols), "generated": user_generated})

    return {
        "generated": generated,
        "errors": errors,
        "users": user_items,
        "skipped": False,
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


def _group_universe_by_priority(items: list[dict[str, Any]], *, default_priority: int) -> list[tuple[int, list[dict[str, Any]]]]:
    buckets: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        item_priority = int(item.get("priority") or default_priority)
        buckets.setdefault(item_priority, []).append(item)
    return [(item_priority, buckets[item_priority]) for item_priority in sorted(buckets.keys(), reverse=True)]


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
        self._last_daily_sync_date: Optional[date] = None
        self._last_full_sync_date: Optional[date] = None

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

        # 启动时先补一次缓存；若还没到 BaoStock 收盘入库窗口，不标记当天正式同步完成。
        startup_now = datetime.now()
        startup_scope = _scheduled_sync_scope(startup_now) or SYNC_SCOPE_DAILY
        await self._do_sync(
            "启动同步",
            scope=startup_scope,
            mark_schedule=bool(_scheduled_sync_scope(startup_now)),
        )

        while self._running:
            try:
                await asyncio.sleep(CHECK_INTERVAL)

                if not self._running:
                    break

                # 判断是否需要同步
                now = datetime.now()

                scope = _scheduled_sync_scope(now)
                if not scope:
                    continue

                if self._has_synced_scope_today(scope, now.date()):
                    continue

                await self._do_sync("定时同步", scope=scope, mark_schedule=True)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("K线同步循环异常: %s", e)
                await asyncio.sleep(60)  # 异常后等 1 分钟再重试

    async def _do_sync(self, trigger: str, *, scope: Optional[str] = None, mark_schedule: bool = True):
        """执行一次完整同步"""
        try:
            symbols = await run_in_threadpool(_get_all_tracked_symbols)
            if not symbols:
                logger.info("📊 [%s] 无需同步：没有跟踪的股票", trigger)
                return

            scope = scope or _scheduled_sync_scope(datetime.now()) or SYNC_SCOPE_DAILY
            freqs = _freqs_for_sync_scope(scope)
            freq_desc = "全部级别" if scope == SYNC_SCOPE_FULL else "仅日线"

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
            if mark_schedule:
                self._mark_scope_synced(scope, self._last_sync_time.date())

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

    def _has_synced_scope_today(self, scope: str, today: date) -> bool:
        if scope == SYNC_SCOPE_FULL:
            return self._last_full_sync_date == today
        if scope == SYNC_SCOPE_DAILY:
            # 全级别同步包含 day，20:30 后跑过 full 就不需要再补 daily。
            return self._last_daily_sync_date == today or self._last_full_sync_date == today
        return False

    def _mark_scope_synced(self, scope: str, sync_date: date) -> None:
        if scope == SYNC_SCOPE_FULL:
            self._last_full_sync_date = sync_date
            self._last_daily_sync_date = sync_date
            return
        if scope == SYNC_SCOPE_DAILY:
            self._last_daily_sync_date = sync_date

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
            "last_daily_sync_date": self._last_daily_sync_date.isoformat() if self._last_daily_sync_date else None,
            "last_full_sync_date": self._last_full_sync_date.isoformat() if self._last_full_sync_date else None,
        }


# 单例实例
kline_sync = KlineSyncWorker()
