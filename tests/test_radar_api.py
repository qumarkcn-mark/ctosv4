"""Radar API contract tests."""

import asyncio
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from server.api import radar
from server.engines.structure.chan_config_presets import get_chan_config_meta


@pytest.fixture(autouse=True)
def clear_radar_structure_cache():
    radar._clear_structure_cache()
    yield
    radar._clear_structure_cache()


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
        "structure_config": get_chan_config_meta("live_tolerant"),
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


def test_intraday_quote_overlay_reclassifies_stale_c_to_a():
    algorithm = {
        "summary": "旧结构切片判 C",
        "current_scenario_id": "C",
        "a_state": "C_TRIGGERED",
        "atoms": {
            "L2": {"price": 67.5},
            "L1": {"price": 67.5},
            "L0": {"price": 67.5},
        },
        "boundaries": {
            "confirm": [
                {"level": "5", "field": "ZG", "value": 66.96, "trigger": "break_above", "meaning": "转强"},
                {"level": "30", "field": "ZG", "value": 69.27, "trigger": "break_above", "meaning": "升级"},
            ],
            "maintain": [{"level": "30", "field": "ZD", "value": 66.1, "trigger": "hold_above"}],
            "invalidate": [{"level": "30", "field": "ZD", "value": 66.1, "trigger": "break_below"}],
        },
        "scenarios": [
            {"id": "A", "state": "BLOCKED"},
            {"id": "B", "state": "FAILED"},
            {"id": "C", "state": "CURRENT"},
        ],
    }

    result = radar._apply_intraday_quote_overlay(algorithm, {"provider": "tencent", "price": 81.0})

    assert result["current_scenario_id"] == "A"
    assert result["confirmation"]["state"] == "A_INTRADAY_FULL_TRIGGERED"
    assert result["confirmation"]["is_provisional"] is True
    assert result["intraday_overlay"]["price"] == 81.0
    assert [item["state"] for item in result["scenarios"]] == ["CURRENT", "CONFIRMED", "PENDING"]


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
    assert "structure" not in data
    assert data["data_source"]["structure"]["provider"] == "baostock"
    assert data["data_source"]["structure"]["adjustflag"] == "2"
    assert data["structure_config"]["preset"] == "live_tolerant"
    assert data["structure_config"]["label"] == "实盘容错"
    assert data["structure_config"]["version"] == "cchan_config.v1"
    assert data["plans"][0]["plan_type"] == "ENTRY"
    assert data["strategy"]["strategy_id"] == "war1_third_buy"
    assert data["strategy"]["strategy_version"] == "1.0.0"
    assert data["strategy"]["freshness_required"] is True
    assert data["deduction"]["version"] == "level_chain_deduction.v1"
    assert data["deduction"]["mode"] == "EMPTY"
    assert data["deduction"]["chain"] == ["day", "30", "5"]
    assert data["algorithm_v2"]["version"] == "radar_algorithm.v2.phase1"
    assert data["algorithm_v2"]["level_chain"] == {"L0": "day", "L1": "30", "L2": "5"}
    assert data["signals_v2"]["version"] == "semantic_signal.v2"
    assert data["signals_v2"]["state"] in {"success", "stale"}
    assert data["signals_v2"]["primary"]["code"]
    assert data["signals_v2"]["context"]["signal_code"] == data["signals_v2"]["primary"]["code"]
    assert data["position_context"]["state"] == "EMPTY"
    assert data["coach_action"]["position_state"] == "EMPTY"
    assert data["coach_action"]["disclaimer"] == radar.DISCLAIMER


