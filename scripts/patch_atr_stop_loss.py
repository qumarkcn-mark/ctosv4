#!/usr/bin/env python3
"""
patch_atr_stop_loss.py
──────────────────────
对 positions 表里所有 stop_loss_price IS NULL 的持仓，
用 BaoStock 拉 60 根日线算 ATR(14)，再以 均价 - 3×ATR 写入止损价。

用法：
    cd /Users/markqu/Desktop/ct-os-v4
    source venv/bin/activate
    python scripts/patch_atr_stop_loss.py

可选参数（直接改下方常量）：
    ATR_PERIOD       = 14     # ATR 周期
    ATR_MULTIPLIER   = 3.0    # 止损 = 均价 - multiplier × ATR
    KLINE_COUNT      = 60     # 拉取日线根数
    DRY_RUN          = False  # True = 只打印不写库
"""

import sqlite3
import logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────
DB_PATH        = "data/ctos.db"
ATR_PERIOD     = 14
ATR_MULTIPLIER = 3.0
KLINE_COUNT    = 60
DRY_RUN        = False       # 改为 True 可预览不写库
# ─────────────────────────────────────────────────────


def baostock_symbol(sym: str) -> str:
    """sh600519 → sh.600519"""
    if "." not in sym and sym[:2] in ("sh", "sz"):
        return f"{sym[:2]}.{sym[2:]}"
    return sym


def fetch_daily_klines(symbol: str, count: int = 60) -> list[dict]:
    """用 BaoStock 拉最近 count 根日线，返回 [{date, open, high, low, close}]"""
    import baostock as bs

    end_date   = datetime.today().strftime("%Y-%m-%d")
    start_date = (datetime.today() - timedelta(days=count * 2)).strftime("%Y-%m-%d")
    bs_sym     = baostock_symbol(symbol)

    lg = bs.login()
    if lg.error_code != "0":
        raise RuntimeError(f"BaoStock login 失败: {lg.error_msg}")

    rs = bs.query_history_k_data_plus(
        bs_sym,
        "date,open,high,low,close",
        start_date=start_date,
        end_date=end_date,
        frequency="d",
        adjustflag="3",   # 不复权
    )
    bs.logout()

    rows = []
    while rs.error_code == "0" and rs.next():
        row = rs.get_row_data()
        try:
            rows.append({
                "date":  row[0],
                "open":  float(row[1]),
                "high":  float(row[2]),
                "low":   float(row[3]),
                "close": float(row[4]),
            })
        except (ValueError, IndexError):
            continue

    # 取最近 count 根
    return rows[-count:] if len(rows) >= count else rows


def calculate_atr(klines: list[dict], period: int = 14) -> float:
    """Wilder ATR，与 server/services/atr_service.py 保持一致"""
    if len(klines) <= period:
        return 0.0

    trs = [klines[0]["high"] - klines[0]["low"]]
    for i in range(1, len(klines)):
        c = klines[i]
        p = klines[i - 1]
        tr = max(
            c["high"] - c["low"],
            abs(c["high"] - p["close"]),
            abs(c["low"]  - p["close"]),
        )
        trs.append(tr)

    atr = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period

    return round(atr, 3)


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur  = conn.cursor()

    cur.execute(
        "SELECT id, symbol, name, avg_cost FROM positions "
        "WHERE quantity > 0 AND stop_loss_price IS NULL"
    )
    positions = cur.fetchall()

    if not positions:
        logger.info("✅ 没有需要补充 ATR 的持仓，全部已设置止损价。")
        conn.close()
        return

    logger.info(f"找到 {len(positions)} 个持仓需要补充 ATR 止损价\n")

    results = []
    for pos in positions:
        sym      = pos["symbol"]
        name     = pos["name"] or sym
        avg_cost = pos["avg_cost"]

        try:
            klines = fetch_daily_klines(sym, KLINE_COUNT)
            if len(klines) < ATR_PERIOD + 1:
                logger.warning(f"  ⚠️  {name}({sym}) K 线不足，跳过（仅得 {len(klines)} 根）")
                continue

            atr       = calculate_atr(klines, ATR_PERIOD)
            stop_loss = max(round(avg_cost - atr * ATR_MULTIPLIER, 3), 0.001)
            distance  = (avg_cost - stop_loss) / avg_cost * 100

            logger.info(
                f"  {name:8s}({sym})  均价={avg_cost:.2f}  "
                f"ATR={atr:.3f}  止损={stop_loss:.2f}  "
                f"距离={distance:.1f}%"
            )
            results.append((stop_loss, pos["id"]))

        except Exception as e:
            logger.error(f"  ❌ {name}({sym}) 计算失败: {e}")

    if not results:
        logger.warning("没有成功计算任何止损价，数据库未修改。")
        conn.close()
        return

    if DRY_RUN:
        logger.info(f"\n[DRY RUN] 以上结果不会写入数据库（共 {len(results)} 条）")
    else:
        for stop_loss, pos_id in results:
            cur.execute(
                "UPDATE positions SET stop_loss_price = ? WHERE id = ?",
                (stop_loss, pos_id),
            )
        conn.commit()
        logger.info(f"\n✅ 已更新 {len(results)} 个持仓的止损价 → data/ctos.db")

    conn.close()


if __name__ == "__main__":
    main()
