from server.db import database
from server.scripts import run_tdx_postmarket_sync as postmarket
from server.workers import kline_sync_worker


def test_market_data_batch_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("CT_OS_DB_PATH", str(tmp_path / "ctos.db"))
    conn = database.get_connection()
    try:
        conn.executescript(database.SCHEMA)
        conn.commit()
    finally:
        conn.close()

    batch = database.create_market_data_batch(
        source="tdx_vipdoc",
        mode="postmarket",
        symbols_count=3,
        meta={"vipdoc": "test"},
    )
    updated = database.update_market_data_batch(
        batch["batch_id"],
        status="success",
        latest_day="2026-05-28",
        latest_1m="2026-05-28 15:00:00",
    )

    assert batch["status"] == "running"
    assert updated["status"] == "success"
    assert updated["finished_at"]
    assert updated["latest_day"] == "2026-05-28"

    monkeypatch.delenv("CT_OS_DB_PATH")


def test_postmarket_sync_uses_market_data_batch(monkeypatch):
    calls = {}

    monkeypatch.setattr(postmarket, "resolve_vipdoc", lambda _vipdoc=None: "/tmp/tdx/vipdoc")
    monkeypatch.setattr(postmarket, "vipdoc_status", lambda _root: {"available": True, "vipdoc": _root})
    monkeypatch.setattr(postmarket, "_get_all_tracked_symbols", lambda: ["sh.600118"])
    def fake_create(**kwargs):
        calls["create"] = kwargs
        return {"batch_id": "batch_postmarket_1"}

    monkeypatch.setattr(postmarket, "create_market_data_batch", fake_create)

    def fake_sync_all(symbols, freqs, *, batch_id=""):
        calls["tracked"] = {"symbols": symbols, "freqs": freqs, "batch_id": batch_id}
        return {
            "total_symbols": len(symbols),
            "updated_symbols": 1,
            "total_written": 2,
            "errors": 0,
            "changed": [
                {"symbol": "sh.600118", "freq": "day", "last_date": "2026-06-02"},
                {"symbol": "sh.600118", "freq": "1", "last_date": "2026-06-02 15:00:00"},
            ],
        }

    def fake_update(batch_id, **fields):
        calls["update"] = {"batch_id": batch_id, **fields}
        return {"batch_id": batch_id, **fields}

    monkeypatch.setattr(postmarket, "_sync_all_symbols_from_tdx_local", fake_sync_all)
    monkeypatch.setattr(postmarket, "enqueue_structure_jobs_for_changes", lambda *_args, **_kwargs: {"count": 1})
    monkeypatch.setattr(postmarket, "prewarm_structure_snapshots", lambda **_kwargs: {"count": 1})
    monkeypatch.setattr(postmarket, "update_market_data_batch", fake_update)

    result = postmarket.run_postmarket_sync(mode="incremental")

    assert result["batch_id"] == "batch_postmarket_1"
    assert calls["create"]["source"] == "tdx_vipdoc"
    assert calls["create"]["mode"] == "postmarket"
    assert calls["tracked"]["batch_id"] == "batch_postmarket_1"
    assert calls["update"]["status"] == "success"
    assert calls["update"]["latest_day"] == "2026-06-02"
    assert calls["update"]["latest_1m"] == "2026-06-02 15:00:00"


def test_watchlist_tdx_init_uses_market_data_batch(monkeypatch):
    calls = {
        "adjusted_batch_ids": [],
        "raw_batch_ids": [],
        "replaced_batch_ids": [],
        "qfq_batch_ids": [],
    }

    monkeypatch.setattr(
        database,
        "create_market_data_batch",
        lambda **kwargs: {"batch_id": "batch_watchlist_1", **kwargs},
    )

    def fake_update(batch_id, **fields):
        calls["update"] = {"batch_id": batch_id, **fields}
        return {"batch_id": batch_id, **fields}

    monkeypatch.setattr(database, "update_market_data_batch", fake_update)

    async def fake_fetch_tdx_klines(_symbol, *, period, count, refresh):
        if period == "1m":
            return []
        return [
            {
                "date": "2026-06-02",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 1000,
                "amount": 10000,
            }
        ]

    import server.services.tdx_bridge_client as tdx_bridge_client
    import server.db.kline_lake as kline_lake
    import server.services.tdx_daily_sync_service as tdx_daily_sync_service
    import server.services.tdx_minute_service as tdx_minute_service
    import server.services.intraday_official_replacement_service as replacement_service
    import server.services.tdx_qfq_normalizer as qfq_normalizer
    import server.engines.ai_native.czsc_snapshot_service as snapshot_service

    monkeypatch.setattr(tdx_bridge_client, "fetch_tdx_klines", fake_fetch_tdx_klines)
    monkeypatch.setattr(tdx_daily_sync_service, "read_tdx_day_klines", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(tdx_daily_sync_service, "aggregate_tdx_week_klines", lambda rows: rows)
    monkeypatch.setattr(tdx_minute_service, "derive_tdx_day_from_minutes", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tdx_minute_service,
        "read_tdx_derived_minute_klines",
        lambda *_args, **_kwargs: [
            {
                "date": "2026-06-02 15:00:00",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 1000,
                "amount": 10000,
            }
        ],
    )

    def fake_upsert_adjusted_bars(*_args, batch_id="", **_kwargs):
        calls["adjusted_batch_ids"].append(batch_id)
        return 1

    def fake_upsert_raw_bars(*_args, batch_id="", **_kwargs):
        calls["raw_batch_ids"].append(batch_id)
        return 1

    monkeypatch.setattr(kline_lake, "upsert_adjusted_bars", fake_upsert_adjusted_bars)
    monkeypatch.setattr(kline_lake, "upsert_raw_bars", fake_upsert_raw_bars)

    def fake_mark_replaced(*_args, batch_id="", **_kwargs):
        calls["replaced_batch_ids"].append(batch_id)
        return 1

    monkeypatch.setattr(replacement_service, "mark_intraday_replaced_for_official_rows", fake_mark_replaced)

    class FakeQfqResult:
        status = "ok"
        reason = ""
        day_factor_count = 1
        written = {"day": 1}
        missing_factor_dates = {}
        total_written = 1

    def fake_rebuild(symbol, *, batch_id="", **_kwargs):
        calls["qfq_batch_ids"].append(batch_id)
        return FakeQfqResult()

    monkeypatch.setattr(qfq_normalizer, "rebuild_tdx_qfq_from_existing_factors", fake_rebuild)
    monkeypatch.setattr(kline_sync_worker, "enqueue_structure_jobs_for_changes", lambda *_args, **_kwargs: {"count": 1})
    monkeypatch.setattr(snapshot_service, "prewarm_structure_snapshots", lambda **_kwargs: {"items": [{"id": 1}]})

    result = kline_sync_worker._sync_new_watchlist_symbol_from_tdx("sh.600118")

    assert result["batch_id"] == "batch_watchlist_1"
    assert set(calls["adjusted_batch_ids"]) == {"batch_watchlist_1"}
    assert calls["raw_batch_ids"] == ["batch_watchlist_1"]
    assert calls["replaced_batch_ids"] == ["batch_watchlist_1"]
    assert calls["qfq_batch_ids"] == ["batch_watchlist_1"]
    assert calls["update"]["batch_id"] == "batch_watchlist_1"
    assert calls["update"]["status"] == "success"
    assert calls["update"]["latest_day"] == "2026-06-02"
    assert calls["update"]["latest_1m"] == "2026-06-02 15:00:00"
