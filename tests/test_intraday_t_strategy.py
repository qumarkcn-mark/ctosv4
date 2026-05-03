import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.decision.intraday_t_features import IntradayTFeatures
from server.engines.decision.intraday_t_profiles import build_intraday_t_risk_config
from server.engines.decision.intraday_t_strategy import (
    IntradayTParentContext,
    IntradayTState,
    evaluate_intraday_t,
    parent_context_from_features,
)
from server.engines.execution.paper_models import PaperAccount, PaperPosition, PaperRiskConfig


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


def features(
    side="sell",
    direction="top",
    strength=0.8,
    stale=False,
    bars_since=1,
    path="PULLBACK_IN_UPTREND",
    distance_to_zg_atr=0.0,
    distance_to_zd_atr=0.0,
    price=0.0,
    event_price=0.0,
    parent_allowed_first_side="SELL",
):
    return IntradayTFeatures(
        symbol="sh.603893",
        as_of="2026-04-29 10:30:00",
        level_chain={"L0": "30", "L1": "5", "L2": "1"},
        paths={"main": path},
        position_to_center={
            "distance_to_zg_atr": distance_to_zg_atr,
            "distance_to_zd_atr": distance_to_zd_atr,
            "price": price,
            "zg": 10.0,
            "zd": 9.0,
        },
        latest_event={
            "side": side,
            "code": "S1" if side == "sell" else "B1",
            "bars_since_event": bars_since,
            **({"price": event_price} if event_price else {}),
        },
        divergence={"direction": direction, "strength": strength},
        volatility={"atr": 0.0},
        freshness={"is_stale": stale},
        parent_context={
            "parent_level": "15",
            "parent_task": "DOWN_LEG",
            "parent_leg_id": "sh.603893:15:down:10:15",
            "allowed_first_side": parent_allowed_first_side,
        },
    )


def test_no_base_position_returns_no_trade():
    empty = PaperAccount(paper_account_id="paper_1", user_id=1, cash=100000.0)

    decision = evaluate_intraday_t(features(), empty, PaperRiskConfig())

    assert decision.decision == "NO_TRADE"
    assert decision.reason == "no_protected_base_position"


def test_sell_then_buy_back_creates_first_leg_intent():
    decision = evaluate_intraday_t(features(), account(), PaperRiskConfig(default_t_qty=100))

    assert decision.decision == "SELL_THEN_BUY_BACK"
    assert decision.intent is not None
    assert decision.intent.side == "SELL"
    assert decision.intent.quantity == 100
    assert decision.intent.dry_run is True
    assert decision.intent.simulator is True
    assert decision.evidence["position_event"]["name"] == "开第一腿卖出#顶背驰"
    assert decision.evidence["position_plan"]["exit_plan"]["force_close"] is True
    assert decision.evidence["signals"]["has_exit_plan"]["matched"] is True


def test_first_leg_decision_contains_complete_position_plan():
    decision = evaluate_intraday_t(features(), account(), PaperRiskConfig(default_t_qty=100, buyback_timeout_bars=20))

    plan = decision.evidence["position_plan"]
    event = decision.evidence["position_event"]

    assert plan["name"] == "intraday_t_base_position"
    assert plan["timeout_bars"] == 20
    assert plan["exit_plan"]["normal_sell_first_exit"] == "bottom_divergence_buyback"
    assert plan["exit_plan"]["fallback_exit"] == "next_bar_open_after_timeout"
    assert event["signals_all"] == [
        "flat_cycle",
        "fresh_event",
        "sell_first_trigger",
        "has_exit_plan",
        "first_leg_path_allowed",
        "parent_allows_sell_first",
        "sell_first_position_quality",
        "expected_edge_after_cost",
    ]


def test_first_leg_requires_position_quality_near_structure_edge():
    decision = evaluate_intraday_t(
        features(distance_to_zg_atr=-0.8),
        account(),
        PaperRiskConfig(default_t_qty=100),
    )

    assert decision.decision == "NO_TRADE"
    assert decision.reason == "no_baseline_t_trigger"
    assert decision.evidence["signals"]["sell_first_trigger"]["matched"] is True
    assert decision.evidence["signals"]["sell_first_position_quality"]["matched"] is False


def test_first_leg_requires_expected_edge_after_cost_when_enabled():
    candidate = replace(
        features(distance_to_zg_atr=0.0, distance_to_zd_atr=1.0),
        volatility={"atr": 0.1},
    )

    decision = evaluate_intraday_t(
        candidate,
        account(),
        PaperRiskConfig(default_t_qty=100, min_expected_edge_after_cost=15.0),
    )

    assert decision.decision == "NO_TRADE"
    assert decision.reason == "no_baseline_t_trigger"
    edge = decision.evidence["signals"]["expected_edge_after_cost"]
    assert edge["matched"] is False
    assert edge["evidence"]["enabled"] is True
    assert edge["evidence"]["structure_edge"] == 100.0
    assert edge["evidence"]["atr_edge"] == 20.0
    assert edge["evidence"]["gross_edge"] == 20.0
    assert edge["evidence"]["net_edge"] < 15.0