def test_success_uses_chan_adapter_structure_when_available(monkeypatch):
    async def fake_adapter(symbol):
        assert symbol == "sh.600519"
        return make_adapter_result()

    monkeypatch.setattr(radar, "_load_adapter_structure", fake_adapter)

    response = asyncio.run(radar.get_radar("sh600519", cost=0.0, qty=0, include_structure=True))
    data = response["data"]

    assert response["status"] == "success"
    assert data["data_source"]["structure"]["adapter"] == "server.engines.structure.chan_adapter"
    assert data["data_source"]["structure"]["compatibility_mode"] is False
    assert data["diagnostics"]["structure_profile"] == "fast"
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
    assert data["deduction"]["status"] in {
        "WAITING_TRIGGER",
        "TRIGGER_FORMING",
        "TRIGGER_CONFIRMED",
    }
    assert data["algorithm_v2"]["path"] in {
        "UPWARD_MAJOR_WAVE",
        "HIGH_VOLATILITY_OSCILLATION",
        "PULLBACK_IN_UPTREND",
        "CENTER_REBOUND",
        "NO_EDGE",
    }


def test_stale_structure_pauses_algorithm_summary(monkeypatch):
    adapter_result = make_adapter_result()
    adapter_result["freshness"]["is_stale"] = True
    adapter_result["freshness"]["stale_reason"] = "LEVEL_STALE"
    adapter_result["freshness"]["levels"]["30"] = {
        "last_bar_at": "2025-03-04 15:00:00",
        "is_stale": True,
        "stale_reason": "LEVEL_STALE",
    }

    async def fake_adapter(symbol):
        return adapter_result

    monkeypatch.setattr(radar, "_load_adapter_structure", fake_adapter)

    response = asyncio.run(radar.get_radar("sh600519", cost=0.0, qty=0))
    data = response["data"]

    assert response["status"] == "success"
    assert data["algorithm_v2"]["confidence"] == "STALE"
    assert data["algorithm_v2"]["summary"].startswith("数据健康异常，暂停走势推演")
    assert data["algorithm_v2"]["data_notes"]["levels"]["30"]["is_stale"] is True


def test_watchlist_data_health_reports_lagging_levels(monkeypatch):
    app_conn = sqlite3.connect(":memory:")
    app_conn.row_factory = sqlite3.Row
    app_conn.executescript(
        """
        CREATE TABLE watchlist_groups (id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, sort_order INTEGER);
        CREATE TABLE watchlist_items (id INTEGER PRIMARY KEY, group_id INTEGER, symbol TEXT, name TEXT, sort_order INTEGER);
        INSERT INTO watchlist_groups (id, user_id, name, sort_order) VALUES (1, 1, '观察', 0);
        INSERT INTO watchlist_items (group_id, symbol, name, sort_order) VALUES (1, 'sz000988', '华工科技', 0);
        """
    )
    lake_conn = sqlite3.connect(":memory:")
    lake_conn.row_factory = sqlite3.Row
    lake_conn.executescript(
        """
        CREATE TABLE klines (
          symbol TEXT, freq TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
          volume REAL, amount REAL, adjustflag TEXT
        );
        INSERT INTO klines VALUES ('sz.000988','day','2026-04-27',1,1,1,1,0,0,'2');
        INSERT INTO klines VALUES ('sz.000988','30','2025-03-04 15:00:00',1,1,1,1,0,0,'2');
        INSERT INTO klines VALUES ('sz.000988','5','2026-04-27 15:00:00',1,1,1,1,0,0,'2');
        """
    )

    class NoCloseConnection:
        def __init__(self, conn):
            self.conn = conn
        def execute(self, *args, **kwargs):
            return self.conn.execute(*args, **kwargs)
        def close(self):
            pass

    monkeypatch.setattr(radar, "get_connection", lambda: NoCloseConnection(app_conn))
    monkeypatch.setattr(radar, "get_lake_connection", lambda source="baostock": lake_conn)

    response = asyncio.run(radar.get_watchlist_data_health(user_id=1))
    item = response["data"]["items"][0]

    assert response["status"] == "success"
    assert response["data"]["stale_count"] == 1
    assert item["symbol"] == "sz.000988"
    assert item["levels"]["30"]["is_stale"] is True
    assert item["levels"]["30"]["stale_reason"] == "LEVEL_STALE"


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
    assert data["deduction"] is None
    assert data["holding_plan"]["plan_type"] == "HOLDING"
    assert data["strategy"]["strategy_id"] == "holding_stage_manager"
    assert data["strategy"]["strategy_version"] == "1.0.0"


