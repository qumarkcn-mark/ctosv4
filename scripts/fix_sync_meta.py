"""
修复 kline_sync_meta 被TDX导入污染的问题
直接在 Mac 终端运行：python3 scripts/fix_sync_meta.py
"""
import sqlite3
from pathlib import Path

DB_PATH = str(Path(__file__).resolve().parent.parent / "data" / "baostock_lake.db")

print(f"数据库: {DB_PATH}")
conn = sqlite3.connect(DB_PATH, timeout=120)

print("步骤1：删除只有TDX数据（无前复权）的sync记录...")
r = conn.execute("""
    DELETE FROM kline_sync_meta
    WHERE freq = 'day'
    AND symbol NOT IN (
        SELECT DISTINCT symbol FROM klines
        WHERE freq = 'day' AND adjustflag = '2'
    )
""")
print(f"  已删除 {r.rowcount} 条")

print("步骤2：修复有前复权数据的last_date...")
r = conn.execute("""
    UPDATE kline_sync_meta
    SET last_date = (
        SELECT MAX(date) FROM klines
        WHERE klines.symbol = kline_sync_meta.symbol
          AND klines.freq   = kline_sync_meta.freq
          AND adjustflag = '2'
    )
    WHERE freq = 'day'
    AND EXISTS (
        SELECT 1 FROM klines
        WHERE klines.symbol = kline_sync_meta.symbol
          AND klines.freq   = kline_sync_meta.freq
          AND adjustflag = '2'
    )
""")
print(f"  已修复 {r.rowcount} 条")

conn.commit()

r1 = conn.execute("SELECT COUNT(*) FROM kline_sync_meta WHERE freq='day'").fetchone()[0]
r2 = conn.execute("SELECT COUNT(DISTINCT symbol) FROM klines WHERE freq='day' AND adjustflag='2'").fetchone()[0]
conn.close()

print(f"\n✅ 完成")
print(f"   剩余sync记录(day): {r1}")
print(f"   有前复权数据的股票: {r2}")
print("\n重启后端服务后K线图恢复正常。")
