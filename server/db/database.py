"""CT-OS V4.0 数据库初始化 — SQLite + 多用户 Ready"""

import logging
import sqlite3
from pathlib import Path
from server.config import DB_PATH

logger = logging.getLogger(__name__)

ALERT_TYPES = (
    "STOP_LOSS",
    "STOP_LOSS_BROKEN",
    "STOP_LOSS_WARNING",
    "CHAN_THIRD_BUY",
    "SIGNAL",
    "REBUY",
    "BREAKEVEN",
    "CHAN_30M_TOP_DIV",
    "CHAN_30M_BOT_DIV",
    "CHAN_ENTRY_SIGNAL",
    "STAGE_VALIDATION_FAIL",
    "STAGE_TIME_EXPIRED",
    "HOLDING_STAGE4",
    "HOLDING_STAGE5",
    "M5_STRUCTURE_BROKEN",
    "TRAILING_STOP_BROKEN",
    "SCANNER_TOP_CANDIDATE",
)

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
    playbook_item_id INTEGER,
    plan_relationship TEXT DEFAULT 'UNKNOWN',
    discipline_tag TEXT,
    coach_event_id TEXT,
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
    entry_thesis_json TEXT,      -- 入场假设：战法/级别/中枢/防守价/目标/触发条件
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
        'STAGE_VALIDATION_FAIL', 'STAGE_TIME_EXPIRED',
        'HOLDING_STAGE4', 'HOLDING_STAGE5',
        'M5_STRUCTURE_BROKEN', 'TRAILING_STOP_BROKEN',
        'SCANNER_TOP_CANDIDATE'
    )),
    trigger_price REAL,
    trigger_direction TEXT CHECK(trigger_direction IN ('ABOVE', 'BELOW')),
    is_triggered INTEGER DEFAULT 0,
    triggered_at DATETIME,
    message TEXT,
    strategy_id TEXT,
    strategy_version TEXT,
    strategy_contract TEXT,
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