def test_holding_lookup_accepts_compact_persisted_symbol(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE positions (
            user_id INTEGER,
            symbol TEXT,
            quantity INTEGER,
            avg_cost REAL,
            current_price REAL,
            stop_loss_price REAL,
            trailing_stop_price REAL,
            entry_date TEXT,
            strategy_type TEXT,
            m5_entry_zg REAL,
            entry_thesis_json TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO positions (
            user_id, symbol, quantity, avg_cost, current_price,
            stop_loss_price, trailing_stop_price, entry_date,
            strategy_type, m5_entry_zg, entry_thesis_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, "sh600519", 1000, 19.0, 22.0, None, None, None, "未知", None, None),
    )

    class Wrapper:
        def execute(self, *args, **kwargs):
            return conn.execute(*args, **kwargs)

        def close(self):
            pass

    monkeypatch.setattr(radar, "get_connection", lambda: Wrapper())
    monkeypatch.setattr(radar, "_load_adapter_structure", lambda symbol: asyncio.sleep(0, result=make_adapter_result()))

    response = asyncio.run(radar.get_radar("sh.600519", user_id=1))
    data = response["data"]

    assert data["mode"] == "HOLDING"
    assert data["position_context"]["is_holding"] is True
    assert data["position_context"]["quantity"] == 1000
    assert data["position_context"]["avg_cost"] == 19.0
    assert data["coach_action"]["position_state"] != "EMPTY"


def test_get_radar_internal_call_handles_query_defaults(monkeypatch):
    class EmptyCursor:
        def fetchone(self):
            return None

    class Wrapper:
        def execute(self, *args, **kwargs):
            return EmptyCursor()

        def close(self):
            pass

    monkeypatch.setattr(radar, "get_connection", lambda: Wrapper())
    monkeypatch.setattr(radar, "_load_adapter_structure", lambda symbol: asyncio.sleep(0, result=make_adapter_result()))

    response = asyncio.run(radar.get_radar("sh600519", user_id=1))

    assert response["status"] == "success"
    assert response["data"]["symbol"] == "sh.600519"
    assert response["data"]["mode"] == "EMPTY"


def test_historical_high_excludes_current_bar_new_high():
    result = radar._historical_high_from_klines([
        {"time": "2026-04-24", "high": 50.0, "close": 48.0},
        {"time": "2026-04-27", "high": 55.0, "close": 53.0},
        {"time": "2026-04-28", "high": 58.0, "close": 54.0},
    ])

    assert result["price"] == 55.0
    assert result["time"] == "2026-04-27"
    assert result["current_bar_high"] == 58.0
    assert result["is_current_bar_new_high"] is True


def test_historical_high_prefers_confirmed_bi_before_current_leg():
    result = radar._historical_high_from_level_data({
        "price": 179.37,
        "klines": [
            {"time": "2026-04-27", "high": 171.96, "close": 170.0},
            {"time": "2026-04-28", "high": 180.0, "close": 179.37},
        ],
        "bis": [
            {"x0": "2026-02-06", "y0": 110.33, "x1": "2026-02-25", "y1": 164.56, "is_up": True},
            {"x0": "2026-02-25", "y0": 164.56, "x1": "2026-03-23", "y1": 124.71, "is_up": False},
            {"x0": "2026-03-23", "y0": 124.71, "x1": "2026-04-10", "y1": 164.8, "is_up": True},
            {"x0": "2026-04-10", "y0": 164.8, "x1": "2026-04-17", "y1": 149.69, "is_up": False},
        ],
    })

    assert result["price"] == 164.8
    assert result["time"] == "2026-04-10"
    assert result["source"] == "confirmed_bi"
    assert result["current_bar_high"] == 180.0
    assert result["is_current_bar_new_high"] is True


def test_position_context_uses_realtime_quote(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE positions (
            user_id INTEGER,
            symbol TEXT,
            quantity INTEGER,
            avg_cost REAL,
            current_price REAL,
            stop_loss_price REAL,
            trailing_stop_price REAL,
            entry_date TEXT,
            strategy_type TEXT,
            m5_entry_zg REAL,
            entry_thesis_json TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO positions (
            user_id, symbol, quantity, avg_cost, current_price,
            stop_loss_price, trailing_stop_price, entry_date,
            strategy_type, m5_entry_zg, entry_thesis_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1, "sh600519", 1000, 19.0, None, 17.0, 21.8, None, "未知", None, None),
    )

    class Wrapper:
        def execute(self, *args, **kwargs):
            return conn.execute(*args, **kwargs)

        def close(self):
            pass

    async def fake_quote(symbol):
        assert symbol == "sh.600519"
        return {"symbol": "sh600519", "name": "贵州茅台", "price": 22.8, "time": "2026-04-28 11:30:00"}

    monkeypatch.setattr(radar, "get_connection", lambda: Wrapper())
    monkeypatch.setattr(radar, "_load_adapter_structure", lambda symbol: asyncio.sleep(0, result=make_adapter_result()))
    monkeypatch.setattr(radar, "get_current_price", fake_quote)

    response = asyncio.run(radar.get_radar("sh600519", user_id=1))
    context = response["data"]["position_context"]

    assert response["data"]["quote"]["available"] is True
    assert context["current_price"] == 22.8
    assert context["price_source"] == "tencent_quote"
    assert context["pnl_pct"] == 20.0
    assert response["data"]["coach_action"]["nearest_risk_line"]["label"] == "移动止盈"


def test_holding_plan_includes_entry_thesis_from_position():
    from server.engines.decision.radar_planner import build_radar_decision

    thesis = {
        "strategy_type": "战法二",
        "entry_level": "5m",
        "original_stop_loss": 9.2,
    }
    strategy, entry_plan, holding_plan, plans = build_radar_decision(
        {},
        {
            "day": {"price": 12.0, "patterns": [], "zg": 11.0, "zd": 10.0},
            "m30": {"patterns": [], "zg": 10.5, "detail_bis": []},
            "m5": {},
        },
        {
            "cost": 10.0,
            "qty": 100,
            "strategy_type": "未知",
            "entry_thesis": thesis,
        },
        "仅供参考",
    )

    assert entry_plan is None
    assert holding_plan["entry_thesis"] == thesis
    assert holding_plan["risk"]["original_stop_loss"] == 9.2
    assert holding_plan["legacy_status"]["strategy_type"] == "战法二"
    assert plans == [holding_plan]


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
    assert data["deduction"]["status"] == "STALE"
    assert data["algorithm_v2"]["path"] == "NO_EDGE"
    assert data["algorithm_v2"]["confidence"] == "STALE"
    assert data["disclaimer"] == radar.DISCLAIMER


def test_empty_mode_stale_adapter_returns_stale_deduction(monkeypatch):
    adapter_result = make_adapter_result()
    adapter_result["freshness"]["is_stale"] = True
    adapter_result["freshness"]["stale_reason"] = "OUTDATED"

    async def fake_adapter(symbol):
        return adapter_result

    monkeypatch.setattr(radar, "_load_adapter_structure", fake_adapter)

    response = asyncio.run(radar.get_radar("sh600519", cost=0.0, qty=0))
    data = response["data"]

    assert response["status"] == "success"
    assert data["deduction"]["status"] == "STALE"
    assert data["deduction"]["confidence"] == "STALE"


def test_radar_reuses_cached_structure_between_requests(monkeypatch):
    calls = {"count": 0}

    async def fake_adapter(symbol):
        calls["count"] += 1
        return make_adapter_result()

    radar._clear_structure_cache()
    monkeypatch.setattr(radar, "_load_adapter_structure", fake_adapter)

    first = asyncio.run(radar.get_radar("sh600519", cost=0.0, qty=0))
    second = asyncio.run(radar.get_radar("sh600519", cost=0.0, qty=0))

    assert first["status"] == "success"
    assert second["status"] == "success"
    assert calls["count"] == 1
    radar._clear_structure_cache()
