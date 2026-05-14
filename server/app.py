"""CT-OS V4.0 交易教练 — FastAPI 入口"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server import config
from server.db.database import init_db, ensure_default_user
from server.db.kline_lake import init_lake
from server.workers.price_monitor import monitor
from server.workers.kline_sync_worker import kline_sync
from server.workers.ai_structure_snapshot_worker import ai_structure_snapshot_worker
from server.workers.ai_structure_context_worker import ai_structure_context_worker
from server.workers.ai_structure_outcome_worker import ai_structure_outcome_worker
from server.services.baostock_service import shutdown_baostock

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _worker_specs():
    return [
        ("price_monitor", monitor, "PRICE_MONITOR_ENABLED"),
        ("kline_sync", kline_sync, "KLINE_SYNC_WORKER_ENABLED"),
        ("ai_structure_snapshot", ai_structure_snapshot_worker, "AI_STRUCTURE_SNAPSHOT_WORKER_ENABLED"),
        ("ai_structure_context", ai_structure_context_worker, "AI_STRUCTURE_CONTEXT_WORKER_ENABLED"),
        ("ai_structure_outcome", ai_structure_outcome_worker, "AI_STRUCTURE_OUTCOME_WORKER_ENABLED"),
    ]


def start_background_workers() -> list[str]:
    """Start configured background workers.

    V5 workers are explicit so deploys can enable snapshot/context/outcome
    separately without accidentally starting legacy structure paths.
    """
    started: list[str] = []
    for name, worker, flag in _worker_specs():
        if name.startswith("ai_structure_") and not getattr(config, "STRUCTURE_WORKER_ENABLED", False):
            logger.info("后台 Worker %s 未启用，跳过启动 (STRUCTURE_WORKER_ENABLED=false)", name)
            continue
        if not getattr(config, flag, False):
            logger.info("后台 Worker %s 未启用，跳过启动 (%s=false)", name, flag)
            continue
        worker.start()
        started.append(name)
    return started


def stop_background_workers() -> None:
    for _name, worker, _flag in _worker_specs():
        worker.stop()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期: 启动时初始化数据库，并启动后台任务"""
    init_db()
    init_lake()
    ensure_default_user()

    # 数据湖孤儿文件清理（静默）
    try:
        from server.services.lake_meta import cleanup_orphan_files
        result = cleanup_orphan_files()
        if result.get("cleaned", 0) > 0:
            logger.info(f"🧹 已清理 {result['cleaned']} 个孤儿文件")
    except Exception as e:
        logger.warning(f"孤儿清理失败: {e}")

    start_background_workers()
    logger.info("🚀 CT-OS V4.0 交易教练已启动")
    yield
    stop_background_workers()
    shutdown_baostock()
    logger.info("👋 CT-OS V4.0 已关闭")


app = FastAPI(
    title="CT-OS V4.0 交易教练",
    description="记录交易 · 纠正行为 · 提醒机会",
    version="4.0.0",
    lifespan=lifespan,
)

# CORS - 允许前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if config.DEBUG else ["https://your-domain.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 路由注册 ──

@app.get("/")
async def root():
    return {"name": "CT-OS V4.0", "role": "交易教练", "status": "ok"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


from server.api import trades, positions, data, trade_imports

app.include_router(trades.router, prefix="/api/trades", tags=["trades"])
app.include_router(positions.router, prefix="/api/positions", tags=["positions"])
app.include_router(data.router, prefix="/api/data", tags=["data"])
app.include_router(trade_imports.router, prefix="/api/trade-imports", tags=["trade imports"])

from server.api import auth, behavior, search
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(behavior.router, prefix="/api/behavior", tags=["behavior coach"])
app.include_router(search.router, prefix="/api/data", tags=["search"])

from server.api import lake
app.include_router(lake.router, prefix="/api/lake", tags=["data lake"])

from server.api import watchlist
app.include_router(watchlist.router, prefix="/api/watchlist", tags=["watchlist"])

from server.api import kronos
app.include_router(kronos.router, prefix="/api/kronos", tags=["kronos tsfm"])

from server.api import ai_structure
app.include_router(ai_structure.router, prefix="/api/ai-structure", tags=["ai native v5 structure"])

# Phase 2+:
# from server.api import alerts, analysis
# app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"])
# app.include_router(analysis.router, prefix="/api/analysis", tags=["analysis"])
