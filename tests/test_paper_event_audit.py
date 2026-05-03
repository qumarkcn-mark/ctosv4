import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.scripts.paper_event_audit import build_event_audit_from_rows, parse_args


def _klines(symbol, day):
    assert symbol == "sh.603893"
    assert day == "2026-04-24"
    return [
        {"date": "2026-04-24 13:35:00", "open": 178.0, "high": 179.0, "low": 177.5, "close": 178.5, "volume": 1000},
        {"date": "2026-04-24 13:36:00", "open": 178.5, "high": 179.5, "low": 178.0, "close": 179.0, "volume": 1100},
        {"date": "2026-04-24 13:37:00", "open": 179.0, "high": 180.0, "low": 178.8, "close": 179.4, "volume": 1200},
        {"date": "2026-04-24 13:38:00", "open": 179.3, "high": 179.6, "low": 178.0, "close": 178.2, "volume": 1300},
        {"date": "2026-04-24 13:39:00", "open": 178.2, "high": 178.5, "low": 177.8, "close": 178.0, "volume": 1400},
    ]


def test_build_event_audit_marks_signal_and_fill_bars():
    rows = [
        {
            "run_id": "run-1",
            "symbol": "sh.603893",
            "as_of": "2026-04-24 13:37:00",
            "decision": "SELL_THEN_BUY_BACK",
            "reason": "top_divergence_sell_first",
            "intent_id": "intent-1",
            "fill_side": "SELL",
            "filled_at": "2026-04-24 13:38:00",
            "fill_price": 179.3,
            "fill_status": "FILLED",
            "evidence_json": json.dumps(
                {
                    "position_event": {"name": "开第一腿卖出#顶背驰", "side": "SELL"},
                    "latest_event": {"code": "S1", "side": "sell", "bars_since_event": 1},
                    "divergence": {"direction": "top", "strength": 0.72},
                    "paths": {"main": "PULLBACK_IN_UPTREND"},
                    "signals": {"fresh_event": {"matched": True}},
                }
            ),
        }
    ]

    audit = build_event_audit_from_rows(rows, before=1, after=2, kline_loader=_klines)

    assert len(audit) == 1
    item = audit[0]
    assert item["symbol"] == "sh.603893"
    assert item["event"]["name"] == "开第一腿卖出#顶背驰"
    assert item["event"]["latest_code"] == "S1"
    assert item["event"]["divergence_direction"] == "top"
    assert item["fill"]["filled_at"] == "2026-04-24 13:38:00"
    assert item["audit_flags"] == []
    assert [k["date"] for k in item["klines"]] == [
        "2026-04-24 13:36:00",
        "2026-04-24 13:37:00",
        "2026-04-24 13:38:00",
        "2026-04-24 13:39:00",
    ]
    assert item["klines"][1]["is_signal_bar"] is True
    assert item["klines"][2]["is_fill_bar"] is True


def test_build_event_audit_flags_stale_event_and_missing_fill():
    rows = [
        {
            "run_id": "run-1",
            "symbol": "sh.603893",
            "as_of": "2026-04-24 13:37:00",
            "decision": "BUY_TO_SELL_BACK",
            "reason": "bottom_divergence_buy_first",
            "intent_id": "intent-2",
            "fill_side": "",
            "filled_at": "",
            "fill_price": None,
            "fill_status": "",
            "evidence_json": json.dumps(
                {
                    "position_event": {"name": "开第一腿买入#底背驰", "side": "BUY"},
                    "latest_event": {"code": "B1", "side": "buy", "bars_since_event": 9},
                    "divergence": {"direction": "bottom", "strength": 0.55},
                    "signals": {"fresh_event": {"matched": False}},
                }
            ),
        }
    ]

    audit = build_event_audit_from_rows(rows, before=1, after=1, kline_loader=_klines)

    assert audit[0]["audit_flags"] == ["stale_event", "missing_fill"]


def test_parse_args_accepts_kline_window_options():
    args = parse_args(
        [
            "--run-id",
            "run-1",
            "--symbol",
            "sh.603893",
            "--before",
            "4",
            "--after",
            "5",
            "--kline-source",
            "qmt",
            "--adjustflag",
            "3",
        ]
    )

    assert args.run_id == "run-1"
    assert args.symbol == "sh.603893"
    assert args.before == 4
    assert args.after == 5
    assert args.kline_source == "qmt"
    assert args.adjustflag == "3"
