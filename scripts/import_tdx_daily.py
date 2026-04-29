"""
TDX 日线数据批量导入工具
从通达信本地 vipdoc 目录读取 .day 文件，批量写入 tdx_lake.db

用法（Mac 终端）:
    sudo python3 scripts/import_tdx_daily.py

TDX vipdoc 挂载路径（默认）: /Volumes/tdx_vipdoc
目标数据库: data/tdx_lake.db（scanner 专用日线事实源）

.day 文件格式（32字节/条）:
    date   : uint32  YYYYMMDD
    open   : uint32  价格 × 100
    high   : uint32  价格 × 100
    low    : uint32  价格 × 100
    close  : uint32  价格 × 100
    amount : float32 成交额（元）
    vol    : uint32  成交量（股）
    reserved: uint32
"""

import os
import struct
import sqlite3
import sys
from pathlib import Path

# ── 配置 ──────────────────────────────────────────────────────────────────────
TDX_VIPDOC   = "/Volumes/tdx_vipdoc"          # SMB 挂载点
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH      = str(PROJECT_ROOT / "data" / "tdx_lake.db")

# 只导入 A 股正股（过滤指数/ETF/债券）
# 沪市 A 股: 60xxxx / 68xxxx
# 深市 A 股: 00xxxx / 30xxxx / 002xxx / 300xxx
SH_PREFIXES = ("sh60", "sh68")
SZ_PREFIXES = ("sz00", "sz30")

RECORD_SIZE = 32
RECORD_FMT  = "<IIIIIfII"   # date, o, h, l, c, amount(f), vol, reserved

BATCH_SIZE  = 5000          # 每次提交条数


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def is_astock(filename: str) -> bool:
    """判断文件名是否属于 A 股正股（排除指数/ETF/债券）"""
    name = filename.lower().replace(".day", "")
    return (
        any(name.startswith(p) for p in SH_PREFIXES) or
        any(name.startswith(p) for p in SZ_PREFIXES)
    )


def tdx_code_to_bs(filename: str) -> str:
    """
    通达信文件名 → BaoStock 格式
    sh600519.day → sh.600519
    sz000001.day → sz.000001
    """
    name = filename.replace(".day", "")          # sh600519
    market = name[:2]                            # sh / sz
    code   = name[2:]                            # 600519
    return f"{market}.{code}"


def parse_day_file(filepath: str) -> list[dict]:
    """解析单个 .day 文件，返回 OHLCV 记录列表"""
    records = []
    try:
        size = os.path.getsize(filepath)
        n    = size // RECORD_SIZE
        with open(filepath, "rb") as f:
            for _ in range(n):
                raw = f.read(RECORD_SIZE)
                if len(raw) < RECORD_SIZE:
                    break
                fields = struct.unpack(RECORD_FMT, raw)
                date_int, o, h, l, c, amount, vol, _ = fields

                # 过滤无效记录
                if date_int < 19900101 or date_int > 20991231:
                    continue
                if c <= 0 or vol <= 0:
                    continue

                # 日期格式转换: 20260423 → "2026-04-23"
                y = date_int // 10000
                m = (date_int % 10000) // 100
                d = date_int % 100
                date_str = f"{y:04d}-{m:02d}-{d:02d}"

                records.append({
                    "date":   date_str,
                    "open":   round(o / 100, 2),
                    "high":   round(h / 100, 2),
                    "low":    round(l / 100, 2),
                    "close":  round(c / 100, 2),
                    "volume": float(vol),          # 单位：股
                    "amount": round(float(amount), 2),
                })
    except Exception as e:
        print(f"  ⚠ 解析失败 {filepath}: {e}")
    return records


def get_conn() -> sqlite3.Connection:
    """打开 TDX 专用日线数据湖 tdx_lake.db。"""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")       # 纯导入，断电丢数据可接受（重跑即可）
    conn.execute("PRAGMA cache_size=-131072")    # 128MB cache
    return conn