def test_first_leg_expected_edge_gate_is_disabled_by_default():
    candidate = features(distance_to_zg_atr=0.0, distance_to_zd_atr=1.0)

    decision = evaluate_intraday_t(candidate, account(), PaperRiskConfig(default_t_qty=100))

    assert decision.decision == "SELL_THEN_BUY_BACK"
    edge = decision.evidence["signals"]["expected_edge_after_cost"]
    assert edge["matched"] is True
    assert edge["evidence"]["enabled"] is False


def test_balanced_profile_relaxes_freshness_divergence_and_position_quality():
    candidate = features(bars_since=8, strength=0.45, distance_to_zg_atr=-0.5)

    strict = evaluate_intraday_t(candidate, account(), build_intraday_t_risk_config(profile="strict"))
    balanced = evaluate_intraday_t(candidate, account(), build_intraday_t_risk_config(profile="balanced"))

    assert strict.decision == "NO_TRADE"
    assert strict.reason == "event_not_fresh"
    assert balanced.decision == "SELL_THEN_BUY_BACK"
    assert balanced.evidence["signals"]["fresh_event"]["evidence"]["max_bars"] == 8
    assert balanced.evidence["signals"]["sell_first_trigger"]["evidence"]["min_divergence_strength"] == 0.45
    assert balanced.evidence["signals"]["sell_first_position_quality"]["evidence"]["threshold"] == -0.5


def test_explore_profile_relaxes_signals_but_keeps_execution_gates():
    config = build_intraday_t_risk_config(profile="explore")

    assert config.event_freshness_bars == 12
    assert config.min_divergence_strength == 0.4
    assert config.sell_first_min_distance_to_zg_atr == -0.8
    assert config.first_leg_confirmation_bars == 1
    assert config.min_expected_edge_after_cost == 5.0
    assert config.min_bars_before_window_end_for_first_leg == 12
    assert config.observe_only is False


def test_loose_observe_profile_records_candidate_without_intent():
    candidate = features(bars_since=18, strength=0.36, distance_to_zg_atr=-1.1)

    decision = evaluate_intraday_t(candidate, account(), build_intraday_t_risk_config(profile="loose_observe"))

    assert decision.decision == "SELL_THEN_BUY_BACK"
    assert decision.status == "CANDIDATE_ONLY"
    assert decision.reason == "observe_only_top_divergence_sell_first"
    assert decision.intent is None
    assert decision.evidence["observe_only"] is True
    assert decision.evidence["signals"]["fresh_event"]["evidence"]["max_bars"] == 20
    assert decision.evidence["signals"]["sell_first_trigger"]["evidence"]["min_divergence_strength"] == 0.35


def test_first_leg_confirmation_waits_for_price_to_move_away_from_top_event():
    candidate = features(
        bars_since=1,
        price=10.2,
        event_price=10.5,
        distance_to_zg_atr=0.2,
        distance_to_zd_atr=1.2,
    )

    decision = evaluate_intraday_t(
        candidate,
        account(),
        PaperRiskConfig(default_t_qty=100, first_leg_confirmation_bars=1),
        IntradayTState(
            pending_first_leg_side="SELL",
            pending_first_leg_condition_id="first_leg_sell_top_divergence",
            pending_first_leg_event_key="event-1",
            pending_first_leg_event_price=10.5,
            pending_first_leg_bars=1,
        ),
    )

    assert decision.decision == "SELL_THEN_BUY_BACK"
    assert decision.reason == "confirmed_first_leg"
    assert decision.intent is not None
    assert decision.evidence["pending_first_leg"]["price_confirmed"] is True


def test_first_leg_confirmation_stores_candidate_before_creating_intent():
    candidate = features(
        bars_since=0,
        price=10.5,
        event_price=10.5,
        distance_to_zg_atr=0.2,
        distance_to_zd_atr=1.2,
    )

    decision = evaluate_intraday_t(
        candidate,
        account(),
        PaperRiskConfig(default_t_qty=100, first_leg_confirmation_bars=1),
    )

    assert decision.decision == "SELL_THEN_BUY_BACK"
    assert decision.status == "CANDIDATE_ONLY"
    assert decision.reason == "pending_first_leg_confirmation"
    assert decision.intent is None
    assert decision.evidence["pending_first_leg"]["event_price"] == 10.5


