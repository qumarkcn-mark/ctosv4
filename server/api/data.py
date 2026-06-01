"""CSV 导入 + 行情查询 API"""

from datetime import datetime, timedelta
from functools import partial
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Query
from fastapi.concurrency import run_in_threadpool

from server.api.auth import get_current_user_id
from server.db.database import get_connection
from server.db.kline_lake import lake_status, query_klines as query_lake_klines, upsert_klines
from server.domain.symbols import normalize_symbol
from server.services.csv_importer import import_csv
from server.services.price_service import (
    get_current_price,
    get_batch_prices,
)
from server.services.intraday_observation_service import ingest_intraday_quote
from server.services.qmt_bridge_client import (
    fetch_qmt_klines,
    qmt_health,
    qmt_log_health,
    qmt_log_quotes,
    qmt_stream_probe,
)
from server.services.tdx_minute_service import (
    derive_tdx_day_from_minutes,
    read_tdx_1m_klines,
    read_tdx_5m_klines,
    read_tdx_derived_minute_klines,
    tdx_minute_status,
)
from server.services.tdx_bridge_client import append_live_quote_1m_bar, fetch_tdx_klines, is_tdx_bridge_enabled
from server.services.tdx_daily_sync_service import (
    get_sync_job,
    latest_sync_job,
    read_tdx_day_klines,
    read_tdx_week_klines,
    resolve_vipdoc,
    start_daily_sync,
    vipdoc_status,
)

router = APIRouter()
_POSTMARKET_SYNC_JOBS: dict[str, dict] = {}

TDX_PERIOD_BY_FREQ = {
    "1": "1m",
    "5": "5m",
    "15": "15m",
    "30": "30m",
    "60": "1h",
    "day": "1d",
    "week": "1w",
}


def _target_tdx_trading_date(now: Optional[datetime] = None) -> str:
    """Return the date that post-market TDX files should have reached."""
    current = (now or datetime.now()).date()
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current.isoformat()


def _latest_row_date(rows: list[dict]) -> str:
    if not rows:
        return ""
    return str(rows[-1].get("date") or "")


def _tdx_postmarket_readiness(vipdoc: Optional[str] = None, sample_limit: int = 24) -> dict:
    """Check whether local TDX has reached today's post-market files.

    This is a cheap preflight for the global post-market button. It samples
    tracked symbols instead of reading the full market, then only runs the heavy
    sync when TDX itself looks updated.
    """
    from server.workers.kline_sync_worker import _get_all_tracked_symbols

    target_date = _target_tdx_trading_date()
    symbols = _get_all_tracked_symbols()[: max(1, min(int(sample_limit or 24), 80))]
    status = _quick_vipdoc_status(vipdoc)
    samples = []
    latest_day = ""
    latest_1m = ""
    latest_5m = ""
    day_ready_count = 0
    minute_ready_count = 0

    for symbol in symbols:
        day_latest = _latest_row_date(read_tdx_day_klines(symbol, limit=1, vipdoc=vipdoc))
        m1_latest = _latest_row_date(read_tdx_1m_klines(symbol, limit=1, vipdoc=vipdoc))
        m5_latest = _latest_row_date(read_tdx_5m_klines(symbol, limit=1, vipdoc=vipdoc))
        latest_day = max(latest_day, day_latest[:10])
        latest_1m = max(latest_1m, m1_latest)
        latest_5m = max(latest_5m, m5_latest)
        day_ready = day_latest[:10] >= target_date
        minute_ready = m1_latest[:10] >= target_date or m5_latest[:10] >= target_date
        day_ready_count += 1 if day_ready else 0
        minute_ready_count += 1 if minute_ready else 0
        if len(samples) < 8:
            samples.append(
                {
                    "symbol": symbol,
                    "day_latest": day_latest,
                    "m1_latest": m1_latest,
                    "m5_latest": m5_latest,
                    "day_ready": day_ready,
                    "minute_ready": minute_ready,
                }
            )

    symbol_count = len(symbols)
    day_ready_ratio = round(day_ready_count / symbol_count, 3) if symbol_count else 0
    minute_ready_ratio = round(minute_ready_count / symbol_count, 3) if symbol_count else 0
    day_ready = bool(symbol_count and day_ready_ratio >= 0.6)
    minute_ready = bool(symbol_count and minute_ready_ratio >= 0.6)
    if not status.get("available"):
        readiness = "error"
        message = "TDX vipdoc 不可用，请先确认 Windows TDX 共享挂载。"
    elif not symbol_count:
        readiness = "empty"
        message = "没有持仓/盯盘/自选股票需要同步。"
    elif day_ready and minute_ready:
        readiness = "ready"
        message = f"TDX 本地数据已到 {target_date}，可以执行盘后同步。"
    elif minute_ready:
        readiness = "ready"
        message = f"TDX 分钟线已到 {target_date} 15:00；日线缓存未追加，将用分钟线生成今日日线。"
    elif day_ready:
        readiness = "partial"
        message = f"TDX 日线已到 {target_date}，分钟线仍未完整更新。"
    else:
        readiness = "stale"
        message = f"TDX 本地数据还没到 {target_date}，请先在 Windows TDX 更新盘后数据。"

    return {
        "status": readiness,
        "message": message,
        "target_date": target_date,
        "vipdoc": status,
        "sampled_symbols": symbol_count,
        "ready_counts": {
            "day": day_ready_count,
            "minute": minute_ready_count,
            "day_ratio": day_ready_ratio,
            "minute_ratio": minute_ready_ratio,
        },
        "latest": {
            "day": latest_day,
            "m1": latest_1m,
            "m5": latest_5m,
        },
        "samples": samples,
    }


