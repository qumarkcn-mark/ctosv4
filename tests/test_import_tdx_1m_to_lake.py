import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.scripts import import_tdx_1m_to_lake


def test_import_tdx_1m_to_lake_writes_rows(monkeypatch):
    imported = {}

    monkeypatch.setattr(import_tdx_1m_to_lake, "init_lake", lambda: None)
    monkeypatch.setattr(
        import_tdx_1m_to_lake,
        "tdx_minute_status",
        lambda symbol: {"available": True},
    )
    monkeypatch.setattr(
        import_tdx_1m_to_lake,
        "read_tdx_1m_klines",
        lambda symbol, **kwargs: [
            {
                "date": "2026-04-28 09:31:00",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 100,
                "amount": 1000,
            }
        ],
    )

    def fake_upsert(symbol, freq, rows, adjustflag="2", source="baostock"):
        imported.update(
            {
                "symbol": symbol,
                "freq": freq,
                "rows": rows,
                "adjustflag": adjustflag,
                "source": source,
            }
        )
        return len(rows)

    monkeypatch.setattr(import_tdx_1m_to_lake, "upsert_klines", fake_upsert)

    summaries = import_tdx_1m_to_lake.import_tdx_1m_to_lake(
        symbols=["sh603893"],
        start_date="2026-04-28 09:30:00",
        end_date="2026-04-28 15:00:00",
    )

    assert summaries[0]["imported"] == 1
    assert imported["symbol"] == "sh.603893"
    assert imported["freq"] == "1"
    assert imported["adjustflag"] == "3"
    assert imported["source"] == "qmt"
