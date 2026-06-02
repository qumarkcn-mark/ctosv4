from server.db import kline_lake


def _reset_lake_connections():
    kline_lake._thread_local.lake_conns = {}


def test_intraday_bars_preserve_status_quality_and_samples(tmp_path, monkeypatch):
    lake_path = tmp_path / "intraday_lake.db"
    monkeypatch.setattr(kline_lake, "INTRADAY_LAKE_PATH", str(lake_path))
    _reset_lake_connections()
    conn = kline_lake.get_lake_write_connection("intraday")
    try:
        conn.executescript(kline_lake.LAKE_SCHEMA)
        conn.executescript(kline_lake.INTRADAY_SCHEMA)
        conn.commit()
    finally:
        conn.close()

    try:
        written = kline_lake.upsert_intraday_bars(
            "sh.600790",
            "1",
            [
                {
                    "bar_time": "2026-05-28 09:31:00",
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.9,
                    "close": 10.1,
                    "volume": 100,
                    "amount": 1000,
                    "bar_status": "FORMING",
                    "source": "tdx_quote_aggregation",
                    "sample_count": 2,
                    "first_quote_at": "2026-05-28 09:31:01",
                    "last_quote_at": "2026-05-28 09:31:45",
                    "quality": "full",
                }
            ],
        )

        rows = kline_lake.query_intraday_bars("sh.600790", "1", limit=10)

        assert written == 1
        assert len(rows) == 1
        assert rows[0]["date"] == "2026-05-28 09:31:00"
        assert rows[0]["bar_status"] == "FORMING"
        assert rows[0]["sample_count"] == 2
        assert rows[0]["quality"] == "full"
    finally:
        _reset_lake_connections()


def test_mark_intraday_replaced_by_official_hides_default_queries(tmp_path, monkeypatch):
    lake_path = tmp_path / "intraday_lake.db"
    monkeypatch.setattr(kline_lake, "INTRADAY_LAKE_PATH", str(lake_path))
    _reset_lake_connections()
    conn = kline_lake.get_lake_write_connection("intraday")
    try:
        conn.executescript(kline_lake.LAKE_SCHEMA)
        conn.executescript(kline_lake.INTRADAY_SCHEMA)
        conn.commit()
    finally:
        conn.close()

    try:
        kline_lake.upsert_intraday_bars(
            "sh.600790",
            "1",
            [
                {
                    "bar_time": "2026-05-28 09:31:00",
                    "open": 10,
                    "high": 10,
                    "low": 10,
                    "close": 10,
                }
            ],
        )

        marked = kline_lake.mark_intraday_replaced_by_official(
            "sh.600790",
            trade_date="2026-05-28",
            batch_id="batch_1",
        )

        assert marked == 1
        assert kline_lake.query_intraday_bars("sh.600790", "1", limit=10) == []
        replaced = kline_lake.query_intraday_bars("sh.600790", "1", limit=10, include_replaced=True)
        assert len(replaced) == 1
        assert replaced[0]["replaced_by_official"] == 1
        assert replaced[0]["batch_id"] == "batch_1"
    finally:
        _reset_lake_connections()