def _run_tdx_postmarket_sync(vipdoc: Optional[str] = None, mode: str = "incremental") -> dict:
    from server.scripts.run_tdx_postmarket_sync import run_postmarket_sync

    return run_postmarket_sync(vipdoc=vipdoc, mode=mode, reset=False)


def _run_tdx_postmarket_sync_job(job_id: str, *, vipdoc: Optional[str], mode: str, readiness: dict) -> None:
    job = _POSTMARKET_SYNC_JOBS[job_id]
    try:
        result = _run_tdx_postmarket_sync(vipdoc=vipdoc, mode=mode)
        tracked = result.get("tracked") or {}
        snapshots = result.get("snapshot_prewarm") or {}
        status = result.get("status") or "success"
        job.update(
            {
                "status": status,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "message": (
                    f"TDX 盘后同步完成：更新 {tracked.get('updated_symbols', 0)}/"
                    f"{tracked.get('total_symbols', 0)} 只，结构刷新 {snapshots.get('count', 0)} 个任务。"
                    if status == "success"
                    else f"TDX 盘后同步部分完成：错误 {tracked.get('errors', 0)} 个，请查看详情。"
                ),
                "tdx_status": readiness,
                "sync_result": result,
            }
        )
    except Exception as exc:
        job.update(
            {
                "status": "error",
                "finished_at": datetime.now().isoformat(timespec="seconds"),
                "message": f"TDX 盘后同步失败: {exc}",
                "tdx_status": readiness,
                "sync_result": None,
                "error": str(exc),
            }
        )


def _quick_vipdoc_status(vipdoc: Optional[str] = None) -> dict:
    from pathlib import Path

    root = Path(resolve_vipdoc(vipdoc))
    available = (root / "sh" / "lday").is_dir() and (root / "sz" / "lday").is_dir()
    return {
        "vipdoc": str(root),
        "available": available,
        "reason": "" if available else "VIPDOC_NOT_FOUND",
    }


def _normalize_kline_sync_interval(interval: Optional[str]) -> Optional[str]:
    """把前端周期参数归一成 BaoStock 级别；不传则保持兼容，刷新全级别。"""
    if interval is None:
        return None
    value = interval.strip().lower()
    aliases = {
        "week": "week",
        "w": "week",
        "day": "day",
        "d": "day",
        "m60": "60",
        "60": "60",
        "m30": "30",
        "30": "30",
        "m15": "15",
        "15": "15",
        "m5": "5",
        "5": "5",
        "m1": "1",
        "1m": "1",
        "1": "1",
    }
    if value not in aliases:
        raise ValueError("interval 只支持 week/day/m60/m30/m15/m5/m1")
    return aliases[value]


