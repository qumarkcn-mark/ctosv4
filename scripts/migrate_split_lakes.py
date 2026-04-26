"""
一次性迁移脚本：把旧 data/kline_lake.db 拆成 TDX 与 BaoStock 两个物理库。

迁移规则：
  - TDX lake:      freq='day' AND adjustflag='3'
  - BaoStock lake: 其他所有 K 线 + 仍有 BaoStock 数据支撑的 kline_sync_meta

旧库不会删除，迁移完成后可保留为回滚备份。
"""

import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
OLD_DB = DATA_DIR / "kline_lake.db"
TDX_DB = DATA_DIR / "tdx_lake.db"
BAOSTOCK_DB = DATA_DIR / "baostock_lake.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS klines (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol   TEXT    NOT NULL,
    freq     TEXT    NOT NULL,
    date     TEXT    NOT NULL,
    open     REAL    NOT NULL,
    high     REAL    NOT NULL,
    low      REAL    NOT NULL,
    close    REAL    NOT NULL,
    volume   REAL    DEFAULT 0,
    amount   REAL    DEFAULT 0,
    adjustflag TEXT  DEFAULT '3',
    UNIQUE(symbol, freq, date)
);
CREATE TABLE IF NOT EXISTS kline_sync_meta (
    symbol      TEXT NOT NULL,
    freq        TEXT NOT NULL,
    last_date   TEXT NOT NULL,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, freq)
);
CREATE TABLE IF NOT EXISTS tdx_sync_meta (
    symbol      TEXT NOT NULL,
    freq        TEXT NOT NULL,
    last_date   TEXT NOT NULL,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, freq)
);
CREATE INDEX IF NOT EXISTS idx_klines_symbol_freq_date ON klines(symbol, freq, date);
"""


def ensure_schema(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def migrate_tdx() -> tuple[int, int]:
    conn = sqlite3.connect(TDX_DB)
    try:
        conn.execute("ATTACH DATABASE ? AS old", (str(OLD_DB),))
        conn.execute(
            """
            INSERT OR REPLACE INTO klines
                (symbol, freq, date, open, high, low, close, volume, amount, adjustflag)
            SELECT symbol, freq, date, open, high, low, close, volume, amount, adjustflag
              FROM old.klines
             WHERE freq='day' AND adjustflag='3'
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO tdx_sync_meta (symbol, freq, last_date)
            SELECT symbol, 'day', MAX(date)
              FROM klines
             WHERE freq='day' AND adjustflag='3'
             GROUP BY symbol
            """
        )
        conn.commit()
        rows, symbols = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT symbol) FROM klines"
        ).fetchone()
        return rows, symbols
    finally:
        conn.close()


def migrate_baostock() -> tuple[int, int, int]:
    conn = sqlite3.connect(BAOSTOCK_DB)
    try:
        conn.execute("ATTACH DATABASE ? AS old", (str(OLD_DB),))
        conn.execute(
            """
            INSERT OR REPLACE INTO klines
                (symbol, freq, date, open, high, low, close, volume, amount, adjustflag)
            SELECT symbol, freq, date, open, high, low, close, volume, amount, adjustflag
              FROM old.klines
             WHERE NOT (freq='day' AND adjustflag='3')
            """
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO kline_sync_meta (symbol, freq, last_date, updated_at)
            SELECT symbol, freq, last_date, updated_at
              FROM old.kline_sync_meta
             WHERE EXISTS (
                   SELECT 1 FROM klines
                    WHERE klines.symbol = old.kline_sync_meta.symbol
                      AND klines.freq = old.kline_sync_meta.freq
             )
            """
        )
        conn.execute(
            """
            DELETE FROM kline_sync_meta
             WHERE freq='day'
               AND NOT EXISTS (
                   SELECT 1 FROM klines
                    WHERE klines.symbol = kline_sync_meta.symbol
                      AND klines.freq = kline_sync_meta.freq
                      AND klines.adjustflag='2'
               )
            """
        )
        conn.commit()
        rows, symbols = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT symbol) FROM klines"
        ).fetchone()
        meta = conn.execute("SELECT COUNT(*) FROM kline_sync_meta").fetchone()[0]
        return rows, symbols, meta
    finally:
        conn.close()


def main() -> None:
    if not OLD_DB.exists():
        raise SystemExit(f"旧数据湖不存在: {OLD_DB}")

    ensure_schema(TDX_DB)
    ensure_schema(BAOSTOCK_DB)
    tdx_rows, tdx_symbols = migrate_tdx()
    bao_rows, bao_symbols, bao_meta = migrate_baostock()

    print("迁移完成")
    print(f"  TDX:      {tdx_symbols} 只 / {tdx_rows:,} 条")
    print(f"  BaoStock: {bao_symbols} 只 / {bao_rows:,} 条 / meta {bao_meta} 条")
    print(f"  旧库保留: {OLD_DB}")


if __name__ == "__main__":
    main()