def test_first_leg_confirmation_signal_explains_unconfirmed_current_event():
    candidate = features(
        bars_since=1,
        price=10.2,
        event_price=10.5,
        distance_to_zg_atr=0.2,
        distance_to_zd_atr=1.2,
    )

    decision = evaluate_intraday_t(
        candidate,
        account(),
        PaperRiskConfig(default_t_qty=100, first_leg_confirmation_bars=1),
    )

    assert decision.status == "CANDIDATE_ONLY"
    confirmation = decision.evidence["signals"]["first_leg_confirmation_ok"]
    assert confirmation["matched"] is True
    assert confirmation["evidence"]["price_confirmed"] is True


def test_first_leg_confirmation_blocks_top_event_before_price_turns_down():
    candidate = features(
        bars_since=1,
        price=10.6,
        event_price=10.5,
        distance_to_zg_atr=0.2,
        distance_to_zd_atr=1.2,
    )

    decision = evaluate_intraday_t(
        candidate,
        account(),
        PaperRiskConfig(default_t_qty=100, first_leg_confirmation_bars=1),
        IntradayTState(
            pending_first_leg_side="SELL",
            pending_first_leg_condition_id="first_leg_sell_top_divergence",
            pending_first_leg_event_key="event-1",
            pending_first_leg_event_price=10.5,
            pending_first_leg_bars=1,
        ),
    )

    assert decision.decision == "NO_TRADE"
    assert decision.reason == "pending_first_leg_confirmation"
    assert decision.evidence["pending_first_leg"]["price_confirmed"] is False


def test_first_leg_requires_allowed_path():
    decision = evaluate_intraday_t(
        features(path="NO_EDGE"),
        account(),
        PaperRiskConfig(default_t_qty=100),
    )

    assert decision.decision == "NO_TRADE"
    assert decision.evidence["signals"]["first_leg_path_allowed"]["matched"] is False


def test_first_leg_requires_parent_task_quality_signal():
    decision = evaluate_intraday_t(
        features(parent_allowed_first_side="BUY"),
        account(),
        PaperRiskConfig(default_t_qty=100),
    )

    assert decision.decision == "NO_TRADE"
    assert decision.evidence["signals"]["parent_allows_sell_first"]["matched"] is False


def test_protected_base_blocks_sell_quantity():
    decision = evaluate_intraday_t(features(), account(), PaperRiskConfig(default_t_qty=200))

    assert decision.decision == "NO_TRADE"
    assert decision.reason == "insufficient_sellable_t_quantity"


def test_stale_structure_blocks_trading_action():
    decision = evaluate_intraday_t(features(stale=True), account(), PaperRiskConfig())

    assert decision.decision == "NO_TRADE"
    assert decision.status == "BLOCKED"
    assert decision.reason == "stale_structure"


def test_second_leg_timeout_records_open_risk():
    state = IntradayTState(
        waiting_second_leg=True,
        first_leg_side="SELL",
        first_leg_intent_id="paper_intent_1",
        bars_since_first_leg=31,
    )

    decision = evaluate_intraday_t(features(side="sell", direction="top"), account(), PaperRiskConfig())
    assert decision.decision == "SELL_THEN_BUY_BACK"

    timeout = evaluate_intraday_t(features(side="sell", direction="top"), account(), PaperRiskConfig(), state)

    assert timeout.decision == "BUYBACK_TIMEOUT"
    assert timeout.status == "INTENT_CREATED"
    assert timeout.reason == "second_leg_timeout_force_close"
    assert timeout.intent is not None
    assert timeout.intent.side == "BUY"
    assert timeout.intent.linked_intent_id == "paper_intent_1"
    assert timeout.evidence["position_event"]["name"] == "第二腿超时强制买回"
    assert timeout.evidence["signals"]["second_leg_timeout"]["matched"] is True
    assert timeout.evidence["as_of"] == "2026-04-29 10:30:00"
    assert timeout.evidence["bars_since_first_leg"] == 31


def test_second_leg_watching_keeps_feature_evidence():
    state = IntradayTState(
        waiting_second_leg=True,
        first_leg_side="BUY",
        first_leg_intent_id="paper_intent_1",
        bars_since_first_leg=3,
    )

    decision = evaluate_intraday_t(features(side="buy", direction="bottom"), account(), PaperRiskConfig(), state)

    assert decision.decision == "SECOND_LEG_WATCHING"
    assert decision.evidence["as_of"] == "2026-04-29 10:30:00"
    assert decision.evidence["latest_event"]["side"] == "buy"
    assert decision.evidence["bars_since_first_leg"] == 3


