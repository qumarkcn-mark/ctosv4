"""TDX 与 BaoStock 数据湖拆分路由测试。"""

import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.db import kline_lake


def reset_lake_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(kline_lake, "DB_PATH", str(tmp_path / "ctos.db"))
    monkeypatch.setattr(kline_lake, "TDX_LAKE_PATH", str(tmp_path / "tdx_lake.db"))
    monkeypatch.setattr(kline_lake, "BAOSTOCK_LAKE_PATH", str(tmp_path / "baostock_lake.db"))
    monkeypatch.setattr(kline_lake, "QMT_LAKE_PATH", str(tmp_path / "qmt_lake.db"))
    monkeypatch.setattr(kline_lake, "LAKE_PATH", str(tmp_path / "baostock_lake.db"))
    kline_lake._thread_local.lake_conns = {}
    kline_lake.init_lake()


def test_day_adjustflag_3_reads_tdx_lake_by_default(monkeypatch, tmp_path):
    reset_lake_paths(monkeypatch, tmp_path)

    tdx_conn = kline_lake.get_lake_write_connection("tdx")
    try:
        tdx_conn.execute(
            """
            INSERT INTO klines
                (symbol, freq, date, open, high, low, close, volume, amount, adjustflag)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("sh.600519", "day", "2026-04-24", 10, 11, 9, 10.5, 1000, 10000, "3"),
        )
        tdx_conn.commit()
    finally:
        tdx_conn.close()

    kline_lake.upsert_klines(
        "sh.600519",
        "day",
        [{"date": "2026-04-23", "open": 20, "high": 21, "low": 19, "close": 20.5}],
        adjustflag="2",
    )

    rows = kline_lake.query_klines("sh.600519", "day", adjustflag="3")

    assert [row["date"] for row in rows] == ["2026-04-24"]
    assert rows[0]["close"] == 10.5


def test_lake_stores_raw_and_qfq_same_bar_side_by_side(monkeypatch, tmp_path):
    reset_lake_paths(monkeypatch, tmp_path)

    raw = [{"date": "2026-05-22", "open": 10, "high": 11, "low": 9, "close": 10}]
    qfq = [{"date": "2026-05-22", "open": 5, "high": 5.5, "low": 4.5, "close": 5}]
    kline_lake.upsert_klines("sh.600790", "day", raw, adjustflag="3", source="tdx")
    kline_lake.upsert_klines("sh.600790", "day", qfq, adjustflag="2", source="tdx")

    assert kline_lake.query_klines("sh.600790", "day", adjustflag="3", source="tdx")[0]["close"] == 10
    assert kline_lake.query_klines("sh.600790", "day", adjustflag="2", source="tdx")[0]["close"] == 5


def test_baostock_writes_do_not_pollute_tdx_lake(monkeypatch, tmp_path):
    reset_lake_paths(monkeypatch, tmp_path)

    written = kline_lake.upsert_klines(
        "sz.000001",
        "60",
        [{"date": "2026-04-24 10:30:00", "open": 8, "high": 9, "low": 7, "close": 8.5}],
    )

    assert written == 1
    assert kline_lake.count_klines("sz.000001", "60") == 1

    tdx_conn = kline_lake.get_lake_connection("tdx")
    tdx_count = tdx_conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0]

    assert tdx_count == 0


def test_init_lake_migrates_legacy_single_lake(monkeypatch, tmp_path):
    legacy = tmp_path / "kline_lake.db"
    conn = sqlite3.connect(legacy)
    try:
        conn.executescript(kline_lake.LAKE_SCHEMA)
        conn.execute(
            """
            INSERT INTO klines
                (symbol, freq, date, open, high, low, close, volume, amount, adjustflag)
            VALUES
                ('sh.600000', 'day', '2026-04-24', 10, 11, 9, 10.5, 1000, 10000, '3'),
                ('sh.600000', '30', '2026-04-24 10:00:00', 10, 11, 9, 10.5, 1000, 10000, '2')
            """
        )
        conn.execute(
            """
            INSERT INTO kline_sync_meta (symbol, freq, last_date)
            VALUES ('sh.600000', '30', '2026-04-24')
            """
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(kline_lake, "DB_PATH", str(tmp_path / "ctos.db"))
    monkeypatch.setattr(kline_lake, "TDX_LAKE_PATH", str(tmp_path / "tdx_lake.db"))
    monkeypatch.setattr(kline_lake, "BAOSTOCK_LAKE_PATH", str(tmp_path / "baostock_lake.db"))
    monkeypatch.setattr(kline_lake, "QMT_LAKE_PATH", str(tmp_path / "qmt_lake.db"))
    monkeypatch.setattr(kline_lake, "LAKE_PATH", str(tmp_path / "baostock_lake.db"))
    kline_lake._thread_local.lake_conns = {}

    kline_lake.init_lake()

    tdx_rows = kline_lake.query_klines("sh.600000", "day", adjustflag="3")
    bao_rows = kline_lake.query_klines("sh.600000", "30", adjustflag="2")
    assert len(tdx_rows) == 1
    assert len(bao_rows) == 1


def test_concurrent_upserts_to_same_lake_are_serialized(monkeypatch, tmp_path):
    reset_lake_paths(monkeypatch, tmp_path)

    def write_symbol(index: int) -> int:
        return kline_lake.upsert_klines(
            f"sh.60{index:04d}",
            "30",
            [
                {
                    "date": f"2026-04-24 10:{index % 60:02d}:00",
                    "open": 10 + index,
                    "high": 11 + index,
                    "low": 9 + index,
                    "close": 10.5 + index,
                    "volume": 1000,
                    "amount": 10000,
                }
            ],
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        written = list(pool.map(write_symbol, range(16)))

    assert sum(written) == 16
    conn = kline_lake.get_lake_connection("baostock")
    count = conn.execute("SELECT COUNT(*) FROM klines WHERE freq='30'").fetchone()[0]
    assert count == 16


def test_lake_status_reports_split_lakes_and_cleanup_candidates(monkeypatch, tmp_path):
    reset_lake_paths(monkeypatch, tmp_path)

    legacy = tmp_path / "kline_lake.db"
    legacy.write_bytes(b"legacy")
    corrupt_dir = tmp_path / "corrupt-backups"
    corrupt_dir.mkdir()
    (corrupt_dir / "bad.db").write_bytes(b"bad")

    kline_lake.upsert_klines(
        "sh.600519",
        "day",
        [{"date": "2026-04-30", "open": 10, "high": 11, "low": 9, "close": 10.5}],
        adjustflag="3",
        source="tdx",
    )
    kline_lake.upsert_klines(
        "sh.600519",
        "5",
        [{"date": "2026-04-30 09:35:00", "open": 10, "high": 11, "low": 9, "close": 10.5}],
        adjustflag="2",
        source="baostock",
    )

    status = kline_lake.lake_status()
    sources = {item["source"]: item for item in status["sources"]}

    assert status["status"] == "ok"
    assert sources["tdx"]["rows"] == 1
    assert sources["tdx"]["role"] == "full_market_daily_fact"
    assert sources["baostock"]["freqs"][0]["freq"] == "5"
    assert sources["qmt"]["health"] == "ok"
    assert status["legacy"]["exists"] is True
    assert status["legacy"]["active"] is False
    assert status["corrupt_backups"]["size_bytes"] == 3
