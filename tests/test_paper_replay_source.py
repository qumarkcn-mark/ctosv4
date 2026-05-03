import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from server.engines.execution.paper_replay_source import (
    build_intraday_t_features_from_history,
    build_replay_steps_from_klines,
    next_bar_after,
)
from server.engines.execution.paper_feature_cache import ReplayFeatureCache, SQLiteReplayFeatureCache, replay_feature_cache_key


def detail_for(level, end_date, close=10.0):
    return {
        "symbol": "sh.603893",
        "freq": level,
        "klines": [
            {"time": "2026-04-29 10:00:00", "open": 9.8, "high": 10.2, "low": 9.7, "close": 10.0, "volume": 1000},
            {"time": end_date, "open": 10.0, "high": 10.5, "low": 9.9, "close": close, "volume": 1000},
        ],
        "bis": [
            {
                "x0": "2026-04-29 10:00:00",
                "x1": end_date,
                "y0": 9.8,
                "y1": close,
                "is_up": True,
                "momentum": {"area": 1.0, "dif_extreme": 0.5},
            }
        ],
        "bi_zhongshus": [{"begin_date": "2026-04-29 10:00:00", "end_date": end_date, "zg": 10.1, "zd": 9.9, "gg": 10.5, "dd": 9.7}],
        "zhongshus": [{"begin_date": "2026-04-29 10:00:00", "end_date": end_date, "zg": 10.1, "zd": 9.9, "gg": 10.5, "dd": 9.7}],
        "bsps": [{"time": end_date, "price": close, "type": "1", "is_buy": False}],
    }


@pytest.mark.anyio
async def test_history_feature_builder_passes_end_date_to_every_level():
    calls = []

    async def fake_loader(symbol, freq, count=500, end_date=None, cchan_preset="live_tolerant", **kwargs):
        calls.append((symbol, freq, end_date))
        return detail_for(freq, end_date, close=10.5)

    features = await build_intraday_t_features_from_history(
        symbol="sh.603893",
        as_of="2026-04-29 10:30:00",
        level_chain={"L0": "30", "L1": "5", "L2": "1"},
        detail_loader=fake_loader,
    )

    assert {call[1] for call in calls} == {"30", "5", "1"}
    assert all(call[2] == "2026-04-29 10:30:00" for call in calls)
    assert features.as_of == "2026-04-29 10:30:00"
    assert features.latest_event_side == "sell"


