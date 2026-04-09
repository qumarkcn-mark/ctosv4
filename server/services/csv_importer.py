"""CSV 交割单导入服务

支持东方财富和同花顺的交割单格式。
"""

import csv
import io
import sqlite3
from datetime import datetime
from typing import Optional

from server.services.position_calc import recalculate_all_positions


def import_csv(
    conn: sqlite3.Connection,
    user_id: int,
    csv_content: str,
    broker: str = "auto",
) -> dict:
    """
    导入券商交割单 CSV。

    Args:
        conn: 数据库连接
        user_id: 用户 ID
        csv_content: CSV 文件文本内容
        broker: "eastmoney" / "ths" / "auto"

    Returns:
        {"imported": 15, "skipped": 2, "errors": [...]}
    """
    # 自动检测格式
    if broker == "auto":
        broker = _detect_broker(csv_content)

    if broker == "eastmoney":
        trades = _parse_eastmoney(csv_content)
    elif broker == "ths":
        trades = _parse_ths(csv_content)
    else:
        return {"imported": 0, "skipped": 0, "errors": [f"未知的券商格式: {broker}"]}

    imported = 0
    skipped = 0
    errors = []

    for i, trade in enumerate(trades):
        try:
            conn.execute(
                """
                INSERT INTO trades
                    (user_id, symbol, name, direction, price, quantity, amount,
                     source, traded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'CSV_IMPORT', ?)
                """,
                (
                    user_id,
                    trade["symbol"],
                    trade.get("name"),
                    trade["direction"],
                    trade["price"],
                    trade["quantity"],
                    trade["price"] * trade["quantity"],
                    trade["traded_at"],
                ),
            )
            imported += 1
        except Exception as e:
            errors.append(f"第 {i + 1} 行: {e}")
            skipped += 1

    # 重算所有持仓
    if imported > 0:
        recalculate_all_positions(conn, user_id)
        conn.commit()

    return {"imported": imported, "skipped": skipped, "errors": errors}


def _detect_broker(csv_content: str) -> str:
    """根据 CSV 内容自动检测券商"""
    header = csv_content[:500].lower()
    if "成交日期" in header and "证券代码" in header:
        if "佣金" in header or "印花税" in header:
            return "eastmoney"
    if "委托日期" in header or "成交均价" in header:
        return "ths"
    return "eastmoney"  # 默认


def _normalize_symbol(code: str, market: str = "") -> str:
    """规范化股票代码为 shXXXXXX / szXXXXXX 格式"""
    code = code.strip().replace(" ", "")
    # 去掉已有的前缀
    for prefix in ("sh", "sz", "SH", "SZ"):
        if code.startswith(prefix):
            code = code[2:]
            break

    # 补齐6位
    code = code.zfill(6)

    # 判断市场
    if market:
        m = market.strip().lower()
        if "上海" in m or "沪" in m or m in ("sh", "ssa"):
            return f"sh{code}"
        elif "深圳" in m or "深" in m or m in ("sz", "sza"):
            return f"sz{code}"

    # 根据代码判断
    if code.startswith(("6", "5", "9")):
        return f"sh{code}"
    else:
        return f"sz{code}"


def _parse_direction(text: str) -> Optional[str]:
    """解析买卖方向"""
    text = text.strip()
    if "买" in text:
        return "BUY"
    elif "卖" in text:
        return "SELL"
    return None


def _parse_eastmoney(csv_content: str) -> list[dict]:
    """解析东方财富交割单"""
    trades = []
    reader = csv.DictReader(io.StringIO(csv_content))

    for row in reader:
        try:
            direction = _parse_direction(row.get("买卖标志", row.get("操作", "")))
            if not direction:
                continue

            symbol = _normalize_symbol(
                row.get("证券代码", ""),
                row.get("交易市场", row.get("市场", "")),
            )
            price = float(row.get("成交价格", row.get("成交均价", "0")))
            quantity = abs(int(float(row.get("成交数量", "0"))))
            traded_at = row.get("成交日期", row.get("交易日期", ""))

            if price > 0 and quantity > 0:
                trades.append({
                    "symbol": symbol,
                    "name": row.get("证券名称", row.get("股票名称", None)),
                    "direction": direction,
                    "price": price,
                    "quantity": quantity,
                    "traded_at": traded_at,
                })
        except (ValueError, KeyError):
            continue

    return trades


def _parse_ths(csv_content: str) -> list[dict]:
    """解析同花顺交割单"""
    trades = []
    reader = csv.DictReader(io.StringIO(csv_content))

    for row in reader:
        try:
            direction = _parse_direction(row.get("操作", row.get("买卖方向", "")))
            if not direction:
                continue

            symbol = _normalize_symbol(
                row.get("证券代码", row.get("代码", "")),
                row.get("市场", ""),
            )
            price = float(row.get("成交均价", row.get("成交价格", "0")))
            quantity = abs(int(float(row.get("成交数量", "0"))))
            traded_at = row.get("成交日期", row.get("委托日期", ""))

            if price > 0 and quantity > 0:
                trades.append({
                    "symbol": symbol,
                    "name": row.get("证券名称", None),
                    "direction": direction,
                    "price": price,
                    "quantity": quantity,
                    "traded_at": traded_at,
                })
        except (ValueError, KeyError):
            continue

    return trades
