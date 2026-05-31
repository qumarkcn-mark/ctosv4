"""CT-OS V4.0 K 线数据湖 — 本地 SQLite 缓存引擎

支持 5 种级别：day / 60m / 30m / 15m / 5m
读取目标：< 5ms（本地 SSD 直读）
写入策略：增量 upsert，按 (symbol, freq, date) 唯一索引去重
"""

import logging
import sqlite3
import threading
import hashlib
import json
from datetime import datetime
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
#   TDX_LAKE_PATH      = 全市场日线事实源（freq=day, adjustflag=3），供 discovery 使用
#   BAOSTOCK_LAKE_PATH = BaoStock 多级别缓存（day/week/60/30/15/5），供 CZSC/持仓/价格使用
#   QMT_LAKE_PATH      = QMT 实时分钟收线缓存（实价、不复权），供盘中预览使用
LakeSource = Literal["tdx", "baostock", "qmt"]
TDX_LAKE_PATH = str(Path(DB_PATH).parent / "tdx_lake.db")
BAOSTOCK_LAKE_PATH = str(Path(DB_PATH).parent / "baostock_lake.db")
QMT_LAKE_PATH = str(Path(DB_PATH).parent / "qmt_lake.db")

# 兼容旧导入：历史代码里的 LAKE_PATH 代表 BaoStock 多级别缓存。
LAKE_PATH = BAOSTOCK_LAKE_PATH

LAKE_SOURCE_ROLES = {
    "tdx": {
        "role": "full_market_daily_fact",
        "description": "TDX 全市场日线事实源，供 discovery / 初筛 / AI Native 候选发现使用。",
        "formal_structure": False,
    },
    "baostock": {
        "role": "multi_level_structure_cache",
        "description": "BaoStock 多级别前复权缓存，供 CZSC / AI Native 结构推理使用。",
        "formal_structure": True,
    },
    "qmt": {
        "role": "realtime_closed_bar_preview",
        "description": "QMT 只读实时 CLOSED K 线缓存，供盘中预览和私有工作站上下文使用。",
        "formal_structure": False,
    },
}

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
    UNIQUE(symbol, freq, date, adjustflag)
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
            _ensure_adjustflag_unique_key(conn)
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


def _ensure_adjustflag_unique_key(conn: sqlite3.Connection) -> None:
    """Migrate old lake schema so raw and qfq rows can coexist."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='klines'"
    ).fetchone()
    table_sql = str(row[0] if row else "")
    normalized = table_sql.replace(" ", "").lower()
    if "unique(symbol,freq,date,adjustflag)" in normalized:
        return
    if "unique(symbol,freq,date)" not in normalized:
        return

    logger.warning("K线数据湖 schema 升级：klines 唯一键加入 adjustflag")
    conn.execute("ALTER TABLE klines RENAME TO klines_old")
    conn.execute(
        """
        CREATE TABLE klines (
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
            UNIQUE(symbol, freq, date, adjustflag)
        )
        """
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO klines
            (id, symbol, freq, date, open, high, low, close, volume, amount, adjustflag)
        SELECT id, symbol, freq, date, open, high, low, close, volume, amount, adjustflag
          FROM klines_old
        """
    )
    conn.execute("DROP TABLE klines_old")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_klines_symbol_freq_date ON klines(symbol, freq, date)")


def _lake_row_count(source: LakeSource) -> int:
    conn = get_lake_write_connection(source)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0])
    finally:
        conn.close()


def _path_size_bytes(path: Path) -> int:
    """统计主库及 WAL/SHM 文件占用，避免只看 .db 低估磁盘使用。"""
    total = 0
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            total += candidate.stat().st_size
    return total


