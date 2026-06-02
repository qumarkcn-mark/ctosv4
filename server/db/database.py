"""CT-OS V4.0 数据库初始化 — SQLite + 多用户 Ready"""

import logging
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4
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

TRADE_SOURCES = (
    "VOICE",
    "MANUAL",
    "CSV_IMPORT",
    "THS_DAILY_SUMMARY_SCREENSHOT",
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
        ('VOICE', 'MANUAL', 'CSV_IMPORT', 'THS_DAILY_SUMMARY_SCREENSHOT')),
    broker TEXT,
    is_aggregated INTEGER DEFAULT 0,
    import_batch_id TEXT,
    import_draft_id INTEGER,
    plan_relationship TEXT DEFAULT 'UNKNOWN',
    discipline_tag TEXT,
    coach_event_id TEXT,
    traded_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 交易截图导入批次。AI 只写草稿，确认后才进入 trades。
CREATE TABLE IF NOT EXISTS trade_import_batches (
    batch_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    broker TEXT NOT NULL,
    import_type TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    image_path TEXT,
    image_sha256 TEXT,
    raw_vision_json TEXT DEFAULT '{}',
    error_message TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    confirmed_at DATETIME
);

CREATE TABLE IF NOT EXISTS trade_import_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL REFERENCES trade_import_batches(batch_id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    row_index INTEGER NOT NULL,
    symbol TEXT,
    name TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('BUY', 'SELL')),
    price REAL NOT NULL,
    quantity INTEGER NOT NULL,
    amount REAL,
    confidence REAL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'DRAFT',
    warnings_json TEXT DEFAULT '[]',
    raw_text TEXT,
    row_fingerprint TEXT NOT NULL,
    matched_candidates_json TEXT DEFAULT '[]',
    duplicate_ack INTEGER DEFAULT 0,
    trade_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(batch_id, row_fingerprint)
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

