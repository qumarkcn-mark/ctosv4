"""
修复日线停留在2021年的问题
原理：删除 adjustflag='2' 中最新日期 < 2024-01-01 的股票全部日线数据，
      下次打开K线图时系统自动从BaoStock重新拉取最新前复权数据。

直接在 Mac 终端运行：
    python3 scripts/fix_stale_klines.py
"""
import sqlite3
from pathlib import Path

DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "baostock_lake.db")
print(f"数据库: {DB_PATH}")

conn = sqlite3.connect(DB_PATH, timeout=120)
conn.execute("PRAGMA journal_mode=WAL")

# ── 诊断 ──────────────────────────────────────────────────────────────────────
print("\n── 当前状况 ──")
total = conn.execute(
    "SELECT COUNT(DISTINCT symbol) FROM klines WHERE adjustflag='2' AND freq='day'"
).fetchone()[0]
print(f"adjustflag='2' 日线股票总数: {total}")

stale = conn.execute("""
    SELECT COUNT(*) FROM (
        SELECT symbol FROM klines
        WHERE adjustflag='2' AND freq='day'
        GROUP BY symbol
        HAVING MAX(date) < '2024-01-01'
    )
""").fetchone()[0]
print(f"最新数据停在 2024-01-01 之前的股票: {stale} 只")

if stale == 0:
    # 看一下分布情况
    rows = conn.execute("""
        SELECT MAX(date) as latest, COUNT(DISTINCT symbol) as cnt
        FROM klines WHERE adjustflag='2' AND freq='day'
        GROUP BY substr(MAX(date),1,4)
        ORDER BY latest
    """).fetchall()
    print("\n按最新日期年份分布:")
    for row in rows:
        print(f"  最新至 {row[0]}: {row[1]} 只")
    conn.close()
    print("\n没有需要修复的股票，K线问题可能另有原因。")
    import sys; sys.exit(0)

# 预览要删除的股票
sample = conn.execute("""
    SELECT symbol, MAX(date) as last_date, COUNT(*) as rows
    FROM klines
    WHERE adjustflag='2' AND freq='day'
      AND symbol IN (
          SELECT symbol FROM klines
          WHERE adjustflag='2' AND freq='day'
          GROUP BY symbol HAVING MAX(date) < '2024-01-01'
      )
    GROUP BY symbol
    ORDER BY last_date
    LIMIT 10
""").fetchall()
print(f"\n示例（前10只）:")
for row in sample:
    print(f"  {row[0]}  last={row[1]}  rows={row[2]}")

# ── 删除 ──────────────────────────────────────────────────────────────────────
print(f"\n步骤1：删除 {stale} 只股票的过时前复权日线数据...")
r = conn.execute("""
    DELETE FROM klines
    WHERE adjustflag='2' AND freq='day'
    AND symbol IN (
        SELECT symbol FROM klines
        WHERE adjustflag='2' AND freq='day'
        GROUP BY symbol
        HAVING MAX(date) < '2024-01-01'
    )
""")
print(f"  已删除 {r.rowcount:,} 条记录")

# 同步清理 kline_sync_meta，让下次自动重新同步
print("步骤2：清理对应的 kline_sync_meta 元数据...")
r2 = conn.execute("""
    DELETE FROM kline_sync_meta
    WHERE freq='day'
    AND symbol NOT IN (
        SELECT DISTINCT symbol FROM klines
        WHERE freq='day' AND adjustflag='2'
    )
""")
print(f"  已清理 {r2.rowcount} 条 sync 元数据")

conn.commit()
conn.close()

print("\n✅ 完成！")
print("   重新打开任意股票K线图，系统会自动从BaoStock拉取最新数据（约5~10秒）。")
print("   之后日线将显示最新前复权数据。")
