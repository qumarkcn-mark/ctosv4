import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.ai_native.model_router import choose_model_route
from server.engines.ai_native.transcript_compiler import compile_structure_transcript


def radar_contract():
    return {
        "symbol": "sh.600519",
        "mode": "EMPTY",
        "freshness": {"is_stale": False},
        "structure": {
            "levels": {
                "day": {
                    "level": "day",
                    "price": 12.3,
                    "state": "IN_CENTER_OSC",
                    "zg": 13.5,
                    "zd": 10.8,
                },
                "30": {
                    "level": "30",
                    "price": 12.3,
                    "state": "IN_CENTER_OSC",
                    "zg": 12.8,
                    "zd": 11.2,
                    "dd": 10.8,
                },
                "5": {
                    "level": "5",
                    "price": 12.3,
                    "state": "IN_CENTER_OSC",
                    "zg": 12.8,
                    "zd": 11.9,
                },
            }
        },
        "algorithm_v2": {
            "path": "OSCILLATION",
            "phase": "IN_CENTER",
            "current_scenario_id": "B",
            "boundaries": {
                "confirm": [{"label": "确认", "value": 12.8}],
                "maintain": [{"label": "观察", "value": 11.9}],
                "invalidate": [{"label": "失效", "value": 10.8}],
            },
        },
    }


def test_model_router_uses_pro_no_thinking_for_simple_structure():
    transcript = compile_structure_transcript(radar_contract())

    route = choose_model_route(transcript)

    assert route.tier == "simple"
    assert route.model_name == "deepseek-v4-pro"
    assert route.thinking_enabled is False


def test_model_router_upgrades_low_level_candidate_to_pro_high():
    contract = radar_contract()
    contract["structure"]["levels"]["30"]["price"] = 10.6
    contract["structure"]["levels"]["5"]["bsps"] = [
        {"type": "2", "is_buy": True, "price": 10.62},
    ]

    transcript = compile_structure_transcript(contract)
    route = choose_model_route(transcript)

    assert route.tier == "hard"
    assert route.model_name == "deepseek-v4-pro"
    assert route.thinking_enabled is True
    assert route.reasoning_effort == "high"
    assert route.difficulty_score >= 40
    assert any("买卖点候选" in reason for reason in route.reasons)


def test_model_router_calibration_forces_pro_max():
    transcript = compile_structure_transcript(radar_contract())

    route = choose_model_route(transcript, calibration=True)

    assert route.tier == "calibration"
    assert route.model_name == "deepseek-v4-pro"
    assert route.thinking_enabled is True
    assert route.reasoning_effort == "max"
    assert route.difficulty_score == 100


def test_model_router_upgrades_holding_near_risk_line_to_pro_high():
    contract = radar_contract()
    contract["mode"] = "HOLDING"
    contract["position_context"] = {
        "is_holding": True,
        "avg_cost": 10.0,
        "current_price": 9.85,
        "pnl_pct": -6.0,
        "risk_flags": ["STRUCTURE_AGAINST_POSITION"],
    }
    contract["coach_action"] = {
        "risk_lines": [{"label": "风控边界", "price": 9.7, "distance_pct": -1.52}],
        "nearest_risk_line": {"label": "风控边界", "price": 9.7, "distance_pct": -1.52},
    }

    transcript = compile_structure_transcript(contract)
    route = choose_model_route(transcript)

    assert route.tier == "hard"
    assert route.thinking_enabled is True
    assert any("持仓" in reason for reason in route.reasons)