def _normalize_kline_query_interval(interval: str) -> str:
    """把外部 K 线查询周期归一成 price_service / TDX 展示层使用的周期。"""
    value = str(interval or "day").strip().lower()
    aliases = {
        "week": "week",
        "w": "week",
        "day": "day",
        "d": "day",
        "m60": "m60",
        "60m": "m60",
        "60": "m60",
        "m30": "m30",
        "30m": "m30",
        "30": "m30",
        "m15": "m15",
        "15m": "m15",
        "15": "m15",
        "m5": "m5",
        "5m": "m5",
        "5": "m5",
        "m1": "m1",
        "1m": "m1",
        "1": "m1",
    }
    if value not in aliases:
        raise HTTPException(400, "interval 只支持 week/day/m60/m30/m15/m5/m1")
    return aliases[value]


def _interval_to_freq(interval: str) -> str:
    return {
        "week": "week",
        "day": "day",
        "m60": "60",
        "m30": "30",
        "m15": "15",
        "m5": "5",
        "m1": "1",
    }[interval]


def _query_tdx_display_klines(symbol: str, freq: str, count: int) -> list[dict]:
    """Read TDX display K lines, preferring front-adjusted rows when present."""
    qfq_rows = query_lake_klines(symbol, freq, limit=count, adjustflag="2", source="tdx")
    raw_rows = query_lake_klines(symbol, freq, limit=count, adjustflag="3", source="tdx")
    if qfq_rows and raw_rows and str(raw_rows[-1].get("date") or "") > str(qfq_rows[-1].get("date") or ""):
        return raw_rows
    return qfq_rows or raw_rows


def _should_backfill_tdx_display_klines(rows: list[dict], count: int) -> bool:
    """Treat tiny partial rows as a cache miss so local TDX can refill the chart."""
    if not rows:
        return True
    requested = max(10, min(int(count or 0), 2000))
    expected_floor = min(requested, 200)
    return requested > 10 and len(rows) < expected_floor


def _read_local_tdx_display_klines(symbol: str, freq: str, count: int) -> list[dict]:
    """Read local TDX files for display only; never writes into the data lake."""
    limit = max(10, min(int(count or 0), 5000))
    if freq == "week":
        return _tdx_week_rows_with_minute_day(symbol, limit=min(limit, 1200))
    if freq == "day":
        return _tdx_day_rows_with_minute_fallback(symbol, limit=limit)
    if freq == "1":
        return read_tdx_1m_klines(symbol, limit)
    if freq in {"5", "15", "30", "60"}:
        return read_tdx_derived_minute_klines(symbol, freq, limit=limit)
    return []


def _display_quality(
    *,
    source: str,
    rows: list[dict],
    requested_count: int,
    fallback_used: bool = False,
    warning: str = "",
) -> dict:
    status = "ok"
    if not rows:
        status = "missing"
    elif _should_backfill_tdx_display_klines(rows, requested_count):
        status = "partial"
    return {
        "status": status,
        "source": source,
        "row_count": len(rows or []),
        "requested_count": int(requested_count or 0),
        "fallback_used": bool(fallback_used),
        "warning": warning,
        "first": rows[0].get("date") if rows else "",
        "last": rows[-1].get("date") if rows else "",
    }


def _query_qmt_today_1m_display_klines(symbol: str, count: int) -> list[dict]:
    """Read today's intraday preview 1m rows from qmt_lake for display only."""
    today = datetime.now().strftime("%Y-%m-%d")
    rows = query_lake_klines(symbol, "1", start_date=today, limit=count, adjustflag="3", source="qmt")
    result = []
    now_minute = datetime.now().strftime("%Y-%m-%d %H:%M")
    for row in rows:
        date = str(row.get("date") or "")
        if not date.startswith(today):
            continue
        result.append(
            {
                "symbol": symbol.replace(".", ""),
                "freq": "1",
                "date": date,
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "volume": row.get("volume", 0),
                "amount": row.get("amount", 0),
                "adjustflag": "3",
                "bar_status": "FORMING" if date[:16] == now_minute else "CLOSED",
                "source": "qmt_lake_1m_preview",
            }
        )
    return result[-count:]


