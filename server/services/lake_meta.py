"""CT-OS V4.0 数据湖元数据服务
================================
扫描 kline_lake.db，聚合每只股票的缓存状态。
提供概览、WAL清理、数据生命周期管理。

V4 架构：单一 kline_lake.db + klines 表（全品种合并存储）
"""

import logging
import os
import sqlite3
from typing import Optional

from server.db.kline_lake import LAKE_PATH, get_lake_connection

logger = logging.getLogger(__name__)

# 周期显示顺序
FREQ_ORDER = ["day", "60", "30", "15", "5"]
FREQ_LABELS = {"day": "日线", "60": "60m", "30": "30m", "15": "15m", "5": "5m"}


def scan_lake_overview() -> dict:
    """
    扫描数据湖概览：已缓存股票、各周期条数、磁盘占用。

    Returns:
        {
            "total_stocks": int,
            "total_bars": int,
            "disk_mb": float,
            "orphan_files": int,
            "stocks": [
                {
                    "symbol": "sh.600519",
                    "total_bars": int,
                    "periods": {
                        "day": {"count": 662, "first": "2023-07-18", "last": "2026-04-13"},
                        "5": {"count": 0},
                    }
                },
            ]
        }
    """
    # 磁盘占用
    disk_bytes = 0
    lake_dir = os.path.dirname(LAKE_PATH)
    orphan_count = 0

    for f in os.listdir(lake_dir):
        fpath = os.path.join(lake_dir, f)
        if os.path.isfile(fpath):
            disk_bytes += os.path.getsize(fpath)
            # 统计孤儿 WAL/SHM 文件
            if (f.endswith("-shm") or f.endswith("-wal")):
                base_db = f.rsplit("-", 1)[0]
                if not os.path.exists(os.path.join(lake_dir, base_db)):
                    orphan_count += 1

    conn = get_lake_connection()
    try:
        # 获取各股票各周期的统计
        cursor = conn.execute("""
            SELECT symbol, freq, COUNT(*) as cnt, MIN(date) as first_date, MAX(date) as last_date
            FROM klines
            GROUP BY symbol, freq
            ORDER BY symbol, freq
        """)

        stock_map = {}
        total_bars = 0

        for row in cursor.fetchall():
            sym = row["symbol"]
            freq = row["freq"]
            cnt = row["cnt"]
            total_bars += cnt

            if sym not in stock_map:
                stock_map[sym] = {"symbol": sym, "total_bars": 0, "periods": {}}

            stock_map[sym]["total_bars"] += cnt
            stock_map[sym]["periods"][freq] = {
                "count": cnt,
                "first": row["first_date"],
                "last": row["last_date"],
            }

        # 补全空周期
        for sym_data in stock_map.values():
            for freq in FREQ_ORDER:
                if freq not in sym_data["periods"]:
                    sym_data["periods"][freq] = {"count": 0}

        # 按总条数降序
        stocks = sorted(stock_map.values(), key=lambda s: s["total_bars"], reverse=True)

        return {
            "total_stocks": len(stocks),
            "total_bars": total_bars,
            "disk_mb": round(disk_bytes / 1024 / 1024, 1),
            "orphan_files": orphan_count,
            "freqs": FREQ_ORDER,
            "stocks": stocks,
        }
    finally:
        conn.close()


def cleanup_orphan_files() -> dict:
    """清理 data/ 目录下的孤儿 -shm/-wal 文件"""
    lake_dir = os.path.dirname(LAKE_PATH)
    cleaned = 0
    freed_bytes = 0

    for f in os.listdir(lake_dir):
        if f.endswith("-shm") or f.endswith("-wal"):
            base_db = f.rsplit("-", 1)[0]
            if not os.path.exists(os.path.join(lake_dir, base_db)):
                fpath = os.path.join(lake_dir, f)
                try:
                    freed_bytes += os.path.getsize(fpath)
                    os.remove(fpath)
                    cleaned += 1
                except Exception as e:
                    logger.warning(f"删除孤儿文件失败 {f}: {e}")

    if cleaned > 0:
        logger.info(f"已清理 {cleaned} 个孤儿文件，释放 {freed_bytes / 1024:.1f} KB")

    return {"cleaned": cleaned, "freed_kb": round(freed_bytes / 1024, 1)}


def delete_stock_data(symbol: str, freq: Optional[str] = None) -> dict:
    """
    删除指定股票的缓存数据。

    Args:
        symbol: 股票代码 (如 sh.600519)
        freq: 周期 (如 "day")。None=删除该股票所有周期
    """
    conn = get_lake_connection()
    try:
        if freq:
            conn.execute("DELETE FROM klines WHERE symbol = ? AND freq = ?", (symbol, freq))
            conn.execute("DELETE FROM kline_sync_meta WHERE symbol = ? AND freq = ?", (symbol, freq))
            detail = f"已删除 {symbol}/{freq}"
        else:
            conn.execute("DELETE FROM klines WHERE symbol = ?", (symbol,))
            conn.execute("DELETE FROM kline_sync_meta WHERE symbol = ?", (symbol,))
            detail = f"已删除 {symbol} 所有数据"
        conn.commit()
        logger.info(f"[LakeMeta] {detail}")
        return {"deleted": True, "detail": detail}
    except Exception as e:
        return {"deleted": False, "detail": f"删除失败: {e}"}
    finally:
        conn.close()


def get_sync_status() -> list[dict]:
    """获取同步元信息：各股票各周期的最后同步日期"""
    conn = get_lake_connection()
    try:
        cursor = conn.execute("""
            SELECT symbol, freq, last_date, updated_at
            FROM kline_sync_meta
            ORDER BY updated_at DESC
        """)
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()

def trigger_manual_fetch(symbol: str, freqs: list[str], start_date: Optional[str] = None, end_date: Optional[str] = None) -> dict:
    """
    触发手动数据拉取
    """
    from server.services.baostock_service import fetch_klines_sync
    import threading
    
    def _fetch_all():
        for freq in freqs:
            try:
                fetch_klines_sync(symbol=symbol, freq=freq, start_date=start_date, end_date=end_date)
            except Exception as e:
                logger.error(f"Manual fetch failed for {symbol} {freq}: {e}")
                
    # 丢入子线程静默处理
    t = threading.Thread(target=_fetch_all)
    t.start()
    
    return {"status": "started", "symbol": symbol, "freqs": freqs}
