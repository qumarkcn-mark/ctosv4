"""Position API contract tests."""

import os
import sqlite3
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import positions


class ConnWrapper:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, *args, **kwargs):
        return self.conn.execute(*args, **kwargs)

    def close(self):
        pass


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            name TEXT,
            quantity INTEGER DEFAULT 0,
            avg_cost REAL DEFAULT 0
        )
        """
    )
    return conn


def test_get_position_returns_empty_shape_for_non_holding(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr(positions, "get_connection", lambda: ConnWrapper(conn))

    response = positions.get_position("sh600519", user_id=1)

    assert response == {
        "symbol": "sh600519",
        "user_id": 1,
        "quantity": 0,
        "is_holding": False,
    }


def test_get_position_accepts_canonical_symbol_for_compact_row(monkeypatch):
    conn = make_conn()
    conn.execute(
        "INSERT INTO positions (user_id, symbol, name, quantity, avg_cost) VALUES (?, ?, ?, ?, ?)",
        (1, "sh600519", "贵州茅台", 100, 1600.0),
    )
    monkeypatch.setattr(positions, "get_connection", lambda: ConnWrapper(conn))

    response = positions.get_position("sh.600519", user_id=1)

    assert response["is_holding"] is True
    assert response["symbol"] == "sh600519"
    assert response["quantity"] == 100
