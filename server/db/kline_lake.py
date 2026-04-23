"""CT-OS V4.0 K 线数据湖 — 本地 SQLite 缓存引擎

支持 5 种级别：day / 60m / 30m / 15m / 5m
读取目标：< 5ms（本地 SSD 直读）
写入策略：增量 upsert，按 (symbol, freq, date) 唯一索引去重
"""

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from server.config import DB_PATH

logger = logging.getLogger(__name__)

# ── 线程本地连接池 ─────────────────────────────────────────────────────────────
# 问题：每次 get_lake_connection() 都新建连接，并发线程同时执行
#       PRAGMA journal_mode=WAL 时，SQLite 需要创建 -shm 共享内存映射文件。
#       多线程同时首次打开 430MB 数据库，-shm 创建存在竞态，导致 "disk I/O error"。
# 方案：每个线程复用同一个连接（thread-local），只在首次创建时进行 WAL 初始化。
#       写入时（upsert_klines）仍使用独立短连接，确保线程安全。
_thread_local = threading.local()
_wal_init_lock = threading.Lock()  # 串行化首次 WAL 初始化，消除竞态

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


def _make_fresh_connection(wal: bool = True) -> sqlite3.Connection:
    """创建一个全新的数据湖连接，并完成 WAL / PRAGMA 初始化。"""
    Path(LAKE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(LAKE_PATH, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    if wal:
        try:
            # 首次 WAL 初始化时持锁，防止多线程同时创建 -shm 文件产生竞态
            with _wal_init_lock:
                conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            # WAL 在某些文件系统（网络盘/沙箱挂载）不可用，降级为 DELETE
            logger.warning("kline_lake WAL 模式不可用，降级为 DELETE: %s", exc)
            try:
                conn.execute("PRAGMA journal_mode=DELETE")
            except Exception:
                pass
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-65536")   # 64MB query cache
    return conn


def get_lake_connection() -> sqlite3.Connection:
    """
    获取当前线程的数据湖连接（线程本地复用）。

    每个工作线程只在首次调用时创建连接，后续复用，避免并发
    PRAGMA journal_mode=WAL 时的 -shm 文件竞态（disk I/O error）。
    """
    conn: Optional[sqlite3.Connection] = getattr(_thread_local, "lake_conn", None)
    if conn is not None:
        # 检查连接是否仍有效（被关闭或数据库文件被替换）
        try:
            conn.execute("SELECT 1")
            return conn
        except Exception:
            pass  # 连接已失效，重建

    conn = _make_fresh_connection()
    _thread_local.lake_conn = conn
    return conn


def get_lake_write_connection() -> sqlite3.Connection:
    """
    获取用于写入的独立短连接（不复用线程本地连接）。
    写操作完成后调用方必须 commit() + close()。
    """
    return _make_fresh_connection()


def init_lake():
    """初始化 K 线数据湖 schema"""
    conn = get_lake_write_connection()
    try:
        conn.executescript(LAKE_SCHEMA)
        conn.commit()
    finally:
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
    # 使用线程本地复用连接（读操作，不 close）
    conn = get_lake_connection()
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


def get_last_sync_date(symbol: str, freq: str) -> Optional[str]:
    """查询某只股票某级别最后同步的日期，用于增量更新（线程本地复用连接）"""
    conn = get_lake_connection()
    cursor = conn.execute(
        "SELECT last_date FROM kline_sync_meta WHERE symbol = ? AND freq = ?",
        (symbol, freq),
    )
    row = cursor.fetchone()
    return row["last_date"] if row else None


def upsert_klines(symbol: str, freq: str, rows: list[dict], adjustflag: str = "2", update_meta: bool = True) -> int:
    """
    增量写入 K 线数据（ON CONFLICT REPLACE）。
    返回实际写入的行数。

    rows 格式：[{"date": str, "open": float, "high": float, "low": float, "close": float, "volume": float, "amount": float}]
    """
    if not rows:
        return 0

    # 写入使用独立短连接（不污染线程本地读连接的事务状态）
    conn = get_lake_write_connection()
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
        if update_meta:
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
    """查询某只股票某级别的缓存 K 线总量，用于 cold-start 检测（线程本地复用连接）"""
    conn = get_lake_connection()
    cursor = conn.execute(
        "SELECT COUNT(*) as cnt FROM klines WHERE symbol = ? AND freq = ?",
        (symbol, freq),
    )
    return cursor.fetchone()["cnt"]


if __name__ == "__main__":
    init_lake()
    print(f"数据湖初始化完成: {LAKE_PATH}")
