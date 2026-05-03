import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.decision.intraday_t_features import IntradayTFeatures
from server.engines.decision.intraday_t_strategy import IntradayTParentContext
from server.engines.execution.paper_models import PaperAccount, PaperPosition, PaperRiskConfig
from server.engines.execution.paper_replay import ReplayStep, replay_intraday_t_steps


def account():
    return PaperAccount(
        paper_account_id="paper_1",
        user_id=1,
        cash=100000.0,
        positions={
            "sh.603893": PaperPosition(
                symbol="sh.603893",
                total_qty=1000,
                available_qty=400,
                protected_base_qty=300,
                avg_cost=10.0,
                last_price=12.0,
            )
        },
    )


def account_with_extra_t_qty():
    return PaperAccount(
        paper_account_id="paper_1",
        user_id=1,
        cash=100000.0,
        positions={
            "sh.603893": PaperPosition(
                symbol="sh.603893",
                total_qty=1000,
                available_qty=600,
                protected_base_qty=300,
                avg_cost=10.0,
                last_price=12.0,
            )
        },
    )


def feature(as_of, side, direction, parent_leg_id="sh.603893:15:down:10:15", price=0.0, event_price=0.0):
    return IntradayTFeatures(
        symbol="sh.603893",
        as_of=as_of,
        level_chain={"L0": "30", "L1": "5", "L2": "1"},
        paths={"main": "PULLBACK_IN_UPTREND"},
        latest_event={
            "side": side,
            "code": "S1" if side == "sell" else "B1",
            "bars_since_event": 1,
            **({"price": event_price} if event_price else {}),
        },
        divergence={"direction": direction, "strength": 0.8},
        freshness={"is_stale": False},
        parent_context={
            "parent_level": "15",
            "parent_task": "DOWN_LEG",
            "parent_leg_id": parent_leg_id,
            "allowed_first_side": "SELL",
        },
        position_to_center={
            "price": price,
            "distance_to_zg_atr": 0.0,
            "distance_to_zd_atr": 1.0,
            "zg": 10.0,
            "zd": 9.0,
        },
    )


def feature_with_bars(as_of, side, direction, bars_since, parent_leg_id="sh.603893:15:down:10:15"):
    item = feature(as_of, side, direction, parent_leg_id)
    return IntradayTFeatures(
        symbol=item.symbol,
        as_of=item.as_of,
        level_chain=item.level_chain,
        paths=item.paths,
        pattern_tags=item.pattern_tags,
        position_to_center=item.position_to_center,
        latest_event={**item.latest_event, "bars_since_event": bars_since},
        divergence=item.divergence,
        freshness=item.freshness,
        parent_context=item.parent_context,
    )


def test_replay_closes_two_leg_t_and_records_metrics():
    result = replay_intraday_t_steps(
        account(),
        [
            ReplayStep(
                features=feature("2026-04-29 10:30:00", "sell", "top"),
                next_bar={"date": "2026-04-29 10:31:00", "open": 12.0, "high": 12.2, "low": 11.8, "close": 12.1, "volume": 10000},
            ),
            ReplayStep(
                features=feature("2026-04-29 10:35:00", "buy", "bottom"),
                next_bar={"date": "2026-04-29 10:36:00", "open": 11.5, "high": 11.8, "low": 11.3, "close": 11.7, "volume": 10000},
            ),
        ],
        PaperRiskConfig(),
    )

    assert len(result.fills) == 2
    assert result.fills[0].side == "SELL"
    assert result.fills[1].side == "BUY"
    assert result.metrics["closed_t_count"] == 1
    assert result.metrics["open_t_count"] == 0
    assert result.metrics["normal_second_leg_count"] == 1
    assert result.metrics["forced_second_leg_count"] == 0
    assert result.metrics["second_leg_watch_count"] == 0
    assert result.metrics["max_open_risk_bars"] == 0
    assert result.metrics["gross_t_pnl"] == 49.99
    assert result.metrics["spread_t_pnl"] == 48.82
    assert result.metrics["total_fees"] == 10.6232
    assert result.metrics["slippage_cost"] == 1.17
    assert result.metrics["net_t_pnl"] == 38.1968
    assert result.metrics["avg_net_t_pnl"] == 38.1968
    assert result.metrics["t_closure_rate"] == 1.0
    assert result.metrics["decision_counts"] == {"SELL_THEN_BUY_BACK": 1, "BUY_THEN_SELL_BACK": 1}
    assert result.metrics["reason_counts"] == {
        "top_divergence_sell_first": 1,
        "buyback_triggered": 1,
    }
    assert result.account.positions["sh.603893"].total_qty == 1000


