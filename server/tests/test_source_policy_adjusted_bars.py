import sqlite3
from datetime import date, timedelta

from server.db import kline_lake
from server.engines.structure.source_policy import (
    resolve_structure_source_policy,
    structure_signature_for_policy,
    query_structure_klines,
)


def _reset_lake_connections():
    kline_lake._thread_local.lake_conns = {}


def _setup_lakes(tmp_path, monkeypatch, *, formal_schema: bool = True):
    monkeypatch.setattr(kline_lake, "TDX_LAKE_PATH", str(tmp_path / "tdx_lake.db"))
    monkeypatch.setattr(kline_lake, "BAOSTOCK_LAKE_PATH", str(tmp_path / "baostock_lake.db"))
    _reset_lake_connections()
    for source in ("tdx", "baostock"):
        conn = kline_lake.get_lake_write_connection(source)
        try:
            conn.executescript(kline_lake.LAKE_SCHEMA)
            if formal_schema:
                conn.executescript(kline_lake.FORMAL_DATA_SCHEMA)
            conn.commit()
        finally:
            conn.close()
    _reset_lake_connections()


def _rows(start: str, count: int, *, close_base: float) -> list[dict]:
    start_day = date.fromisoformat(start)
    rows = []
    for idx in range(count):
        close = close_base + idx * 0.1
        rows.append(
            {
                "date": (start_day + timedelta(days=idx)).isoformat(),
                "open": close - 0.05,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": 1000 + idx,
                "amount": 10000 + idx,
                "qfq_factor": 1.0 + idx / 10000,
            }
        )
    return rows


def test_structure_policy_prefers_tdx_adjusted_bars(tmp_path, monkeypatch):
    _setup_lakes(tmp_path, monkeypatch)
    try:
        kline_lake.upsert_adjusted_bars(
            "sh.600790",
            "day",
            _rows("2026-04-01", 40, close_base=10),
            dataset="tdx_qfq",
            source="tdx",
        )
        kline_lake.upsert_adjusted_bars(
            "sh.600790",
            "day",
            _rows("2026-03-01", 40, close_base=20),
            dataset="baostock_qfq",
            source="baostock",
        )

        policy = resolve_structure_source_policy(symbol="sh.600790", level="day", limit=120)
        rows = query_structure_klines(symbol="sh.600790", level="day", limit=5, policy=policy)
        signature = structure_signature_for_policy(symbol="sh.600790", level="day", limit=5, policy=policy)

        assert policy["version"] == "structure_source_policy.v2"
        assert policy["selected"]["source"] == "tdx"
        assert policy["selected"]["storage"] == "adjusted_bars"
        assert policy["selected"]["dataset"] == "tdx_qfq"
        assert rows[-1]["date"] == "2026-05-10"
        assert rows[-1]["close"] == 13.9
        assert signature["storage"] == "adjusted_bars"
        assert signature["dataset"] == "tdx_qfq"
        assert signature["signature"]
    finally:
        _reset_lake_connections()


def test_structure_policy_falls_back_to_legacy_klines_when_formal_table_is_missing(tmp_path, monkeypatch):
    _setup_lakes(tmp_path, monkeypatch, formal_schema=False)
    try:
        kline_lake.upsert_klines(
            "sh.600790",
            "day",
            _rows("2026-04-01", 40, close_base=30),
            adjustflag="2",
            source="baostock",
        )

        policy = resolve_structure_source_policy(symbol="sh.600790", level="day", limit=120)
        rows = query_structure_klines(symbol="sh.600790", level="day", limit=5, policy=policy)
        signature = structure_signature_for_policy(symbol="sh.600790", level="day", limit=5, policy=policy)

        assert policy["selected"]["source"] == "baostock"
        assert policy["selected"]["storage"] == "legacy_klines"
        assert rows[-1]["close"] == 33.9
        assert signature["source"] == "baostock"
        assert signature["row_count"] == 5
    finally:
        _reset_lake_connections()


def test_query_adjusted_bars_returns_empty_when_formal_table_is_missing(tmp_path, monkeypatch):
    _setup_lakes(tmp_path, monkeypatch, formal_schema=False)
    try:
        assert kline_lake.query_adjusted_bars("sh.600790", "day", source="tdx") == []
        signature = kline_lake.get_adjusted_bars_window_signature("sh.600790", "day", source="tdx")
        assert signature["row_count"] == 0
        assert signature["storage"] == "adjusted_bars"
    finally:
        _reset_lake_connections()
