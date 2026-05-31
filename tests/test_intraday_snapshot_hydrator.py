from server.engines.ai_native import intraday_snapshot_hydrator as hydrator


def _bar(dt: str, open_price: float, close: float, volume: float = 1000):
    high = max(open_price, close) + 0.1
    low = min(open_price, close) - 0.1
    return {
        "date": dt,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": close * volume,
    }


def test_intraday_snapshot_reads_today_1m_and_marks_preview(monkeypatch):
    rows = []
    price = 100.0
    for idx in range(240):
        hour = 9 + (31 + idx) // 60
        minute = (31 + idx) % 60
        if idx >= 120:
            hour = 13 + (idx - 120) // 60
            minute = 1 + (idx - 120) % 60
        price += 0.02
        rows.append(_bar(f"2026-05-26 {hour:02d}:{minute:02d}:00", price - 0.01, price, volume=1000 + idx))

    monkeypatch.setattr(hydrator, "read_tdx_1m_klines", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(hydrator, "read_tdx_day_klines", lambda *_args, **_kwargs: [{"date": "2026-05-25", "close": 100}])

    snapshot = hydrator.hydrate_intraday_snapshot("sh603893", trade_date="2026-05-26", recent_bar_count=10)

    assert snapshot["available"] is True
    assert snapshot["usage"] == "validate_previous_plan"
    assert snapshot["coverage"]["quality"] == "complete_from_open"
    assert snapshot["coverage"]["bar_count"] == 240
    assert snapshot["price"]["prev_close"] == 100
    assert snapshot["macd_1m"]["basis"] == "closed_1m"
    assert len(snapshot["recent_1m_bars"]) == 10
    assert snapshot["relation_to_previous_plan"]["status"] == "ai_should_judge"


def test_intraday_snapshot_reports_missing_today(monkeypatch):
    monkeypatch.setattr(hydrator, "read_tdx_1m_klines", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(hydrator, "query_klines", lambda *_args, **_kwargs: [])
    snapshot = hydrator.hydrate_intraday_snapshot("sh603893", trade_date="2026-05-26")

    assert snapshot["available"] is False
    assert snapshot["reason"] == "NO_TDX_1M_TODAY"


def test_intraday_snapshot_defaults_to_latest_available_1m_date(monkeypatch):
    rows = [_bar(f"2026-05-27 09:{31 + idx:02d}:00", 100 + idx * 0.01, 100.01 + idx * 0.01) for idx in range(20)]

    def fake_read(_symbol, limit=240, start_date=None, end_date=None, **_kwargs):
        if limit == 1 and not start_date:
            return [rows[-1]]
        if start_date == "2026-05-27":
            return rows
        return []

    monkeypatch.setattr(hydrator, "read_tdx_1m_klines", fake_read)
    monkeypatch.setattr(hydrator, "read_tdx_day_klines", lambda *_args, **_kwargs: [{"date": "2026-05-26", "close": 100}])

    snapshot = hydrator.hydrate_intraday_snapshot("sh603893")

    assert snapshot["available"] is True
    assert snapshot["date"] == "2026-05-27"
    assert snapshot["date_basis"] == "latest_available_tdx_1m"
    assert snapshot["coverage"]["bar_count"] == 20


def test_intraday_snapshot_falls_back_to_tdx_lake_1m(monkeypatch):
    rows = [_bar(f"2026-05-27 09:{31 + idx:02d}:00", 100 + idx * 0.01, 100.01 + idx * 0.01) for idx in range(20)]

    def fake_query(_symbol, _freq, **kwargs):
        if kwargs.get("limit") == 1:
            return [rows[-1]]
        if kwargs.get("start_date") == "2026-05-27":
            return rows
        return []

    monkeypatch.setattr(hydrator, "read_tdx_1m_klines", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(hydrator, "query_klines", fake_query)
    monkeypatch.setattr(hydrator, "read_tdx_day_klines", lambda *_args, **_kwargs: [{"date": "2026-05-26", "close": 100}])

    snapshot = hydrator.hydrate_intraday_snapshot("sh603893")

    assert snapshot["available"] is True
    assert snapshot["date"] == "2026-05-27"
    assert snapshot["date_basis"] == "latest_available_tdx_1m"
    assert snapshot["recent_1m_bars"][-1]["t"] == "09:50"


def test_intraday_snapshot_prefers_qmt_preview_lake(monkeypatch):
    qmt_rows = [
        {
            "date": "2026-05-27 10:18:00",
            "open": 32.2,
            "high": 32.4,
            "low": 32.1,
            "close": 32.3,
            "volume": 1000,
            "amount": 32300,
        },
        {
            "date": "2026-05-27 10:19:00",
            "open": 32.3,
            "high": 32.7,
            "low": 32.2,
            "close": 32.6,
            "volume": 1500,
            "amount": 48900,
        },
    ]

    def fake_query(symbol, freq, start_date=None, end_date=None, limit=2000, adjustflag="2", source=None):
        if source == "qmt" and freq == "1":
            return qmt_rows
        return []

    monkeypatch.setattr(hydrator, "query_klines", fake_query)
    monkeypatch.setattr(hydrator, "read_tdx_1m_klines", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(hydrator, "read_tdx_day_klines", lambda *_args, **_kwargs: [])

    snapshot = hydrator.hydrate_intraday_snapshot("sz002158", trade_date="2026-05-27")

    assert snapshot["available"] is True
    assert snapshot["date"] == "2026-05-27"
    assert snapshot["date_basis"] == "requested_trade_date"
    assert snapshot["recent_1m_bars"][-1]["t"] == "10:19"
    assert snapshot["recent_1m_bars"][-1]["c"] == 32.6
