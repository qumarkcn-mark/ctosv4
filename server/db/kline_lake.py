"""CT-OS V4.0 K 线数据湖 — 本地 SQLite 缓存引擎

支持 5 种级别：day / 60m / 30m / 15m / 5m
读取目标：< 5ms（本地 SSD 直读）
写入策略：增量 upsert，按 (symbol, freq, date) 唯一索引去重
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from server.config import DB_PATH

logger = logging.getLogger(__name__)

# K 线数据湖专属路径（与主库分离，方便单独维护/迁移）
LAKE_PATH = str(Path(DB_PATH).parent / "kline_lake.db")

LAKE_SCHEMA = """
-- K 线主表：全品种、全级别合并存储，按 (symbol, freq, date) 唯一
CREATE TABLE IF NOT EXISTS klines (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol   TEXT    NOT NULL,    -- e.g. "sh.600519"
    freq     TEXT    NOT NULL,    -- "day" / "60" / "30" / "15" / "5" / "1"
    date     TEXT    NOT NULL,    -- "2024-01-02" 或 "2024-01-02 09:30:00"
    open     REAL    NOT NULL,
    high     REAL    NOT NULL,
    low      REAL    NOT NULL,
    close    REAL    NOT NULL,
    volume   REAL    DEFAULT 0,
    amount   REAL    DEFAULT 0,
    adjustflag TEXT  DEFAULT '3', -- 1=后复权 2=前复权 3=不复权
    UNIQUE(symbol, freq, date)
);

-- 同步元信息表：记录每只股票每个级别最后一次同步的时间
CREATE TABLE IF NOT EXISTS kline_sync_meta (
    symbol      TEXT NOT NULL,
    freq        TEXT NOT NULL,
    last_date   TEXT NOT NULL,    -- 最后同步到的日期
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, freq)
);

CREATE INDEX IF NOT EXISTS idx_klines_symbol_freq_date ON klines(symbol, freq, date);
"""


def get_lake_connection() -> sqlite3.Connection:
    """获取数据湖连接，WAL 模式加速并发读"""
    Path(LAKE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(LAKE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")   # 写入性能提升，安全性接受
    conn.execute("PRAGMA cache_size=-65536")    # 64MB query cache
    return conn


def init_lake():
    """初始化 K 线数据湖 schema"""
    conn = get_lake_connection()
    conn.executescript(LAKE_SCHEMA)
    conn.commit()
    conn.close()
    logger.info("K线数据湖已初始化: %s", LAKE_PATH)


def query_klines(
    symbol: str,
    freq: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 2000,
    adjustflag: str = "2",  # 默认前复权
) -> list[dict]:
    """
    从本地数据湖快速读取 K 线数据。
    返回按 date 正序排列的列表（最旧的在前），与 lightweight-charts 期望的格式对齐。

    Args:
        symbol: BaoStock 格式代码, 如 "sh.600519"
        freq: "day" / "60" / "30" / "15" / "5"
        start_date: "2023-01-01"（可选，不填则取最新 limit 根）
        end_date: "2024-01-01"（可选）
        limit: 最多返回条数
        adjustflag: "1"=后复权 "2"=前复权 "3"=不复权

    Returns:
        [{"date": "2024-01-02", "open": 1800.0, "high": ..., "low": ..., "close": ..., "volume": ...}, ...]
    """
    conn = get_lake_connection()
    try:
        conditions = ["symbol = ?", "freq = ?", "adjustflag = ?"]
        params: list = [symbol, freq, adjustflag]

        if start_date:
            conditions.append("date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("date <= ?")
            params.append(end_date)

        where_clause = " AND ".join(conditions)
        sql = f"""
            SELECT date, open, high, low, close, volume, amount
            FROM klines
            WHERE {where_clause}
            ORDER BY date DESC
            LIMIT {limit}
        """
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()

        # 反转为正序（最旧在前）
        result = [
            {
                "date": row["date"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "amount": row["amount"],
            }
            for row in reversed(rows)
        ]
        return result
    finally:
        conn.close()


def get_last_sync_date(symbol: str, freq: str) -> Optional[str]:
    """查询某只股票某级别最后同步的日期，用于增量更新"""
    conn = get_lake_connection()
    try:
        cursor = conn.execute(
            "SELECT last_date FROM kline_sync_meta WHERE symbol = ? AND freq = ?",
            (symbol, freq),
        )
        row = cursor.fetchone()
        return row["last_date"] if row else None
    finally:
        conn.close()


def upsert_klines(symbol: str, freq: str, rows: list[dict], adjustflag: str = "2") -> int:
    """
    增量写入 K 线数据（ON CONFLICT REPLACE）。
    返回实际写入的行数。

    rows 格式：[{"date": str, "open": float, "high": float, "low": float, "close": float, "volume": float, "amount": float}]
    """
    if not rows:
        return 0

    conn = get_lake_connection()
    try:
        data = [
            (symbol, freq, r["date"], r["open"], r["high"], r["low"],
             r["close"], r.get("volume", 0), r.get("amount", 0), adjustflag)
            for r in rows
        ]
        conn.executemany(
            """
            INSERT OR REPLACE INTO klines
                (symbol, freq, date, open, high, low, close, volume, amount, adjustflag)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            data,
        )

        # 更新 sync_meta
        last_date = max(r["date"] for r in rows)
        conn.execute(
            """
            INSERT OR REPLACE INTO kline_sync_meta (symbol, freq, last_date)
            VALUES (?, ?, ?)
            """,
            (symbol, freq, last_date),
        )
        conn.commit()
        logger.debug("upsert %d klines for %s/%s, last_date=%s", len(data), symbol, freq, last_date)
        return len(data)
    finally:
        conn.close()


def count_klines(symbol: str, freq: str) -> int:
    """查询某只股票某级别的缓存 K 线总量，用于 cold-start 检测"""
    conn = get_lake_connection()
    try:
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM klines WHERE symbol = ? AND freq = ?",
            (symbol, freq),
        )
        return cursor.fetchone()["cnt"]
    finally:
        conn.close()


if __name__ == "__main__":
    init_lake()
    print(f"数据湖初始化完成: {LAKE_PATH}")
