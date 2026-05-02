import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from server.engines.decision.intraday_t_features import IntradayTFeatures
from server.engines.execution.paper_replay import ReplayStep
from server.scripts import paper_replay_pool


def feature(as_of, side, direction):
    return IntradayTFeatures(
        symbol="sh.603893",
        as_of=as_of,
        level_chain={"L0": "30", "L1": "5", "L2": "1"},
        paths={"main": "PULLBACK_IN_UPTREND"},
        latest_event={"side": side, "code": "S1" if side == "sell" else "B1", "bars_since_event": 1},
        divergence={"direction": direction, "strength": 0.8},
        freshness={"is_stale": False},
    )


def blocked_feature(as_of):
    return IntradayTFeatures(
        symbol="sh.603893",
        as_of=as_of,
        level_chain={"L0": "30", "L1": "5", "L2": "1"},
        paths={"main": "NO_EDGE"},
        position_to_center={"distance_to_zg_atr": -0.8, "distance_to_zd_atr": 0.8},
        latest_event={"side": "sell", "code": "S1", "bars_since_event": 1},
        divergence={"direction": "top", "strength": 0.8},
        freshness={"is_stale": False},
        parent_context={"allowed_first_side": "SELL"},
    )


@pytest.mark.anyio
async def test_run_replay_pool_summarizes_fake_steps_without_persist(monkeypatch):
    async def fake_steps(**kwargs):
        return [
            ReplayStep(
                features=feature("2026-04-29 10:30:00", "sell", "top"),
                next_bar={"date": "2026-04-29 10:31:00", "open": 12.0, "high": 12.2, "low": 11.8, "close": 12.1, "volume": 10000},
            ),
            ReplayStep(
                features=feature("2026-04-29 10:35:00", "buy", "bottom"),
                next_bar={"date": "2026-04-29 10:36:00", "open": 11.5, "high": 11.8, "low": 11.3, "close": 11.7, "volume": 10000},
            ),
        ]

    monkeypatch.setattr(paper_replay_pool, "init_db", lambda: None)
    monkeypatch.setattr(paper_replay_pool, "_latest_close", lambda symbol, **kwargs: 12.0)
    monkeypatch.setattr(paper_replay_pool, "build_replay_steps_from_klines", fake_steps)

    summaries = await paper_replay_pool.run_replay_pool(
        symbols=["sh.603893"],
        start_date="2026-04-29 10:30:00",
        end_date="2026-04-29 10:36:00",
        persist=False,
        persist_feature_cache=False,
    )

    assert summaries[0]["symbol"] == "sh.603893"
    assert summaries[0]["steps"] == 2
    assert summaries[0]["closed_t_count"] == 1
    assert summaries[0]["open_t_count"] == 0
    assert summaries[0]["t_closure_rate"] == 1.0
    assert summaries[0]["reason_counts"] == {
        "top_divergence_sell_first": 1,
        "buyback_triggered": 1,
    }
    assert summaries[0]["feature_cache"] == {"hits": 0, "misses": 0, "size": 0}


@pytest.mark.anyio
async def test_run_replay_pool_passes_strategy_profile_to_config(monkeypatch):
    captured = {}

    async def fake_steps(**kwargs):
        return []

    def fake_replay(account, steps, config, **kwargs):
        captured["config"] = config
        return paper_replay_pool.ReplayResult(account=account, decisions=[], fills=[], metrics={})

    monkeypatch.setattr(paper_replay_pool, "init_db", lambda: None)
    monkeypatch.setattr(paper_replay_pool, "_latest_close", lambda symbol, **kwargs: 12.0)
    monkeypatch.setattr(paper_replay_pool, "build_replay_steps_from_klines", fake_steps)
    monkeypatch.setattr(paper_replay_pool, "replay_intraday_t_steps", fake_replay)

    await paper_replay_pool.run_replay_pool(
        symbols=["sh.603893"],
        start_date="2026-04-29 10:30:00",
        end_date="2026-04-29 10:36:00",
        strategy_profile="loose",
        min_second_leg_bars=7,
        event_freshness_bars=9,
        min_expected_edge_after_cost=18.5,
        expected_edge_atr_multiple=3.0,
        first_leg_confirmation_bars=1,
        second_leg_confirmation_bars=2,
        min_bars_before_window_end_for_first_leg=8,
        persist=False,
        persist_feature_cache=False,
    )

    assert captured["config"].profile == "loose"
    assert captured["config"].min_second_leg_bars == 7
    assert captured["config"].event_freshness_bars == 9
    assert captured["config"].min_divergence_strength == 0.4
    assert captured["config"].min_expected_edge_after_cost == 18.5
    assert captured["config"].expected_edge_atr_multiple == 3.0
    assert captured["config"].first_leg_confirmation_bars == 1
    assert captured["config"].second_leg_confirmation_bars == 2
    assert captured["config"].min_bars_before_window_end_for_first_leg == 8