@pytest.mark.anyio
async def test_replay_steps_use_next_bar_only_for_fill_not_feature_signal():
    seen_end_dates = []

    async def fake_loader(symbol, freq, count=500, end_date=None, cchan_preset="live_tolerant", **kwargs):
        seen_end_dates.append(end_date)
        return detail_for(freq, end_date, close=10.5)

    def fake_klines(symbol, freq, start_date=None, end_date=None, limit=240):
        return [
            {"date": "2026-04-29 10:30:00", "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.4, "volume": 1000},
            {"date": "2026-04-29 10:31:00", "open": 99.0, "high": 100.0, "low": 98.0, "close": 99.5, "volume": 1000},
        ]

    steps = await build_replay_steps_from_klines(
        symbol="sh.603893",
        start_date="2026-04-29 10:30:00",
        end_date="2026-04-29 10:31:00",
        detail_loader=fake_loader,
        kline_loader=fake_klines,
    )

    assert len(steps) == 1
    assert steps[0].features.as_of == "2026-04-29 10:30:00"
    assert steps[0].next_bar["open"] == 99.0
    assert set(seen_end_dates) == {"2026-04-29 10:30:00"}


def test_next_bar_after_returns_first_bar_strictly_after_as_of():
    def fake_klines(symbol, freq, start_date=None, end_date=None, limit=5):
        return [
            {"date": "2026-04-29 10:30:00", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1},
            {"date": "2026-04-29 10:31:00", "open": 11, "high": 11, "low": 11, "close": 11, "volume": 1},
        ]

    bar = next_bar_after(
        "sh.603893",
        as_of="2026-04-29 10:30:00",
        kline_loader=fake_klines,
    )

    assert bar is not None
    assert bar.time == "2026-04-29 10:31:00"
    assert bar.open == 11


@pytest.mark.anyio
async def test_history_feature_builder_caps_compute_bars_for_replay():
    calls = []

    async def fake_loader(symbol, freq, **kwargs):
        calls.append((freq, kwargs))
        return detail_for(freq, kwargs["end_date"], close=10.5)

    await build_intraday_t_features_from_history(
        symbol="sh.603893",
        as_of="2026-04-29 10:30:00",
        count=180,
        level_chain={"L0": "30", "L1": "5", "L2": "1"},
        detail_loader=fake_loader,
    )

    assert calls
    assert all(kwargs["max_compute_bars"] == 180 for _, kwargs in calls)


@pytest.mark.anyio
async def test_feature_cache_reuses_same_as_of_feature_build():
    calls = []

    async def fake_loader(symbol, freq, count=500, end_date=None, cchan_preset="live_tolerant", **kwargs):
        calls.append((symbol, freq, end_date))
        return detail_for(freq, end_date, close=10.5)

    cache = ReplayFeatureCache()
    kwargs = {
        "symbol": "sh.603893",
        "as_of": "2026-04-29 10:30:00",
        "level_chain": {"L0": "30", "L1": "5", "L2": "1"},
        "detail_loader": fake_loader,
        "feature_cache": cache,
    }

    first = await build_intraday_t_features_from_history(**kwargs)
    second = await build_intraday_t_features_from_history(**kwargs)

    assert first is second
    assert len(calls) == 3
    assert cache.stats() == {"hits": 1, "misses": 1, "size": 1}


@pytest.mark.anyio
async def test_tdx_1m_replay_routes_only_one_minute_detail_to_qmt_source():
    calls = []

    async def fake_loader(symbol, freq, **kwargs):
        calls.append((freq, kwargs))
        return detail_for(freq, kwargs["end_date"], close=10.5)

    await build_intraday_t_features_from_history(
        symbol="sh.603893",
        as_of="2026-04-29 10:30:00",
        level_chain={"L0": "15", "L1": "5", "L2": "1"},
        detail_loader=fake_loader,
        detail_source="tdx_1m_replay",
    )

    by_freq = {freq: kwargs for freq, kwargs in calls}
    assert by_freq["1"]["kline_source"] == "qmt"
    assert by_freq["1"]["adjustflag"] == "3"
    assert "kline_source" not in by_freq["5"]
    assert "kline_source" not in by_freq["15"]


@pytest.mark.anyio
async def test_sqlite_feature_cache_persists_and_reuses_features():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE paper_feature_cache (
            cache_key TEXT PRIMARY KEY,
            cache_version TEXT NOT NULL,
            symbol TEXT NOT NULL,
            as_of TEXT NOT NULL,
            level_chain_json TEXT NOT NULL,
            count INTEGER NOT NULL,
            cchan_preset TEXT NOT NULL,
            features_json TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    calls = 0
    key = replay_feature_cache_key(
        symbol="sh.603893",
        as_of="2026-04-29 10:30:00",
        level_chain={"L0": "30", "L1": "5", "L2": "1"},
        count=500,
        cchan_preset="live_tolerant",
    )

    async def factory():
        nonlocal calls
        calls += 1
        async def fake_loader(symbol, freq, count=500, end_date=None, cchan_preset="live_tolerant", **kwargs):
            return detail_for(freq, end_date, close=10.5)

        return await build_intraday_t_features_from_history(
            symbol="sh.603893",
            as_of="2026-04-29 10:30:00",
            level_chain={"L0": "30", "L1": "5", "L2": "1"},
            detail_loader=fake_loader,
        )

    cache = SQLiteReplayFeatureCache(conn=conn)
    first = await cache.get_or_build(key, factory)
    fresh_cache = SQLiteReplayFeatureCache(conn=conn)
    second = await fresh_cache.get_or_build(key, factory)

    assert first.symbol == second.symbol == "sh.603893"
    assert second.parent_context == first.parent_context
    assert calls == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_feature_cache").fetchone()[0] == 1
    assert fresh_cache.stats()["disk_hits"] == 1
