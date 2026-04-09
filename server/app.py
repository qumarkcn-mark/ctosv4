"""CT-OS V4.0 交易教练 — FastAPI 入口"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.config import DEBUG
from server.db.database import init_db, ensure_default_user


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期: 启动时初始化数据库"""
    init_db()
    ensure_default_user()
    print("🚀 CT-OS V4.0 交易教练已启动")
    yield
    print("👋 CT-OS V4.0 已关闭")


app = FastAPI(
    title="CT-OS V4.0 交易教练",
    description="记录交易 · 纠正行为 · 提醒机会",
    version="4.0.0",
    lifespan=lifespan,
)

# CORS - 允许前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if DEBUG else ["https://your-domain.com"],
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


from server.api import trades, positions, data

app.include_router(trades.router, prefix="/api/trades", tags=["trades"])
app.include_router(positions.router, prefix="/api/positions", tags=["positions"])
app.include_router(data.router, prefix="/api/data", tags=["data"])

# Phase 2+:
# from server.api import alerts, auth, analysis
# app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"])
# app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
# app.include_router(analysis.router, prefix="/api/analysis", tags=["analysis"])
