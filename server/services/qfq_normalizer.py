"""本地标准前复权 K 线生成。

BaoStock 在除权当天可能出现“当日价已除权、历史价未按最新因子重算”的
混合状态。CZSC 不能直接消费这种序列，否则会把除权跳空当成真实走势。

本模块只负责数据层标准化：用不复权 K 线作为事实源，用日线 pctChg 反推
连续可比的前复权日线，再把同一交易日的分钟线按日线比例缩放。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional

from server.db.kline_lake import query_klines, upsert_adjusted_bars, upsert_qfq_factors
from server.domain.symbols import normalize_symbol
from server.services.baostock_service import CHUNK_DAYS, FREQ_MAP, _bs_query

logger = logging.getLogger(__name__)

MINUTE_FREQS = ("60", "30", "15", "5")


@dataclass(frozen=True)
class QfqBuildResult:
    symbol: str
    day_rows: int
    week_rows: int
    minute_rows: dict[str, int]
    start_date: str
    end_date: str
    suspicious_gaps: list[dict]

    @property
    def total_rows(self) -> int:
        return self.day_rows + self.week_rows + sum(self.minute_rows.values())


def build_qfq_day_rows(raw_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """用 pctChg 从最新收盘价反推标准前复权日线。

    raw_rows 必须按日期正序，且包含不复权 OHLC 和 pctChg。最新一根保持真实
    价格，历史价格按涨跌幅链条反推，得到一条连续可比的前复权序列。
    """
    rows = [dict(row) for row in raw_rows if _valid_price_row(row)]
    if not rows:
        return [], []

    adjusted_closes: list[float] = [0.0] * len(rows)
    adjusted_closes[-1] = float(rows[-1]["close"])

    for index in range(len(rows) - 2, -1, -1):
        pct_chg = _parse_pct_chg(rows[index + 1].get("pctChg"))
        adjusted_closes[index] = adjusted_closes[index + 1] / (1 + pct_chg / 100)

    result: list[dict] = []
    suspicious_gaps: list[dict] = []
    previous_adjusted_close: Optional[float] = None

    for index, row in enumerate(rows):
        raw_close = float(row["close"])
        ratio = adjusted_closes[index] / raw_close if raw_close else 1.0
        adjusted = _scale_ohlc(row, ratio)
        adjusted["volume"] = float(row.get("volume", 0) or 0)
        adjusted["amount"] = float(row.get("amount", 0) or 0)
        adjusted["qfq_factor"] = ratio
        result.append(adjusted)

        if previous_adjusted_close:
            actual_pct = (adjusted["close"] / previous_adjusted_close - 1) * 100
            stated_pct = _parse_pct_chg(row.get("pctChg"))
            if abs(actual_pct - stated_pct) > 0.02:
                suspicious_gaps.append({
                    "date": row["date"],
                    "actual_pct": round(actual_pct, 4),
                    "stated_pct": round(stated_pct, 4),
                    "diff": round(actual_pct - stated_pct, 4),
                })
        previous_adjusted_close = adjusted["close"]

    return result, suspicious_gaps


def normalize_minute_rows(raw_rows: Iterable[dict], day_qfq_rows: Iterable[dict]) -> list[dict]:
    """按交易日复权比例缩放分钟线 OHLC。"""
    factor_by_day = {
        str(row["date"])[:10]: float(row.get("qfq_factor", 1.0) or 1.0)
        for row in day_qfq_rows
    }
    normalized: list[dict] = []
    for row in raw_rows:
        if not _valid_price_row(row):
            continue
        trade_day = str(row["date"])[:10]
        factor = factor_by_day.get(trade_day)
        if factor is None:
            continue
        item = _scale_ohlc(row, factor)
        item["volume"] = float(row.get("volume", 0) or 0)
        item["amount"] = float(row.get("amount", 0) or 0)
        item["qfq_factor"] = factor
        normalized.append(item)
    return normalized


def aggregate_week_rows(day_rows: Iterable[dict]) -> list[dict]:
    """从标准前复权日线聚合周线，避免直接使用外部周线复权结果。"""
    buckets: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for row in day_rows:
        try:
            date_value = datetime.strptime(str(row["date"])[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        iso_year, iso_week, _ = date_value.isocalendar()
        buckets[(iso_year, iso_week)].append(row)

    weeks: list[dict] = []
    for key in sorted(buckets):
        rows = sorted(buckets[key], key=lambda item: item["date"])
        first = rows[0]
        last = rows[-1]
        weeks.append({
            "date": str(last["date"])[:10],
            "open": float(first["open"]),
            "high": max(float(row["high"]) for row in rows),
            "low": min(float(row["low"]) for row in rows),
            "close": float(last["close"]),
            "volume": sum(float(row.get("volume", 0) or 0) for row in rows),
            "amount": sum(float(row.get("amount", 0) or 0) for row in rows),
        })
    return weeks


def rebuild_symbol_qfq(
    symbol: str,
    *,
    start_date: str = "2010-01-01",
    end_date: Optional[str] = None,
    include_minutes: bool = True,
    target_freqs: Optional[Iterable[str]] = None,
) -> QfqBuildResult:
    """重建单只股票的标准前复权缓存，写入 adjustflag=2。"""
    canonical_symbol = normalize_symbol(symbol)
    effective_end = end_date or datetime.today().strftime("%Y-%m-%d")
    requested = set(target_freqs or (["day", "week", *MINUTE_FREQS] if include_minutes else ["day", "week"]))
    if requested & {"week", *MINUTE_FREQS}:
        requested.add("day")
    day_rows = _query_raw_with_pct(canonical_symbol, "day", start_date, effective_end)
    qfq_day_rows, suspicious = build_qfq_day_rows(day_rows)
    if not qfq_day_rows:
        return QfqBuildResult(canonical_symbol, 0, 0, {}, start_date, effective_end, suspicious)

    day_written = 0
    upsert_qfq_factors(canonical_symbol, qfq_day_rows, source_name="baostock_pctchg", lake_source="baostock")
    if "day" in requested:
        day_written = upsert_adjusted_bars(
            canonical_symbol,
            "day",
            qfq_day_rows,
            dataset="baostock_qfq",
            source="baostock",
        )
    if "week" in requested:
        week_rows = aggregate_week_rows(qfq_day_rows)
    else:
        week_rows = []
    week_written = 0
    if "week" in requested and week_rows:
        week_written = upsert_adjusted_bars(
            canonical_symbol,
            "week",
            week_rows,
            dataset="baostock_qfq",
            source="baostock",
        )

    minute_written: dict[str, int] = {}
    for freq in MINUTE_FREQS:
        if freq not in requested:
            continue
        raw_minute_rows = _query_raw_without_pct(canonical_symbol, freq, start_date, effective_end)
        qfq_minute_rows = normalize_minute_rows(raw_minute_rows, qfq_day_rows)
        if qfq_minute_rows:
            minute_written[freq] = upsert_adjusted_bars(
                canonical_symbol,
                freq,
                qfq_minute_rows,
                source="baostock",
                dataset="baostock_qfq",
            )
        else:
            minute_written[freq] = 0

    logger.info(
        "标准前复权重建完成 %s [%s~%s]: day=%d, minutes=%s, suspicious=%d",
        canonical_symbol,
        start_date,
        effective_end,
        day_written,
        minute_written,
        len(suspicious),
    )
    return QfqBuildResult(canonical_symbol, day_written, week_written, minute_written, start_date, effective_end, suspicious)


def detect_qfq_inconsistency(symbol: str, *, limit: int = 60, threshold_pct: float = 5.0) -> list[dict]:
    """检测缓存中的前复权序列是否出现疑似除权污染跳空。"""
    rows = query_klines(normalize_symbol(symbol), "day", limit=limit, adjustflag="2", source="baostock")
    issues: list[dict] = []
    for previous, current in zip(rows, rows[1:]):
        prev_close = float(previous.get("close") or 0)
        close = float(current.get("close") or 0)
        if not prev_close or not close:
            continue
        pct = (close / prev_close - 1) * 100
        if abs(pct) >= threshold_pct:
            issues.append({
                "date": current["date"],
                "previous_date": previous["date"],
                "previous_close": prev_close,
                "close": close,
                "pct": round(pct, 4),
            })
    return issues


def _query_raw_with_pct(symbol: str, freq: str, start_date: str, end_date: str) -> list[dict]:
    bs_freq = FREQ_MAP.get(freq)
    if not bs_freq:
        raise ValueError(f"unsupported freq: {freq}")
    return _query_raw_chunks(symbol, freq, bs_freq, start_date, end_date)


def _query_raw_without_pct(symbol: str, freq: str, start_date: str, end_date: str) -> list[dict]:
    bs_freq = FREQ_MAP.get(freq)
    if not bs_freq:
        raise ValueError(f"unsupported freq: {freq}")
    return _query_raw_chunks(symbol, freq, bs_freq, start_date, end_date)


def _query_raw_chunks(symbol: str, freq: str, bs_freq: str, start_date: str, end_date: str) -> list[dict]:
    chunk_days = CHUNK_DAYS.get(freq, 180)
    rows: list[dict] = []
    cursor_date = start_date
    while cursor_date <= end_date:
        chunk_end = (datetime.strptime(cursor_date, "%Y-%m-%d") + timedelta(days=chunk_days)).strftime("%Y-%m-%d")
        if chunk_end > end_date:
            chunk_end = end_date
        rows.extend(_bs_query(symbol, bs_freq, cursor_date, chunk_end, "3"))
        cursor_date = (datetime.strptime(chunk_end, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    return rows


def _scale_ohlc(row: dict, factor: float) -> dict:
    return {
        "date": row["date"],
        "open": round(float(row["open"]) * factor, 4),
        "high": round(float(row["high"]) * factor, 4),
        "low": round(float(row["low"]) * factor, 4),
        "close": round(float(row["close"]) * factor, 4),
    }


def _parse_pct_chg(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _valid_price_row(row: dict) -> bool:
    try:
        return float(row.get("open", 0) or 0) > 0 and float(row.get("close", 0) or 0) > 0
    except (TypeError, ValueError):
        return False
