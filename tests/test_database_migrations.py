"""SQLite schema migration tests."""

import os
import sqlite3
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.db.database import ALERT_TYPES, migrate_alert_type_check


def make_old_alerts_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            openid TEXT UNIQUE NOT NULL
        );
        INSERT INTO users (id, openid) VALUES (1, 'dev_user');
        CREATE TABLE positions (
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
        CREATE TABLE scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date TEXT NOT NULL,
            symbol TEXT NOT NULL,
            strategy TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE TABLE alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT NOT NULL,
            alert_type TEXT NOT NULL CHECK(alert_type IN
                ('STOP_LOSS', 'SIGNAL', 'REBUY', 'BREAKEVEN')),
            trigger_price REAL,
            trigger_direction TEXT CHECK(trigger_direction IN ('ABOVE', 'BELOW')),
            is_triggered INTEGER DEFAULT 0,
            triggered_at DATETIME,
            message TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX idx_alerts_user ON alerts(user_id, is_triggered);
        INSERT INTO alerts (
            id, user_id, symbol, alert_type, trigger_price, trigger_direction,
            is_triggered, message
        )
        VALUES (7, 1, 'sh600519', 'STOP_LOSS', 100.0, 'BELOW', 0, '旧提醒');
        """
    )
    return conn


def test_migrate_alert_type_check_preserves_rows_and_allows_new_types():
    conn = make_old_alerts_conn()

    migrate_alert_type_check(conn)

    old_row = conn.execute("SELECT * FROM alerts WHERE id = 7").fetchone()
    assert old_row["symbol"] == "sh600519"
    assert old_row["alert_type"] == "STOP_LOSS"
    assert old_row["strategy_id"] is None

    conn.execute(
        """
        INSERT INTO alerts (user_id, symbol, alert_type, message)
        VALUES (?, ?, ?, ?)
        """,
        (1, "sz000001", "CHAN_30M_BOT_DIV", "新增缠论提醒"),
    )
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 2


def test_migrate_alert_type_check_is_noop_when_schema_is_current():
    conn = make_old_alerts_conn()
    migrate_alert_type_check(conn)

    before = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='alerts'"
    ).fetchone()["sql"]

    migrate_alert_type_check(conn)

    after = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='alerts'"
    ).fetchone()["sql"]
    assert before == after
    assert all(alert_type in after for alert_type in ALERT_TYPES)
    assert "strategy_id TEXT" in after
    assert "strategy_version TEXT" in after
    assert "strategy_contract TEXT" in after


def test_init_migrations_create_coach_event_tables():
    conn = make_old_alerts_conn()
    from server.db.database import run_migrations

    run_migrations(conn)

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"coach_events", "strategy_triggers", "alert_deliveries"}.issubset(tables)


def test_init_migrations_create_daily_playbook_tables():
    conn = make_old_alerts_conn()
    conn.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            price REAL NOT NULL,
            quantity INTEGER NOT NULL,
            amount REAL NOT NULL,
            traded_at DATETIME NOT NULL
        )
        """
    )
    from server.db.database import run_migrations

    run_migrations(conn)

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    trade_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(trades)").fetchall()
    }
    item_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(daily_playbook_items)").fetchall()
    }
    assert {"daily_playbooks", "daily_playbook_items"}.issubset(tables)
    assert {"playbook_item_id", "plan_relationship", "discipline_tag", "coach_event_id"}.issubset(trade_columns)
    assert {"source", "source_json"}.issubset(item_columns)


def test_run_migrations_adds_position_entry_thesis_json():
    conn = make_old_alerts_conn()
    from server.db.database import run_migrations

    run_migrations(conn)

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(positions)").fetchall()
    }
    assert "entry_thesis_json" in columns


def test_run_migrations_create_paper_trading_tables():
    conn = make_old_alerts_conn()
    from server.db.database import run_migrations

    run_migrations(conn)

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {
        "paper_accounts",
        "paper_positions",
        "paper_replay_runs",
        "paper_decisions",
        "paper_intents",
        "paper_fills",
        "paper_feature_cache",
    }.issubset(tables)

    fill_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(paper_fills)").fetchall()
    }
    assert {"commission", "stamp_tax", "slippage", "fill_status"}.issubset(fill_columns)
    cache_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(paper_feature_cache)").fetchall()
    }
    assert {"cache_key", "cache_version", "features_json", "level_chain_json"}.issubset(cache_columns)


def test_run_migrations_create_ai_reasoning_runs_table():
    conn = make_old_alerts_conn()
    from server.db.database import run_migrations

    run_migrations(conn)

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "ai_reasoning_runs" in tables

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(ai_reasoning_runs)").fetchall()
    }
    assert {
        "structure_fingerprint",
        "transcript_json",
        "memory_context_json",
        "ai_output_json",
        "gate_result_json",
        "gate_status",
        "model_route_json",
        "replay_status",
    }.issubset(columns)

    indexes = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert "idx_ai_reasoning_runs_symbol_created" in indexes
    assert "idx_ai_reasoning_runs_fingerprint" in indexes
    assert "idx_ai_reasoning_runs_replay" in indexes


def test_run_migrations_create_stop_reduce_shadow_training_tables():
    conn = make_old_alerts_conn()
    from server.db.database import run_migrations

    run_migrations(conn)

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {
        "ai_rebalance_runs",
        "ai_rebalance_intents",
        "ai_stop_reduce_scores",
        "ai_case_memory",
        "ai_calibration_stats",
        "ai_stop_reduce_daily_runs",
        "fundamental_snapshots",
        "ai_holding_plans",
    }.issubset(tables)

    intent_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(ai_rebalance_intents)").fetchall()
    }
    assert {"source_plan_id", "idempotency_key", "conditions_json", "evidence_refs_json", "disclaimer"}.issubset(intent_columns)

    score_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(ai_stop_reduce_scores)").fetchall()
    }
    assert {"settlement_source", "settlement_prices_json", "lesson_candidate"}.issubset(score_columns)

    indexes = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert "idx_ai_case_memory_key" in indexes
    assert "idx_fundamental_snapshots_symbol" in indexes
    assert "idx_ai_holding_plans_user_date" in indexes
    assert "idx_ai_rebalance_intents_plan" in indexes
    assert "idx_ai_stop_reduce_daily_runs_user_date" in indexes


def test_schema_bootstrap_survives_old_rebalance_intents_without_source_plan_id():
    conn = make_old_alerts_conn()
    conn.executescript(
        """
        CREATE TABLE ai_rebalance_intents (
            intent_id TEXT PRIMARY KEY,
            run_id TEXT,
            user_id INTEGER NOT NULL REFERENCES users(id),
            symbol TEXT NOT NULL,
            intent_type TEXT NOT NULL,
            action TEXT NOT NULL,
            current_weight_pct REAL,
            target_weight_pct REAL,
            quantity_policy TEXT,
            idempotency_key TEXT NOT NULL UNIQUE,
            as_of TEXT NOT NULL,
            conditions_json TEXT DEFAULT '{}',
            reason_json TEXT DEFAULT '{}',
            evidence_refs_json TEXT DEFAULT '{}',
            disclaimer TEXT NOT NULL DEFAULT '仅供参考，不构成投资建议',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    from server.db.database import SCHEMA, run_migrations

    conn.executescript(SCHEMA)
    run_migrations(conn)

    intent_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(ai_rebalance_intents)").fetchall()
    }
    assert "source_plan_id" in intent_columns

    indexes = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    }
    assert "idx_ai_rebalance_intents_plan" in indexes
