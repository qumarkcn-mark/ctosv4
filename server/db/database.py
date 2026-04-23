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
    trailing_stop_price REAL,   -- 台阶止损持久化（六阶段状态机使用，只上移不下移）
    entry_date TEXT,             -- 入场日期（YYYY-MM-DD），Stage 0 验证窗口计算用
    strategy_type TEXT DEFAULT '未知', -- 入场战法：战法一/战法二/未知
    m5_entry_zg REAL,           -- 入场时5分中枢ZG，结构失效判断用
    days_held INTEGER,
    updated_at DATETIME,
    UNIQUE(user_id, symbol)
);

-- 提醒规则
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    alert_type TEXT NOT NULL CHECK(alert_type IN (
        'STOP_LOSS', 'STOP_LOSS_BROKEN', 'STOP_LOSS_WARNING',
        'CHAN_THIRD_BUY', 'SIGNAL', 'REBUY', 'BREAKEVEN',
        'CHAN_30M_TOP_DIV', 'CHAN_30M_BOT_DIV', 'CHAN_ENTRY_SIGNAL',
        'STAGE_VALIDATION_FAIL', 'STAGE_TIME_EXPIRED'
    )),
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

-- 多元宇宙日志（每日分类快照 + 结算）
CREATE TABLE IF NOT EXISTS multiverse_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    
    -- 多级别结构快照
    structure_json TEXT NOT NULL,
    
    -- 各级别完全分类
    classifications_json TEXT NOT NULL,
    highlighted_json TEXT,
    
    -- 结算（次日填入）
    outcome_json TEXT,
    outcome_reason TEXT,
    outcome_price REAL,
    settlement_status TEXT DEFAULT 'PENDING',
    settled_at DATETIME,
    
    -- 评分
    day_correct INTEGER,
    m30_correct INTEGER,
    m5_correct INTEGER,
    
    -- 树结构
    parent_id INTEGER REFERENCES multiverse_snapshots(id),
    
    -- AI 复盘
    ai_review TEXT,
    
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, symbol, snapshot_date)
);

-- 注：watchlist 旧表已迁移至 watchlist_groups/watchlist_items，已从生产DB DROP，此处不再创建

-- 全局仓位战略记录
CREATE TABLE IF NOT EXISTS portfolio_strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    context_json TEXT NOT NULL,
    strategy_markdown TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_trades_user ON trades(user_id, traded_at);
CREATE INDEX IF NOT EXISTS idx_positions_user ON positions(user_id);
CREATE INDEX IF NOT EXISTS idx_alerts_user ON alerts(user_id, is_triggered);
CREATE INDEX IF NOT EXISTS idx_radar_deductions_user ON radar_deductions(user_id, symbol);
CREATE INDEX IF NOT EXISTS idx_mv_symbol_date ON multiverse_snapshots(user_id, symbol, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_portfolio_strategies_user ON portfolio_strategies(user_id, created_at);

-- 自选股分组 (支持重仓/短线/观察等自定义分组)
CREATE TABLE IF NOT EXISTS watchlist_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, name)
);

-- 自选股条目
CREATE TABLE IF NOT EXISTS watchlist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL REFERENCES watchlist_groups(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    name TEXT,
    sort_order INTEGER DEFAULT 0,
    added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(group_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_wl_groups_user ON watchlist_groups(user_id);
CREATE INDEX IF NOT EXISTS idx_wl_items_group ON watchlist_items(group_id);

"""


def get_connection() -> sqlite3.Connection:
    """获取 SQLite 连接，开启 WAL 模式和外键约束"""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def run_migrations(conn: sqlite3.Connection):
    """存量数据库迁移：只做幂等的 ALTER TABLE ADD COLUMN。

    每次新增列时在此追加一条 migration，已存在的列会被忽略。
    """
    migrations = [
        # 迁移 M001：positions 表新增 entry_date（Stage 0 验证窗口）
        "ALTER TABLE positions ADD COLUMN entry_date TEXT",
        # 迁移 M002：positions 表新增 strategy_type（入场战法记忆）
        "ALTER TABLE positions ADD COLUMN strategy_type TEXT DEFAULT '未知'",
        # 迁移 M003：positions 表新增 m5_entry_zg（5分入场中枢ZG，结构失效判断）
        "ALTER TABLE positions ADD COLUMN m5_entry_zg REAL",
        # 迁移 M004：alerts 表补充新类型（不修改 CHECK 约束，软兼容）
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
            logger.info("[迁移] 执行成功: %s", sql[:60])
        except Exception as e:
            # 列已存在时 SQLite 报 "duplicate column name"，直接忽略
            if "duplicate column" in str(e).lower():
                pass
            else:
                logger.warning("[迁移] 忽略异常: %s — %s", sql[:60], e)
    conn.commit()


def init_db():
    """初始化数据库 schema，并执行存量迁移"""
    conn = get_connection()
    conn.executescript(SCHEMA)
    conn.commit()
    run_migrations(conn)
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
