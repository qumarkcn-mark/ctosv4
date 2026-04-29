"""Characterization tests for /api/chan/matrix/v2."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import chan


def make_level(level: str, price: float = 20.0, patterns=None) -> dict:
    return {
        "level": level,
        "state": "UPWARD_LEAVING",
        "zd": 18.0,
        "zg": 19.5,
        "price": price,
        "patterns": patterns or [],
        "zoushi_type": {"type": "盘整", "zs_count": 1},
        "classifications": [],
        "bi_count": 8,
        "zs_count": 1,
        "last_bi_dir": "up",
        "data_status": "ok",
        "kline_count": 200,
        "detail_bis": [
            {"y0": 18.0, "y1": 20.0, "is_up": True, "start_date": "2026-04-24 10:00", "end_date": "2026-04-24 10:30"}
        ],
        "recent_klines": [
            {"date": "2026-04-24 15:00:00", "close": price, "high": price + 0.2, "low": price - 0.2}
        ],
        "div_info": None,
        "has_bottom_fractal": False,
        "has_top_fractal": False,
    }


def make_matrix() -> dict:
    buy_patterns = ["二买确认", "底背驰"]
    return {
        "symbol": "sh.600519",
        "matrix_a": [
            make_level("day", 20.0, buy_patterns),
            make_level("m30", 20.0, buy_patterns),
            make_level("m5", 20.0, buy_patterns),
        ],
        "matrix_b": [
            make_level("day", 20.0, buy_patterns),
            make_level("m60", 20.0, buy_patterns),
            make_level("m15", 20.0, buy_patterns),
        ],
        "week": make_level("week", 20.0, buy_patterns),
        "interval_nesting_a": {"direction": "up", "levels": ["day", "m30", "m5"]},
        "interval_nesting_b": {"direction": "up", "levels": ["day", "m60", "m15"]},
        "forward_analysis_a": {},
        "forward_analysis_b": {},
        "strategy_classification": {
            "strategy_type": "战法一",
            "summary": "战法一观察",
            "primary": {"stop_price": 18.0},
        },
    }


def setup_function():
    chan._v2_cache.clear()


def test_matrix_v2_empty_mode_keeps_legacy_entry_fields(monkeypatch):
    async def fake_analyze(symbol, holding=None):
        assert symbol == "sh.600519"
        assert holding is None
        return make_matrix()

    monkeypatch.setattr(chan, "analyze_matrix_state", fake_analyze)

    response = asyncio.run(chan.get_chan_matrix_v2("sh600519", cost=0.0, qty=0))
    data = response["data"]

    assert response["status"] == "success"
    assert data["symbol"] == "sh.600519"
    assert data["entry_checklist"]["all_passed"] is True
    assert data["strategy_classification"]["strategy_type"] == "战法一"
    assert "position_sizing" in data
    assert "stop_atr_check" in data
    assert "targets" in data
    assert "reward_ratio" in data
    assert data["holding_status"]["stage"] == "empty"
    assert data["data_freshness"]["is_stale"] is False


def test_matrix_v2_holding_mode_keeps_holding_fields(monkeypatch):
    async def fake_analyze(symbol, holding=None):
        assert holding == {"cost": 19.0, "qty": 1000}
        return make_matrix()

    monkeypatch.setattr(chan, "analyze_matrix_state", fake_analyze)

    response = asyncio.run(chan.get_chan_matrix_v2("sh.600519", cost=19.0, qty=1000))
    data = response["data"]

    assert response["status"] == "success"
    assert data["entry_checklist"] is not None
    assert data["holding_status"] is not None
    assert data["holding_status"]["stage"] != "empty"
    assert "holding_stage_v2" in data
    assert data["strategy_classification"] is not None
    assert data["data_freshness"]["is_stale"] is False


def test_matrix_v2_engine_error_keeps_stable_error_envelope(monkeypatch):
    async def fake_analyze(symbol, holding=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(chan, "analyze_matrix_state", fake_analyze)

    response = asyncio.run(chan.get_chan_matrix_v2("sh-600519", cost=0.0, qty=0))
    data = response["data"]

    assert response["status"] == "error"
    assert data["symbol"] == "sh.600519"
    assert data["error"]["code"] == "ENGINE_ERROR"
    assert data["error"]["fallback_used"] is True
    assert data["entry_checklist"] is None
    assert data["holding_status"] is None
    assert data["data_freshness"]["is_stale"] is True
    assert data["data_freshness"]["stale_reason"] == "ENGINE_ERROR"
