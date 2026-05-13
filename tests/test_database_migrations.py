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
    assert {"cache_key", "cache_version", "features_json", "level_chain_json", "engine_preset"}.issubset(cache_columns)


def test_run_migrations_backfills_old_paper_feature_cache_engine_preset():
    conn = make_old_alerts_conn()
    conn.execute(
        """
        CREATE TABLE paper_feature_cache (
            cache_key TEXT PRIMARY KEY,
            cache_version TEXT NOT NULL,
            symbol TEXT NOT NULL,
            as_of TEXT NOT NULL,
            level_chain_json TEXT NOT NULL,
            count INTEGER NOT NULL,
            cchan_preset TEXT NOT NULL,
            features_json TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        INSERT INTO paper_feature_cache (
            cache_key, cache_version, symbol, as_of, level_chain_json,
            count, cchan_preset, features_json
        )
        VALUES ('k1', 'v1', 'sh600519', '2026-05-12', '{}', 120, 'live_tolerant', '{}')
        """
    )
    conn.commit()
    from server.db.database import run_migrations

    run_migrations(conn)

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(paper_feature_cache)").fetchall()
    }
    assert "engine_preset" in columns
    row = conn.execute("SELECT cchan_preset, engine_preset FROM paper_feature_cache WHERE cache_key = 'k1'").fetchone()
    assert row["cchan_preset"] == "live_tolerant"
    assert row["engine_preset"] == "live_tolerant"
