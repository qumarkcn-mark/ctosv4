"""Push rule contract tests."""

import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.decision.push_rules import (
    build_alert_message,
    build_scanner_signal_payload,
    build_alert_strategy_contract,
    evaluate_price_alerts,
    evaluate_scanner_candidate_alert,
)


def test_evaluate_price_alerts_uses_trailing_stop_over_original_stop():
    alerts = evaluate_price_alerts(
        {
            "symbol": "sh600519",
            "stop_loss_price": 95.0,
            "trailing_stop_price": 108.0,
            "m5_entry_zg": 110.0,
        },
        current_price=107.5,
    )

    assert [alert.alert_type for alert in alerts] == ["TRAILING_STOP_BROKEN"]
    assert alerts[0].extra["effective_stop"] == 108.0


def test_evaluate_price_alerts_detects_m5_structure_before_stop():
    alerts = evaluate_price_alerts(
        {
            "symbol": "sh600519",
            "stop_loss_price": 95.0,
            "trailing_stop_price": 0,
            "m5_entry_zg": 110.0,
        },
        current_price=106.0,
    )

    assert [alert.alert_type for alert in alerts] == ["M5_STRUCTURE_BROKEN"]
    assert alerts[0].dedupe_node == "structure:M5_ZG:110.000"


def test_build_alert_message_always_adds_risk_disclaimer():
    message = build_alert_message(
        "M5_STRUCTURE_BROKEN",
        name="贵州茅台",
        current_price=106.0,
        m5_entry_zg=110.0,
    )

    assert "结构失效" in message
    assert "仅供参考" in message


def test_push_strategy_contract_maps_holding_and_scanner_alerts():
    holding = build_alert_strategy_contract("TRAILING_STOP_BROKEN")
    scanner = build_alert_strategy_contract("SCANNER_TOP_CANDIDATE")

    assert holding["strategy_id"] == "holding_stage_manager"
    assert scanner["strategy_id"] == "war1_third_buy"


def test_evaluate_scanner_candidate_alert_requires_ready_high_score():
    assert evaluate_scanner_candidate_alert({"status": "pending", "score": 99}) is None
    assert evaluate_scanner_candidate_alert({"status": "ready", "score": 79}) is None

    alert = evaluate_scanner_candidate_alert(
        {
            "status": "ready",
            "symbol": "sz000001",
            "strategy": "war2",
            "score": 88,
            "close": 12.3,
            "stop_loss": 11.2,
            "rr_ratio": 2.1,
        }
    )
    assert alert.alert_type == "SCANNER_TOP_CANDIDATE"
    assert alert.trigger_price == 12.3
    assert alert.signal_code == "d1_zs_above_breakout_medium"
    assert alert.signal_context["stop_loss_price"] == 11.2


def test_scanner_signal_payload_and_message_use_semantic_language():
    payload = build_scanner_signal_payload(
        {
            "status": "ready",
            "symbol": "sz000001",
            "strategy": "war1",
            "score": 92,
            "close": 12.3,
            "stop_loss": 11.2,
            "rr_ratio": 2.1,
            "chan_desc": "日线三买",
        }
    )
    primary = payload["primary"]

    assert primary["code"] == "d1_zs_above_bs3_strong"
    assert primary["label_plain"] == "日线级别回踩支撑位不破，信号较强"
    assert primary["action"] == "建议买入，止损 11.20"

    message = build_alert_message(
        "SCANNER_TOP_CANDIDATE",
        name="sz000001",
        score=92,
        signal_code=primary["code"],
        signal_label=primary["label_plain"],
        signal_action=primary["action"],
    )

    assert "d1_zs_above_bs3_strong" in message
    assert "建议买入" in message
    assert "仅供参考" in message
