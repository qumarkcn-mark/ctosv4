"""Entry thesis persistence tests."""

import json
import os
import sqlite3
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services.entry_thesis import build_entry_thesis_from_trade
from server.services.position_calc import recalculate_position


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT,
            direction TEXT NOT NULL,
            price REAL NOT NULL,
            quantity INTEGER NOT NULL,
            amount REAL NOT NULL,
            stop_loss_price REAL,
            reason_text TEXT,
            reason_category TEXT,
            trend_direction TEXT,
            source TEXT DEFAULT 'MANUAL',
            traded_at DATETIME NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT,
            quantity INTEGER NOT NULL,
            avg_cost REAL NOT NULL,
            current_price REAL,
            unrealized_pnl REAL,
            stop_loss_price REAL,
            trailing_stop_price REAL,
            entry_date TEXT,
            strategy_type TEXT DEFAULT '未知',
            m5_entry_zg REAL,
            entry_thesis_json TEXT,
            days_held INTEGER,
            updated_at DATETIME,
            UNIQUE(user_id, symbol)
        );
        """
    )
    return conn


def test_build_entry_thesis_preserves_structured_entry_fields():
    thesis = build_entry_thesis_from_trade(
        trade_id=7,
        symbol="sh600519",
        source="MANUAL",
        traded_at="2026-04-26T10:00:00",
        strategy_type="战法一",
        entry_level="5m",
        entry_zg=101.0,
        entry_zd=99.0,
        m5_entry_zg=101.0,
        original_stop_loss=95.0,
        initial_target=120.0,
        trigger_conditions=[{"type": "m5_buy", "status": "PASS"}],
    )

    assert thesis["strategy_type"] == "战法一"
    assert thesis["entry_level"] == "5m"
    assert thesis["entry_center"] == {"zg": 101.0, "zd": 99.0, "m5_zg": 101.0}
    assert thesis["original_stop_loss"] == 95.0
    assert thesis["initial_target"] == 120.0
    assert thesis["trigger_conditions"][0]["type"] == "m5_buy"
    assert thesis["degradation"] == []


def test_recalculate_position_creates_unknown_thesis_for_csv_like_buy():
    conn = make_conn()
    conn.execute(
        """
        INSERT INTO trades (
            user_id, symbol, name, direction, price, quantity, amount,
            stop_loss_price, source, traded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, "sz000001", "平安银行", "BUY", 10.0, 100, 1000.0, 9.2, "CSV_IMPORT", "2026-04-26T10:00:00"),
    )

    recalculate_position(conn, 1, "sz000001")

    row = conn.execute("SELECT * FROM positions WHERE symbol='sz000001'").fetchone()
    thesis = json.loads(row["entry_thesis_json"])
    assert row["strategy_type"] == "未知"
    assert row["entry_date"] == "2026-04-26"
    assert thesis["source"] == "CSV_IMPORT"
    assert thesis["strategy_type"] == "未知"
    assert "missing_structure" in thesis["degradation"]
    assert thesis["original_stop_loss"] == 9.2
