from server.db import kline_lake
from server.services.tdx_qfq_normalizer import (
    build_qfq_day_rows_from_gbbq_events,
    rebuild_tdx_qfq_from_existing_factors,
)


def _init_lake(tmp_path, monkeypatch):
    monkeypatch.setattr(kline_lake, "DB_PATH", str(tmp_path / "ctos.db"))
    monkeypatch.setattr(kline_lake, "TDX_LAKE_PATH", str(tmp_path / "tdx_lake.db"))
    monkeypatch.setattr(kline_lake, "BAOSTOCK_LAKE_PATH", str(tmp_path / "baostock_lake.db"))
    monkeypatch.setattr(kline_lake, "QMT_LAKE_PATH", str(tmp_path / "qmt_lake.db"))
    monkeypatch.setattr(kline_lake, "LAKE_PATH", str(tmp_path / "baostock_lake.db"))
    kline_lake._thread_local.lake_conns = {}
    kline_lake.init_lake()


def test_tdx_qfq_rebuild_scales_raw_minute_with_existing_day_factor(tmp_path, monkeypatch):
    _init_lake(tmp_path, monkeypatch)
    kline_lake.upsert_klines(
        "sh.600790",
        "day",
        [
            {"date": "2026-05-21", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100, "amount": 1000},
            {"date": "2026-05-22", "open": 20, "high": 22, "low": 18, "close": 20, "volume": 100, "amount": 1000},
        ],
        adjustflag="3",
        source="tdx",
    )
    kline_lake.upsert_klines(
        "sh.600790",
        "day",
        [
            {"date": "2026-05-21", "open": 5, "high": 5.5, "low": 4.5, "close": 5, "volume": 100, "amount": 1000},
            {"date": "2026-05-22", "open": 20, "high": 22, "low": 18, "close": 20, "volume": 100, "amount": 1000},
        ],
        adjustflag="2",
        source="tdx",
    )
    kline_lake.upsert_klines(
        "sh.600790",
        "1",
        [
            {"date": "2026-05-21 09:31:00", "open": 10, "high": 10.4, "low": 9.8, "close": 10.2, "volume": 10, "amount": 100},
            {"date": "2026-05-22 09:31:00", "open": 20, "high": 20.4, "low": 19.8, "close": 20.2, "volume": 10, "amount": 100},
        ],
        adjustflag="3",
        source="tdx",
    )

    result = rebuild_tdx_qfq_from_existing_factors("sh600790", target_freqs=["week", "1"])

    assert result.status == "ok"
    assert result.day_factor_count == 2
    assert result.written["1"] == 2
    rows = kline_lake.query_klines("sh.600790", "1", adjustflag="2", source="tdx")
    assert rows[0]["open"] == 5
    assert rows[0]["close"] == 5.1
    assert rows[1]["open"] == 20
    week_rows = kline_lake.query_klines("sh.600790", "week", adjustflag="2", source="tdx")
    assert week_rows[-1]["close"] == 20


def test_tdx_qfq_rebuild_skips_without_day_factor(tmp_path, monkeypatch):
    import server.services.tdx_qfq_normalizer as normalizer

    _init_lake(tmp_path, monkeypatch)
    monkeypatch.setattr(normalizer, "_read_gbbq_df", lambda **kwargs: (_ for _ in ()).throw(FileNotFoundError()))
    kline_lake.upsert_klines(
        "sh.600790",
        "day",
        [{"date": "2026-05-22", "open": 20, "high": 22, "low": 18, "close": 20, "volume": 100, "amount": 1000}],
        adjustflag="3",
        source="tdx",
    )

    result = rebuild_tdx_qfq_from_existing_factors("sh600790", target_freqs=["1"])

    assert result.status == "skipped"
    assert result.reason == "NO_TDX_DAY_QFQ_FACTOR_OR_GBBQ"
    assert result.total_written == 0


def test_tdx_qfq_rebuild_skips_when_existing_day_factor_is_stale(tmp_path, monkeypatch):
    import server.services.tdx_qfq_normalizer as normalizer

    _init_lake(tmp_path, monkeypatch)
    monkeypatch.setattr(normalizer, "_read_gbbq_df", lambda **kwargs: (_ for _ in ()).throw(FileNotFoundError()))
    kline_lake.upsert_klines(
        "sh.600790",
        "day",
        [
            {"date": "2026-05-21", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100, "amount": 1000},
            {"date": "2026-05-22", "open": 20, "high": 22, "low": 18, "close": 20, "volume": 100, "amount": 1000},
        ],
        adjustflag="3",
        source="tdx",
    )
    kline_lake.upsert_klines(
        "sh.600790",
        "day",
        [{"date": "2026-05-21", "open": 5, "high": 5.5, "low": 4.5, "close": 5, "volume": 100, "amount": 1000}],
        adjustflag="2",
        source="tdx",
    )
    kline_lake.upsert_klines(
        "sh.600790",
        "5",
        [{"date": "2026-05-22 15:00:00", "open": 20, "high": 20, "low": 20, "close": 20, "volume": 10, "amount": 100}],
        adjustflag="3",
        source="tdx",
    )

    result = rebuild_tdx_qfq_from_existing_factors("sh600790", target_freqs=["5"])

    assert result.status == "skipped"
    assert result.reason == "STALE_TDX_DAY_QFQ_FACTOR"
    assert result.missing_factor_dates["day"] == 1
    assert kline_lake.query_klines("sh.600790", "5", adjustflag="2", source="tdx") == []


