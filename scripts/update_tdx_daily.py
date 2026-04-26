"""
TDX 日线增量更新脚本
每日盘后运行，只同步上次同步日期之后的新记录（秒级完成）

逻辑：
  - 读 kline_sync_meta 获取每只股票的 last_date
  - 只读 .day 文件尾部（最近100条），过滤 date > last_date
  - 批量写入 tdx_lake.db（adjustflag='3'）
"""

import os
import struct
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── 配置 ──────────────────────────────────────────────────────────────────────
TDX_VIPDOC   = "/Volumes/tdx_vipdoc"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH      = str(PROJECT_ROOT / "data" / "tdx_lake.db")

SH_PREFIXES  = ("sh60", "sh68")
SZ_PREFIXES  = ("sz00", "sz30")

RECORD_SIZE  = 32
RECORD_FMT   = "<IIIIIfII"
TAIL_RECORDS = 100      # 每次只读文件尾部这么多条，覆盖约1个月新数据
BATCH_SIZE   = 2000


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def is_astock(filename: str) -> bool:
    name = filename.lower().replace(".day", "")
    return (any(name.startswith(p) for p in SH_PREFIXES) or
            any(name.startswith(p) for p in SZ_PREFIXES))


def tdx_code_to_bs(filename: str) -> str:
    name = filename.replace(".day", "")
    return f"{name[:2]}.{name[2:]}"


def int_to_date(date_int: int) -> str:
    y = date_int // 10000
    m = (date_int % 10000) // 100
    d = date_int % 100
    return f"{y:04d}-{m:02d}-{d:02d}"


def is_trading_day() -> bool:
    """简单判断今天是否交易日（排除周末）"""
    return datetime.now().weekday() < 5


def parse_new_records(filepath: str, last_date: Optional[str]) -> list[dict]:
    """
    读取 .day 文件，返回 date > last_date 的新记录。
    增量模式：只读文件尾部 TAIL_RECORDS 条，避免全量读取（SMB加速）。
    首次导入（last_date=None）：读全部。
    """
    records = []
    try:
        size = os.path.getsize(filepath)
        n    = size // RECORD_SIZE
        if n == 0:
            return records

        # 增量：从尾部读；首次：从头读
        start = max(0, n - TAIL_RECORDS) if last_date else 0

        with open(filepath, "rb") as f:
            f.seek(start * RECORD_SIZE)
            for _ in range(n - start):
                raw = f.read(RECORD_SIZE)
                if len(raw) < RECORD_SIZE:
                    break
                fields   = struct.unpack(RECORD_FMT, raw)
                date_int = fields[0]
                if date_int < 19900101 or date_int > 20991231:
                    continue
                date_str = int_to_date(date_int)
                if last_date and date_str <= last_date:
                    continue
                o, h, l, c = fields[1], fields[2], fields[3], fields[4]
                amount, vol = fields[5], fields[6]
                if c <= 0 or vol <= 0:
                    continue
                records.append({
                    "date":   date_str,
                    "open":   round(o / 100, 2),
                    "high":   round(h / 100, 2),
                    "low":    round(l / 100, 2),
                    "close":  round(c / 100, 2),
                    "volume": float(vol),
                    "amount": round(float(amount), 2),
                })
    except Exception as e:
        print(f"  ⚠ 解析失败 {filepath}: {e}")
    return records


# ── 数据库 ────────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-65536")
    return conn


def ensure_tdx_schema(conn: sqlite3.Connection):
    """确保 TDX 专用日线数据湖 schema 存在。"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS klines (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol     TEXT    NOT NULL,
            freq       TEXT    NOT NULL,
            date       TEXT    NOT NULL,
            open       REAL    NOT NULL,
            high       REAL    NOT NULL,
            low        REAL    NOT NULL,
            close      REAL    NOT NULL,
            volume     REAL    DEFAULT 0,
            amount     REAL    DEFAULT 0,
            adjustflag TEXT    DEFAULT '3',
            UNIQUE(symbol, freq, date)
        );
        CREATE TABLE IF NOT EXISTS kline_sync_meta (
            symbol     TEXT NOT NULL,
            freq       TEXT NOT NULL,
            last_date  TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, freq)
        );
        CREATE TABLE IF NOT EXISTS tdx_sync_meta (
            symbol    TEXT NOT NULL,
            freq      TEXT NOT NULL,
            last_date TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, freq)
        );
        CREATE INDEX IF NOT EXISTS idx_klines_symbol_freq_date
            ON klines(symbol, freq, date);
    """)
    conn.commit()


def get_last_dates(conn: sqlite3.Connection) -> dict[str, str]:
    """一次性读取所有 symbol 的 last_date（从TDX专属表）"""
    rows = conn.execute(
        "SELECT symbol, last_date FROM tdx_sync_meta WHERE freq='day'"
    ).fetchall()
    return {row["symbol"]: row["last_date"] for row in rows}


def upsert_records(conn: sqlite3.Connection, symbol: str, rows: list[dict]):
    data = [
        (symbol, "day", r["date"], r["open"], r["high"], r["low"],
         r["close"], r["volume"], r["amount"], "3")
        for r in rows
    ]
    conn.executemany(
        """INSERT OR REPLACE INTO klines
               (symbol, freq, date, open, high, low, close, volume, amount, adjustflag)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        data,
    )
    # 写TDX专属表，不动kline_sync_meta（避免干扰BaoStock同步）
    last_date = max(r["date"] for r in rows)
    conn.execute(
        """INSERT OR REPLACE INTO tdx_sync_meta (symbol, freq, last_date)
           VALUES (?, 'day', ?)""",
        (symbol, last_date),
    )


# ── 主流程 ────────────────────────────────────────────────────────────────────

def collect_files() -> list[tuple[str, str]]:
    result = []
    for market in ("sh", "sz"):
        lday_dir = os.path.join(TDX_VIPDOC, market, "lday")
        if not os.path.isdir(lday_dir):
            continue
        for fname in sorted(os.listdir(lday_dir)):
            if fname.endswith(".day") and is_astock(fname):
                result.append((tdx_code_to_bs(fname),
                                os.path.join(lday_dir, fname)))
    return result


def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] TDX 增量更新开始")

    # 检查挂载点
    if not os.path.isdir(TDX_VIPDOC):
        print(f"❌ 挂载点不可用: {TDX_VIPDOC}")
        sys.exit(1)

    # 非交易日跳过（可注释掉强制跑）
    if not is_trading_day():
        print("今天是非交易日，跳过更新")
        sys.exit(0)

    files = collect_files()
    print(f"找到 {len(files)} 只 A 股文件")

    conn       = get_conn()
    ensure_tdx_schema(conn)
    last_dates = get_last_dates(conn)

    total_symbols = 0
    total_new     = 0
    pending       = 0        # 未提交条数

    for symbol, filepath in files:
        last_date = last_dates.get(symbol)
        new_rows  = parse_new_records(filepath, last_date)

        if not new_rows:
            continue

        upsert_records(conn, symbol, new_rows)
        total_symbols += 1
        total_new     += len(new_rows)
        pending       += len(new_rows)

        if pending >= BATCH_SIZE:
            conn.commit()
            pending = 0

    conn.commit()
    conn.close()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] 完成：更新 {total_symbols} 只股票，新增 {total_new:,} 条")


if __name__ == "__main__":
    main()
