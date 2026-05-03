import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.scripts.paper_decision_report import summarize_decisions


def test_summarize_decisions_counts_reasons_and_feature_distributions():
    rows = [
        {
            "symbol": "sh.603893",
            "decision": "NO_TRADE",
            "reason": "event_not_fresh",
            "evidence_json": json.dumps(
                {
                    "paths": {"main": "HIGH_VOLATILITY_OSCILLATION"},
                    "pattern_tags": ["mid_center_retest"],
                    "latest_event": {"side": "buy", "bars_since_event": 97},
                    "divergence": {"strength": 0.42},
                    "signals": {
                        "fresh_event": {"matched": False, "evidence": {"bars_since_event": 97}},
                        "buy_first_trigger": {"matched": False, "evidence": {}},
                        "has_exit_plan": {"matched": True, "evidence": {}},
                        "first_leg_path_allowed": {"matched": True, "evidence": {}},
                        "parent_allows_buy_first": {"matched": True, "evidence": {}},
                        "buy_first_position_quality": {"matched": True, "evidence": {}},
                    },
                    "position_event": {"name": ""},
                }
            ),
        },
        {
            "symbol": "sz.300724",
            "decision": "NO_TRADE",
            "reason": "weak_divergence",
            "evidence_json": json.dumps(
                {
                    "paths": {"main": "LOW_VOLATILITY_OSCILLATION"},
                    "pattern_tags": ["mid_center_retest", "near_center_top"],
                    "latest_event": {"side": "sell", "bars_since_event": 2},
                    "divergence": {"strength": 0.28},
                    "signals": {
                        "fresh_event": {"matched": True, "evidence": {"bars_since_event": 2}},
                        "sell_first_trigger": {"matched": True, "evidence": {}},
                        "has_exit_plan": {"matched": True, "evidence": {}},
                        "first_leg_path_allowed": {"matched": False, "evidence": {"path": "LOW_VOLATILITY_OSCILLATION"}},
                        "parent_allows_sell_first": {"matched": True, "evidence": {"allowed": "SELL"}},
                        "sell_first_position_quality": {"matched": False, "evidence": {"distance_to_zg_atr": -2.84}},
                        "expected_edge_after_cost": {"matched": False, "evidence": {"net_edge": -14.3}},
                    },
                    "position_event": {"name": "开第一腿卖出#顶背驰"},
                }
            ),
        },
        {
            "symbol": "sz.002176",
            "decision": "NO_TRADE",
            "reason": "event_not_fresh",
            "evidence_json": json.dumps(
                {
                    "paths": {"main": "NO_EDGE"},
                    "pattern_tags": [],
                    "latest_event": {},
                    "divergence": {},
                    "signals": {},
                }
            ),
        },
    ]
    runs = [
        {
            "run_id": "run-1",
            "symbol": "sh.603893",
            "status": "completed",
            "metrics_json": json.dumps(
                {
                    "filled_count": 1,
                    "closed_t_count": 0,
                    "open_t_count": 1,
                    "second_leg_watch_count": 2,
                    "max_open_risk_bars": 1,
                    "gross_t_pnl": 20.0,
                    "spread_t_pnl": 18.0,
                    "total_fees": 10.0,
                    "slippage_cost": 2.0,
                    "net_t_pnl": 8.0,
                    "realized_pnl": 0.0,
                }
            ),
        }
    ]

    report = summarize_decisions(rows, runs)

    assert report["run_count"] == 1
    assert report["decision_count"] == 3
    assert report["decision_counts"] == {"NO_TRADE": 3}
    assert report["reason_counts"] == {"event_not_fresh": 2, "weak_divergence": 1}
    assert report["blocker_counts"] == {
        "fresh_event": 1,
        "first_leg_path_allowed": 1,
        "sell_first_position_quality": 1,
        "expected_edge_after_cost": 1,
        "no_actionable_event": 1,
    }
    assert report["position_event_counts"] == {"开第一腿卖出#顶背驰": 1}
    assert report["event_side_counts"] == {"buy": 1, "sell": 1}
    assert report["path_counts"] == {
        "HIGH_VOLATILITY_OSCILLATION": 1,
        "LOW_VOLATILITY_OSCILLATION": 1,
        "NO_EDGE": 1,
    }
    assert report["pattern_counts"] == {"mid_center_retest": 2, "near_center_top": 1}
    assert report["bars_since_event"] == {"count": 2, "min": 2, "median": 49.5, "max": 97, "avg": 49.5}
    assert report["divergence_strength"] == {
        "count": 2,
        "min": 0.28,
        "median": 0.35,
        "max": 0.42,
        "avg": 0.35,
    }
    assert report["runs"][0]["filled_count"] == 1
    assert report["symbols"][0]["open_t_count"] == 1
    assert report["symbols"][0]["max_open_risk_bars"] == 1
    assert report["symbols"][0]["gross_t_pnl"] == 20.0
    assert report["symbols"][0]["total_fees"] == 10.0
    assert report["symbols"][0]["slippage_cost"] == 2.0
    assert report["symbols"][0]["net_t_pnl"] == 8.0
    assert report["symbols"][0]["symbol"] == "sh.603893"
    assert report["symbols"][1]["top_blockers"] == {
        "no_actionable_event": 1,
    }
    assert report["symbols"][2]["top_blockers"] == {
        "first_leg_path_allowed": 1,
        "sell_first_position_quality": 1,
        "expected_edge_after_cost": 1,
    }
