"""Radar API contract tests."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import radar


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
            "strategy_id": "war1_third_buy",
            "strategy_version": "1.0.0",
            "strategy_type": "战法一",
            "summary": "战法一观察",
            "primary": {"stop_price": 18.0},
        },
    }


def make_adapter_result() -> dict:
    return {
        "adapter_version": "chan_adapter.v1",
        "symbol": "sh.600519",
        "data_source": {
            "structure": {
                "provider": "baostock",
                "adjustflag": "2",
                "engine": "chan.py",
                "adapter": "server.engines.structure.chan_adapter",
            }
        },
        "freshness": {
            "source": "baostock",
            "adjustflag": "2",
            "last_bar_at": "2026-04-24 15:00:00",
            "checked_at": "2026-04-24T20:35:00+08:00",
            "is_stale": False,
            "stale_reason": "",
            "levels": {
                "day": {"last_bar_at": "2026-04-24 15:00:00", "is_stale": False}
            },
        },
        "levels": {
            "day": {
                "level": "day",
                "klines": [{"time": "2026-04-24", "close": 20.0}],
                "bis": [{"x0": "2026-04-23", "x1": "2026-04-24", "is_up": True}],
                "segs": [],
                "bi_zhongshus": [
                    {"begin_date": "2026-04-20", "end_date": "2026-04-24", "zg": 19.5, "zd": 18.0}
                ],
                "seg_zhongshus": [],
                "bsps": [],
                "stats": {"bi_count": 1, "seg_count": 0, "bi_zs_count": 1},
                "price": 20.0,
                "zg": 19.5,
                "zd": 18.0,
                "patterns": ["二买"],
                "zoushi_type": {"type": "盘整", "zs_count": 1},
                "classifications": [],
                "last_bi_dir": "up",
                "source": {
                    "provider": "baostock",
                    "adjustflag": "2",
                    "engine": "chan.py",
                    "adapter": "server.engines.structure.chan_adapter",
                },
            },
            "30": {
                "level": "30",
                "klines": [{"time": "2026-04-24", "close": 20.0}],
                "bis": [],
                "segs": [],
                "bi_zhongshus": [{"zg": 19.5, "zd": 18.0}],
                "seg_zhongshus": [],
                "bsps": [],
                "stats": {},
                "price": 20.0,
                "zg": 19.5,
                "zd": 18.0,
                "patterns": ["底背驰"],
                "zoushi_type": {"type": "盘整", "zs_count": 1},
                "classifications": [],
                "last_bi_dir": "up",
                "source": {
                    "provider": "baostock",
                    "adjustflag": "2",
                    "engine": "chan.py",
                    "adapter": "server.engines.structure.chan_adapter",
                },
            },
            "5": {
                "level": "5",
                "klines": [{"time": "2026-04-24", "close": 20.0}],
                "bis": [],
                "segs": [],
                "bi_zhongshus": [{"zg": 19.5, "zd": 18.0}],
                "seg_zhongshus": [],
                "bsps": [],
                "stats": {},
                "price": 20.0,
                "zg": 19.5,
                "zd": 18.0,
                "patterns": ["底背驰"],
                "zoushi_type": {"type": "盘整", "zs_count": 1},
                "classifications": [],
                "last_bi_dir": "up",
                "source": {
                    "provider": "baostock",
                    "adjustflag": "2",
                    "engine": "chan.py",
                    "adapter": "server.engines.structure.chan_adapter",
                },
            },
        },
        "level_relations": {"adapter": True},
    }


def test_empty_mode_returns_entry_plan_and_no_holding_plan(monkeypatch):
    monkeypatch.setattr(radar, "_load_adapter_structure", lambda symbol: asyncio.sleep(0, result=make_adapter_result()))

    response = asyncio.run(radar.get_radar("sh600519", user_id=None, cost=0.0, qty=0))
    data = response["data"]

    assert response["status"] == "success"
    assert data["api_version"] == "radar.v1"
    assert data["symbol"] == "sh.600519"
    assert data["mode"] == "EMPTY"
    assert data["entry_plan"] is not None
    assert data["holding_plan"] is None
    assert data["disclaimer"] == radar.DISCLAIMER
    assert data["structure"]["levels"]["day"]["source"]["engine"] == "chan.py"
    assert data["data_source"]["structure"]["provider"] == "baostock"
    assert data["data_source"]["structure"]["adjustflag"] == "2"
    assert data["plans"][0]["plan_type"] == "ENTRY"


def test_success_uses_chan_adapter_structure_when_available(monkeypatch):
    async def fake_adapter(symbol):
        assert symbol == "sh.600519"
        return make_adapter_result()

    monkeypatch.setattr(radar, "_load_adapter_structure", fake_adapter)

    response = asyncio.run(radar.get_radar("sh600519", cost=0.0, qty=0))
    data = response["data"]

    assert response["status"] == "success"
    assert data["data_source"]["structure"]["adapter"] == "server.engines.structure.chan_adapter"
    assert data["data_source"]["structure"]["compatibility_mode"] is False
    assert data["freshness"]["last_bar_at"] == "2026-04-24 15:00:00"
    assert data["structure"]["levels"]["day"]["bis"][0]["is_up"] is True
    assert data["structure"]["levels"]["day"]["active_zhongshu"]["zg"] == 19.5
    assert data["structure"]["systems"]["short_term"]["interval_nesting"] == {"adapter": True}
    assert data["entry_plan"] is not None
    condition_status = {
        item["condition_id"]: item["status"]
        for item in data["entry_plan"]["conditions"]
    }
    assert condition_status["day_buy_node"] == "PASS"
    assert condition_status["thirty_min_buy_node"] == "PASS"
    assert condition_status["five_min_entry_bar"] == "PASS"


def test_empty_mode_populates_entry_risk_targets_and_sizing(monkeypatch):
    adapter_result = make_adapter_result()
    adapter_result["levels"]["day"]["bis"] = [
        {"is_up": True, "is_sure": True, "y1": 24.0},
        {"is_up": False, "is_sure": True, "y1": 19.0},
        {"is_up": True, "is_sure": True, "y1": 23.0},
    ]
    adapter_result["levels"]["day"]["bi_zhongshus"] = [
        {"zg": 19.5, "zd": 18.0},
        {"zg": 25.0, "zd": 22.0},
    ]

    async def fake_adapter(symbol):
        return adapter_result

    monkeypatch.setattr(radar, "_load_adapter_structure", fake_adapter)

    response = asyncio.run(
        radar.get_radar(
            "sh600519",
            cost=0.0,
            qty=0,
            account_value=100000.0,
            risk_pct=0.01,
            atr=0.5,
        )
    )
    entry_plan = response["data"]["entry_plan"]

    assert entry_plan["risk"]["stop_reference"]["level"] == "5"
    assert entry_plan["risk"]["stop_check"]["verdict"] == "合理"
    assert entry_plan["targets"][0]["price"] == 23.0
    assert entry_plan["position_sizing"]["suggested_shares"] == 2000
    assert entry_plan["reward_ratio"]["ratio"] == 6.0


def test_holding_mode_returns_holding_plan_and_no_entry_plan(monkeypatch):
    monkeypatch.setattr(radar, "_load_adapter_structure", lambda symbol: asyncio.sleep(0, result=make_adapter_result()))

    response = asyncio.run(radar.get_radar("sh.600519", cost=19.0, qty=1000))
    data = response["data"]

    assert response["status"] == "success"
    assert data["mode"] == "HOLDING"
    assert data["entry_plan"] is None
    assert data["holding_plan"] is not None
    assert data["holding_plan"]["plan_type"] == "HOLDING"
    assert data["plans"][0]["plan_type"] == "HOLDING"
    assert data["disclaimer"] == radar.DISCLAIMER


def test_engine_error_returns_stable_error_envelope(monkeypatch):
    async def fake_adapter(symbol):
        raise RuntimeError("boom")

    monkeypatch.setattr(radar, "_load_adapter_structure", fake_adapter)

    response = asyncio.run(radar.get_radar("sh-600519", user_id=None, cost=0.0, qty=0))
    data = response["data"]

    assert response["status"] == "error"
    assert data["api_version"] == "radar.v1"
    assert data["symbol"] == "sh.600519"
    assert data["error"]["code"] == "ENGINE_ERROR"
    assert data["freshness"]["is_stale"] is True
    assert data["freshness"]["stale_reason"] == "ENGINE_ERROR"
    assert data["disclaimer"] == radar.DISCLAIMER
