"""CT-OS V4.0 数据库初始化 — SQLite + 多用户 Ready"""

import logging
import sqlite3
from pathlib import Path
from server.config import DB_PATH

logger = logging.getLogger(__name__)

SCHEMA = """
-- 用户 (微信 OAuth)
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    openid TEXT UNIQUE NOT NULL,
    nickname TEXT,
    avatar_url TEXT,
    settings_json TEXT DEFAULT '{}',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 交易记录 (核心表)
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    name TEXT,
    direction TEXT NOT NULL CHECK(direction IN ('BUY', 'SELL')),
    price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    amount REAL NOT NULL,
    stop_loss_price REAL,
    reason_text TEXT,
    reason_category TEXT CHECK(reason_category IN
        ('CHAN_SIGNAL', 'FRIEND_TIP', 'FEELING', 'OTHER')),
    trend_direction TEXT,
    source TEXT DEFAULT 'MANUAL' CHECK(source IN
        ('VOICE', 'MANUAL', 'CSV_IMPORT')),
    traded_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 实时持仓 (由 trades 聚合计算)
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    name TEXT,
    quantity INTEGER NOT NULL,
    avg_cost REAL NOT NULL,
    current_price REAL,
    unrealized_pnl REAL,
    stop_loss_price REAL,
    trailing_stop_price REAL,
    days_held INTEGER,
    updated_at DATETIME,
    UNIQUE(user_id, symbol)
);

-- 提醒规则
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    alert_type TEXT NOT NULL CHECK(alert_type IN
        ('STOP_LOSS', 'STOP_LOSS_BROKEN', 'STOP_LOSS_WARNING', 'CHAN_THIRD_BUY', 'SIGNAL', 'REBUY', 'BREAKEVEN')),
    trigger_price REAL,
    trigger_direction TEXT CHECK(trigger_direction IN ('ABOVE', 'BELOW')),
    is_triggered INTEGER DEFAULT 0,
    triggered_at DATETIME,
    message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 行为分析缓存
CREATE TABLE IF NOT EXISTS behavior_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    period TEXT NOT NULL CHECK(period IN
        ('DAILY', 'WEEKLY', 'MONTHLY', 'ALL_TIME')),
    total_trades INTEGER,
    win_rate REAL,
    profit_loss_ratio REAL,
    avg_hold_days REAL,
    max_drawdown REAL,
    trend_compliance_rate REAL,
    stop_loss_execution_rate REAL,
    early_exit_count INTEGER,
    counter_trend_count INTEGER,
    period_start DATE,
    calculated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- AI 雷达推演快照
CREATE TABLE IF NOT EXISTS radar_deductions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    matrix_state_json TEXT NOT NULL,
    ai_summary TEXT NOT NULL,
    ai_deduction_json TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_trades_user ON trades(user_id, traded_at);
CREATE INDEX IF NOT EXISTS idx_positions_user ON positions(user_id);
CREATE INDEX IF NOT EXISTS idx_alerts_user ON alerts(user_id, is_triggered);
CREATE INDEX IF NOT EXISTS idx_radar_deductions_user ON radar_deductions(user_id, symbol);
"""


def get_connection() -> sqlite3.Connection:
    """获取 SQLite 连接，开启 WAL 模式和外键约束"""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """初始化数据库 schema"""
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    logger.info("数据库已初始化: %s", DB_PATH)


def ensure_default_user():
    """确保默认用户存在 (开发阶段)"""
    conn = get_connection()
    cursor = conn.execute(
        "SELECT id FROM users WHERE openid = ?", ("dev_user",)
    )
    if cursor.fetchone() is None:
        conn.execute(
            "INSERT INTO users (openid, nickname) VALUES (?, ?)",
            ("dev_user", "开发者"),
        )
        conn.commit()
        logger.info("默认用户已创建: dev_user")
    conn.close()


if __name__ == "__main__":
    init_db()
    ensure_default_user()
