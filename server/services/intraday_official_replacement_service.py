"""Mark intraday preview bars after official TDX bars are persisted."""

from __future__ import annotations

from server.db.kline_lake import mark_intraday_replaced_by_official
from server.domain.symbols import normalize_symbol


def mark_intraday_replaced_for_official_rows(
    symbol: str,
    freq: str,
    rows: list[dict],
    *,
    batch_id: str = "",
) -> int:
    """Hide same-day quote preview bars when official 1m rows are stored.

    Only official 1m rows replace quote-aggregated preview rows. Higher
    frequencies are derived from 1m and should not independently mark preview.
    """
    if str(freq) != "1" or not rows:
        return 0
    canonical = normalize_symbol(symbol)
    trade_dates = {
        str(row.get("date") or row.get("bar_time") or "")[:10]
        for row in rows
        if row.get("date") or row.get("bar_time")
    }
    marked = 0
    for trade_date in sorted(day for day in trade_dates if day):
        marked += mark_intraday_replaced_by_official(
            canonical,
            trade_date=trade_date,
            freq="1",
            batch_id=batch_id,
        )
    return marked