def _merge_m1_display_rows(history_rows: list[dict], preview_rows: list[dict], count: int) -> list[dict]:
    """Merge formal/replay 1m history with today's preview tape by timestamp."""
    merged: dict[str, dict] = {}
    for row in history_rows or []:
        date = str(row.get("date") or "")
        if date:
            merged[date] = row
    for row in preview_rows or []:
        date = str(row.get("date") or "")
        if date:
            merged[date] = row
    return [merged[key] for key in sorted(merged)][-count:]


def _sync_local_tdx_history_to_lake(symbol: str, freq: str, count: int = 5000) -> tuple[int, list[dict]]:
    """Import local TDX .day/.lc1 derived history bars when bridge has no rows."""
    if freq == "week":
        rows = _tdx_week_rows_with_minute_day(symbol, limit=min(count, 1200))
    elif freq == "day":
        rows = _tdx_day_rows_with_minute_fallback(symbol, limit=count)
    elif freq in {"1", "5", "15", "30", "60"}:
        rows = read_tdx_derived_minute_klines(symbol, freq, limit=count)
    else:
        rows = []
    written = upsert_klines(symbol, freq, rows, adjustflag="3", source="tdx")
    return written, rows


def _tdx_day_rows_with_minute_fallback(symbol: str, limit: int = 5000, vipdoc: Optional[str] = None) -> list[dict]:
    rows = read_tdx_day_klines(symbol, limit=limit, vipdoc=vipdoc)
    target_date = _target_tdx_trading_date()
    if rows and str(rows[-1].get("date") or "")[:10] >= target_date:
        return rows
    derived = derive_tdx_day_from_minutes(symbol, target_date, vipdoc=vipdoc)
    if not derived:
        return rows
    rows = [row for row in rows if str(row.get("date") or "")[:10] != target_date]
    return [*rows, derived][-max(1, min(int(limit), 20000)):]


def _tdx_week_rows_with_minute_day(symbol: str, limit: int = 1200, vipdoc: Optional[str] = None) -> list[dict]:
    from server.services.tdx_daily_sync_service import aggregate_tdx_week_klines

    day_rows = _tdx_day_rows_with_minute_fallback(symbol, limit=20000, vipdoc=vipdoc)
    return aggregate_tdx_week_klines(day_rows)[-max(1, min(int(limit), 5000)):]


# ── CSV 导入 ──

@router.post("/import/csv")
async def import_csv_file(
    file: UploadFile = File(...),
    broker: str = Query("auto", description="eastmoney/ths/auto"),
    current_user_id: int = Depends(get_current_user_id),
):
    """导入券商交割单 CSV"""
    from fastapi.concurrency import run_in_threadpool
    user_id = current_user_id

    content = await file.read()
    csv_text = content.decode("utf-8-sig")  # 东方财富 CSV 带 BOM

    def _db_import():
        conn = get_connection()
        try:
            result = import_csv(conn, user_id, csv_text, broker)
            return result
        finally:
            conn.close()

    return await run_in_threadpool(_db_import)


# ── 行情查询 ──

@router.get("/price/{symbol}")
async def query_price(symbol: str):
    """查询单只股票当前价格"""
    result = await get_current_price(symbol)
    if not result:
        raise HTTPException(404, f"行情查询失败: {symbol}")
    _ingest_price_quotes_for_intraday({symbol: result})
    return result


@router.get("/prices")
async def query_batch_prices(symbols: str = Query(..., description="逗号分隔的代码")):
    """批量查询股票价格"""
    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(400, "请提供至少一个股票代码")
    if len(symbol_list) > 30:
        raise HTTPException(400, "最多同时查询 30 只")

    results = await get_batch_prices(symbol_list)
    _ingest_price_quotes_for_intraday(results)
    return {"count": len(results), "prices": results}


def _ingest_price_quotes_for_intraday(results: dict[str, dict]) -> None:
    """Feed visible quote polling into the live intraday preview cache."""
    for raw_symbol, quote in (results or {}).items():
        if not isinstance(quote, dict):
            continue
        symbol = str(quote.get("symbol") or raw_symbol or "").strip()
        if not symbol:
            continue
        try:
            ingest_intraday_quote(symbol, quote)
        except Exception:
            continue


# ── K 线数据 (给前端图表用) ──

