import asyncio

from server.scripts import import_tdx_native_to_lake


def test_import_tdx_native_to_lake_writes_periods(monkeypatch):
    imported = []

    monkeypatch.setattr(import_tdx_native_to_lake, "init_lake", lambda: None)

    async def fake_fetch(symbol, period="1m", count=5000, dividend_type="none"):
        return [
            {
                "date": "2026-05-22" if period in {"1d", "1w"} else "2026-05-22 15:00:00",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 100,
                "amount": 1000,
            }
        ]

    def fake_upsert(symbol, freq, rows, adjustflag="2", update_meta=True, source="baostock"):
        imported.append(
            {
                "symbol": symbol,
                "freq": freq,
                "adjustflag": adjustflag,
                "update_meta": update_meta,
                "source": source,
                "rows": rows,
            }
        )
        return len(rows)

    monkeypatch.setattr(import_tdx_native_to_lake, "fetch_tdx_klines", fake_fetch)
    monkeypatch.setattr(import_tdx_native_to_lake, "upsert_klines", fake_upsert)

    summaries = asyncio.run(
        import_tdx_native_to_lake.import_tdx_native_to_lake(
            symbols=["sh600790"],
            periods=["5m", "30m", "1d"],
            count=100,
        )
    )

    assert [item["freq"] for item in imported] == ["5", "30", "day"]
    assert all(item["adjustflag"] == "2" for item in imported)
    assert all(item["update_meta"] is True for item in imported)
    assert all(item["source"] == "tdx" for item in imported)
    assert summaries[-1]["last"] == "2026-05-22"


def test_import_tdx_native_to_lake_supports_week(monkeypatch):
    imported = []

    monkeypatch.setattr(import_tdx_native_to_lake, "init_lake", lambda: None)

    async def fake_fetch(symbol, period="1m", count=5000, dividend_type="none"):
        return [
            {
                "date": "2026-05-22",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 100,
                "amount": 1000,
            }
        ]

    def fake_upsert(symbol, freq, rows, adjustflag="2", update_meta=True, source="baostock"):
        imported.append(
            {
                "symbol": symbol,
                "freq": freq,
                "rows": rows,
                "adjustflag": adjustflag,
                "update_meta": update_meta,
                "source": source,
            }
        )
        return len(rows)

    monkeypatch.setattr(import_tdx_native_to_lake, "fetch_tdx_klines", fake_fetch)
    monkeypatch.setattr(import_tdx_native_to_lake, "upsert_klines", fake_upsert)

    summaries = asyncio.run(
        import_tdx_native_to_lake.import_tdx_native_to_lake(
            symbols=["sh600790"],
            periods=["1w"],
            count=100,
        )
    )

    assert imported[0]["freq"] == "week"
    assert imported[0]["adjustflag"] == "2"
    assert imported[0]["update_meta"] is True
    assert imported[0]["source"] == "tdx"
    assert summaries[0]["last"] == "2026-05-22"
