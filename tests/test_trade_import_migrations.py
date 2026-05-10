import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.db.database import run_migrations


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO users (id) VALUES (1)")
    conn.execute(
        """
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT,
            quantity INTEGER NOT NULL,
            avg_cost REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            alert_type TEXT NOT NULL CHECK(alert_type IN ('STOP_LOSS')),
            trigger_price REAL,
            trigger_direction TEXT CHECK(trigger_direction IN ('ABOVE', 'BELOW')),
            is_triggered INTEGER DEFAULT 0,
            triggered_at DATETIME,
            message TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('BUY', 'SELL')),
            price REAL NOT NULL,
            quantity INTEGER NOT NULL,
            amount REAL NOT NULL,
            source TEXT DEFAULT 'MANUAL' CHECK(source IN ('VOICE', 'MANUAL', 'CSV_IMPORT')),
            traded_at DATETIME NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    return conn


def test_migration_allows_ths_screenshot_trade_source():
    conn = _conn()
    run_migrations(conn)

    trade_columns = {row["name"] for row in conn.execute("PRAGMA table_info(trades)").fetchall()}
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    assert {"broker", "is_aggregated", "import_batch_id", "import_draft_id"}.issubset(trade_columns)
    assert {"trade_import_batches", "trade_import_drafts"}.issubset(tables)

    conn.execute(
        """
        INSERT INTO trades (
            user_id, symbol, direction, price, quantity, amount, source, traded_at
        )
        VALUES (1, 'sh600519', 'BUY', 100, 100, 10000, 'THS_DAILY_SUMMARY_SCREENSHOT', '2026-05-06T15:00:00')
        """
    )
    assert conn.execute("SELECT source FROM trades").fetchone()["source"] == "THS_DAILY_SUMMARY_SCREENSHOT"