@router.get("/klines/{symbol}")
async def query_klines(
    symbol: str,
    interval: str = Query("day", description="week / day / m60 / m30 / m15 / m5 / m1"),
    count: int = Query(200, ge=10, le=2000)
):
    """获取 K 线数据用于前端图表渲染"""
    normalized_interval = _normalize_kline_query_interval(interval)
    canonical_symbol = normalize_symbol(symbol)
    freq = _interval_to_freq(normalized_interval)
    data_quality = {}
    if normalized_interval == "m1":
        klines = await run_in_threadpool(_query_tdx_display_klines, canonical_symbol, freq, count)
        source = "tdx_lake"
        fallback_used = False
        if _should_backfill_tdx_display_klines(klines, count):
            local_rows = await run_in_threadpool(_read_local_tdx_display_klines, canonical_symbol, freq, count)
            if local_rows and len(local_rows) >= len(klines):
                klines = local_rows
                source = "tdx_local_display"
                fallback_used = True
        qmt_preview = await run_in_threadpool(_query_qmt_today_1m_display_klines, canonical_symbol, count)
        if qmt_preview:
            source = f"{source}+qmt_lake_1m_preview"
        klines = _merge_m1_display_rows(klines, qmt_preview, count)
        quote = await get_current_price(canonical_symbol)
        if quote:
            try:
                ingest_intraday_quote(canonical_symbol, quote)
            except Exception:
                pass
        klines = append_live_quote_1m_bar(klines, quote, symbol, count)
        if quote:
            source = f"{source}+live_quote"
        interval = "m1"
        data_quality = _display_quality(
            source=source,
            rows=klines,
            requested_count=count,
            fallback_used=fallback_used,
            warning="1分钟展示为盘中预览数据；正式盘后结构以 TDX 同步后的数据湖为准。" if fallback_used or qmt_preview or quote else "",
        )
        if not klines:
            minute_status = await run_in_threadpool(tdx_minute_status, symbol)
            reason = minute_status.get("reason") or "NO_LOCAL_1M_ROWS"
            vipdoc = minute_status.get("vipdoc") or ""
            raise HTTPException(
                404,
                (
                    f"无法获取 {symbol} 的 1分钟K线：Windows TDX bridge /kline 未返回数据，"
                    f"本机 TDX vipdoc 也不可用（{reason}{'，' + vipdoc if vipdoc else ''}）。"
                    "请先在 Windows bridge 补 /kline，或挂载 TDX vipdoc。"
                ),
            )
    else:
        klines = await run_in_threadpool(_query_tdx_display_klines, canonical_symbol, freq, count)
        source = "tdx_lake"
        fallback_used = False
        if _should_backfill_tdx_display_klines(klines, count):
            local_rows = await run_in_threadpool(_read_local_tdx_display_klines, canonical_symbol, freq, count)
            if local_rows and len(local_rows) >= len(klines):
                klines = local_rows
                source = "tdx_local_display"
                fallback_used = True
        data_quality = _display_quality(
            source=source,
            rows=klines,
            requested_count=count,
            fallback_used=fallback_used,
            warning=(
                "当前仅用于看图展示，未写入数据湖；如需生成结构和复权数据，请点击手动同步。"
                if fallback_used
                else ""
            ),
        )
        if not klines:
            raise HTTPException(
                404,
                _missing_tdx_kline_message(symbol, normalized_interval),
            )
        interval = normalized_interval
    if not klines:
        raise HTTPException(404, f"无法获取 {symbol} 的 {interval} K 线数据")
    return {
        "symbol": symbol,
        "interval": interval,
        "count": len(klines),
        "klines": klines,
        "data_quality": data_quality,
    }


def _missing_tdx_kline_message(symbol: str, interval: str) -> str:
    minute_status = tdx_minute_status(symbol)
    reason = minute_status.get("reason") or "NO_LOCAL_MINUTE_ROWS"
    vipdoc = minute_status.get("vipdoc") or ""
    path = minute_status.get("path") or ""
    bridge_state = "TDX bridge 未配置" if not is_tdx_bridge_enabled() else "TDX bridge 未返回数据"
    native_file = ".lc5" if interval == "m5" else ".lc1/.lc5"
    details = f"{reason}"
    if vipdoc:
        details += f"，vipdoc={vipdoc}"
    if path:
        details += f"，path={path}"
    return (
        f"无法获取 {symbol} 的 {interval} K线：{bridge_state}，"
        f"本地 TDX {native_file} 不可用（{details}）。"
        "请先挂载 Windows new_tdx64/vipdoc 共享，或执行 TDX 盘后同步。"
    )


