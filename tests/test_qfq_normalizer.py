from server.services.qfq_normalizer import (
    aggregate_week_rows,
    build_qfq_day_rows,
    normalize_minute_rows,
    rebuild_symbol_qfq,
)


def test_build_qfq_day_rows_uses_pct_chg_to_remove_ex_right_gap():
    rows = [
        {
            "date": "2026-05-15",
            "open": 43.71,
            "high": 45.03,
            "low": 42.69,
            "close": 43.18,
            "volume": 12282085,
            "amount": 535868613.96,
            "pctChg": "4.907700",
        },
        {
            "date": "2026-05-18",
            "open": 32.88,
            "high": 32.88,
            "low": 31.50,
            "close": 32.33,
            "volume": 9899413,
            "amount": 319756600.19,
            "pctChg": "-2.444200",
        },
    ]

    qfq_rows, suspicious = build_qfq_day_rows(rows)

    assert suspicious == []
    assert qfq_rows[-1]["close"] == 32.33
    assert qfq_rows[0]["close"] == 33.14
    assert qfq_rows[0]["open"] == 33.5468
    assert round((qfq_rows[-1]["close"] / qfq_rows[0]["close"] - 1) * 100, 4) == -2.4442


def test_normalize_minute_rows_uses_same_day_factor():
    qfq_day_rows = [
        {"date": "2026-05-15", "qfq_factor": 0.767485},
        {"date": "2026-05-18", "qfq_factor": 1.0},
    ]
    minute_rows = [
        {
            "date": "2026-05-15 15:00:00",
            "open": 43.0,
            "high": 44.0,
            "low": 42.0,
            "close": 43.18,
            "volume": 100,
            "amount": 1000,
        },
        {
            "date": "2026-05-18 09:35:00",
            "open": 32.0,
            "high": 33.0,
            "low": 31.0,
            "close": 32.33,
            "volume": 200,
            "amount": 2000,
        },
    ]

    normalized = normalize_minute_rows(minute_rows, qfq_day_rows)

    assert normalized[0]["close"] == round(43.18 * 0.767485, 4)
    assert normalized[1]["close"] == 32.33
    assert normalized[0]["volume"] == 100


def test_aggregate_week_rows_from_qfq_day_rows():
    rows = [
        {"date": "2026-05-11", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100, "amount": 1000},
        {"date": "2026-05-12", "open": 11, "high": 13, "low": 10, "close": 12, "volume": 200, "amount": 2000},
        {"date": "2026-05-18", "open": 20, "high": 22, "low": 19, "close": 21, "volume": 300, "amount": 3000},
    ]

    weeks = aggregate_week_rows(rows)

    assert weeks == [
        {"date": "2026-05-12", "open": 10, "high": 13, "low": 9, "close": 12, "volume": 300, "amount": 3000},
        {"date": "2026-05-18", "open": 20, "high": 22, "low": 19, "close": 21, "volume": 300, "amount": 3000},
    ]


def test_rebuild_symbol_qfq_reports_week_rows(monkeypatch):
    raw_days = [
        {
            "date": "2026-05-11",
            "open": 10,
            "high": 12,
            "low": 9,
            "close": 11,
            "volume": 100,
            "amount": 1000,
            "pctChg": "0",
        },
        {
            "date": "2026-05-12",
            "open": 11,
            "high": 13,
            "low": 10,
            "close": 12,
            "volume": 200,
            "amount": 2000,
            "pctChg": "9.0909",
        },
    ]
    writes = []

    monkeypatch.setattr("server.services.qfq_normalizer._query_raw_with_pct", lambda *args: raw_days)
    monkeypatch.setattr("server.services.qfq_normalizer._query_raw_without_pct", lambda *args: [])

    def fake_upsert(symbol, freq, rows, adjustflag="2", source="baostock"):
        writes.append((symbol, freq, list(rows), adjustflag, source))
        return len(rows)

    monkeypatch.setattr("server.services.qfq_normalizer.upsert_klines", fake_upsert)

    result = rebuild_symbol_qfq(
        "sh.600000",
        start_date="2026-05-11",
        end_date="2026-05-12",
        include_minutes=False,
        target_freqs=["week"],
    )

    assert result.day_rows == 2
    assert result.week_rows == 1
    assert result.total_rows == 3
    assert [item[1] for item in writes] == ["day", "week"]