def test_build_tdx_qfq_day_rows_from_gbbq_events():
    import pandas as pd

    raw_rows = [
        {"date": "2026-05-20", "open": 20, "high": 21, "low": 19, "close": 20, "volume": 100, "amount": 1000},
        {"date": "2026-05-21", "open": 20, "high": 21, "low": 19, "close": 20, "volume": 100, "amount": 1000},
        {"date": "2026-05-22", "open": 15.5, "high": 16, "low": 15, "close": 16, "volume": 100, "amount": 1000},
    ]
    gbbq = pd.DataFrame(
        [
            {
                "market": 0,
                "code": "301076",
                "datetime": 20260522,
                "category": 1,
                "hongli_panqianliutong": 0,
                "peigujia_qianzongguben": 0,
                "songgu_qianzongguben": 3,
                "peigu_houzongguben": 0,
            }
        ]
    )

    rows = build_qfq_day_rows_from_gbbq_events("sz.301076", raw_rows, gbbq)

    assert len(rows) == 3
    assert rows[0]["close"] == 15.3846
    assert rows[1]["close"] == 15.3846
    assert rows[2]["close"] == 16
    assert round(rows[0]["qfq_factor"], 6) == round(10 / 13, 6)


def test_tdx_qfq_rebuild_generates_day_factor_from_gbbq(tmp_path, monkeypatch):
    import pandas as pd
    import server.services.tdx_qfq_normalizer as normalizer

    _init_lake(tmp_path, monkeypatch)
    kline_lake.upsert_klines(
        "sz.301076",
        "day",
        [
            {"date": "2026-05-21", "open": 20, "high": 21, "low": 19, "close": 20, "volume": 100, "amount": 1000},
            {"date": "2026-05-22", "open": 15.5, "high": 16, "low": 15, "close": 16, "volume": 100, "amount": 1000},
        ],
        adjustflag="3",
        source="tdx",
    )
    kline_lake.upsert_klines(
        "sz.301076",
        "5",
        [{"date": "2026-05-21 15:00:00", "open": 20, "high": 20, "low": 20, "close": 20, "volume": 10, "amount": 100}],
        adjustflag="3",
        source="tdx",
    )
    monkeypatch.setattr(
        normalizer,
        "_read_gbbq_df",
        lambda **kwargs: pd.DataFrame(
            [
                {
                    "market": 0,
                    "code": "301076",
                    "datetime": 20260522,
                    "category": 1,
                    "hongli_panqianliutong": 0,
                    "peigujia_qianzongguben": 0,
                    "songgu_qianzongguben": 3,
                    "peigu_houzongguben": 0,
                }
            ]
        ),
    )

    result = rebuild_tdx_qfq_from_existing_factors("sz301076", target_freqs=["5"])

    assert result.status == "ok"
    assert result.written["day"] == 2
    assert result.written["5"] == 1
    day_rows = kline_lake.query_klines("sz.301076", "day", adjustflag="2", source="tdx")
    minute_rows = kline_lake.query_klines("sz.301076", "5", adjustflag="2", source="tdx")
    assert day_rows[0]["close"] == 15.3846
    assert minute_rows[0]["close"] == 15.3846


def test_tdx_qfq_rebuild_respects_empty_target_freqs(tmp_path, monkeypatch):
    _init_lake(tmp_path, monkeypatch)
    kline_lake.upsert_klines(
        "sh.600790",
        "day",
        [{"date": "2026-05-22", "open": 20, "high": 22, "low": 18, "close": 20, "volume": 100, "amount": 1000}],
        adjustflag="3",
        source="tdx",
    )
    kline_lake.upsert_klines(
        "sh.600790",
        "day",
        [{"date": "2026-05-22", "open": 20, "high": 22, "low": 18, "close": 20, "volume": 100, "amount": 1000}],
        adjustflag="2",
        source="tdx",
    )
    kline_lake.upsert_klines(
        "sh.600790",
        "5",
        [{"date": "2026-05-22 15:00:00", "open": 20, "high": 20, "low": 20, "close": 20, "volume": 10, "amount": 100}],
        adjustflag="3",
        source="tdx",
    )

    result = rebuild_tdx_qfq_from_existing_factors("sh600790", target_freqs=[])

    assert result.status == "ok"
    assert result.written == {}
    assert result.total_written == 0
    assert kline_lake.query_klines("sh.600790", "5", adjustflag="2", source="tdx") == []