# ── K 线数据同步 ──

@router.post("/sync-klines")
async def sync_klines():
    """旧 BaoStock 全量同步入口已下线，避免运行态误写 BaoStock。"""
    return {
        "status": "disabled",
        "source": "tdx",
        "message": "全量 BaoStock 同步已关闭。请使用 TDX 盘后同步或单股 TDX 刷新。",
    }


@router.post("/sync-klines/{symbol}")
async def sync_symbol_klines(
    symbol: str,
    interval: Optional[str] = Query(None, description="week / day / m60 / m30 / m15 / m5 / m1；不传则刷新全级别"),
):
    """轻量刷新当前股票 TDX K 线数据，供看盘页手动刷新使用。"""
    from server.workers.kline_sync_worker import ALL_FREQS, enqueue_structure_jobs_for_changes

    try:
        canonical_symbol = normalize_symbol(symbol)
        requested_freq = _normalize_kline_sync_interval(interval)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    started_at = datetime.now().isoformat(timespec="seconds")
    freqs = [requested_freq] if requested_freq else list(ALL_FREQS)
    results = []
    total_written = 0
    error_count = 0
    changed = []
    needs_day_factor = "day" not in freqs and any(freq in {"week", "1", "5", "15", "30", "60"} for freq in freqs)
    if needs_day_factor:
        try:
            factor_rows = await fetch_tdx_klines(canonical_symbol, period="1d", count=5000, refresh=True)
            factor_written = await run_in_threadpool(
                partial(upsert_klines, canonical_symbol, "day", factor_rows, adjustflag="2", source="tdx")
            )
            total_written += factor_written
            if factor_written > 0:
                results.append({
                    "freq": "day_factor",
                    "period": "1d",
                    "written": factor_written,
                    "status": "ok",
                    "first": factor_rows[0]["date"] if factor_rows else "",
                    "last": factor_rows[-1]["date"] if factor_rows else "",
                    "source": "tdx_bridge",
                    "adjustflag": "2",
                })
        except Exception as exc:
            error_count += 1
            results.append({
                "freq": "day_factor",
                "period": "1d",
                "written": 0,
                "status": "error",
                "source": "tdx",
                "error": str(exc),
            })

    for freq in freqs:
        period = TDX_PERIOD_BY_FREQ.get(freq)
        if not period:
            results.append({"freq": freq, "written": 0, "status": "skipped", "reason": "UNSUPPORTED_TDX_PERIOD"})
            continue
        try:
            rows = await fetch_tdx_klines(canonical_symbol, period=period, count=5000, refresh=True)
            source = "tdx_bridge"
            adjustflag = "2"
            if not rows:
                written, rows = await run_in_threadpool(_sync_local_tdx_history_to_lake, canonical_symbol, freq, 5000)
                source = "tdx_local_history"
                adjustflag = "3"
            else:
                written = await run_in_threadpool(
                    partial(
                        upsert_klines,
                        canonical_symbol,
                        freq,
                        rows,
                        adjustflag=adjustflag,
                        source="tdx",
                    )
                )
            total_written += written
            results.append({
                "freq": freq,
                "period": period,
                "written": written,
                "status": "ok" if rows else "no_data",
                "first": rows[0]["date"] if rows else "",
                "last": rows[-1]["date"] if rows else "",
                "source": source,
                "adjustflag": adjustflag,
            })
            if written > 0 and adjustflag == "2":
                changed.append({"symbol": canonical_symbol, "freq": freq, "written": written})
        except Exception as exc:  # 单级别失败不阻断其他级别
            error_count += 1
            results.append({
                "freq": freq,
                "period": period,
                "written": 0,
                "status": "error",
                "source": "tdx",
                "error": str(exc),
            })

    qfq_rebuild = {}
    try:
        from server.services.tdx_qfq_normalizer import rebuild_tdx_qfq_from_existing_factors

        qfq_result = await run_in_threadpool(
            rebuild_tdx_qfq_from_existing_factors,
            canonical_symbol,
            target_freqs=[freq for freq in freqs if freq != "day"],
        )
        qfq_rebuild = {
            "status": qfq_result.status,
            "reason": qfq_result.reason,
            "day_factor_count": qfq_result.day_factor_count,
            "written": qfq_result.written,
            "missing_factor_dates": qfq_result.missing_factor_dates,
        }
        if qfq_result.total_written > 0:
            total_written += qfq_result.total_written
            for freq, written in qfq_result.written.items():
                if written > 0:
                    changed.append({"symbol": canonical_symbol, "freq": freq, "written": written})
    except Exception as exc:
        error_count += 1
        qfq_rebuild = {"status": "error", "error": str(exc)}

    structure_jobs = enqueue_structure_jobs_for_changes(
        changed,
        priority=90,
        reason="manual_symbol_tdx_sync",
    )
    return {
        "status": "success" if error_count == 0 else "partial",
        "source": "tdx",
        "symbol": canonical_symbol,
        "freqs": freqs,
        "total_written": total_written,
        "errors": error_count,
        "results": results,
        "qfq_rebuild": qfq_rebuild,
        "structure_jobs": structure_jobs,
        "started_at": started_at,
        "finished_at": datetime.now().isoformat(timespec="seconds"),
    }


