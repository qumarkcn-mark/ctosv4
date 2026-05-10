import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.ai_native.fusion_chan_adapter import build_chan_analysis_from_radar_contract
from server.engines.ai_native.fusion_kronos_adapter import build_kronos_forecast_from_service_result


def chan_contract():
    return {
        "symbol": "sh.600519",
        "mode": "EMPTY",
        "structure": {
            "levels": {
                "day": {"level": "day", "price": 12.3, "state": "UPWARD", "zg": 11.9, "zd": 10.8},
                "30": {"level": "30", "price": 12.3, "state": "WAITING", "zg": 11.8, "zd": 11.2},
                "5": {"level": "5", "price": 12.3, "state": "REPAIR", "zg": 12.8, "zd": 11.9},
            }
        },
        "algorithm_v2": {
            "path": "UPWARD_MAJOR_WAVE",
            "phase": "BREAKOUT_EXTENSION",
            "boundaries": {
                "confirm": [{"label": "确认", "value": 12.8, "level": "5"}],
                "maintain": [{"label": "观察", "value": 11.9, "level": "5"}],
                "invalidate": [{"label": "失效", "value": 11.2, "level": "30"}],
            },
        },
    }


def kronos_service_result():
    return {
        "symbol": "sh.600519",
        "background": "Bullish Background",
        "resonance_score": 22.5,
        "resonance_type": "Global Bullish Resonance",
        "levels": {
            "day": {
                "symbol": "sh.600519",
                "interval": "day",
                "change_pct": 2.1,
                "force_score": 21.0,
                "verdict": "Positive Force",
                "last_date": "2026-05-05",
                "forecast_data": [
                    {"date": "2026-05-06", "open": 12.3, "high": 12.7, "low": 12.2, "close": 12.6, "volume": 1000},
                    {"date": "2026-05-07", "open": 12.6, "high": 12.9, "low": 12.5, "close": 12.8, "volume": 1100},
                    {"date": "2026-05-08", "open": 12.8, "high": 12.85, "low": 12.4, "close": 12.5, "volume": 1300},
                ],
            },
            "30": {
                "symbol": "sh.600519",
                "interval": "30",
                "change_pct": 1.4,
                "force_score": 18.0,
                "verdict": "Positive Force",
                "last_date": "2026-05-05 14:30:00",
                "forecast_data": [
                    {"date": "2026-05-05 15:00:00", "open": 12.3, "high": 12.5, "low": 12.2, "close": 12.45, "volume": 500},
                    {"date": "2026-05-06 10:00:00", "open": 12.45, "high": 12.8, "low": 12.4, "close": 12.7, "volume": 700},
                ],
            },
        },
    }


def test_kronos_adapter_passes_raw_forecast_evidence_without_path_probability_proxy():
    chan = build_chan_analysis_from_radar_contract(chan_contract())
    result = build_kronos_forecast_from_service_result(
        kronos_service_result(),
        chan_analysis=chan,
        model_name="local-a-share-kronos",
    )

    assert result.version == "kronos_forecast.v45"
    assert result.symbol == "sh.600519"
    assert result.model_name == "local-a-share-kronos"
    assert result.horizon == 2
    assert result.forecast_mean[0].close == 12.45
    assert result.regime_shift_score > 0
    assert "force_score" not in result.signal_validation
    assert "verdict" not in result.signal_validation
    assert "resonance_type" not in result.signal_validation
    assert not hasattr(result, "path_probabilities")
    assert len(result.recursive_constraints) == 1
    constraint = result.recursive_constraints[0]
    assert constraint.parent_level == "day"
    assert constraint.child_level == "30"
    assert constraint.alignment == "ALIGNED"
    assert constraint.parent_direction == "UP"
    assert constraint.child_direction == "UP"
    assert "不得替代结构触发" in constraint.fusion_instruction
    assert all("force_score" not in item and "verdict" not in item for item in constraint.evidence)


def test_kronos_adapter_marks_recursive_divergence_without_path_probability():
    payload = kronos_service_result()
    payload["levels"]["30"]["change_pct"] = -1.8
    payload["levels"]["30"]["forecast_data"] = [
        {"date": "2026-05-05 15:00:00", "close": 12.7},
        {"date": "2026-05-06 10:00:00", "close": 12.2},
    ]

    result = build_kronos_forecast_from_service_result(payload)

    assert result.recursive_constraints[0].alignment == "DIVERGENT"
    assert result.recursive_constraints[0].parent_direction == "UP"
    assert result.recursive_constraints[0].child_direction == "DOWN"
    assert "时间/价格参考的不确定性" in result.recursive_constraints[0].fusion_instruction


def test_kronos_adapter_ignores_deprecated_path_probabilities_even_with_sample_count():
    payload = kronos_service_result()
    payload["levels"]["30"]["sample_count"] = 120
    payload["levels"]["30"]["path_probabilities"] = [
        {
            "chan_path_id": "A",
            "probability": 0.57,
            "confidence": "HIGH",
            "matching_logic": "120 sampled 30m paths cluster above the Chan trigger.",
            "evidence": ["native_monte_carlo"],
        },
        {
            "chan_path_id": "B",
            "probability": 0.33,
            "matching_logic": "sideways sampled branch.",
        },
    ]

    result = build_kronos_forecast_from_service_result(payload)

    assert result.sample_count == 120
    assert not hasattr(result, "path_probabilities")
    assert "path_probabilities_deprecated_ignored" in result.warnings


def test_kronos_adapter_ignores_deprecated_path_probabilities_without_sample_count():
    payload = kronos_service_result()
    payload["levels"]["30"]["path_probabilities"] = [
        {
            "chan_path_id": "A",
            "probability": 0.57,
            "matching_logic": "missing sample count should not be trusted.",
        },
    ]

    result = build_kronos_forecast_from_service_result(payload)

    assert result.sample_count == 0
    assert not hasattr(result, "path_probabilities")
    assert "path_probabilities_deprecated_ignored" in result.warnings


def test_kronos_adapter_returns_unavailable_result_for_empty_service_output():
    chan = build_chan_analysis_from_radar_contract(chan_contract())
    result = build_kronos_forecast_from_service_result(None, chan_analysis=chan)

    assert result.symbol == "sh.600519"
    assert not hasattr(result, "path_probabilities")
    assert "kronos_unavailable" in result.warnings
