"""fundamental_service MVP 测试。"""

import os
import asyncio
import sqlite3
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services import fundamental_service as fs


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            settings_json TEXT
        );
        CREATE TABLE scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            strategy TEXT,
            status TEXT DEFAULT 'pending',
            score REAL,
            close REAL,
            stop_loss REAL,
            target REAL,
            rr_ratio REAL,
            atr_pct REAL,
            volume_ratio REAL,
            chan_desc TEXT,
            fundamental TEXT,
            llm_verdict TEXT,
            llm_summary TEXT,
            llm_pros TEXT,
            llm_cons TEXT,
            llm_red_flags TEXT,
            fundamental_at DATETIME,
            retry_count INTEGER DEFAULT 0
        );
        INSERT INTO users (id, settings_json) VALUES (1, '{}');
        INSERT INTO scan_results
            (id, symbol, strategy, status, score, close, stop_loss, target,
             rr_ratio, atr_pct, volume_ratio, chan_desc)
        VALUES
            (1, 'sz000001', 'war1', 'pending', 82, 10.5, 9.8, 12.0,
             2.1, 0.05, 0.7, '日线三买');
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


def test_analyze_batch_writes_ready_fallback_without_api_key(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr(fs, "get_connection", lambda: ConnWrapper(conn))
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    result = asyncio.run(fs.analyze_batch([(1, "sz000001", "war1")], concurrency=1))

    assert result[0]["status"] == "ready"
    row = conn.execute("SELECT * FROM scan_results WHERE id=1").fetchone()
    assert row["status"] == "ready"
    assert row["llm_verdict"] == "中性"
    assert "仅技术面" in row["llm_summary"]
    assert "technical_only_mvp" in row["fundamental"]
    assert "技术赔率" in row["llm_pros"]


def test_fallback_analysis_flags_wide_atr():
    context = {
        "technical": {
            "rr_ratio": 1.5,
            "atr_pct": 0.09,
        }
    }

    result = fs.fallback_analysis(context)

    assert result["verdict"] == "中性"
    assert result["cons"] == ["ATR止损幅度偏大（9.0%）"]


def test_clean_string_list_rejects_non_strings_and_limits_size():
    result = fs._clean_string_list(
        ["  利多  ", {"bad": "shape"}, "", "x" * 100, "第二", "第三", "第四", "第五", "第六"],
        max_items=5,
        max_chars=10,
    )

    assert result == ["利多", "x" * 10, "第二"]
