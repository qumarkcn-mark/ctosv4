from server.db import kline_lake
from server.services.formal_lake_backfill_service import backfill_formal_tables_from_legacy
from server.services import tdx_minute_service


def _reset_lake_connections():
    kline_lake._thread_local.lake_conns = {}


def _row(date: str, close: float, factor: float = 1.0) -> dict:
    return {
        "date": date,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 100,
        "amount": 1000,
        "qfq_factor": factor,
    }


def test_raw_and_adjusted_bars_double_write_legacy_klines(tmp_path, monkeypatch):
    lake_path = tmp_path / "tdx_lake.db"
    monkeypatch.setattr(kline_lake, "TDX_LAKE_PATH", str(lake_path))
    _reset_lake_connections()
    conn = kline_lake.get_lake_write_connection("tdx")
    try:
        conn.executescript(kline_lake.LAKE_SCHEMA)
        conn.executescript(kline_lake.FORMAL_DATA_SCHEMA)
        conn.commit()
    finally:
        conn.close()

    try:
        raw_written = kline_lake.upsert_raw_bars(
            "sh.600790",
            "day",
            [_row("2026-05-28", 10.0)],
            batch_id="batch_raw",
        )
        adjusted_written = kline_lake.upsert_adjusted_bars(
            "sh.600790",
            "day",
            [_row("2026-05-28", 10.5, factor=1.05)],
            batch_id="batch_qfq",
        )

        raw_legacy = kline_lake.query_klines("sh.600790", "day", adjustflag="3", source="tdx", limit=10)
        qfq_legacy = kline_lake.query_klines("sh.600790", "day", adjustflag="2", source="tdx", limit=10)
        adjusted = kline_lake.query_adjusted_bars("sh.600790", "day", source="tdx", limit=10)

        assert raw_written == 1
        assert adjusted_written == 1
        assert raw_legacy[0]["close"] == 10.0
        assert qfq_legacy[0]["close"] == 10.5
        assert adjusted[0]["dataset"] == "tdx_qfq"
        assert adjusted[0]["batch_id"] == "batch_qfq"
        assert adjusted[0]["factor"] == 1.05
    finally:
        _reset_lake_connections()


def test_qfq_factors_are_persisted_with_signature(tmp_path, monkeypatch):
    lake_path = tmp_path / "tdx_lake.db"
    monkeypatch.setattr(kline_lake, "TDX_LAKE_PATH", str(lake_path))
    _reset_lake_connections()
    conn = kline_lake.get_lake_write_connection("tdx")
    try:
        conn.executescript(kline_lake.LAKE_SCHEMA)
        conn.executescript(kline_lake.FORMAL_DATA_SCHEMA)
        conn.commit()
    finally:
        conn.close()

    try:
        written = kline_lake.upsert_qfq_factors(
            "sh.600790",
            [_row("2026-05-28", 10.5, factor=1.05)],
            batch_id="batch_factor",
        )
        conn = kline_lake.get_lake_connection("tdx")
        factor = conn.execute("SELECT * FROM qfq_factors WHERE symbol = ?", ("sh.600790",)).fetchone()

        assert written == 1
        assert factor["factor"] == 1.05
        assert factor["batch_id"] == "batch_factor"
        assert factor["factor_signature"]
    finally:
        _reset_lake_connections()


def test_init_lake_creates_intraday_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(kline_lake, "TDX_LAKE_PATH", str(tmp_path / "tdx.db"))
    monkeypatch.setattr(kline_lake, "BAOSTOCK_LAKE_PATH", str(tmp_path / "bao.db"))
    monkeypatch.setattr(kline_lake, "QMT_LAKE_PATH", str(tmp_path / "qmt.db"))
    monkeypatch.setattr(kline_lake, "INTRADAY_LAKE_PATH", str(tmp_path / "intraday.db"))
    _reset_lake_connections()

    try:
        kline_lake.init_lake()
        conn = kline_lake.get_lake_connection("intraday")
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='intraday_bars'"
        ).fetchone()
        assert table
    finally:
        _reset_lake_connections()


def test_backfill_formal_tables_from_legacy_klines(tmp_path, monkeypatch):
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
        kline_lake.upsert_klines(
            "sh.600790",
            "day",
            [_row("2026-05-28", 10.0)],
            adjustflag="3",
            source="tdx",
        )
        kline_lake.upsert_klines(
            "sh.600790",
            "day",
            [_row("2026-05-28", 9.0)],
            adjustflag="2",
            source="tdx",
        )

        result = backfill_formal_tables_from_legacy(
            source="tdx",
            symbols=["sh.600790"],
            freqs=["day"],
            batch_id="test_backfill",
        )
        raw = kline_lake.query_klines("sh.600790", "day", adjustflag="3", source="tdx", limit=1)
        adjusted = kline_lake.query_adjusted_bars("sh.600790", "day", source="tdx", limit=1)
        conn = kline_lake.get_lake_connection("tdx")
        factor = conn.execute(
            "SELECT * FROM qfq_factors WHERE symbol = ? AND trade_date = ?",
            ("sh.600790", "2026-05-28"),
        ).fetchone()

        assert result["totals"]["raw_bars"] == 1
        assert result["totals"]["adjusted_bars"] == 1
        assert result["totals"]["qfq_factors"] == 1
        assert raw[0]["close"] == 10.0
        assert adjusted[0]["close"] == 9.0
        assert adjusted[0]["dataset"] == "tdx_qfq"
        assert adjusted[0]["factor"] == 0.9
        assert factor["factor"] == 0.9
        assert factor["batch_id"] == "test_backfill"
    finally:
        _reset_lake_connections()


def test_derive_tdx_day_from_minutes_builds_raw_day(monkeypatch):
    monkeypatch.setattr(
        tdx_minute_service,
        "read_tdx_5m_klines",
        lambda *_args, **_kwargs: [
            {
                "date": "2026-06-02 09:35:00",
                "open": 10.0,
                "high": 10.5,
                "low": 9.9,
                "close": 10.2,
                "volume": 100,
                "amount": 1000,
            },
            {
                "date": "2026-06-02 15:00:00",
                "open": 10.2,
                "high": 10.8,
                "low": 10.1,
                "close": 10.6,
                "volume": 200,
                "amount": 2200,
            },
        ],
    )
    monkeypatch.setattr(tdx_minute_service, "read_tdx_1m_klines", lambda *_args, **_kwargs: [])

    row = tdx_minute_service.derive_tdx_day_from_minutes("sh.600118", "2026-06-02")

    assert row["date"] == "2026-06-02"
    assert row["open"] == 10.0
    assert row["high"] == 10.8
    assert row["low"] == 9.9
    assert row["close"] == 10.6
    assert row["volume"] == 300
    assert row["amount"] == 3200
    assert row["source"] == "tdx_minute_derived_day"
