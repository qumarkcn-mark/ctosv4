import asyncio

from server.db import database
from server.services.intraday_observation_service import get_intraday_observation, reset_intraday_observation_cache
from server.workers import intraday_quote_sampler_worker as worker


def setup_db(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    conn = database.get_connection()
    try:
        conn.execute("INSERT INTO users (id, openid, nickname) VALUES (1, 'u1', 'U1')")
        conn.execute(
            "INSERT INTO positions (user_id, symbol, name, quantity, avg_cost) VALUES (1, 'sz.300394', '天孚通信', 100, 300)"
        )
        conn.execute(
            """
            INSERT INTO watchlist_groups (id, user_id, name, sort_order)
            VALUES (10, 1, '自选', 1), (20, 1, '观察', 2)
            """
        )
        conn.execute(
            "INSERT INTO watchlist_items (group_id, symbol, name, sort_order) VALUES (10, 'sh.688008', '澜起科技', 1)"
        )
        conn.execute(
            "INSERT INTO watchlist_items (group_id, symbol, name, sort_order) VALUES (20, 'sh.600790', '轻纺城', 1)"
        )
        conn.commit()
    finally:
        conn.close()


def test_load_watchboard_symbols(monkeypatch, tmp_path):
    setup_db(monkeypatch, tmp_path)

    assert worker.load_watchboard_symbols() == ["sh.600790", "sh.688008", "sz.300394"]


def test_sampler_tick_ingests_quotes(monkeypatch, tmp_path):
    setup_db(monkeypatch, tmp_path)
    reset_intraday_observation_cache()

    async def fake_quotes(symbols):
        return {
            "sz300394": {
                "symbol": "sz300394",
                "price": 372.7,
                "trade_datetime": "2026-05-22 13:59:53",
                "quote_time": "13:59:53",
                "source": "tdx_tq",
            },
            "sh688008": {
                "symbol": "sh688008",
                "price": 260.0,
                "trade_datetime": "2026-05-22 13:59:53",
                "quote_time": "13:59:53",
                "source": "tdx_tq",
            },
            "sh600790": {
                "symbol": "sh600790",
                "price": 4.01,
                "trade_datetime": "2026-05-22 13:59:53",
                "quote_time": "13:59:53",
                "source": "tdx_tq",
            },
        }

    async def fake_history(symbol, interval="m5", count=240, allow_short_fresh_cache=True):
        return []

    monkeypatch.setattr(worker, "fetch_tdx_quotes", fake_quotes)
    monkeypatch.setattr("server.services.intraday_observation_service.get_minute_klines", fake_history)
    monkeypatch.setattr("server.services.intraday_observation_service.query_klines", lambda *args, **kwargs: [])

    sampler = worker.IntradayQuoteSamplerWorker(interval_seconds=1, max_symbols=10)
    result = asyncio.run(sampler.tick())
    payload = asyncio.run(get_intraday_observation("sz.300394", quote=None))

    assert result == {"symbols": 3, "quotes": 3}
    assert payload["coverage"]["bar_count_1m"] == 1
    assert payload["levels"]["1m"]["last_bar_status"] == "FORMING"