-- AI Native V5 CZSC-only 结构快照。V5 只保留 CZSC snapshot/context/job 表。
CREATE TABLE IF NOT EXISTS structure_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    level TEXT NOT NULL,
    engine TEXT NOT NULL DEFAULT 'czsc' CHECK(engine = 'czsc'),
    engine_version TEXT NOT NULL DEFAULT '',
    adapter_version TEXT NOT NULL DEFAULT '',
    compute_profile TEXT NOT NULL,
    data_signature TEXT NOT NULL,
    data_as_of TEXT NOT NULL DEFAULT '',
    snapshot_json TEXT NOT NULL,
    raw_bi_context_json TEXT NOT NULL DEFAULT '{}',
    structure_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'fresh',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, level, engine, compute_profile, data_signature)
);
CREATE INDEX IF NOT EXISTS idx_v5_structure_snapshots_latest
ON structure_snapshots(symbol, level, engine, compute_profile, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_v5_structure_snapshots_fingerprint
ON structure_snapshots(structure_fingerprint);

CREATE TABLE IF NOT EXISTS structure_snapshot_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    level TEXT NOT NULL,
    engine TEXT NOT NULL DEFAULT 'czsc' CHECK(engine = 'czsc'),
    compute_profile TEXT NOT NULL,
    data_signature TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    status TEXT NOT NULL DEFAULT 'PENDING',
    reason TEXT NOT NULL DEFAULT '',
    requested_by_user_id INTEGER,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    next_run_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    locked_by TEXT NOT NULL DEFAULT '',
    locked_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    result_snapshot_id TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_v5_snapshot_jobs_pick
ON structure_snapshot_jobs(status, next_run_at, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_v5_snapshot_jobs_symbol
ON structure_snapshot_jobs(symbol, level, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS ai_structure_contexts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    context_id TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    context_fingerprint TEXT NOT NULL,
    source_snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
    raw_context_json TEXT NOT NULL DEFAULT '{}',
    reasoning_json TEXT NOT NULL DEFAULT '{}',
    main_level TEXT NOT NULL DEFAULT '',
    trigger_level TEXT NOT NULL DEFAULT '',
    coach_summary TEXT NOT NULL DEFAULT '',
    background_json TEXT NOT NULL DEFAULT '{}',
    boundary_json TEXT NOT NULL DEFAULT '{}',
    summary_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'fresh',
    stale_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, symbol, context_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_v5_contexts_latest
ON ai_structure_contexts(user_id, symbol, updated_at DESC);

CREATE TABLE IF NOT EXISTS ai_structure_reasoning_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    context_id TEXT NOT NULL DEFAULT '',
    source_snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
    prompt_version TEXT NOT NULL,
    think_model TEXT NOT NULL DEFAULT '',
    summary_model TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'PENDING',
    full_reasoning_text TEXT NOT NULL DEFAULT '',
    summary_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, symbol, prompt_version, source_snapshot_ids_json)
);
CREATE INDEX IF NOT EXISTS idx_v5_reasoning_runs_latest
ON ai_structure_reasoning_runs(user_id, symbol, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_v5_reasoning_runs_context
ON ai_structure_reasoning_runs(user_id, context_id);

CREATE TABLE IF NOT EXISTS ai_trigger_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_id TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    mode TEXT NOT NULL,
    trigger_reason TEXT NOT NULL,
    decision TEXT NOT NULL,
    skip_reason TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    context_id TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ai_trigger_logs_user_symbol
ON ai_trigger_logs(user_id, symbol, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_trigger_logs_reason
ON ai_trigger_logs(trigger_reason, decision, created_at DESC);

CREATE TABLE IF NOT EXISTS ai_structure_context_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    compute_profile TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    source_snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
    priority INTEGER NOT NULL DEFAULT 50,
    status TEXT NOT NULL DEFAULT 'PENDING',
    reason TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    next_run_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    locked_by TEXT NOT NULL DEFAULT '',
    locked_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    result_context_id TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_v5_context_jobs_pick
ON ai_structure_context_jobs(status, next_run_at, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_v5_context_jobs_symbol
ON ai_structure_context_jobs(user_id, symbol, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS scenario_branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    branch_id TEXT NOT NULL UNIQUE,
    idempotency_key TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    context_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    branch_type TEXT NOT NULL,
    main_level TEXT NOT NULL DEFAULT '',
    trigger_level TEXT NOT NULL DEFAULT '',
    trigger_condition_json TEXT NOT NULL DEFAULT '{}',
    invalidate_condition_json TEXT NOT NULL DEFAULT '{}',
    next_recheck TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    source_context_version TEXT NOT NULL DEFAULT '',
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, context_id, branch_type, trigger_condition_json, invalidate_condition_json)
);
CREATE INDEX IF NOT EXISTS idx_v5_scenario_branches_context
ON scenario_branches(user_id, context_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_v5_scenario_branches_symbol
ON scenario_branches(user_id, symbol, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS ai_structure_chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    latest_context_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_v5_chat_sessions_symbol
ON ai_structure_chat_sessions(user_id, symbol, updated_at DESC);

CREATE TABLE IF NOT EXISTS ai_structure_chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    context_id TEXT NOT NULL,
    role TEXT NOT NULL,
    question_text TEXT NOT NULL DEFAULT '',
    intent_type TEXT NOT NULL DEFAULT '',
    answer_json TEXT NOT NULL DEFAULT '{}',
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    reminder_candidates_json TEXT NOT NULL DEFAULT '[]',
    risk_disclaimer TEXT NOT NULL DEFAULT '仅供参考，不构成投资建议',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_v5_chat_messages_session
ON ai_structure_chat_messages(user_id, session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ai_structure_reminder_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    alert_id INTEGER NOT NULL,
    coach_event_id TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL DEFAULT '',
    message_id TEXT NOT NULL DEFAULT '',
    context_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    trigger_price REAL NOT NULL,
    direction TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_v5_reminder_links_user_symbol
ON ai_structure_reminder_links(user_id, symbol, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS scenario_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    outcome_id TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    branch_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    outcome_score REAL,
    settlement_window TEXT NOT NULL DEFAULT '',
    trigger_price REAL,
    triggered_price REAL,
    invalidated_price REAL,
    expired_at TEXT,
    user_followed_plan INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, branch_id, settlement_window, checked_at)
);
CREATE INDEX IF NOT EXISTS idx_v5_scenario_outcomes_user_symbol
ON scenario_outcomes(user_id, symbol, checked_at DESC);

CREATE TABLE IF NOT EXISTS ai_symbol_memory_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    profile_json TEXT NOT NULL DEFAULT '{}',
    stats_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, symbol)
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_import_draft_unique
    ON trades(user_id, import_draft_id)
    WHERE import_draft_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trade_import_batches_user ON trade_import_batches(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_trade_import_drafts_batch ON trade_import_drafts(batch_id, status);
CREATE INDEX IF NOT EXISTS idx_trade_import_drafts_fingerprint ON trade_import_drafts(user_id, row_fingerprint);
CREATE INDEX IF NOT EXISTS idx_positions_user ON positions(user_id);
CREATE INDEX IF NOT EXISTS idx_alerts_user ON alerts(user_id, is_triggered);
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

-- ── Paper Trading：日内 T 模拟盘实验台 ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS paper_accounts (
    paper_account_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    cash REAL NOT NULL,
    realized_pnl REAL DEFAULT 0,
    trade_count INTEGER DEFAULT 0,
    metadata_json TEXT DEFAULT '{}',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS paper_positions (
    paper_account_id TEXT NOT NULL REFERENCES paper_accounts(paper_account_id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    total_qty INTEGER NOT NULL,
    available_qty INTEGER NOT NULL,
    protected_base_qty INTEGER NOT NULL,
    avg_cost REAL NOT NULL,
    last_price REAL DEFAULT 0,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (paper_account_id, symbol)
);

CREATE TABLE IF NOT EXISTS paper_replay_runs (
    run_id TEXT PRIMARY KEY,
    paper_account_id TEXT NOT NULL REFERENCES paper_accounts(paper_account_id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT,
    strategy_id TEXT NOT NULL,
    started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    ended_at DATETIME,
    config_json TEXT DEFAULT '{}',
    metrics_json TEXT DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'RUNNING'
);

CREATE TABLE IF NOT EXISTS paper_intents (
    intent_id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES paper_replay_runs(run_id),
    paper_account_id TEXT NOT NULL REFERENCES paper_accounts(paper_account_id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
    quantity INTEGER NOT NULL,
    status TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    linked_intent_id TEXT,
    created_at DATETIME NOT NULL,
    price_policy_json TEXT DEFAULT '{}',
    reason_json TEXT DEFAULT '{}',
    risk_checks_json TEXT DEFAULT '[]',
    simulator INTEGER NOT NULL DEFAULT 1,
    dry_run INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS paper_decisions (
    decision_id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES paper_replay_runs(run_id),
    paper_account_id TEXT NOT NULL REFERENCES paper_accounts(paper_account_id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    as_of TEXT NOT NULL,
    decision TEXT NOT NULL,
    decision_status TEXT NOT NULL,
    reason TEXT,
    intent_id TEXT REFERENCES paper_intents(intent_id),
    evidence_json TEXT DEFAULT '{}',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS paper_fills (
    fill_id TEXT PRIMARY KEY,
    intent_id TEXT NOT NULL REFERENCES paper_intents(intent_id),
    run_id TEXT REFERENCES paper_replay_runs(run_id),
    paper_account_id TEXT NOT NULL REFERENCES paper_accounts(paper_account_id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
    quantity INTEGER NOT NULL,
    fill_price REAL NOT NULL,
    amount REAL NOT NULL,
    commission REAL DEFAULT 0,
    stamp_tax REAL DEFAULT 0,
    transfer_fee REAL DEFAULT 0,
    slippage REAL DEFAULT 0,
    price_source TEXT,
    fill_status TEXT NOT NULL,
    reason TEXT,
    filled_at DATETIME NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_feature_cache (
    cache_key TEXT PRIMARY KEY,
    cache_version TEXT NOT NULL,
    symbol TEXT NOT NULL,
    as_of TEXT NOT NULL,
    level_chain_json TEXT NOT NULL,
    count INTEGER NOT NULL,
    engine_preset TEXT NOT NULL,
    features_json TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 行情数据同步批次：记录盘中/盘后数据流水线状态，支撑数据溯源和 snapshot 失效判断。
CREATE TABLE IF NOT EXISTS market_data_batches (
    batch_id      TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    mode          TEXT NOT NULL,
    started_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at   TEXT,
    symbols_count INTEGER DEFAULT 0,
    latest_day    TEXT NOT NULL DEFAULT '',
    latest_1m     TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'running',
    error_message TEXT NOT NULL DEFAULT '',
    meta_json     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_market_data_batches_status
ON market_data_batches(source, mode, status, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_paper_accounts_user ON paper_accounts(user_id);
CREATE INDEX IF NOT EXISTS idx_paper_positions_account ON paper_positions(paper_account_id);
CREATE INDEX IF NOT EXISTS idx_paper_replay_user ON paper_replay_runs(user_id, started_at);
CREATE INDEX IF NOT EXISTS idx_paper_decisions_run ON paper_decisions(run_id);
CREATE INDEX IF NOT EXISTS idx_paper_decisions_reason ON paper_decisions(user_id, symbol, reason);
CREATE INDEX IF NOT EXISTS idx_paper_intents_run ON paper_intents(run_id);
CREATE INDEX IF NOT EXISTS idx_paper_intents_symbol ON paper_intents(user_id, symbol, created_at);
CREATE INDEX IF NOT EXISTS idx_paper_fills_run ON paper_fills(run_id);
CREATE INDEX IF NOT EXISTS idx_paper_fills_symbol ON paper_fills(user_id, symbol, filled_at);
CREATE INDEX IF NOT EXISTS idx_paper_feature_cache_symbol_time ON paper_feature_cache(symbol, as_of);

"""


def _current_db_path() -> str:
    """支持测试和本地工具临时切换数据库路径。"""
    return os.getenv("CT_OS_DB_PATH") or os.getenv("DB_PATH") or DB_PATH


def get_connection() -> sqlite3.Connection:
    """获取 SQLite 连接，开启 WAL 模式和外键约束"""
    db_path = _current_db_path()
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_market_data_batch(
    *,
    source: str,
    mode: str,
    batch_id: str | None = None,
    symbols_count: int = 0,
    meta: dict | None = None,
) -> dict:
    """创建行情数据批次记录，供盘中/盘后流水线溯源。"""
    resolved_id = batch_id or f"mdb_{uuid4().hex}"
    now = datetime.now().isoformat(timespec="seconds")
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO market_data_batches (
                batch_id, source, mode, started_at, symbols_count, status, meta_json
            )
            VALUES (?, ?, ?, ?, ?, 'running', ?)
            """,
            (resolved_id, source, mode, now, int(symbols_count or 0), json.dumps(meta or {}, ensure_ascii=False)),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM market_data_batches WHERE batch_id = ?", (resolved_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def update_market_data_batch(batch_id: str, **fields) -> dict | None:
    """更新行情数据批次状态；未知字段会被忽略。"""
    allowed = {
        "finished_at",
        "symbols_count",
        "latest_day",
        "latest_1m",
        "status",
        "error_message",
        "meta_json",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return None
    if updates.get("status") in {"success", "partial", "failed", "error"} and not updates.get("finished_at"):
        updates["finished_at"] = datetime.now().isoformat(timespec="seconds")
    if isinstance(updates.get("meta_json"), dict):
        updates["meta_json"] = json.dumps(updates["meta_json"], ensure_ascii=False)
    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = list(updates.values())
    conn = get_connection()
    try:
        conn.execute(f"UPDATE market_data_batches SET {assignments} WHERE batch_id = ?", [*values, batch_id])
        conn.commit()
        row = conn.execute("SELECT * FROM market_data_batches WHERE batch_id = ?", (batch_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


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
        "ALTER TABLE trades ADD COLUMN plan_relationship TEXT DEFAULT 'UNKNOWN'",
        "ALTER TABLE trades ADD COLUMN discipline_tag TEXT",
        "ALTER TABLE trades ADD COLUMN coach_event_id TEXT",
        "ALTER TABLE trades ADD COLUMN broker TEXT",
        "ALTER TABLE trades ADD COLUMN is_aggregated INTEGER DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN import_batch_id TEXT",
        "ALTER TABLE trades ADD COLUMN import_draft_id INTEGER",
        # 迁移 M004：alerts 表补充新类型（不修改 CHECK 约束，软兼容）
        # 迁移 M005：scan_results 表新增 fundamental（LLM 调研原始上下文）
        "ALTER TABLE scan_results ADD COLUMN fundamental TEXT",
        # 迁移 M006：alerts 表记录触发时的策略合同快照
        "ALTER TABLE alerts ADD COLUMN strategy_id TEXT",
        "ALTER TABLE alerts ADD COLUMN strategy_version TEXT",
        "ALTER TABLE alerts ADD COLUMN strategy_contract TEXT",
        # 迁移 M020：AI Native V5 保存独立推演结果，结构事实和 AI 判断分离
        "ALTER TABLE ai_structure_contexts ADD COLUMN reasoning_json TEXT NOT NULL DEFAULT '{}'",
        "ALTER TABLE ai_structure_contexts ADD COLUMN main_level TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE ai_structure_contexts ADD COLUMN trigger_level TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE ai_structure_contexts ADD COLUMN coach_summary TEXT NOT NULL DEFAULT ''",
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
        """
        CREATE TABLE IF NOT EXISTS trade_import_batches (
            batch_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            broker TEXT NOT NULL,
            import_type TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            image_path TEXT,
            image_sha256 TEXT,
            raw_vision_json TEXT DEFAULT '{}',
            error_message TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            confirmed_at DATETIME
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS trade_import_drafts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id TEXT NOT NULL REFERENCES trade_import_batches(batch_id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            row_index INTEGER NOT NULL,
            symbol TEXT,
            name TEXT NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('BUY', 'SELL')),
            price REAL NOT NULL,
            quantity INTEGER NOT NULL,
            amount REAL,
            confidence REAL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'DRAFT',
            warnings_json TEXT DEFAULT '[]',
            raw_text TEXT,
            row_fingerprint TEXT NOT NULL,
            matched_candidates_json TEXT DEFAULT '[]',
            duplicate_ack INTEGER DEFAULT 0,
            trade_id INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(batch_id, row_fingerprint)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_trades_import_batch ON trades(user_id, import_batch_id)",
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_import_draft_unique
        ON trades(user_id, import_draft_id)
        WHERE import_draft_id IS NOT NULL
        """,
        "CREATE INDEX IF NOT EXISTS idx_trade_import_batches_user ON trade_import_batches(user_id, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_trade_import_drafts_batch ON trade_import_drafts(batch_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_trade_import_drafts_fingerprint ON trade_import_drafts(user_id, row_fingerprint)",
        # 迁移 M012：Paper Trading 模拟盘实验台
        """
        CREATE TABLE IF NOT EXISTS paper_accounts (
            paper_account_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            cash REAL NOT NULL,
            realized_pnl REAL DEFAULT 0,
            trade_count INTEGER DEFAULT 0,
            metadata_json TEXT DEFAULT '{}',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS paper_positions (
            paper_account_id TEXT NOT NULL REFERENCES paper_accounts(paper_account_id) ON DELETE CASCADE,
            symbol TEXT NOT NULL,
            total_qty INTEGER NOT NULL,
            available_qty INTEGER NOT NULL,
            protected_base_qty INTEGER NOT NULL,
            avg_cost REAL NOT NULL,
            last_price REAL DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (paper_account_id, symbol)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS paper_replay_runs (
            run_id TEXT PRIMARY KEY,
            paper_account_id TEXT NOT NULL REFERENCES paper_accounts(paper_account_id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT,
            strategy_id TEXT NOT NULL,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            ended_at DATETIME,
            config_json TEXT DEFAULT '{}',
            metrics_json TEXT DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'RUNNING'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS paper_intents (
            intent_id TEXT PRIMARY KEY,
            run_id TEXT REFERENCES paper_replay_runs(run_id),
            paper_account_id TEXT NOT NULL REFERENCES paper_accounts(paper_account_id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT NOT NULL,
            side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
            quantity INTEGER NOT NULL,
            status TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            strategy_id TEXT NOT NULL,
            strategy_version TEXT NOT NULL,
            linked_intent_id TEXT,
            created_at DATETIME NOT NULL,
            price_policy_json TEXT DEFAULT '{}',
            reason_json TEXT DEFAULT '{}',
            risk_checks_json TEXT DEFAULT '[]',
            simulator INTEGER NOT NULL DEFAULT 1,
            dry_run INTEGER NOT NULL DEFAULT 1
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS paper_decisions (
            decision_id TEXT PRIMARY KEY,
            run_id TEXT REFERENCES paper_replay_runs(run_id),
            paper_account_id TEXT NOT NULL REFERENCES paper_accounts(paper_account_id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT NOT NULL,
            as_of TEXT NOT NULL,
            decision TEXT NOT NULL,
            decision_status TEXT NOT NULL,
            reason TEXT,
            intent_id TEXT REFERENCES paper_intents(intent_id),
            evidence_json TEXT DEFAULT '{}',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS paper_fills (
            fill_id TEXT PRIMARY KEY,
            intent_id TEXT NOT NULL REFERENCES paper_intents(intent_id),
            run_id TEXT REFERENCES paper_replay_runs(run_id),
            paper_account_id TEXT NOT NULL REFERENCES paper_accounts(paper_account_id),
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT NOT NULL,
            side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
            quantity INTEGER NOT NULL,
            fill_price REAL NOT NULL,
            amount REAL NOT NULL,
            commission REAL DEFAULT 0,
            stamp_tax REAL DEFAULT 0,
            transfer_fee REAL DEFAULT 0,
            slippage REAL DEFAULT 0,
            price_source TEXT,
            fill_status TEXT NOT NULL,
            reason TEXT,
            filled_at DATETIME NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS paper_feature_cache (
            cache_key TEXT PRIMARY KEY,
            cache_version TEXT NOT NULL,
            symbol TEXT NOT NULL,
            as_of TEXT NOT NULL,
            level_chain_json TEXT NOT NULL,
            count INTEGER NOT NULL,
            engine_preset TEXT NOT NULL,
            features_json TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_paper_accounts_user ON paper_accounts(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_paper_positions_account ON paper_positions(paper_account_id)",
        "CREATE INDEX IF NOT EXISTS idx_paper_replay_user ON paper_replay_runs(user_id, started_at)",
        "CREATE INDEX IF NOT EXISTS idx_paper_decisions_run ON paper_decisions(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_paper_decisions_reason ON paper_decisions(user_id, symbol, reason)",
        "CREATE INDEX IF NOT EXISTS idx_paper_intents_run ON paper_intents(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_paper_intents_symbol ON paper_intents(user_id, symbol, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_paper_fills_run ON paper_fills(run_id)",
        "CREATE INDEX IF NOT EXISTS idx_paper_fills_symbol ON paper_fills(user_id, symbol, filled_at)",
        "CREATE INDEX IF NOT EXISTS idx_paper_feature_cache_symbol_time ON paper_feature_cache(symbol, as_of)",
        # 迁移 M016：AI Native V5 CZSC-only 结构快照与任务
        """
        CREATE TABLE IF NOT EXISTS structure_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id TEXT NOT NULL UNIQUE,
            symbol TEXT NOT NULL,
            level TEXT NOT NULL,
            engine TEXT NOT NULL DEFAULT 'czsc' CHECK(engine = 'czsc'),
            engine_version TEXT NOT NULL DEFAULT '',
            adapter_version TEXT NOT NULL DEFAULT '',
            compute_profile TEXT NOT NULL,
            data_signature TEXT NOT NULL,
            data_as_of TEXT NOT NULL DEFAULT '',
            snapshot_json TEXT NOT NULL,
            raw_bi_context_json TEXT NOT NULL DEFAULT '{}',
            structure_fingerprint TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'fresh',
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(symbol, level, engine, compute_profile, data_signature)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_v5_structure_snapshots_latest ON structure_snapshots(symbol, level, engine, compute_profile, updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_v5_structure_snapshots_fingerprint ON structure_snapshots(structure_fingerprint)",
        """
        CREATE TABLE IF NOT EXISTS structure_snapshot_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL UNIQUE,
            idempotency_key TEXT NOT NULL UNIQUE,
            symbol TEXT NOT NULL,
            level TEXT NOT NULL,
            engine TEXT NOT NULL DEFAULT 'czsc' CHECK(engine = 'czsc'),
            compute_profile TEXT NOT NULL,
            data_signature TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 50,
            status TEXT NOT NULL DEFAULT 'PENDING',
            reason TEXT NOT NULL DEFAULT '',
            requested_by_user_id INTEGER,
            retry_count INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 3,
            next_run_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            locked_by TEXT NOT NULL DEFAULT '',
            locked_at TEXT,
            started_at TEXT,
            finished_at TEXT,
            result_snapshot_id TEXT NOT NULL DEFAULT '',
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_v5_snapshot_jobs_pick ON structure_snapshot_jobs(status, next_run_at, priority DESC, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_v5_snapshot_jobs_symbol ON structure_snapshot_jobs(symbol, level, status, updated_at DESC)",
        # 迁移 M017：AI Native V5 用户态结构上下文与场景分支
        """
        CREATE TABLE IF NOT EXISTS ai_structure_contexts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            context_id TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            context_fingerprint TEXT NOT NULL,
            source_snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
            raw_context_json TEXT NOT NULL DEFAULT '{}',
            reasoning_json TEXT NOT NULL DEFAULT '{}',
            main_level TEXT NOT NULL DEFAULT '',
            trigger_level TEXT NOT NULL DEFAULT '',
            coach_summary TEXT NOT NULL DEFAULT '',
            background_json TEXT NOT NULL DEFAULT '{}',
            boundary_json TEXT NOT NULL DEFAULT '{}',
            summary_text TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'fresh',
            stale_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, symbol, context_fingerprint)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_v5_contexts_latest ON ai_structure_contexts(user_id, symbol, updated_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS ai_structure_reasoning_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT NOT NULL,
            context_id TEXT NOT NULL DEFAULT '',
            source_snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
            prompt_version TEXT NOT NULL,
            think_model TEXT NOT NULL DEFAULT '',
            summary_model TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'PENDING',
            full_reasoning_text TEXT NOT NULL DEFAULT '',
            summary_json TEXT NOT NULL DEFAULT '{}',
            error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, symbol, prompt_version, source_snapshot_ids_json)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_v5_reasoning_runs_latest ON ai_structure_reasoning_runs(user_id, symbol, updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_v5_reasoning_runs_context ON ai_structure_reasoning_runs(user_id, context_id)",
        """
        CREATE TABLE IF NOT EXISTS ai_trigger_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trigger_id TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT NOT NULL,
            mode TEXT NOT NULL,
            trigger_reason TEXT NOT NULL,
            decision TEXT NOT NULL,
            skip_reason TEXT NOT NULL DEFAULT '',
            run_id TEXT NOT NULL DEFAULT '',
            context_id TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_ai_trigger_logs_user_symbol ON ai_trigger_logs(user_id, symbol, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_ai_trigger_logs_reason ON ai_trigger_logs(trigger_reason, decision, created_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS ai_structure_context_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL UNIQUE,
            idempotency_key TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT NOT NULL,
            compute_profile TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            source_snapshot_ids_json TEXT NOT NULL DEFAULT '[]',
            priority INTEGER NOT NULL DEFAULT 50,
            status TEXT NOT NULL DEFAULT 'PENDING',
            reason TEXT NOT NULL DEFAULT '',
            retry_count INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 3,
            next_run_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            locked_by TEXT NOT NULL DEFAULT '',
            locked_at TEXT,
            started_at TEXT,
            finished_at TEXT,
            result_context_id TEXT NOT NULL DEFAULT '',
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_v5_context_jobs_pick ON ai_structure_context_jobs(status, next_run_at, priority DESC, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_v5_context_jobs_symbol ON ai_structure_context_jobs(user_id, symbol, status, updated_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS scenario_branches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branch_id TEXT NOT NULL UNIQUE,
            idempotency_key TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            context_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            branch_type TEXT NOT NULL,
            main_level TEXT NOT NULL DEFAULT '',
            trigger_level TEXT NOT NULL DEFAULT '',
            trigger_condition_json TEXT NOT NULL DEFAULT '{}',
            invalidate_condition_json TEXT NOT NULL DEFAULT '{}',
            next_recheck TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            source_context_version TEXT NOT NULL DEFAULT '',
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, context_id, branch_type, trigger_condition_json, invalidate_condition_json)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_v5_scenario_branches_context ON scenario_branches(user_id, context_id, status, updated_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_v5_scenario_branches_symbol ON scenario_branches(user_id, symbol, status, updated_at DESC)",
        # 迁移 M018：AI Native V5 问答会话和消息
        """
        CREATE TABLE IF NOT EXISTS ai_structure_chat_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT NOT NULL,
            latest_context_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_v5_chat_sessions_symbol ON ai_structure_chat_sessions(user_id, symbol, updated_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS ai_structure_chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT NOT NULL,
            context_id TEXT NOT NULL,
            role TEXT NOT NULL,
            question_text TEXT NOT NULL DEFAULT '',
            intent_type TEXT NOT NULL DEFAULT '',
            answer_json TEXT NOT NULL DEFAULT '{}',
            evidence_refs_json TEXT NOT NULL DEFAULT '[]',
            reminder_candidates_json TEXT NOT NULL DEFAULT '[]',
            risk_disclaimer TEXT NOT NULL DEFAULT '仅供参考，不构成投资建议',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_v5_chat_messages_session ON ai_structure_chat_messages(user_id, session_id, created_at DESC)",
        # 迁移 M019：AI Native V5 提醒桥接、分支结算与单票记忆
        """
        CREATE TABLE IF NOT EXISTS ai_structure_reminder_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dedupe_key TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT NOT NULL,
            alert_id INTEGER NOT NULL,
            coach_event_id TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL DEFAULT '',
            context_id TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            trigger_price REAL NOT NULL,
            direction TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_v5_reminder_links_user_symbol ON ai_structure_reminder_links(user_id, symbol, status, updated_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS scenario_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outcome_id TEXT NOT NULL UNIQUE,
            user_id INTEGER NOT NULL REFERENCES users(id),
            branch_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            outcome TEXT NOT NULL,
            outcome_score REAL,
            settlement_window TEXT NOT NULL DEFAULT '',
            trigger_price REAL,
            triggered_price REAL,
            invalidated_price REAL,
            expired_at TEXT,
            user_followed_plan INTEGER,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, branch_id, settlement_window, checked_at)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_v5_scenario_outcomes_user_symbol ON scenario_outcomes(user_id, symbol, checked_at DESC)",
        """
        CREATE TABLE IF NOT EXISTS ai_symbol_memory_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT NOT NULL,
            profile_json TEXT NOT NULL DEFAULT '{}',
            stats_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, symbol)
        )
        """,
        # 迁移 M021：T+0 做T教练状态缓存 + watchlist 扩展
        """
        CREATE TABLE IF NOT EXISTS t0_state_cache (
            user_id          INTEGER NOT NULL,
            symbol           TEXT NOT NULL,
            state            TEXT NOT NULL DEFAULT 'IDLE',
            pivot_zd         REAL,
            pivot_zg         REAL,
            entry_price      REAL,
            target_price     REAL,
            stop_structural  REAL,
            stop_catastrophic REAL,
            t0_qty           INTEGER DEFAULT 0,
            friction_per_share REAL,
            is_grid_viable   INTEGER DEFAULT 0,
            daily_pnl        REAL DEFAULT 0,
            daily_trades     INTEGER DEFAULT 0,
            daily_stop_count INTEGER DEFAULT 0,
            signal           TEXT,
            signal_price     REAL,
            reason           TEXT DEFAULT '',
            state_json       TEXT DEFAULT '{}',
            updated_at       TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (user_id, symbol)
        )
        """,
        "ALTER TABLE watchlist_items ADD COLUMN t0_enabled INTEGER DEFAULT 0",
        "ALTER TABLE watchlist_items ADD COLUMN t0_qty INTEGER DEFAULT 0",
        """
        CREATE TABLE IF NOT EXISTS market_data_batches (
            batch_id      TEXT PRIMARY KEY,
            source        TEXT NOT NULL,
            mode          TEXT NOT NULL,
            started_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at   TEXT,
            symbols_count INTEGER DEFAULT 0,
            latest_day    TEXT NOT NULL DEFAULT '',
            latest_1m     TEXT NOT NULL DEFAULT '',
            status        TEXT NOT NULL DEFAULT 'running',
            error_message TEXT NOT NULL DEFAULT '',
            meta_json     TEXT NOT NULL DEFAULT '{}'
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_market_data_batches_status
        ON market_data_batches(source, mode, status, started_at DESC)
        """,
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
    migrate_paper_feature_cache_engine_preset(conn)
    conn.commit()
    migrate_trade_source_check(conn)
    conn.commit()
    migrate_alert_type_check(conn)
    conn.commit()


def migrate_paper_feature_cache_engine_preset(conn: sqlite3.Connection):
    """Backfill paper_feature_cache.engine_preset for databases created before CZSC-only naming."""
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='paper_feature_cache'"
    ).fetchone()
    if not table:
        return
    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(paper_feature_cache)").fetchall()
    }
    if "engine_preset" not in columns:
        conn.execute("ALTER TABLE paper_feature_cache ADD COLUMN engine_preset TEXT")
        if "cchan_preset" in columns:
            conn.execute(
                """
                UPDATE paper_feature_cache
                   SET engine_preset = COALESCE(NULLIF(cchan_preset, ''), 'live_tolerant')
                 WHERE engine_preset IS NULL OR engine_preset = ''
                """
            )
        else:
            conn.execute(
                """
                UPDATE paper_feature_cache
                   SET engine_preset = 'live_tolerant'
                 WHERE engine_preset IS NULL OR engine_preset = ''
                """
            )


def migrate_trade_source_check(conn: sqlite3.Connection):
    """修复 trades.source CHECK 约束漂移。

    SQLite 不能直接修改 CHECK；截图导入新增 source 时必须重建 trades 表。
    """
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trades'"
    ).fetchone()
    if not table:
        return

    desired_columns = {
        "broker": "TEXT",
        "is_aggregated": "INTEGER DEFAULT 0",
        "import_batch_id": "TEXT",
        "import_draft_id": "INTEGER",
    }
    existing_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(trades)").fetchall()
    }
    for column_name, column_type in desired_columns.items():
        if column_name not in existing_columns:
            try:
                conn.execute(f"ALTER TABLE trades ADD COLUMN {column_name} {column_type}")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='trades'"
    ).fetchone()
    create_sql = row["sql"] if row else ""
    if all(source in create_sql for source in TRADE_SOURCES):
        return

    logger.info("[迁移] trades.source CHECK 约束需要重建")
    allowed = ", ".join(f"'{source}'" for source in TRADE_SOURCES)

    columns = [
        ("id", "INTEGER PRIMARY KEY AUTOINCREMENT"),
        ("user_id", "INTEGER NOT NULL REFERENCES users(id)"),
        ("symbol", "TEXT NOT NULL"),
        ("name", "TEXT"),
        ("direction", "TEXT NOT NULL CHECK(direction IN ('BUY', 'SELL'))"),
        ("price", "REAL NOT NULL"),
        ("quantity", "INTEGER NOT NULL"),
        ("amount", "REAL NOT NULL"),
        ("stop_loss_price", "REAL"),
        ("reason_text", "TEXT"),
        ("reason_category", "TEXT CHECK(reason_category IN ('CHAN_SIGNAL', 'FRIEND_TIP', 'FEELING', 'OTHER'))"),
        ("trend_direction", "TEXT"),
        ("source", f"TEXT DEFAULT 'MANUAL' CHECK(source IN ({allowed}))"),
        ("broker", "TEXT"),
        ("is_aggregated", "INTEGER DEFAULT 0"),
        ("import_batch_id", "TEXT"),
        ("import_draft_id", "INTEGER"),
        ("plan_relationship", "TEXT DEFAULT 'UNKNOWN'"),
        ("discipline_tag", "TEXT"),
        ("coach_event_id", "TEXT"),
        ("traded_at", "DATETIME NOT NULL"),
        ("created_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
    ]

    try:
        conn.execute("BEGIN")
        conn.execute("ALTER TABLE trades RENAME TO trades_old")
        conn.execute(
            "CREATE TABLE trades ("
            + ", ".join(f"{name} {definition}" for name, definition in columns)
            + ")"
        )
        old_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(trades_old)").fetchall()
        }
        copy_columns = [name for name, _ in columns if name in old_columns]
        conn.execute(
            f"""
            INSERT INTO trades ({", ".join(copy_columns)})
            SELECT {", ".join(copy_columns)}
            FROM trades_old
            """
        )
        conn.execute("DROP TABLE trades_old")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_user ON trades(user_id, traded_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_import_batch ON trades(user_id, import_batch_id)")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_import_draft_unique
            ON trades(user_id, import_draft_id)
            WHERE import_draft_id IS NOT NULL
            """
        )
        conn.commit()
        logger.info("[迁移] trades.source CHECK 约束重建完成")
    except Exception:
        conn.rollback()
        raise


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
