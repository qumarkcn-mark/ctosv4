"""CT-OS V4.0 数据湖元数据服务
================================
扫描 BaoStock 数据湖，聚合每只股票的缓存状态。
提供概览、WAL清理、数据生命周期管理。

V5 架构：TDX 日线与 BaoStock 多级别缓存物理拆库。
本服务只管理 BaoStock lake，正式结构快照由 BaoStock K 线 + CZSC 生成。
"""

import logging
import os
import sqlite3
from typing import Optional

from server.db.kline_lake import LAKE_PATH, get_lake_connection, get_lake_write_connection

logger = logging.getLogger(__name__)

# 周期显示顺序
FREQ_ORDER = ["week", "day", "60", "30", "15", "5"]
FREQ_LABELS = {"week": "周线", "day": "日线", "60": "60m", "30": "30m", "15": "15m", "5": "5m"}


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

    # 读操作：使用线程本地复用连接，不 close
    conn = get_lake_connection()
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
    # 写操作：使用独立短连接，必须 close
    conn = get_lake_write_connection()
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
    """获取同步元信息：各股票各周期的最后同步日期（只读，复用线程本地连接）"""
    conn = get_lake_connection()
    cursor = conn.execute("""
        SELECT symbol, freq, last_date, updated_at
        FROM kline_sync_meta
        ORDER BY updated_at DESC
    """)
    return [dict(row) for row in cursor.fetchall()]

def trigger_manual_fetch(
    symbol: str,
    freqs: list[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    force_refresh: bool = False,
) -> dict:
    """
    触发手动数据拉取。

    Args:
        force_refresh: 强制全量刷新。清除 sync_meta 和已有 klines，
                       从头拉取完整数据。用于修复被 BaoStock 截断的脏数据。
    """
    from server.services.baostock_service import fetch_klines_sync
    import threading

    def _fetch_all():
        for freq in freqs:
            try:
                # 强制刷新：先清除旧数据和同步标记
                if force_refresh:
                    logger.info("[ManualFetch] 强制刷新 %s/%s，清除旧数据...", symbol, freq)
                    # 写操作：独立短连接
                    conn = get_lake_write_connection()
                    try:
                        conn.execute("DELETE FROM kline_sync_meta WHERE symbol = ? AND freq = ?", (symbol, freq))
                        conn.execute("DELETE FROM klines WHERE symbol = ? AND freq = ?", (symbol, freq))
                        conn.commit()
                    finally:
                        conn.close()

                written = fetch_klines_sync(symbol=symbol, freq=freq, start_date=start_date, end_date=end_date)
                logger.info("[ManualFetch] 完成 %s/%s: %d 条", symbol, freq, written)
            except Exception as e:
                logger.error("[ManualFetch] 失败 %s/%s: %s", symbol, freq, e)

    # 丢入子线程处理（不阻塞 API 响应）
    t = threading.Thread(target=_fetch_all, daemon=True)
    t.start()

    return {"status": "started", "symbol": symbol, "freqs": freqs, "force_refresh": force_refresh}
