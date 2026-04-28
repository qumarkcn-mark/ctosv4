"""CT-OS V4.0 K 线数据湖 — 本地 SQLite 缓存引擎

支持 5 种级别：day / 60m / 30m / 15m / 5m
读取目标：< 5ms（本地 SSD 直读）
写入策略：增量 upsert，按 (symbol, freq, date) 唯一索引去重
"""

import logging
import sqlite3
import threading
from pathlib import Path
from typing import Literal, Optional

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
_write_locks = {
    "tdx": threading.Lock(),
    "baostock": threading.Lock(),
    "qmt": threading.Lock(),
}

# K 线数据湖拆分：
#   TDX_LAKE_PATH      = 全市场日线事实源（freq=day, adjustflag=3），供 scanner 使用
#   BAOSTOCK_LAKE_PATH = BaoStock 多级别缓存（day/week/60/30/15/5），供缠论/持仓/价格使用
#   QMT_LAKE_PATH      = QMT 实时分钟收线缓存（实价、不复权），供盘中雷达预览使用
LakeSource = Literal["tdx", "baostock", "qmt"]
TDX_LAKE_PATH = str(Path(DB_PATH).parent / "tdx_lake.db")
BAOSTOCK_LAKE_PATH = str(Path(DB_PATH).parent / "baostock_lake.db")
QMT_LAKE_PATH = str(Path(DB_PATH).parent / "qmt_lake.db")

# 兼容旧导入：历史代码里的 LAKE_PATH 代表 BaoStock 多级别缓存。
LAKE_PATH = BAOSTOCK_LAKE_PATH

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

-- TDX 专属同步元信息表，避免污染 BaoStock 多级别缓存的同步状态。
CREATE TABLE IF NOT EXISTS tdx_sync_meta (
    symbol      TEXT NOT NULL,
    freq        TEXT NOT NULL,
    last_date   TEXT NOT NULL,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, freq)
);