@router.get("/sync-status")
async def sync_status():
    """查询 K 线自动同步状态"""
    from server.workers.kline_sync_worker import kline_sync
    return kline_sync.status


@router.get("/lake/status")
async def query_lake_status():
    """查询 TDX / BaoStock / QMT 三个 K 线数据湖的只读状态。"""
    return await run_in_threadpool(lake_status)


# ── QMT 只读行情桥 ──

@router.get("/qmt/health")
async def query_qmt_health():
    """查询 Windows QMT 只读行情桥状态。"""
    return await qmt_health()


@router.get("/qmt/klines/{symbol}")
async def query_qmt_klines(
    symbol: str,
    period: str = Query("5m", description="1m / 5m / 15m / 30m / 1h / 1d"),
    count: int = Query(240, ge=1, le=1000),
    cache_closed: bool = Query(True, description="是否缓存 CLOSED K 线到 qmt_lake"),
):
    """从 QMT 桥读取实时分钟 K 线；FORMING K 线只返回不入正式缓存。"""
    try:
        return await fetch_qmt_klines(symbol, period=period, limit=count, cache_closed=cache_closed)
    except Exception as exc:
        raise HTTPException(502, f"QMT 行情桥查询失败: {exc}") from exc


@router.get("/qmt/stream-probe/{symbol}")
async def query_qmt_stream_probe(
    symbol: str,
    period: str = Query("tick", description="tick / 1m / 5m"),
):
    """探测 Windows QMT SSE 网关订阅链路；不作为正式结构源。"""
    try:
        return await qmt_stream_probe(symbol, period=period)
    except Exception as exc:
        raise HTTPException(502, f"QMT SSE 网关探测失败: {exc}") from exc


# ── QMT 日志行情旁路：只做盘中 preview，不进正式结构 ──

@router.get("/qmt-log/health")
async def query_qmt_log_health():
    """查询 Windows QMT 日志行情旁路状态；仅用于 preview。"""
    return await qmt_log_health()


@router.get("/qmt-log/quotes")
async def query_qmt_log_quotes(symbols: str = Query(..., description="逗号分隔的股票代码")):
    """读取 QMT 日志行情快照；不作为正式 CZSC 结构源。"""
    symbol_list = [item.strip() for item in symbols.split(",") if item.strip()]
    if not symbol_list:
        raise HTTPException(400, "请提供至少一个股票代码")
    if len(symbol_list) > 50:
        raise HTTPException(400, "最多同时查询 50 只")
    try:
        return await qmt_log_quotes(symbol_list)
    except Exception as exc:
        raise HTTPException(502, f"QMT 日志行情查询失败: {exc}") from exc


# ── TDX 本地 1 分钟展示源 ──

@router.get("/tdx/vipdoc/status")
async def query_tdx_vipdoc_status(vipdoc: Optional[str] = None):
    """检查本地/挂载的 TDX vipdoc 数据源规模与可用性。"""
    return await run_in_threadpool(vipdoc_status, vipdoc)