def _readonly_connection(path: Path) -> sqlite3.Connection:
    """打开只读连接；状态接口不能意外创建空库。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _summarize_lake(source: LakeSource) -> dict:
    path = Path(get_lake_path(source))
    info = {
        "source": source,
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": _path_size_bytes(path),
        "size_mb": round(_path_size_bytes(path) / 1024 / 1024, 1),
        "health": "missing",
        **LAKE_SOURCE_ROLES[source],
    }
    if not path.exists():
        return info

    try:
        conn = _readonly_connection(path)
        try:
            total = conn.execute(
                """
                SELECT COUNT(*) AS rows,
                       COUNT(DISTINCT symbol) AS symbols,
                       MIN(date) AS first_date,
                       MAX(date) AS last_date
                  FROM klines
                """
            ).fetchone()
            freqs = conn.execute(
                """
                SELECT freq,
                       COUNT(*) AS rows,
                       COUNT(DISTINCT symbol) AS symbols,
                       MIN(date) AS first_date,
                       MAX(date) AS last_date
                  FROM klines
                 GROUP BY freq
                 ORDER BY rows DESC
                """
            ).fetchall()
            info.update(
                {
                    "health": "ok",
                    "rows": int(total["rows"] or 0),
                    "symbols": int(total["symbols"] or 0),
                    "first_date": total["first_date"],
                    "last_date": total["last_date"],
                    "freqs": [dict(row) for row in freqs],
                }
            )
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        info.update({"health": "malformed", "error": str(exc)})
    except Exception as exc:
        info.update({"health": "error", "error": str(exc)})
    return info


def lake_status() -> dict:
    """
    汇总当前 K 线数据链路状态。

    这是只读观测接口，给前台调试页、运维检查和 AI Native 预检使用。
    """
    sources = [_summarize_lake(source) for source in ("tdx", "baostock", "qmt")]
    data_dir = Path(DB_PATH).parent
    legacy_path = data_dir / "kline_lake.db"
    corrupt_dir = data_dir / "corrupt-backups"
    corrupt_files = []
    if corrupt_dir.exists():
        corrupt_files = [
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "size_mb": round(path.stat().st_size / 1024 / 1024, 1),
            }
            for path in sorted(corrupt_dir.iterdir())
            if path.is_file()
        ]

    return {
        "status": "ok" if all(item["health"] in {"ok", "missing"} for item in sources) else "degraded",
        "data_dir": str(data_dir),
        "total_size_bytes": sum(item["size_bytes"] for item in sources),
        "total_size_mb": round(sum(item["size_bytes"] for item in sources) / 1024 / 1024, 1),
        "sources": sources,
        "legacy": {
            "path": str(legacy_path),
            "exists": legacy_path.exists(),
            "size_bytes": _path_size_bytes(legacy_path),
            "size_mb": round(_path_size_bytes(legacy_path) / 1024 / 1024, 1),
            "active": False,
            "cleanup_safe_after_split_verified": legacy_path.exists(),
        },
        "corrupt_backups": {
            "path": str(corrupt_dir),
            "exists": corrupt_dir.exists(),
            "files": corrupt_files,
            "size_bytes": sum(item["size_bytes"] for item in corrupt_files),
            "size_mb": round(sum(item["size_bytes"] for item in corrupt_files) / 1024 / 1024, 1),
        },
    }


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
    if freq == "week":
        result = _collapse_week_rows(result)
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
            if freq == "week":
                rows = _collapse_week_rows(rows)
            data = [
                (symbol, freq, r["date"], r["open"], r["high"], r["low"],
                 r["close"], r.get("volume", 0), r.get("amount", 0), adjustflag)
                for r in rows
            ]
            if freq == "week":
                _delete_same_week_rows(conn, symbol=symbol, adjustflag=adjustflag, rows=rows)
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


def _delete_same_week_rows(
    conn: sqlite3.Connection,
    *,
    symbol: str,
    adjustflag: str,
    rows: list[dict],
) -> None:
    """Remove stale rolling weekly bars before writing a new weekly aggregate.

    A current week bar is dated with the latest available trading day. Without
    this cleanup, Monday/Tuesday/Wednesday post-market syncs can leave multiple
    bars for the same trading week because the date changes every day.
    """
    incoming_buckets = {
        bucket
        for bucket in (_week_bucket(row.get("date")) for row in rows)
        if bucket
    }
    if not incoming_buckets:
        return
    existing = conn.execute(
        """
        SELECT date FROM klines
         WHERE symbol = ? AND freq = 'week' AND adjustflag = ?
        """,
        (symbol, adjustflag),
    ).fetchall()
    stale_dates = [
        row["date"]
        for row in existing
        if _week_bucket(row["date"]) in incoming_buckets
    ]
    if not stale_dates:
        return
    placeholders = ",".join("?" for _ in stale_dates)
    conn.execute(
        f"""
        DELETE FROM klines
         WHERE symbol = ? AND freq = 'week' AND adjustflag = ?
           AND date IN ({placeholders})
        """,
        [symbol, adjustflag, *stale_dates],
    )


def _collapse_week_rows(rows: list[dict]) -> list[dict]:
    """Collapse duplicate same-week rows into one OHLCV weekly bar.

    Some upstream providers can return daily bars even when the request is
    labelled as weekly. The data lake must still expose exactly one row per ISO
    trading week for `freq=week`.
    """
    buckets: dict[tuple[int, int], list[dict]] = {}
    passthrough: list[dict] = []
    for row in sorted(rows or [], key=lambda item: str(item.get("date") or "")):
        bucket = _week_bucket(row.get("date"))
        if not bucket:
            passthrough.append(row)
            continue
        buckets.setdefault(bucket, []).append(row)

    collapsed = []
    for bucket_rows in buckets.values():
        first = bucket_rows[0]
        last = bucket_rows[-1]
        collapsed.append({
            **last,
            "date": last.get("date"),
            "open": first.get("open"),
            "high": max(_num(row.get("high")) for row in bucket_rows),
            "low": min(_num(row.get("low")) for row in bucket_rows),
            "close": last.get("close"),
            "volume": sum(_num(row.get("volume")) for row in bucket_rows),
            "amount": sum(_num(row.get("amount")) for row in bucket_rows),
        })
    return sorted([*passthrough, *collapsed], key=lambda item: str(item.get("date") or ""))


def _week_bucket(value: object) -> tuple[int, int] | None:
    try:
        day = datetime.strptime(str(value or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    iso_year, iso_week, _ = day.isocalendar()
    return iso_year, iso_week


def _num(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def count_klines(symbol: str, freq: str, source: LakeSource = "baostock") -> int:
    """查询某只股票某级别的缓存 K 线总量，用于 cold-start 检测（线程本地复用连接）"""
    conn = get_lake_connection(source)
    cursor = conn.execute(
        "SELECT COUNT(*) as cnt FROM klines WHERE symbol = ? AND freq = ?",
        (symbol, freq),
    )
    return cursor.fetchone()["cnt"]


def get_kline_window_signature(
    symbol: str,
    freq: str,
    *,
    end_date: Optional[str] = None,
    limit: int = 2000,
    adjustflag: str = "2",
    source: Optional[LakeSource] = None,
) -> dict:
    """
    返回最近一个计算窗口的轻量数据签名。

    结构快照不能只看 last_date；历史补数据、复权修正、OHLC 修订都可能在
    last_date 不变时改变结构。这里对最新 limit 根做聚合 fingerprint，
    作为 P0 持久快照的命中条件。
    """
    safe_limit = max(1, int(limit or 1))
    read_source = _infer_read_source(freq, adjustflag, source)
    conn = get_lake_connection(read_source)
    conditions = ["symbol = ?", "freq = ?", "adjustflag = ?"]
    params: list = [symbol, freq, adjustflag]
    if end_date:
        conditions.append("date <= ?")
        params.append(end_date)
    where_clause = " AND ".join(conditions)
    rows = conn.execute(
        f"""
        SELECT date, open, high, low, close, volume, amount
          FROM klines
         WHERE {where_clause}
         ORDER BY date DESC
         LIMIT ?
        """,
        [*params, safe_limit],
    ).fetchall()
    if not rows:
        return {
            "source": read_source,
            "row_count": 0,
            "first_date": "",
            "last_date": "",
            "signature": "",
        }

    ordered = list(reversed(rows))
    compact = [
        [
            row["date"],
            round(float(row["open"] or 0), 6),
            round(float(row["high"] or 0), 6),
            round(float(row["low"] or 0), 6),
            round(float(row["close"] or 0), 6),
            round(float(row["volume"] or 0), 4),
            round(float(row["amount"] or 0), 4),
        ]
        for row in ordered
    ]
    payload = {
        "symbol": symbol,
        "freq": freq,
        "source": read_source,
        "adjustflag": adjustflag,
        "end_date": end_date or "",
        "limit": safe_limit,
        "rows": compact,
    }
    signature = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "source": read_source,
        "row_count": len(ordered),
        "first_date": str(ordered[0]["date"]),
        "last_date": str(ordered[-1]["date"]),
        "signature": signature,
    }


if __name__ == "__main__":
    init_lake()
    print(f"TDX 数据湖初始化完成: {TDX_LAKE_PATH}")
    print(f"BaoStock 数据湖初始化完成: {BAOSTOCK_LAKE_PATH}")
