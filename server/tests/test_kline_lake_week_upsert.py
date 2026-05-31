from server.db import kline_lake


def _week_row(date: str, close: float) -> dict:
    return {
        "date": date,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 100,
        "amount": 1000,
    }


def _reset_lake_connections():
    kline_lake._thread_local.lake_conns = {}


def test_week_upsert_replaces_prior_partial_bar_in_same_iso_week(tmp_path, monkeypatch):
    lake_path = tmp_path / "tdx_lake.db"
    monkeypatch.setattr(kline_lake, "TDX_LAKE_PATH", str(lake_path))
    _reset_lake_connections()
    conn = kline_lake.get_lake_write_connection("tdx")
    try:
        conn.executescript(kline_lake.LAKE_SCHEMA)
        conn.commit()
    finally:
        conn.close()

    try:
        kline_lake.upsert_klines("sh.600790", "week", [_week_row("2026-05-25", 10.0)], adjustflag="3", source="tdx")
        kline_lake.upsert_klines("sh.600790", "week", [_week_row("2026-05-26", 11.0)], adjustflag="3", source="tdx")

        rows = kline_lake.query_klines("sh.600790", "week", limit=10, adjustflag="3", source="tdx")

        assert [row["date"] for row in rows] == ["2026-05-26"]
        assert rows[0]["close"] == 11.0
    finally:
        _reset_lake_connections()


def test_week_upsert_keeps_other_weeks_and_adjustflags(tmp_path, monkeypatch):
    lake_path = tmp_path / "tdx_lake.db"
    monkeypatch.setattr(kline_lake, "TDX_LAKE_PATH", str(lake_path))
    _reset_lake_connections()
    conn = kline_lake.get_lake_write_connection("tdx")
    try:
        conn.executescript(kline_lake.LAKE_SCHEMA)
        conn.commit()
    finally:
        conn.close()

    try:
        kline_lake.upsert_klines("sh.600790", "week", [_week_row("2026-05-22", 9.0)], adjustflag="3", source="tdx")
        kline_lake.upsert_klines("sh.600790", "week", [_week_row("2026-05-25", 10.0)], adjustflag="2", source="tdx")
        kline_lake.upsert_klines("sh.600790", "week", [_week_row("2026-05-26", 11.0)], adjustflag="3", source="tdx")

        raw_rows = kline_lake.query_klines("sh.600790", "week", limit=10, adjustflag="3", source="tdx")
        qfq_rows = kline_lake.query_klines("sh.600790", "week", limit=10, adjustflag="2", source="tdx")

        assert [row["date"] for row in raw_rows] == ["2026-05-22", "2026-05-26"]
        assert [row["date"] for row in qfq_rows] == ["2026-05-25"]
    finally:
        _reset_lake_connections()


def test_week_upsert_collapses_incoming_daily_rows_for_same_week(tmp_path, monkeypatch):
    lake_path = tmp_path / "tdx_lake.db"
    monkeypatch.setattr(kline_lake, "TDX_LAKE_PATH", str(lake_path))
    _reset_lake_connections()
    conn = kline_lake.get_lake_write_connection("tdx")
    try:
        conn.executescript(kline_lake.LAKE_SCHEMA)
        conn.commit()
    finally:
        conn.close()

    rows = [
        {"date": "2026-05-25", "open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "volume": 100, "amount": 1000},
        {"date": "2026-05-26", "open": 10.6, "high": 12.0, "low": 10.2, "close": 11.5, "volume": 200, "amount": 2000},
        {"date": "2026-05-29", "open": 11.6, "high": 11.8, "low": 10.0, "close": 10.8, "volume": 300, "amount": 3000},
    ]

    try:
        kline_lake.upsert_klines("sh.600790", "week", rows, adjustflag="3", source="tdx")

        result = kline_lake.query_klines("sh.600790", "week", limit=10, adjustflag="3", source="tdx")

        assert len(result) == 1
        assert result[0]["date"] == "2026-05-29"
        assert result[0]["open"] == 10.0
        assert result[0]["high"] == 12.0
        assert result[0]["low"] == 9.5
        assert result[0]["close"] == 10.8
        assert result[0]["volume"] == 600
        assert result[0]["amount"] == 6000
    finally:
        _reset_lake_connections()
