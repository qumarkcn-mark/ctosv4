"""Trade API entry thesis tests."""

import asyncio
import json
import os
import sqlite3
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import trades


def make_conn():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
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


class ConnWrapper:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, *args, **kwargs):
        return self.conn.execute(*args, **kwargs)

    def commit(self):
        return self.conn.commit()

    def close(self):
        pass


def test_create_buy_trade_persists_entry_thesis(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr(trades, "get_connection", lambda: ConnWrapper(conn))

    trade = trades.TradeCreate(
        symbol="sh600519",
        name="贵州茅台",
        direction="BUY",
        price=100.0,
        quantity=100,
        stop_loss_price=92.0,
        reason_category="CHAN_SIGNAL",
        reason_text="5分钟买点确认",
        trend_direction="UP",
        strategy_type="战法一",
        entry_level="5m",
        entry_zg=101.0,
        entry_zd=99.0,
        m5_entry_zg=101.0,
        initial_target=120.0,
        trigger_conditions=[{"type": "m5_buy", "status": "PASS"}],
        traded_at="2026-04-26T10:00:00",
    )

    asyncio.run(trades.create_trade(trade, user_id=1))

    row = conn.execute("SELECT * FROM positions WHERE symbol = 'sh600519'").fetchone()
    thesis = json.loads(row["entry_thesis_json"])
    assert row["strategy_type"] == "战法一"
    assert row["entry_date"] == "2026-04-26"
    assert row["m5_entry_zg"] == 101.0
    assert thesis["strategy_type"] == "战法一"
    assert thesis["entry_level"] == "5m"
    assert thesis["entry_center"]["zg"] == 101.0
    assert thesis["original_stop_loss"] == 92.0
    assert thesis["initial_target"] == 120.0
