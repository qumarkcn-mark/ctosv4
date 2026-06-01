"""Custom Robust T+0 Backtest Runner for Active Watchlist Symbols.

Queries 1M K-lines from qmt_lake.db, dynamically synthesizes 5M K-lines for alignment,
and runs the V2 State Machine T+0 backtest in memory without writing to database.
"""
from __future__ import annotations

import sqlite3
import datetime
from collections import defaultdict
from pathlib import Path
from server.engines.t0.t0_backtest import BacktestConfig, run_backtest, print_backtest_report


def query_1m_klines(symbol: str, start_date: str, end_date: str) -> list[dict]:
    db_path = Path("data/qmt_lake.db")
    if not db_path.exists():
        raise FileNotFoundError(f"Database not found: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT date, open, high, low, close, volume, amount
          FROM klines
         WHERE symbol = ? AND freq = '1' AND adjustflag = '3'
           AND date(date) >= ? AND date(date) <= ?
         ORDER BY date
        """,
        (symbol, start_date, end_date),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def synthesize_5m(klines_1m: list[dict]) -> list[dict]:
    buckets = defaultdict(list)
    for k in klines_1m:
        dt_str = k["date"]
        if len(dt_str) < 19:
            continue
        try:
            dt = datetime.datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        
        minute = dt.minute
        if minute == 0 and dt.hour in (9, 13):
            m_bucket = 5
            h_bucket = dt.hour
        else:
            m_bucket = ((minute - 1) // 5 + 1) * 5
            h_bucket = dt.hour
            if m_bucket == 60:
                h_bucket += 1
                m_bucket = 0
                if h_bucket == 12:
                    h_bucket = 11
                    m_bucket = 30
                    
        bucket_time = f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d} {h_bucket:02d}:{m_bucket:02d}:00"
        buckets[bucket_time].append(k)
        
    synthesized = []
    for bucket_time, bars in sorted(buckets.items()):
        first = bars[0]
        last = bars[-1]
        synthesized.append({
            "date": bucket_time,
            "open": first["open"],
            "high": max(b["high"] for b in bars),
            "low": min(b["low"] for b in bars),
            "close": last["close"],
            "volume": sum(b["volume"] for b in bars),
            "amount": sum(b["amount"] for b in bars),
        })
    return synthesized


def patched_run_backtest(symbol: str, start_date: str, end_date: str) -> None:
    # 1. Load raw 1M K-lines from QMT
    klines_1m = query_1m_klines(symbol, start_date, end_date)
    if not klines_1m:
        print(f"\n❌ {symbol}: 没有找到 1M K线数据 ({start_date} ~ {end_date})")
        return
        
    # 2. Synthesize 5M K-lines
    klines_5m = synthesize_5m(klines_1m)
    print(f"📊 {symbol}: 载入 {len(klines_1m)} 根 1M K线 -> 合成 {len(klines_5m)} 根 5M K线")

    # 3. Patch the kline_lake query inside run_backtest dynamically
    import server.engines.t0.t0_backtest as backtest_module
    
    # Backup original query function
    original_query = backtest_module.query_klines
    
    # Mock query_klines to return our in-memory synthesized K-lines
    def mock_query(sym, freq, **kwargs):
        if freq == "1":
            return klines_1m
        if freq == "5":
            return klines_5m
        return []
        
    backtest_module.query_klines = mock_query
    
    try:
        config = BacktestConfig(
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            t0_qty=100,
            use_paper_db=False,  # 纯内存运行，不影响实盘
        )
        result = run_backtest(config)
        print_backtest_report(result)
    finally:
        # Restore original function
        backtest_module.query_klines = original_query


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run active T+0 backtest.")
    parser.add_argument("--symbol", action="append", help="Target symbol, e.g. sh.688008. Can repeat.")
    parser.add_argument("--start-date", default="2026-05-20", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", default="2026-06-01", help="End date (YYYY-MM-DD)")
    args = parser.parse_args()
    
    target_symbols = args.symbol or [
        "sh.688008",  # 澜起科技
        "sz.300394",  # 天孚通信
        "sz.002475",  # 立讯精密
        "sh.688256",  # 寒武纪
        "sz.300548",  # 长芯博创
    ]
    
    print(f"============================================================")
    print(f"  CT-OS V4.0 量化做T策略历史回测  |  区间: {args.start_date} ~ {args.end_date}")
    print(f"============================================================")
    
    for symbol in target_symbols:
        patched_run_backtest(symbol, args.start_date, args.end_date)