@router.post("/tdx/sync/daily")
async def start_tdx_daily_sync(
    vipdoc: Optional[str] = Query(None, description="TDX vipdoc 路径；不填则自动探测"),
    mode: str = Query("incremental", description="incremental / full"),
    reset: bool = Query(False, description="是否先清空 tdx_lake 日线再导入"),
):
    """启动 TDX 全 A 日线同步后台任务。"""
    try:
        return await run_in_threadpool(start_daily_sync, vipdoc, mode, reset)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/tdx/sync/postmarket")
async def start_tdx_postmarket_sync(
    background_tasks: BackgroundTasks,
    vipdoc: Optional[str] = Query(None, description="TDX vipdoc 路径；不填则自动探测"),
    mode: str = Query("incremental", description="incremental / full"),
    force: bool = Query(False, description="即使预检不是 ready 也强制同步"),
):
    """执行全局 TDX 盘后同步；供顶部全局按钮使用。"""
    if mode not in {"incremental", "full"}:
        raise HTTPException(400, "mode 只支持 incremental / full")

    readiness = await run_in_threadpool(_tdx_postmarket_readiness, vipdoc)
    if readiness["status"] != "ready" and not force:
        return {
            "status": readiness["status"],
            "message": readiness["message"],
            "tdx_status": readiness,
            "sync_result": None,
            "skipped": True,
        }

    for existing in reversed(list(_POSTMARKET_SYNC_JOBS.values())):
        if existing.get("status") == "running":
            return {
                "status": "running",
                "message": "TDX 盘后同步正在进行中。",
                "tdx_status": existing.get("tdx_status") or readiness,
                "sync_result": None,
                "job_id": existing.get("job_id"),
                "skipped": False,
            }

    job_id = uuid4().hex[:12]
    _POSTMARKET_SYNC_JOBS[job_id] = {
        "job_id": job_id,
        "status": "running",
        "message": "TDX 盘后同步已开始，后台正在导入数据并刷新结构。",
        "tdx_status": readiness,
        "sync_result": None,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "finished_at": "",
    }
    background_tasks.add_task(_run_tdx_postmarket_sync_job, job_id, vipdoc=vipdoc, mode=mode, readiness=readiness)
    return {
        "status": "running",
        "message": _POSTMARKET_SYNC_JOBS[job_id]["message"],
        "tdx_status": readiness,
        "sync_result": None,
        "job_id": job_id,
        "skipped": False,
    }


@router.get("/tdx/sync/postmarket/latest")
async def query_latest_tdx_postmarket_sync():
    """查询最近一次 TDX 盘后同步任务。"""
    if not _POSTMARKET_SYNC_JOBS:
        return {}
    return next(reversed(_POSTMARKET_SYNC_JOBS.values())).copy()


@router.get("/tdx/sync/latest")
async def query_latest_tdx_sync():
    """查询最近一次 TDX 同步任务。"""
    return latest_sync_job()


@router.get("/tdx/sync/{job_id}")
async def query_tdx_sync_job(job_id: str):
    """查询指定 TDX 同步任务进度。"""
    try:
        return get_sync_job(job_id)
    except KeyError as exc:
        raise HTTPException(404, f"TDX 同步任务不存在: {job_id}") from exc

@router.get("/tdx/minute/health")
async def query_tdx_minute_health(symbol: Optional[str] = None):
    """查询本地 TDX 1分钟数据是否可用。"""
    return tdx_minute_status(symbol)


@router.get("/tdx/minute/{symbol}")
async def query_tdx_1m_klines(
    symbol: str,
    count: int = Query(240, ge=1, le=5000),
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS"),
):
    """读取本地 TDX 1分钟 K 线，仅用于 Kline 展示/历史回放。"""
    rows = await run_in_threadpool(
        read_tdx_1m_klines,
        symbol,
        limit=count,
        start_date=start_date,
        end_date=end_date,
    )
    if not rows:
        raise HTTPException(404, f"无法读取 {symbol} 的 TDX 本地1分钟数据")
    return {
        "status": "ok",
        "symbol": rows[-1]["symbol"],
        "interval": "1m",
        "source": "tdx_local_1m",
        "usage": "display_replay_only",
        "count": len(rows),
        "klines": rows,
    }