-- AI Native Radar 影子系统运行记录。只供新推理闭环使用，不影响老 Radar。
CREATE TABLE IF NOT EXISTS ai_reasoning_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    mode TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    prompt_version TEXT NOT NULL,
    model_name TEXT NOT NULL,
    structure_fingerprint TEXT NOT NULL,
    transcript_json TEXT NOT NULL,
    memory_context_json TEXT,
    ai_output_json TEXT,
    gate_result_json TEXT NOT NULL,
    gate_status TEXT NOT NULL,
    replay_status TEXT NOT NULL DEFAULT 'PENDING',
    replay_score REAL,
    outcome_json TEXT,
    disclaimer TEXT NOT NULL DEFAULT '仅供参考，不构成投资建议'
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
CREATE INDEX IF NOT EXISTS idx_ai_reasoning_runs_symbol_created ON ai_reasoning_runs(symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_reasoning_runs_fingerprint ON ai_reasoning_runs(structure_fingerprint);
CREATE INDEX IF NOT EXISTS idx_ai_reasoning_runs_replay ON ai_reasoning_runs(user_id, replay_status, created_at DESC);
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

-- Coach/Event Log：策略触发、提醒候选、用户动作的统一审计线
CREATE TABLE IF NOT EXISTS coach_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT,
    occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    source TEXT NOT NULL,
    severity TEXT NOT NULL,
    dedupe_key TEXT UNIQUE NOT NULL,
    strategy_json TEXT,
    data_source_json TEXT,
    freshness_json TEXT,
    structure_ref_json TEXT,
    evidence_json TEXT,
    message_json TEXT,
    user_response_json TEXT,
    outcome_json TEXT,
    metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS strategy_triggers (
    trigger_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES coach_events(event_id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    plan_id TEXT,
    condition_id TEXT,
    condition_status TEXT NOT NULL,
    triggered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    mode TEXT,
    data_source_json TEXT,
    freshness_json TEXT,
    evidence_json TEXT,
    dedupe_key TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_deliveries (
    delivery_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES coach_events(event_id),
    alert_id INTEGER,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT,
    channel TEXT NOT NULL,
    delivery_status TEXT NOT NULL,
    attempted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    message TEXT,
    error TEXT,
    dedupe_key TEXT UNIQUE NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_coach_events_user_time ON coach_events(user_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_coach_events_symbol ON coach_events(symbol, occurred_at);
CREATE INDEX IF NOT EXISTS idx_strategy_triggers_user ON strategy_triggers(user_id, strategy_id, triggered_at);
CREATE INDEX IF NOT EXISTS idx_alert_deliveries_event ON alert_deliveries(event_id);

-- 今日作战台：盘前计划、盘中响应、盘后复盘的纪律闭环
CREATE TABLE IF NOT EXISTS daily_playbooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    trade_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'OPEN',
    generated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    source_json TEXT,
    summary_json TEXT,
    UNIQUE(user_id, trade_date)
);

CREATE TABLE IF NOT EXISTS daily_playbook_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playbook_id INTEGER NOT NULL REFERENCES daily_playbooks(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    name TEXT,
    mode TEXT NOT NULL,
    plan_id TEXT,
    strategy_id TEXT,
    status TEXT NOT NULL DEFAULT 'WATCHING',
    trigger_json TEXT,
    invalidation_json TEXT,
    radar_snapshot_json TEXT,
    response_json TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_daily_playbook_user_date ON daily_playbooks(user_id, trade_date);
CREATE INDEX IF NOT EXISTS idx_daily_playbook_items_playbook ON daily_playbook_items(playbook_id);
CREATE INDEX IF NOT EXISTS idx_daily_playbook_items_symbol ON daily_playbook_items(user_id, symbol);

-- ── 选股扫描结果表 ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scan_results (
    id              INTEGER  PRIMARY KEY AUTOINCREMENT,
    scan_date       TEXT     NOT NULL,                  -- '2026-04-24'
    symbol          TEXT     NOT NULL,
    strategy        TEXT     NOT NULL,                  -- 'war1' | 'war2'
    status          TEXT     NOT NULL DEFAULT 'pending',-- pending|analyzing|ready|failed
    -- 技术面快照（扫描时写入，不变）
    score           REAL     DEFAULT 0,
    close           REAL,
    stop_loss       REAL,
    target          REAL,
    rr_ratio        REAL,
    atr_pct         REAL,
    volume_ratio    REAL,
    chan_desc       TEXT,
    -- LLM 基本面结果（fundamental_service 写入）
    fundamental      TEXT,                               -- JSON object，原始调研/抓取数据
    llm_verdict     TEXT,                               -- '支持'|'中性'|'回避'
    llm_summary     TEXT,
    llm_pros        TEXT,                               -- JSON array
    llm_cons        TEXT,                               -- JSON array
    llm_red_flags   TEXT,                               -- JSON array
    fundamental_at  DATETIME,
    retry_count     INTEGER  DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scan_date, symbol, strategy)
);

CREATE INDEX IF NOT EXISTS idx_scan_date_status ON scan_results(scan_date, status);
CREATE INDEX IF NOT EXISTS idx_scan_symbol      ON scan_results(symbol);

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
        # 迁移 M008：positions 表新增 entry_thesis_json（统一入场假设）
        "ALTER TABLE positions ADD COLUMN entry_thesis_json TEXT",
        # 迁移 M009：trades 表新增计划关系，用于计划内/计划外复盘
        "ALTER TABLE trades ADD COLUMN playbook_item_id INTEGER",
        "ALTER TABLE trades ADD COLUMN plan_relationship TEXT DEFAULT 'UNKNOWN'",
        "ALTER TABLE trades ADD COLUMN discipline_tag TEXT",
        "ALTER TABLE trades ADD COLUMN coach_event_id TEXT",
        # 迁移 M004：alerts 表补充新类型（不修改 CHECK 约束，软兼容）
        # 迁移 M005：scan_results 表新增 fundamental（LLM 调研原始上下文）
        "ALTER TABLE scan_results ADD COLUMN fundamental TEXT",
        # 迁移 M006：alerts 表记录触发时的策略合同快照
        "ALTER TABLE alerts ADD COLUMN strategy_id TEXT",
        "ALTER TABLE alerts ADD COLUMN strategy_version TEXT",
        "ALTER TABLE alerts ADD COLUMN strategy_contract TEXT",
        # 迁移 M007：Coach/Event Log 最小三表
        """
        CREATE TABLE IF NOT EXISTS coach_events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT,
            occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            source TEXT NOT NULL,
            severity TEXT NOT NULL,
            dedupe_key TEXT UNIQUE NOT NULL,
            strategy_json TEXT,
            data_source_json TEXT,
            freshness_json TEXT,
            structure_ref_json TEXT,
            evidence_json TEXT,
            message_json TEXT,
            user_response_json TEXT,
            outcome_json TEXT,
            metadata_json TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS strategy_triggers (
            trigger_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES coach_events(event_id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT,
            strategy_id TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            plan_id TEXT,
            condition_id TEXT,
            condition_status TEXT NOT NULL,
            triggered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            mode TEXT,
            data_source_json TEXT,
            freshness_json TEXT,
            evidence_json TEXT,
            dedupe_key TEXT UNIQUE NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS alert_deliveries (
            delivery_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL REFERENCES coach_events(event_id),
            alert_id INTEGER,
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT,
            channel TEXT NOT NULL,
            delivery_status TEXT NOT NULL,
            attempted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            message TEXT,
            error TEXT,
            dedupe_key TEXT UNIQUE NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_coach_events_user_time ON coach_events(user_id, occurred_at)",
        "CREATE INDEX IF NOT EXISTS idx_coach_events_symbol ON coach_events(symbol, occurred_at)",
        "CREATE INDEX IF NOT EXISTS idx_strategy_triggers_user ON strategy_triggers(user_id, strategy_id, triggered_at)",
        "CREATE INDEX IF NOT EXISTS idx_alert_deliveries_event ON alert_deliveries(event_id)",
        # 迁移 M010：今日作战台
        """
        CREATE TABLE IF NOT EXISTS daily_playbooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            trade_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            generated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            source_json TEXT,
            summary_json TEXT,
            UNIQUE(user_id, trade_date)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS daily_playbook_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playbook_id INTEGER NOT NULL REFERENCES daily_playbooks(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT NOT NULL,
            name TEXT,
            mode TEXT NOT NULL,
            plan_id TEXT,
            strategy_id TEXT,
            status TEXT NOT NULL DEFAULT 'WATCHING',
            trigger_json TEXT,
            invalidation_json TEXT,
            radar_snapshot_json TEXT,
            response_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_daily_playbook_user_date ON daily_playbooks(user_id, trade_date)",
        "CREATE INDEX IF NOT EXISTS idx_daily_playbook_items_playbook ON daily_playbook_items(playbook_id)",
        "CREATE INDEX IF NOT EXISTS idx_daily_playbook_items_symbol ON daily_playbook_items(user_id, symbol)",
        # 迁移 M013：AI Native Radar 影子系统运行记录
        """
        CREATE TABLE IF NOT EXISTS ai_reasoning_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            mode TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            prompt_version TEXT NOT NULL,
            model_name TEXT NOT NULL,
            structure_fingerprint TEXT NOT NULL,
            transcript_json TEXT NOT NULL,
            memory_context_json TEXT,
            ai_output_json TEXT,
            gate_result_json TEXT NOT NULL,
            gate_status TEXT NOT NULL,
            replay_status TEXT NOT NULL DEFAULT 'PENDING',
            replay_score REAL,
            outcome_json TEXT,
            disclaimer TEXT NOT NULL DEFAULT '仅供参考，不构成投资建议'
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_ai_reasoning_runs_symbol_created ON ai_reasoning_runs(symbol, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_ai_reasoning_runs_fingerprint ON ai_reasoning_runs(structure_fingerprint)",
        "CREATE INDEX IF NOT EXISTS idx_ai_reasoning_runs_replay ON ai_reasoning_runs(user_id, replay_status, created_at DESC)",
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
    migrate_alert_type_check(conn)
    conn.commit()


def migrate_alert_type_check(conn: sqlite3.Connection):
    """修复 alerts.alert_type CHECK 约束漂移。

    SQLite 不能直接 ALTER CHECK，只能创建新表、复制数据、重命名。
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(alerts)").fetchall()}
    for column_name, column_type in (
        ("strategy_id", "TEXT"),
        ("strategy_version", "TEXT"),
        ("strategy_contract", "TEXT"),
    ):
        if column_name not in columns:
            conn.execute(f"ALTER TABLE alerts ADD COLUMN {column_name} {column_type}")

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='alerts'"
    ).fetchone()
    create_sql = row["sql"] if row else ""
    if all(alert_type in create_sql for alert_type in ALERT_TYPES):
        return

    logger.info("[迁移] alerts.alert_type CHECK 约束需要重建")
    allowed = ", ".join(f"'{alert_type}'" for alert_type in ALERT_TYPES)

    try:
        conn.execute("BEGIN")
        conn.execute("ALTER TABLE alerts RENAME TO alerts_old")
        conn.execute(
            f"""
            CREATE TABLE alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                symbol TEXT NOT NULL,
                alert_type TEXT NOT NULL CHECK(alert_type IN ({allowed})),
                trigger_price REAL,
                trigger_direction TEXT CHECK(trigger_direction IN ('ABOVE', 'BELOW')),
                is_triggered INTEGER DEFAULT 0,
                triggered_at DATETIME,
                message TEXT,
                strategy_id TEXT,
                strategy_version TEXT,
                strategy_contract TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            INSERT INTO alerts (
                id, user_id, symbol, alert_type, trigger_price, trigger_direction,
                is_triggered, triggered_at, message, strategy_id, strategy_version,
                strategy_contract, created_at
            )
            SELECT
                id, user_id, symbol, alert_type, trigger_price, trigger_direction,
                is_triggered, triggered_at, message, strategy_id, strategy_version,
                strategy_contract, created_at
            FROM alerts_old
            """
        )
        conn.execute("DROP TABLE alerts_old")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_user ON alerts(user_id, is_triggered)")
        conn.commit()
        logger.info("[迁移] alerts.alert_type CHECK 约束重建完成")
    except Exception:
        conn.rollback()
        raise


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