def test_replay_metrics_count_no_trade_reasons():
    stale_feature = IntradayTFeatures(
        symbol="sh.603893",
        as_of="2026-04-29 10:30:00",
        level_chain={"L0": "30", "L1": "5", "L2": "1"},
        paths={"main": "PULLBACK_IN_UPTREND"},
        latest_event={"side": "sell", "bars_since_event": 1},
        divergence={"direction": "top", "strength": 0.8},
        freshness={"is_stale": True},
    )

    result = replay_intraday_t_steps(
        account(),
        [ReplayStep(features=stale_feature)],
        PaperRiskConfig(),
    )

    assert result.metrics["filled_count"] == 0
    assert result.metrics["decision_counts"] == {"NO_TRADE": 1}
    assert result.metrics["reason_counts"] == {"stale_structure": 1}
    assert result.metrics["decision_status_counts"] == {"BLOCKED": 1}


def test_parent_cycle_budget_blocks_new_first_leg_after_one_closed_t():
    result = replay_intraday_t_steps(
        account(),
        [
            ReplayStep(
                features=feature("2026-04-29 10:30:00", "sell", "top"),
                next_bar={"date": "2026-04-29 10:31:00", "open": 12.0, "high": 12.2, "low": 11.8, "close": 12.1, "volume": 10000},
            ),
            ReplayStep(
                features=feature("2026-04-29 10:35:00", "buy", "bottom"),
                next_bar={"date": "2026-04-29 10:36:00", "open": 11.5, "high": 11.8, "low": 11.3, "close": 11.7, "volume": 10000},
            ),
            ReplayStep(
                features=feature("2026-04-29 10:40:00", "sell", "top"),
                next_bar={"date": "2026-04-29 10:41:00", "open": 12.1, "high": 12.2, "low": 11.9, "close": 12.0, "volume": 10000},
            ),
        ],
        PaperRiskConfig(),
        parent_context=IntradayTParentContext(
            parent_level="15",
            parent_task="DOWN_LEG",
            allowed_first_side="SELL",
            max_cycles=1,
        ),
    )

    assert len(result.fills) == 2
    assert result.decisions[-1].decision == "NO_TRADE"
    assert result.decisions[-1].reason == "parent_cycle_budget_used"


def test_auto_parent_context_blocks_new_first_leg_inside_same_parent_bi():
    result = replay_intraday_t_steps(
        account(),
        [
            ReplayStep(
                features=feature("2026-04-29 10:30:00", "sell", "top"),
                next_bar={"date": "2026-04-29 10:31:00", "open": 12.0, "high": 12.2, "low": 11.8, "close": 12.1, "volume": 10000},
            ),
            ReplayStep(
                features=feature("2026-04-29 10:35:00", "buy", "bottom"),
                next_bar={"date": "2026-04-29 10:36:00", "open": 11.5, "high": 11.8, "low": 11.3, "close": 11.7, "volume": 10000},
            ),
            ReplayStep(
                features=feature("2026-04-29 10:40:00", "sell", "top"),
                next_bar={"date": "2026-04-29 10:41:00", "open": 12.1, "high": 12.2, "low": 11.9, "close": 12.0, "volume": 10000},
            ),
        ],
        PaperRiskConfig(),
        auto_parent_context=True,
        parent_max_cycles=1,
    )

    assert len(result.fills) == 2
    assert result.decisions[-1].reason == "parent_cycle_budget_used"


