"""Backfill formal market-data tables from the legacy `klines` table.

旧数据湖已经积累了大量 `klines(adjustflag=2/3)`。formal 数据层上线后，
这些历史数据需要一次性迁移到 `raw_bars` / `adjusted_bars`，否则新的
CZSC source policy 会因为 formal 表为空而继续落回 legacy。
"""

from __future__ import annotations

import sqlite3
from typing import Iterable

from server.db.kline_lake import FORMAL_DATA_SCHEMA, LakeSource, get_lake_write_connection


DEFAULT_FREQS = ("week", "day", "60", "30", "15", "5", "1")


def backfill_formal_tables_from_legacy(
    *,
    source: LakeSource = "tdx",
    symbols: Iterable[str] | None = None,
    freqs: Iterable[str] | None = None,
    batch_id: str = "legacy_formal_backfill_v1",
) -> dict:
    """Copy legacy K-line rows into formal raw/qfq tables.

    这是幂等操作：同一个 symbol/freq/date/dataset 会 upsert 覆盖。
    """
    if source not in {"tdx", "baostock"}:
        raise ValueError(f"unsupported formal backfill source: {source}")

    normalized_freqs = tuple(dict.fromkeys(str(freq) for freq in (freqs or DEFAULT_FREQS)))
    dataset_raw = f"{source}_raw"
    dataset_qfq = f"{source}_qfq"
    factor_source = "legacy_day_close_ratio"
    conn = get_lake_write_connection(source)
    try:
        conn.executescript(FORMAL_DATA_SCHEMA)
        symbol_list = _resolve_symbols(conn, symbols)
        totals = {"raw_bars": 0, "adjusted_bars": 0, "qfq_factors": 0}
        details = []
        for symbol in symbol_list:
            for freq in normalized_freqs:
                raw = _backfill_raw_bars(conn, symbol, freq, dataset=dataset_raw, batch_id=batch_id)
                adjusted = _backfill_adjusted_bars(conn, symbol, freq, dataset=dataset_qfq, batch_id=batch_id)
                totals["raw_bars"] += raw
                totals["adjusted_bars"] += adjusted
                if raw or adjusted:
                    details.append({"symbol": symbol, "freq": freq, "raw_bars": raw, "adjusted_bars": adjusted})
            factors = _backfill_qfq_factors(
                conn,
                symbol,
                dataset=dataset_qfq,
                factor_source=factor_source,
                batch_id=batch_id,
            )
            totals["qfq_factors"] += factors
        conn.commit()
        return {
            "status": "success",
            "source": source,
            "symbols": len(symbol_list),
            "freqs": list(normalized_freqs),
            "batch_id": batch_id,
            "totals": totals,
            "details": details,
        }
    finally:
        conn.close()


def _resolve_symbols(conn: sqlite3.Connection, symbols: Iterable[str] | None) -> list[str]:
    if symbols is not None:
        return sorted({str(symbol).strip() for symbol in symbols if str(symbol).strip()})
    rows = conn.execute("SELECT DISTINCT symbol FROM klines ORDER BY symbol").fetchall()
    return [str(row["symbol"]) for row in rows]


def _backfill_raw_bars(
    conn: sqlite3.Connection,
    symbol: str,
    freq: str,
    *,
    dataset: str,
    batch_id: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO raw_bars (
            symbol, freq, date, open, high, low, close, volume, amount, dataset, batch_id
        )
        SELECT symbol, freq, date, open, high, low, close, volume, amount, ?, ?
          FROM klines
         WHERE symbol = ? AND freq = ? AND adjustflag = '3'
        ON CONFLICT(symbol, freq, date, dataset)
        DO UPDATE SET
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume,
            amount = excluded.amount,
            batch_id = excluded.batch_id,
            updated_at = CURRENT_TIMESTAMP
        """,
        (dataset, batch_id, symbol, freq),
    )
    return int(cursor.rowcount or 0)


def _backfill_adjusted_bars(
    conn: sqlite3.Connection,
    symbol: str,
    freq: str,
    *,
    dataset: str,
    batch_id: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO adjusted_bars (
            symbol, freq, date, open, high, low, close, volume, amount,
            factor, dataset, factor_signature, batch_id
        )
        SELECT q.symbol, q.freq, q.date, q.open, q.high, q.low, q.close, q.volume, q.amount,
               CASE WHEN r.close > 0 THEN q.close / r.close ELSE 1.0 END,
               ?, 'legacy_backfill_v1', ?
          FROM klines q
          LEFT JOIN klines r
            ON r.symbol = q.symbol
           AND r.freq = q.freq
           AND r.date = q.date
           AND r.adjustflag = '3'
         WHERE q.symbol = ? AND q.freq = ? AND q.adjustflag = '2'
        ON CONFLICT(symbol, freq, date, dataset)
        DO UPDATE SET
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume,
            amount = excluded.amount,
            factor = excluded.factor,
            dataset = excluded.dataset,
            factor_signature = excluded.factor_signature,
            batch_id = excluded.batch_id,
            updated_at = CURRENT_TIMESTAMP
        """,
        (dataset, batch_id, symbol, freq),
    )
    return int(cursor.rowcount or 0)


def _backfill_qfq_factors(
    conn: sqlite3.Connection,
    symbol: str,
    *,
    dataset: str,
    factor_source: str,
    batch_id: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO qfq_factors (
            symbol, trade_date, factor, source, factor_signature, batch_id
        )
        SELECT q.symbol,
               substr(q.date, 1, 10) AS trade_date,
               CASE WHEN r.close > 0 THEN q.close / r.close ELSE 1.0 END AS factor,
               ?,
               ?,
               ?
          FROM klines q
          JOIN klines r
            ON r.symbol = q.symbol
           AND r.freq = q.freq
           AND substr(r.date, 1, 10) = substr(q.date, 1, 10)
           AND r.adjustflag = '3'
         WHERE q.symbol = ?
           AND q.freq = 'day'
           AND q.adjustflag = '2'
           AND q.close > 0
           AND r.close > 0
        ON CONFLICT(symbol, trade_date, source)
        DO UPDATE SET
            factor = excluded.factor,
            factor_signature = excluded.factor_signature,
            batch_id = excluded.batch_id,
            updated_at = CURRENT_TIMESTAMP
        """,
        (factor_source, f"{dataset}:legacy_day_close_ratio", batch_id, symbol),
    )
    return int(cursor.rowcount or 0)