def ensure_schema(conn: sqlite3.Connection):
    """确保数据库表存在"""
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
        -- TDX专属同步元信息表，不污染BaoStock的kline_sync_meta
        CREATE TABLE IF NOT EXISTS tdx_sync_meta (
            symbol     TEXT NOT NULL,
            freq       TEXT NOT NULL,
            last_date  TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, freq)
        );
        CREATE INDEX IF NOT EXISTS idx_klines_symbol_freq_date
            ON klines(symbol, freq, date);
    """)
    conn.commit()


def upsert_batch(conn: sqlite3.Connection, symbol: str, rows: list[dict]):
    """批量写入，使用 INSERT OR REPLACE"""
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
    if rows:
        last_date = max(r["date"] for r in rows)
        conn.execute(
            """INSERT OR REPLACE INTO tdx_sync_meta (symbol, freq, last_date)
               VALUES (?, ?, ?)""",
            (symbol, "day", last_date),
        )


# ── 主流程 ────────────────────────────────────────────────────────────────────

def collect_day_files() -> list[tuple[str, str]]:
    """收集所有 A 股 .day 文件，返回 [(symbol, filepath), ...]"""
    result = []
    for market in ("sh", "sz"):
        lday_dir = os.path.join(TDX_VIPDOC, market, "lday")
        if not os.path.isdir(lday_dir):
            print(f"  ⚠ 目录不存在: {lday_dir}")
            continue
        for fname in sorted(os.listdir(lday_dir)):
            if not fname.endswith(".day"):
                continue
            if not is_astock(fname):
                continue
            symbol   = tdx_code_to_bs(fname)
            filepath = os.path.join(lday_dir, fname)
            result.append((symbol, filepath))
    return result


def main():
    print("=" * 60)
    print("TDX 日线数据导入工具")
    print(f"数据源: {TDX_VIPDOC}")
    print(f"目标库: {DB_PATH}")
    print("=" * 60)

    # 检查挂载点
    if not os.path.isdir(TDX_VIPDOC):
        print(f"❌ 挂载点不存在: {TDX_VIPDOC}")
        print("请先设置 TDX_SMB_USER/TDX_SMB_PASS/TDX_SMB_HOST，并挂载 vipdoc 到 /Volumes/tdx_vipdoc")
        sys.exit(1)

    # 收集文件
    print("\n扫描 .day 文件...")
    files = collect_day_files()
    print(f"找到 {len(files)} 只 A 股")

    if not files:
        print("❌ 未找到任何 A 股文件，请检查路径")
        sys.exit(1)

    # 连接数据库
    conn = get_conn()
    ensure_schema(conn)

    # 批量导入
    total_symbols  = 0
    total_records  = 0
    total_skipped  = 0

    print(f"\n开始导入（每 100 只显示进度）...\n")

    for i, (symbol, filepath) in enumerate(files, 1):
        rows = parse_day_file(filepath)
        if not rows:
            total_skipped += 1
            continue

        upsert_batch(conn, symbol, rows)
        total_records += len(rows)
        total_symbols += 1

        # 每 BATCH_SIZE 条提交一次
        if total_records % BATCH_SIZE < len(rows):
            conn.commit()

        # 进度显示
        if i % 100 == 0 or i == len(files):
            pct = i / len(files) * 100
            print(f"  [{i:4d}/{len(files)}] {pct:5.1f}%  "
                  f"已导入 {total_symbols} 只 / {total_records:,} 条")

    # 最终提交
    conn.commit()
    conn.close()

    print("\n" + "=" * 60)
    print(f"✅ 导入完成")
    print(f"   股票数量: {total_symbols} 只")
    print(f"   数据条数: {total_records:,} 条")
    print(f"   跳过(空): {total_skipped} 只")
    print("=" * 60)


if __name__ == "__main__":
    main()
