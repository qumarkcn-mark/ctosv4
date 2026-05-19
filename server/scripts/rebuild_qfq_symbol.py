"""重建单只股票的本地标准前复权 K 线。

示例：
    PYTHONPATH=. python -m server.scripts.rebuild_qfq_symbol sz.301076 --start-date 2021-10-11
"""

from __future__ import annotations

import argparse
import json

from server.services.qfq_normalizer import detect_qfq_inconsistency, rebuild_symbol_qfq


def main() -> None:
    parser = argparse.ArgumentParser(description="重建单只股票标准前复权 K 线")
    parser.add_argument("symbol", help="股票代码，如 sz.301076")
    parser.add_argument("--start-date", default="2010-01-01", help="起始日期，默认 2010-01-01")
    parser.add_argument("--end-date", default=None, help="结束日期，默认今天")
    parser.add_argument("--no-minutes", action="store_true", help="只重建日线和周线")
    args = parser.parse_args()

    before = detect_qfq_inconsistency(args.symbol, limit=90)
    result = rebuild_symbol_qfq(
        args.symbol,
        start_date=args.start_date,
        end_date=args.end_date,
        include_minutes=not args.no_minutes,
    )
    after = detect_qfq_inconsistency(args.symbol, limit=90)

    print(json.dumps({
        "symbol": result.symbol,
        "start_date": result.start_date,
        "end_date": result.end_date,
        "day_rows": result.day_rows,
        "minute_rows": result.minute_rows,
        "total_rows": result.total_rows,
        "before_inconsistency": before[:10],
        "after_inconsistency": after[:10],
        "suspicious_gaps": result.suspicious_gaps[:10],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