@pytest.mark.anyio
async def test_run_replay_pool_accepts_loose_observe_profile(monkeypatch):
    captured = {}

    async def fake_steps(**kwargs):
        return []

    def fake_replay(account, steps, config, **kwargs):
        captured["config"] = config
        return paper_replay_pool.ReplayResult(account=account, decisions=[], fills=[], metrics={})

    monkeypatch.setattr(paper_replay_pool, "init_db", lambda: None)
    monkeypatch.setattr(paper_replay_pool, "_latest_close", lambda symbol, **kwargs: 12.0)
    monkeypatch.setattr(paper_replay_pool, "build_replay_steps_from_klines", fake_steps)
    monkeypatch.setattr(paper_replay_pool, "replay_intraday_t_steps", fake_replay)

    await paper_replay_pool.run_replay_pool(
        symbols=["sh.603893"],
        start_date="2026-04-29 10:30:00",
        end_date="2026-04-29 10:36:00",
        strategy_profile="loose_observe",
        persist=False,
        persist_feature_cache=False,
    )

    assert captured["config"].profile == "loose_observe"
    assert captured["config"].observe_only is True
    assert captured["config"].event_freshness_bars == 20


def test_account_defaults_avg_cost_to_reference_price(monkeypatch):
    monkeypatch.setattr(paper_replay_pool, "_latest_close", lambda symbol, **kwargs: 178.0)

    account = paper_replay_pool._account_for_symbol(
        symbol="sh.603893",
        user_id=1,
        initial_cash=100000.0,
        base_qty=1000,
        protected_base_qty=300,
        available_qty=400,
        avg_cost=0.0,
    )

    position = account.positions["sh.603893"]
    assert position.avg_cost == 178.0
    assert position.last_price == 178.0


def test_latest_close_uses_requested_replay_source(monkeypatch):
    calls = []

    def fake_query(symbol, freq, end_date=None, limit=1, source=None, adjustflag="2"):
        calls.append((freq, end_date, source, adjustflag))
        if freq == "1" and source == "qmt" and adjustflag == "3":
            return [{"date": end_date, "close": 179.16}]
        return []

    monkeypatch.setattr(paper_replay_pool, "query_klines", fake_query)

    price = paper_replay_pool._latest_close(
        "sh.603893",
        source="qmt",
        adjustflag="3",
        end_date="2026-04-24 13:30:00",
    )

    assert price == 179.16
    assert calls[0] == ("1", "2026-04-24 13:30:00", "qmt", "3")


def test_run_id_uses_label_and_auto_version_suffix():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE paper_replay_runs (run_id TEXT PRIMARY KEY)")
    base = paper_replay_pool._make_run_id(
        user_id=1,
        symbol="sz.300724",
        start_date="2026-04-24 13:30:00",
        end_date="2026-04-24 13:36:00",
        run_label="quality filter v1",
    )
    assert base == "paper_run_1_sz300724_2026-04-24_13_30_00_2026-04-24_13_36_00_quality_filter_v1"

    conn.execute("INSERT INTO paper_replay_runs (run_id) VALUES (?)", (base,))
    conn.execute("INSERT INTO paper_replay_runs (run_id) VALUES (?)", (f"{base}_v2",))

    assert paper_replay_pool._unique_run_id(conn, base) == f"{base}_v3"


def test_print_decisions_includes_reasons_and_fills(capsys):
    account = paper_replay_pool.PaperAccount(
        paper_account_id="paper_1",
        user_id=1,
        cash=100000.0,
        positions={
            "sh.603893": paper_replay_pool.PaperPosition(
                symbol="sh.603893",
                total_qty=1000,
                available_qty=400,
                protected_base_qty=300,
                avg_cost=10.0,
                last_price=12.0,
            )
        },
    )
    result = paper_replay_pool.replay_intraday_t_steps(
        account,
        [
            ReplayStep(
                features=feature("2026-04-29 10:30:00", "sell", "top"),
                next_bar={
                    "date": "2026-04-29 10:31:00",
                    "open": 12.0,
                    "high": 12.2,
                    "low": 11.8,
                    "close": 12.1,
                    "volume": 10000,
                },
            )
        ],
        paper_replay_pool.PaperRiskConfig(),
    )

    paper_replay_pool.print_decisions("sh.603893", result)

    output = capsys.readouterr().out
    assert "SELL_THEN_BUY_BACK top_divergence_sell_first" in output
    assert "event=S1 bars=1 div=top/0.8000" in output
    assert "FILL FILLED SELL qty=100 price=11.9940" in output


def test_print_decisions_includes_signal_blockers(capsys):
    account = paper_replay_pool.PaperAccount(
        paper_account_id="paper_1",
        user_id=1,
        cash=100000.0,
        positions={
            "sh.603893": paper_replay_pool.PaperPosition(
                symbol="sh.603893",
                total_qty=1000,
                available_qty=400,
                protected_base_qty=300,
                avg_cost=10.0,
                last_price=12.0,
            )
        },
    )
    result = paper_replay_pool.replay_intraday_t_steps(
        account,
        [ReplayStep(features=blocked_feature("2026-04-29 10:30:00"))],
        paper_replay_pool.PaperRiskConfig(),
    )

    paper_replay_pool.print_decisions("sh.603893", result)

    output = capsys.readouterr().out
    assert "BLOCKERS" in output
    assert "first_leg_path_allowed=false path=NO_EDGE" in output
    assert "sell_first_position_quality=false zg_atr=-0.8000" in output
