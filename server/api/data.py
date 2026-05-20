"""CSV 导入 + 行情查询 API"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.concurrency import run_in_threadpool

from server.api.auth import get_current_user_id
from server.db.database import get_connection
from server.db.kline_lake import lake_status
from server.domain.symbols import normalize_symbol
from server.services.csv_importer import import_csv
from server.services.price_service import (
    get_current_price,
    get_batch_prices,
    get_daily_klines,
    get_weekly_klines,
    get_minute_klines,
)
from server.services.qmt_bridge_client import (
    fetch_qmt_klines,
    qmt_health,
    qmt_log_health,
    qmt_log_quotes,
    qmt_stream_probe,
)
from server.services.tdx_minute_service import read_tdx_1m_klines, tdx_minute_status
from server.services.tdx_daily_sync_service import (
    get_sync_job,
    latest_sync_job,
    start_daily_sync,
    vipdoc_status,
)

router = APIRouter()


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
    }
    if value not in aliases:
        raise ValueError("interval 只支持 week/day/m60/m30/m15/m5")
    return aliases[value]


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
    return {"count": len(results), "prices": results}


# ── K 线数据 (给前端图表用) ──

@router.get("/klines/{symbol}")
async def query_klines(
    symbol: str,
    interval: str = Query("day", description="week / day / m60 / m30 / m15 / m5"),
    count: int = Query(200, ge=10, le=2000)
):
    """获取 K 线数据用于前端图表渲染"""
    if interval == "week":
        klines = await get_weekly_klines(symbol, count=count, allow_short_fresh_cache=True)
    elif interval == "day":
        klines = await get_daily_klines(symbol, count=count, allow_short_fresh_cache=True)
    else:
        klines = await get_minute_klines(symbol, interval=interval, count=count, allow_short_fresh_cache=True)
    if not klines:
        raise HTTPException(404, f"无法获取 {symbol} 的 {interval} K 线数据")
    return {"symbol": symbol, "interval": interval, "count": len(klines), "klines": klines}


# ── K 线数据同步 ──

@router.post("/sync-klines")
async def sync_klines():
    """手动触发所有自选股 K 线数据同步"""
    from server.workers.kline_sync_worker import kline_sync
    result = await kline_sync.force_sync()
    return result


@router.post("/sync-klines/{symbol}")
async def sync_symbol_klines(
    symbol: str,
    interval: Optional[str] = Query(None, description="week / day / m60 / m30 / m15 / m5；不传则刷新全级别"),
):
    """轻量刷新当前股票 K 线数据，供看盘页手动刷新使用。"""
    from server.services.baostock_service import refresh_symbol_qfq
    from server.workers.kline_sync_worker import ALL_FREQS, enqueue_structure_jobs_for_changes

    try:
        canonical_symbol = normalize_symbol(symbol)
        requested_freq = _normalize_kline_sync_interval(interval)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    def _sync_one_symbol():
        started_at = datetime.now().isoformat(timespec="seconds")
        freqs = [requested_freq] if requested_freq else list(ALL_FREQS)
        results = []
        total_written = 0
        error_count = 0
        changed = []

        for freq in freqs:
            try:
                written = refresh_symbol_qfq(canonical_symbol, freq)
                total_written += written
                results.append({"freq": freq, "written": written, "status": "ok"})
                if written > 0:
                    changed.append({"symbol": canonical_symbol, "freq": freq, "written": written})
            except Exception as exc:  # 单级别失败不阻断其他级别
                error_count += 1
                results.append({
                    "freq": freq,
                    "written": 0,
                    "status": "error",
                    "error": str(exc),
                })

        structure_jobs = enqueue_structure_jobs_for_changes(
            changed,
            priority=90,
            reason="manual_symbol_sync",
        )
        return {
            "status": "success" if error_count == 0 else "partial",
            "symbol": canonical_symbol,
            "freqs": freqs,
            "total_written": total_written,
            "errors": error_count,
            "results": results,
            "structure_jobs": structure_jobs,
            "started_at": started_at,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }

    return await run_in_threadpool(_sync_one_symbol)


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
