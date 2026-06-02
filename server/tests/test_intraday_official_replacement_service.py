from server.db import kline_lake
from server.services.intraday_official_replacement_service import mark_intraday_replaced_for_official_rows


def _reset_lake_connections():
    kline_lake._thread_local.lake_conns = {}


def test_mark_intraday_replaced_for_official_1m_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(kline_lake, "INTRADAY_LAKE_PATH", str(tmp_path / "intraday.db"))
    _reset_lake_connections()
    conn = kline_lake.get_lake_write_connection("intraday")
    try:
        conn.executescript(kline_lake.LAKE_SCHEMA)
        conn.executescript(kline_lake.FORMAL_DATA_SCHEMA)
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
                    "bar_time": "2026-06-02 10:01:00",
                    "open": 10,
                    "high": 10.2,
                    "low": 9.9,
                    "close": 10.1,
                }
            ],
        )

        marked = mark_intraday_replaced_for_official_rows(
            "sh.600790",
            "1",
            [{"date": "2026-06-02 10:01:00"}],
            batch_id="official_batch",
        )

        assert marked == 1
        assert kline_lake.query_intraday_bars("sh.600790", "1", limit=10) == []
        replaced = kline_lake.query_intraday_bars("sh.600790", "1", limit=10, include_replaced=True)
        assert replaced[0]["replaced_by_official"] == 1
        assert replaced[0]["batch_id"] == "official_batch"
    finally:
        _reset_lake_connections()


def test_mark_intraday_replaced_ignores_non_1m_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(kline_lake, "INTRADAY_LAKE_PATH", str(tmp_path / "intraday.db"))
    _reset_lake_connections()
    conn = kline_lake.get_lake_write_connection("intraday")
    try:
        conn.executescript(kline_lake.LAKE_SCHEMA)
        conn.executescript(kline_lake.FORMAL_DATA_SCHEMA)
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
                    "bar_time": "2026-06-02 10:01:00",
                    "open": 10,
                    "high": 10.2,
                    "low": 9.9,
                    "close": 10.1,
                }
            ],
        )

        marked = mark_intraday_replaced_for_official_rows(
            "sh.600790",
            "5",
            [{"date": "2026-06-02 10:05:00"}],
        )

        assert marked == 0
        assert len(kline_lake.query_intraday_bars("sh.600790", "1", limit=10)) == 1
    finally:
        _reset_lake_connections()
