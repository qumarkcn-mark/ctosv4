"""Phase 0 止血测试：扫描器变量和 watchlist 新 schema。"""

import os
import sqlite3
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services.chan_scanner import _scan_war1
from server.api.rotation import _build_rotation_strategy, _fetch_rows


class FakeZhongshu:
    is_sure = True
    high = 10.0
    low = 9.0


class FakeKlData:
    zs_list = [FakeZhongshu()]


def make_bar(i, close=10.5, low=10.05, volume=1000):
    return {
        "date": f"2026-01-{(i % 28) + 1:02d}",
        "open": close - 0.1,
        "high": close + 0.2,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": close * volume,
    }


def test_scan_war1_dict_rows_do_not_raise_name_error():
    rows = [make_bar(i) for i in range(70)]

    # 这里主要锁住曾经的 bug：列表推导里误用未定义 row。
    result = _scan_war1(rows, FakeKlData())

    assert result is None or result.strategy == "war1"


def test_rotation_fetch_rows_reads_watchlist_groups_schema(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE positions (
            user_id INTEGER,
            symbol TEXT,
            name TEXT,
            quantity INTEGER,
            avg_cost REAL,
            current_price REAL
        );
        CREATE TABLE watchlist_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            sort_order INTEGER
        );
        CREATE TABLE watchlist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER,
            symbol TEXT,
            name TEXT,
            sort_order INTEGER
        );
        INSERT INTO positions VALUES (1, 'sh600519', '贵州茅台', 100, 100.0, 110.0);
        INSERT INTO watchlist_groups (id, user_id, name, sort_order) VALUES (1, 1, '观察', 0);
        INSERT INTO watchlist_items (group_id, symbol, name, sort_order)
            VALUES (1, 'sh600519', '贵州茅台', 0),
                   (1, 'sz000001', '平安银行', 1);
        """
    )

    class ConnWrapper:
        def execute(self, *args, **kwargs):
            return conn.execute(*args, **kwargs)

        def close(self):
            pass

    monkeypatch.setattr("server.api.rotation.get_connection", lambda: ConnWrapper())

    positions, watchlist = _fetch_rows(1)

    assert [p["symbol"] for p in positions] == ["sh600519"]
    assert [w["symbol"] for w in watchlist] == ["sz000001"]


def test_rotation_strategy_contract_is_versioned_and_plan_only():
    strategy = _build_rotation_strategy(
        {
            "cut": [{"symbol": "sh600519"}],
            "add": [],
            "rotate": [],
        }
    )

    assert strategy["strategy_id"] == "rotation_comparison"
    assert strategy["strategy_version"] == "0.1.0"
    assert strategy["status"] == "TRIGGERED"
    assert strategy["outputs"] == ["plans"]
    assert "orders" not in strategy["outputs"]


def test_rotation_compact_contract_keeps_plan_shape(monkeypatch):
    from server.api.rotation import _build_comparison

    holdings = [
        {
            "symbol": "sh600519",
            "name": "贵州茅台",
            "sort_score": 70,
            "state_label": "中枢震荡",
            "lifecycle_node": "持仓观察",
        }
    ]
    candidates = [
        {
            "symbol": "sz000001",
            "name": "平安银行",
            "sort_score": 80,
            "state_label": "三买确认",
            "lifecycle_node": "候选观察",
        }
    ]

    comparison = _build_comparison(holdings, candidates)

    assert comparison["holdings_count"] == 1
    assert comparison["candidates_count"] == 1
    assert comparison["strongest_candidate"]["symbol"] == "sz000001"
    assert "分数只用于排序" in comparison["focus"]
