#!/usr/bin/env python3
"""CT-OS V4.0 历史 K 线离线回填脚本

用法示例：
  # 回填单只股票所有级别
  python -m server.scripts.backfill_klines --symbol sh.600519

  # 批量回填多只股票
  python -m server.scripts.backfill_klines --symbol sh.600519 sh.000001 sz.000858

  # 只回填日线
  python -m server.scripts.backfill_klines --symbol sh.600519 --freq day

  # 指定起始日期（默认日线从 2015 年、分钟线从 2019 年开始）
  python -m server.scripts.backfill_klines --symbol sh.600519 --start 2020-01-01
"""

import argparse
import logging
import sys
import time
from pathlib import Path

# 确保 server 包可以 import
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from server.db.kline_lake import init_lake, count_klines
from server.services.baostock_service import fetch_klines_sync, FREQ_MAP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill")

# 默认回填的级别列表
DEFAULT_FREQS = ["week", "day", "60", "30", "15", "5"]


def backfill(symbols: list[str], freqs: list[str], start_date: str = None) -> None:
    """批量回填 K 线数据湖"""
    init_lake()

    total_written = 0
    t0 = time.time()

    for symbol in symbols:
        for freq in freqs:
            before = count_klines(symbol, freq)
            try:
                written = fetch_klines_sync(symbol, freq, start_date=start_date)
                after = count_klines(symbol, freq)
                total_written += written
                print(f"  ✅ {symbol}/{freq:<5}: +{written:>5} 条  (总计: {after} 条)")
            except Exception as e:
                print(f"  ❌ {symbol}/{freq}: {e}")

    elapsed = time.time() - t0
    print(f"\n回填完成！共写入 {total_written} 条 K 线，耗时 {elapsed:.1f}s")


def main():
    parser = argparse.ArgumentParser(description="CT-OS K线历史数据回填工具")
    parser.add_argument(
        "--symbol", nargs="+", required=True,
        help="股票代码列表 (BaoStock 格式, 如 sh.600519 sz.000001)"
    )
    parser.add_argument(
        "--freq", nargs="+", choices=list(FREQ_MAP.keys()),
        default=DEFAULT_FREQS,
        help=f"K线级别 (默认: {' '.join(DEFAULT_FREQS)})"
    )
    parser.add_argument(
        "--start", type=str, default=None,
        help="数据起始日期 (格式: YYYY-MM-DD，默认: 日线2015年/分钟线2019年)"
    )
    args = parser.parse_args()

    print(f"\n🚀 开始回填 {len(args.symbol)} 只股票 × {len(args.freq)} 个级别")
    print(f"   股票: {', '.join(args.symbol)}")
    print(f"   级别: {', '.join(args.freq)}")
    if args.start:
        print(f"   起始: {args.start}")
    print()

    backfill(args.symbol, args.freq, start_date=args.start)


if __name__ == "__main__":
    main()