def test_auto_parent_context_resets_budget_when_parent_bi_changes():
    result = replay_intraday_t_steps(
        account_with_extra_t_qty(),
        [
            ReplayStep(
                features=feature("2026-04-29 10:30:00", "sell", "top", "sh.603893:15:down:10:15"),
                next_bar={"date": "2026-04-29 10:31:00", "open": 12.0, "high": 12.2, "low": 11.8, "close": 12.1, "volume": 10000},
            ),
            ReplayStep(
                features=feature("2026-04-29 10:35:00", "buy", "bottom", "sh.603893:15:down:10:15"),
                next_bar={"date": "2026-04-29 10:36:00", "open": 11.5, "high": 11.8, "low": 11.3, "close": 11.7, "volume": 10000},
            ),
            ReplayStep(
                features=feature("2026-04-29 10:40:00", "sell", "top", "sh.603893:15:down:15:30"),
                next_bar={"date": "2026-04-29 10:41:00", "open": 12.1, "high": 12.2, "low": 11.9, "close": 12.0, "volume": 10000},
            ),
        ],
        PaperRiskConfig(),
        auto_parent_context=True,
        parent_max_cycles=1,
    )

    assert len(result.fills) == 3
    assert result.decisions[-1].decision == "SELL_THEN_BUY_BACK"


def test_replay_force_closes_second_leg_on_timeout():
    result = replay_intraday_t_steps(
        account(),
        [
            ReplayStep(
                features=feature("2026-04-29 10:30:00", "sell", "top"),
                next_bar={"date": "2026-04-29 10:31:00", "open": 12.0, "high": 12.2, "low": 11.8, "close": 12.1, "volume": 10000},
            ),
            ReplayStep(
                features=feature("2026-04-29 10:35:00", "sell", "top"),
                next_bar={"date": "2026-04-29 10:36:00", "open": 11.8, "high": 12.0, "low": 11.6, "close": 11.7, "volume": 10000},
            ),
        ],
        PaperRiskConfig(buyback_timeout_bars=0),
    )

    assert [fill.side for fill in result.fills] == ["SELL", "BUY"]
    assert result.decisions[-1].decision == "BUYBACK_TIMEOUT"
    assert result.decisions[-1].reason == "second_leg_timeout_force_close"
    assert result.decisions[-1].intent is not None
    assert result.decisions[-1].intent.linked_intent_id == "paper_intent_1"
    assert result.metrics["closed_t_count"] == 1
    assert result.metrics["open_t_count"] == 0
    assert result.metrics["forced_second_leg_count"] == 1
    assert result.metrics["closed_t_cycle_count"] == 1
    assert result.metrics["max_open_risk_bars"] == 0
    assert result.account.positions["sh.603893"].total_qty == 1000


def test_replay_metrics_track_open_second_leg_risk():
    result = replay_intraday_t_steps(
        account(),
        [
            ReplayStep(
                features=feature("2026-04-29 10:30:00", "sell", "top"),
                next_bar={"date": "2026-04-29 10:31:00", "open": 12.0, "high": 12.2, "low": 11.8, "close": 12.1, "volume": 10000},
            ),
            ReplayStep(features=feature("2026-04-29 10:32:00", "sell", "top")),
            ReplayStep(features=feature("2026-04-29 10:33:00", "sell", "top")),
            ReplayStep(features=feature("2026-04-29 10:34:00", "sell", "top")),
        ],
        PaperRiskConfig(),
    )

    assert result.metrics["first_leg_count"] == 1
    assert result.metrics["closed_t_count"] == 0
    assert result.metrics["open_t_count"] == 1
    assert result.metrics["second_leg_watch_count"] == 3
    assert result.metrics["max_open_risk_bars"] == 2
    assert result.metrics["last_open_risk_bars"] == 2
    assert result.metrics["avg_open_risk_bars"] == 1.0


def test_replay_min_second_leg_interval_delays_normal_buyback():
    result = replay_intraday_t_steps(
        account(),
        [
            ReplayStep(
                features=feature("2026-04-29 10:30:00", "sell", "top"),
                next_bar={"date": "2026-04-29 10:31:00", "open": 12.0, "high": 12.2, "low": 11.8, "close": 12.1, "volume": 10000},
            ),
            ReplayStep(features=feature("2026-04-29 10:32:00", "buy", "bottom")),
            ReplayStep(features=feature("2026-04-29 10:33:00", "buy", "bottom")),
            ReplayStep(features=feature("2026-04-29 10:34:00", "buy", "bottom")),
        ],
        PaperRiskConfig(min_second_leg_bars=5),
    )

    assert result.metrics["filled_count"] == 1
    assert result.metrics["closed_t_count"] == 0
    assert result.metrics["open_t_count"] == 1
    assert result.decisions[-1].decision == "SECOND_LEG_WATCHING"
    assert result.decisions[-1].evidence["signals"]["second_leg_interval_ok"]["matched"] is False


