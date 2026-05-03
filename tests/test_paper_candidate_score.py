import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.scripts.paper_candidate_score import parse_args, score_candidate_rows


def candidate_row(side="SELL"):
    latest_side = "sell" if side == "SELL" else "buy"
    return {
        "run_id": "run_1",
        "symbol": "sh.603893",
        "as_of": "2026-04-29 10:30:00",
        "decision": "SELL_THEN_BUY_BACK" if side == "SELL" else "BUY_THEN_SELL_BACK",
        "decision_status": "CANDIDATE_ONLY",
        "reason": "observe_only",
        "evidence_json": json.dumps(
            {
                "symbol": "sh.603893",
                "as_of": "2026-04-29 10:30:00",
                "paths": {"main": "HIGH_VOLATILITY_OSCILLATION"},
                "latest_event": {"side": latest_side, "code": "S1", "bars_since_event": 1},
                "divergence": {"direction": "top" if side == "SELL" else "bottom", "strength": 0.7},
                "position_event": {"name": "候选", "side": side},
            }
        ),
    }


def klines(_symbol, _day):
    return [
        {"date": "2026-04-29 10:29:00", "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.1},
        {"date": "2026-04-29 10:30:00", "open": 10.1, "high": 10.4, "low": 10.0, "close": 10.2},
        {"date": "2026-04-29 10:31:00", "open": 10.0, "high": 10.3, "low": 9.8, "close": 9.9},
        {"date": "2026-04-29 10:32:00", "open": 9.9, "high": 10.1, "low": 9.5, "close": 9.6},
        {"date": "2026-04-29 10:33:00", "open": 9.6, "high": 9.8, "low": 9.4, "close": 9.5},
        {"date": "2026-04-29 10:34:00", "open": 9.5, "high": 10.8, "low": 9.5, "close": 10.7},
    ]


def test_score_sell_first_candidate_uses_future_low_as_favorable_space():
    report = score_candidate_rows(
        [candidate_row("SELL")],
        horizons=(3,),
        min_net_edge=5,
        quantity=100,
        kline_loader=klines,
    )

    item = report["items"][0]
    score = item["scores"]["3"]
    assert item["side"] == "SELL"
    assert item["entry_price"] == 10.0
    assert score["favorable_price"] == 9.4
    assert score["adverse_price"] == 10.3
    assert score["gross_edge"] == 60.0
    assert score["adverse_edge"] == 30.0
    assert score["passed"] is True
    assert report["summary"]["3"]["pass_count"] == 1


def test_score_buy_first_candidate_uses_future_high_as_favorable_space():
    report = score_candidate_rows(
        [candidate_row("BUY")],
        horizons=(5,),
        min_net_edge=5,
        quantity=100,
        kline_loader=klines,
    )

    score = report["items"][0]["scores"]["5"]
    assert score["favorable_price"] == 10.8
    assert score["adverse_price"] == 9.4
    assert score["gross_edge"] == 80.0
    assert score["adverse_edge"] == 60.0
    assert score["passed"] is True


def test_candidate_score_parse_args_accepts_multiple_run_ids():
    args = parse_args(
        [
            "--run-ids",
            "run_a",
            "run_b",
            "--run-label",
            "sample",
            "--horizon",
            "3",
            "8",
            "--min-net-edge",
            "12.5",
        ]
    )

    assert args.run_ids == ["run_a", "run_b"]
    assert args.run_label == "sample"
    assert args.horizon == [3, 8]
    assert args.min_net_edge == 12.5