def test_second_leg_requires_minimum_interval_before_normal_buyback():
    state = IntradayTState(
        waiting_second_leg=True,
        first_leg_side="SELL",
        first_leg_intent_id="paper_intent_1",
        bars_since_first_leg=3,
    )
    config = PaperRiskConfig(min_second_leg_bars=5)

    early = evaluate_intraday_t(features(side="buy", direction="bottom"), account(), config, state)
    ready = evaluate_intraday_t(
        features(side="buy", direction="bottom"),
        account(),
        config,
        IntradayTState(
            waiting_second_leg=True,
            first_leg_side="SELL",
            first_leg_intent_id="paper_intent_1",
            bars_since_first_leg=5,
        ),
    )

    assert early.decision == "SECOND_LEG_WATCHING"
    assert early.evidence["signals"]["second_leg_interval_ok"]["matched"] is False
    assert ready.decision == "BUY_THEN_SELL_BACK"
    assert ready.evidence["signals"]["second_leg_interval_ok"]["matched"] is True


def test_second_leg_requires_fresh_event_before_normal_buyback():
    state = IntradayTState(
        waiting_second_leg=True,
        first_leg_side="SELL",
        first_leg_intent_id="paper_intent_1",
        bars_since_first_leg=10,
    )

    decision = evaluate_intraday_t(
        features(side="buy", direction="bottom", bars_since=9),
        account(),
        PaperRiskConfig(event_freshness_bars=5),
        state,
    )

    assert decision.decision == "SECOND_LEG_WATCHING"
    assert decision.evidence["signals"]["fresh_event"]["matched"] is False
    assert decision.evidence["signals"]["buy_first_trigger"]["matched"] is True


def test_second_leg_requires_confirmation_bars_before_normal_buyback():
    state = IntradayTState(
        waiting_second_leg=True,
        first_leg_side="SELL",
        first_leg_intent_id="paper_intent_1",
        bars_since_first_leg=10,
    )
    config = PaperRiskConfig(second_leg_confirmation_bars=2, event_freshness_bars=5)

    early = evaluate_intraday_t(
        features(side="buy", direction="bottom", bars_since=0),
        account(),
        config,
        state,
    )
    confirmed = evaluate_intraday_t(
        features(side="buy", direction="bottom", bars_since=2),
        account(),
        config,
        state,
    )

    assert early.decision == "SECOND_LEG_WATCHING"
    assert early.evidence["signals"]["second_leg_confirmation_ok"]["matched"] is False
    assert early.evidence["signals"]["buy_first_trigger"]["matched"] is True
    assert confirmed.decision == "BUY_THEN_SELL_BACK"
    assert confirmed.evidence["signals"]["second_leg_confirmation_ok"]["matched"] is True


def test_parent_context_blocks_wrong_first_leg_direction():
    parent = IntradayTParentContext(
        parent_level="15",
        parent_task="DOWN_LEG",
        allowed_first_side="SELL",
        max_cycles=1,
    )

    decision = evaluate_intraday_t(
        features(side="buy", direction="bottom", parent_allowed_first_side="BUY"),
        account(),
        PaperRiskConfig(),
        parent=parent,
    )

    assert decision.decision == "NO_TRADE"
    assert decision.reason == "parent_direction_blocked"


def test_parent_context_blocks_used_cycle_budget():
    parent = IntradayTParentContext(
        parent_level="15",
        parent_task="DOWN_LEG",
        allowed_first_side="SELL",
        max_cycles=1,
        used_cycles=1,
    )

    decision = evaluate_intraday_t(features(side="sell", direction="top"), account(), PaperRiskConfig(), parent=parent)

    assert decision.decision == "NO_TRADE"
    assert decision.reason == "parent_cycle_budget_used"


def test_parent_context_blocks_consumed_event():
    consumed = "sh.603893:sell:S1::"
    parent = IntradayTParentContext(
        parent_level="15",
        parent_task="DOWN_LEG",
        allowed_first_side="SELL",
        max_cycles=1,
        consumed_event_keys=frozenset([consumed]),
    )

    decision = evaluate_intraday_t(features(side="sell", direction="top"), account(), PaperRiskConfig(), parent=parent)

    assert decision.decision == "NO_TRADE"
    assert decision.reason == "event_already_consumed"


def test_parent_context_from_features_preserves_budget_inside_same_parent_leg():
    parent = parent_context_from_features(features(), max_cycles=1)
    assert parent is not None
    advanced = IntradayTParentContext(
        parent_level=parent.parent_level,
        parent_task=parent.parent_task,
        parent_leg_id=parent.parent_leg_id,
        allowed_first_side=parent.allowed_first_side,
        max_cycles=parent.max_cycles,
        used_cycles=1,
        consumed_event_keys=frozenset(["evt"]),
    )

    synced = parent_context_from_features(features(), max_cycles=1, previous=advanced)

    assert synced is not None
    assert synced.used_cycles == 1
    assert synced.consumed_event_keys == frozenset(["evt"])