def test_replay_does_not_close_second_leg_on_stale_buyback_signal():
    result = replay_intraday_t_steps(
        account(),
        [
            ReplayStep(
                features=feature("2026-04-29 10:30:00", "sell", "top"),
                next_bar={"date": "2026-04-29 10:31:00", "open": 12.0, "high": 12.2, "low": 11.8, "close": 12.1, "volume": 10000},
            ),
            ReplayStep(
                features=feature_with_bars("2026-04-29 10:40:00", "buy", "bottom", 9),
                next_bar={"date": "2026-04-29 10:41:00", "open": 11.5, "high": 11.8, "low": 11.3, "close": 11.7, "volume": 10000},
            ),
        ],
        PaperRiskConfig(event_freshness_bars=5),
    )

    assert result.metrics["filled_count"] == 1
    assert result.metrics["closed_t_count"] == 0
    assert result.decisions[-1].decision == "SECOND_LEG_WATCHING"
    assert result.decisions[-1].evidence["signals"]["fresh_event"]["matched"] is False


def test_replay_confirms_pending_first_leg_before_fill():
    result = replay_intraday_t_steps(
        account(),
        [
            ReplayStep(
                features=feature("2026-04-29 10:30:00", "sell", "top", price=10.5, event_price=10.5),
                next_bar={"date": "2026-04-29 10:31:00", "open": 10.4, "high": 10.5, "low": 10.1, "close": 10.2, "volume": 10000},
            ),
            ReplayStep(
                features=feature("2026-04-29 10:31:00", "sell", "top", price=10.2, event_price=10.5),
                next_bar={"date": "2026-04-29 10:32:00", "open": 10.1, "high": 10.2, "low": 9.8, "close": 9.9, "volume": 10000},
            ),
        ],
        PaperRiskConfig(first_leg_confirmation_bars=1),
    )

    assert result.decisions[0].status == "CANDIDATE_ONLY"
    assert result.decisions[0].reason == "pending_first_leg_confirmation"
    assert result.decisions[1].decision == "SELL_THEN_BUY_BACK"
    assert result.decisions[1].reason == "confirmed_first_leg"
    assert [fill.side for fill in result.fills] == ["SELL"]


def test_replay_blocks_first_leg_when_window_is_too_short():
    result = replay_intraday_t_steps(
        account(),
        [
            ReplayStep(
                features=feature("2026-04-29 10:30:00", "sell", "top"),
                next_bar={"date": "2026-04-29 10:31:00", "open": 12.0, "high": 12.2, "low": 11.8, "close": 12.1, "volume": 10000},
            ),
            ReplayStep(
                features=feature("2026-04-29 10:31:00", "sell", "top"),
                next_bar={"date": "2026-04-29 10:32:00", "open": 12.1, "high": 12.2, "low": 11.9, "close": 12.0, "volume": 10000},
            ),
        ],
        PaperRiskConfig(min_bars_before_window_end_for_first_leg=3),
    )

    assert result.metrics["filled_count"] == 0
    assert result.metrics["reason_counts"] == {"insufficient_window_for_first_leg": 2}
    assert result.decisions[0].decision == "NO_TRADE"
    assert result.decisions[0].reason == "insufficient_window_for_first_leg"
    assert result.decisions[0].evidence["window_guard"]["remaining_bars"] == 1
    assert result.decisions[0].evidence["window_guard"]["blocked_decision"] == "SELL_THEN_BUY_BACK"


def test_replay_window_guard_does_not_block_second_leg():
    result = replay_intraday_t_steps(
        account(),
        [
            ReplayStep(
                features=feature("2026-04-29 10:30:00", "sell", "top"),
                next_bar={"date": "2026-04-29 10:31:00", "open": 12.0, "high": 12.2, "low": 11.8, "close": 12.1, "volume": 10000},
            ),
            ReplayStep(
                features=feature("2026-04-29 10:31:00", "buy", "bottom"),
                next_bar={"date": "2026-04-29 10:32:00", "open": 11.5, "high": 11.8, "low": 11.3, "close": 11.7, "volume": 10000},
            ),
        ],
        PaperRiskConfig(min_bars_before_window_end_for_first_leg=1),
    )

    assert [fill.side for fill in result.fills] == ["SELL", "BUY"]
    assert result.metrics["closed_t_count"] == 1
    assert result.decisions[-1].decision == "BUY_THEN_SELL_BACK"