CREATE INDEX IF NOT EXISTS idx_klines_symbol_freq_date ON klines(symbol, freq, date);
"""


def get_lake_path(source: LakeSource = "baostock") -> str:
    """返回指定数据源的数据湖路径。"""
    if source == "tdx":
        return TDX_LAKE_PATH
    if source == "baostock":
        return BAOSTOCK_LAKE_PATH
    if source == "qmt":
        return QMT_LAKE_PATH
    raise ValueError(f"未知数据湖来源: {source}")


def _infer_read_source(freq: str, adjustflag: str, source: Optional[LakeSource]) -> LakeSource:
    """读路径默认按业务语义路由：TDX 只承载不复权日线。"""
    if source:
        return source
    if freq == "day" and adjustflag == "3":
        return "tdx"
    return "baostock"


def _make_fresh_connection(source: LakeSource = "baostock", wal: bool = True) -> sqlite3.Connection:
    """创建一个全新的数据湖连接，并完成 WAL / PRAGMA 初始化。"""
    lake_path = get_lake_path(source)
    Path(lake_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(lake_path, check_same_thread=False, timeout=15)
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


def get_lake_connection(source: LakeSource = "baostock") -> sqlite3.Connection:
    """
    获取当前线程的数据湖连接（线程本地复用）。

    每个工作线程只在首次调用时创建连接，后续复用，避免并发
    PRAGMA journal_mode=WAL 时的 -shm 文件竞态（disk I/O error）。
    """
    conns: dict[str, sqlite3.Connection] = getattr(_thread_local, "lake_conns", {})
    conn: Optional[sqlite3.Connection] = conns.get(source)
    if conn is not None:
        # 检查连接是否仍有效（被关闭或数据库文件被替换）
        try:
            conn.execute("SELECT 1")
            return conn
        except Exception:
            pass  # 连接已失效，重建

    conn = _make_fresh_connection(source)
    conns[source] = conn
    _thread_local.lake_conns = conns
    return conn


def get_lake_write_connection(source: LakeSource = "baostock") -> sqlite3.Connection:
    """
    获取用于写入的独立短连接（不复用线程本地连接）。
    写操作完成后调用方必须 commit() + close()。
    """
    return _make_fresh_connection(source)


def init_lake():
    """初始化 TDX、BaoStock、QMT 三个 K 线数据湖 schema。"""
    for source in ("tdx", "baostock", "qmt"):
        conn = get_lake_write_connection(source)
        try:
            conn.executescript(LAKE_SCHEMA)
            conn.commit()
        finally:
            conn.close()
        logger.info("K线数据湖已初始化[%s]: %s", source, get_lake_path(source))
    _maybe_migrate_legacy_lake()


def _maybe_migrate_legacy_lake() -> None:
    """首次启动时把旧单库拆到新双库，避免部署后历史 K 线不可见。"""
    old_path = Path(DB_PATH).parent / "kline_lake.db"
    if not old_path.exists():
        return
    if _lake_row_count("tdx") > 0 or _lake_row_count("baostock") > 0:
        return

    logger.warning("检测到旧 K 线数据湖，开始自动拆分迁移: %s", old_path)
    tdx_conn = get_lake_write_connection("tdx")
    bao_conn = get_lake_write_connection("baostock")
    try:
        for conn in (tdx_conn, bao_conn):
            conn.execute("ATTACH DATABASE ? AS old_lake", (str(old_path),))

        tdx_conn.execute(
            """
            INSERT OR REPLACE INTO klines
                (symbol, freq, date, open, high, low, close, volume, amount, adjustflag)
            SELECT symbol, freq, date, open, high, low, close, volume, amount, adjustflag
              FROM old_lake.klines
             WHERE freq='day' AND adjustflag='3'
            """
        )
        tdx_conn.execute(
            """
            INSERT OR REPLACE INTO tdx_sync_meta (symbol, freq, last_date)
            SELECT symbol, 'day', MAX(date)
              FROM klines
             WHERE freq='day' AND adjustflag='3'
             GROUP BY symbol
            """
        )

        bao_conn.execute(
            """
            INSERT OR REPLACE INTO klines
                (symbol, freq, date, open, high, low, close, volume, amount, adjustflag)
            SELECT symbol, freq, date, open, high, low, close, volume, amount, adjustflag
              FROM old_lake.klines
             WHERE NOT (freq='day' AND adjustflag='3')
            """
        )
        bao_conn.execute(
            """
            INSERT OR REPLACE INTO kline_sync_meta (symbol, freq, last_date, updated_at)
            SELECT symbol, freq, last_date, updated_at
              FROM old_lake.kline_sync_meta
             WHERE EXISTS (
                   SELECT 1 FROM klines
                    WHERE klines.symbol = old_lake.kline_sync_meta.symbol
                      AND klines.freq = old_lake.kline_sync_meta.freq
             )
            """
        )

        tdx_conn.commit()
        bao_conn.commit()
        logger.warning(
            "旧 K 线数据湖自动迁移完成: tdx=%d, baostock=%d",
            _lake_row_count("tdx"),
            _lake_row_count("baostock"),
        )
    except sqlite3.Error:
        tdx_conn.rollback()
        bao_conn.rollback()
        logger.exception("旧 K 线数据湖自动迁移失败")
        raise
    finally:
        tdx_conn.close()
        bao_conn.close()


def _lake_row_count(source: LakeSource) -> int:
    conn = get_lake_write_connection(source)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0])
    finally:
        conn.close()


def query_klines(
    symbol: str,
    freq: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 2000,
    adjustflag: str = "2",  # 默认前复权
    source: Optional[LakeSource] = None,
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
    conn = get_lake_connection(_infer_read_source(freq, adjustflag, source))
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


def get_last_sync_date(symbol: str, freq: str, source: LakeSource = "baostock") -> Optional[str]:
    """查询某只股票某级别最后同步的日期，用于增量更新（线程本地复用连接）"""
    conn = get_lake_connection(source)
    cursor = conn.execute(
        "SELECT last_date FROM kline_sync_meta WHERE symbol = ? AND freq = ?",
        (symbol, freq),
    )
    row = cursor.fetchone()
    return row["last_date"] if row else None


def upsert_klines(
    symbol: str,
    freq: str,
    rows: list[dict],
    adjustflag: str = "2",
    update_meta: bool = True,
    source: LakeSource = "baostock",
) -> int:
    """
    增量写入 K 线数据（ON CONFLICT REPLACE）。
    返回实际写入的行数。

    rows 格式：[{"date": str, "open": float, "high": float, "low": float, "close": float, "volume": float, "amount": float}]
    """
    if not rows:
        return 0

    # 写入使用独立短连接；同一物理 lake 内串行写，避免并发冷启动拉取互抢 SQLite 写锁。
    with _write_locks[source]:
        conn = get_lake_write_connection(source)
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


def count_klines(symbol: str, freq: str, source: LakeSource = "baostock") -> int:
    """查询某只股票某级别的缓存 K 线总量，用于 cold-start 检测（线程本地复用连接）"""
    conn = get_lake_connection(source)
    cursor = conn.execute(
        "SELECT COUNT(*) as cnt FROM klines WHERE symbol = ? AND freq = ?",
        (symbol, freq),
    )
    return cursor.fetchone()["cnt"]


if __name__ == "__main__":
    init_lake()
    print(f"TDX 数据湖初始化完成: {TDX_LAKE_PATH}")
    print(f"BaoStock 数据湖初始化完成: {BAOSTOCK_LAKE_PATH}")
