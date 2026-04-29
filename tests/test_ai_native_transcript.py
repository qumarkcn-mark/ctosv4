import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
                    "state": "UPWARD_LEAVING",
                    "zg": 11.9,
                    "zd": 10.8,
                    "patterns": ["底背驰"],
                },
                "30": {
                    "level": "30",
                    "price": 12.3,
                    "state": "WAITING_FOR_PULLBACK",
                    "zg": 11.8,
                    "zd": 11.2,
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
            "path": "UPWARD_MAJOR_WAVE",
            "phase": "BREAKOUT_EXTENSION",
            "current_scenario_id": "B",
            "boundaries": {
                "confirm": [{"label": "历史前高", "value": 12.8}],
                "maintain": [{"label": "观察区间下沿", "value": 11.9}],
                "invalidate": [{"label": "短线失效", "value": 11.9}],
                "support": [{"label": "大级别防线", "value": 10.8}],
            },
        },
    }


def test_compile_structure_transcript_collects_allowed_prices_and_fingerprint():
    transcript = compile_structure_transcript(radar_contract())

    assert transcript.symbol == "sh.600519"
    assert transcript.mode == "EMPTY"
    assert transcript.fingerprint_version == "fingerprint.v1"
    assert "UPWARD_MAJOR_WAVE" in transcript.structure_fingerprint
    assert {level.role for level in transcript.levels} == {"L0", "L1", "L2"}

    prices = {round(item.value, 2) for item in transcript.allowed_prices}
    assert {12.3, 12.8, 11.9, 10.8}.issubset(prices)
    assert transcript.reasoning_boundaries.confirm[0].value == 12.8


def test_compile_structure_transcript_handles_stale_and_missing_structure():
    contract = {
        "symbol": "sz.000001",
        "mode": "HOLDING",
        "freshness": {"is_stale": True},
        "structure": {"levels": {}},
        "position_context": {"is_holding": True, "cost": 10.0, "pnl_percentage": -12.0},
    }

    transcript = compile_structure_transcript(contract)

    assert transcript.stale is True
    assert transcript.mode == "HOLDING"
    assert transcript.position_context
    assert transcript.position_context.cost == 10.0
    assert all(level.raw_state == "UNKNOWN" for level in transcript.levels)

