from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.signal import build_signal_v2
from server.engines.signal.compiler import calc_strength, compile_signal


def atom(**overrides):
    base = {
        "public_level": "day",
        "level": "day",
        "price": 20.2,
        "position_state": "UP_RETEST",
        "center": {"zd": 18.0, "zg": 20.0, "dd": 17.6, "gg": 20.5},
        "event_sequence": [
            {"code": "B3A", "is_buy": True, "is_current": True, "time": "2026-05-09", "price": 20.1}
        ],
        "buy_events": [],
        "sell_events": [],
        "divergence": {"direction": "", "severity": "", "is_valid": False},
        "momentum_compare": {"area_ratio": 0.35, "is_weaker": True},
    }
    base.update(overrides)
    return base


def test_compile_signal_uses_algorithm_v2_atom_fields():
    parts = compile_signal(atom(), {"path": "PULLBACK_IN_UPTREND"})

    assert parts.code == "d1_zs_above_bs3_strong"


def test_compile_signal_falls_back_to_divergence_and_path():
    div_atom = atom(
        public_level="30",
        position_state="CENTER_INSIDE",
        event_sequence=[],
        divergence={"direction": "top", "severity": "转折确认", "is_valid": True},
        momentum_compare={"area_ratio": 0.72},
    )
    trend_atom = atom(
        public_level="5",
        position_state="DOWN_LEAVING",
        event_sequence=[],
        divergence={},
        momentum_compare={},
    )

    assert compile_signal(div_atom, {}).code == "m30_zs_inside_top_div_medium"
    assert compile_signal(trend_atom, {"path": "DOWNWARD_DEFENSE"}).code == "m5_zs_below_trend_down_weak"


def test_calc_strength_boundaries():
    assert calc_strength(0.49) == "strong"
    assert calc_strength(0.5) == "medium"
    assert calc_strength(0.79) == "medium"
    assert calc_strength(0.8) == "weak"
    assert calc_strength(None) == "weak"


def test_build_signal_v2_returns_primary_context_and_resonance():
    algorithm = {
        "path": "PULLBACK_IN_UPTREND",
        "current_scenario_id": "B",
        "a_state": "A_NOT_TRIGGERED",
        "atoms": {
            "L0": atom(),
            "L1": atom(public_level="30", event_sequence=[], divergence={"direction": "bottom", "is_valid": True}),
            "L2": atom(public_level="5", event_sequence=[], position_state="CENTER_INSIDE"),
        },
        "boundaries": {
            "confirm": [{"level": "day", "field": "ZG", "value": 20.0}],
            "invalidate": [{"level": "day", "field": "ZD", "value": 18.0}],
            "pressure": [{"level": "day", "field": "GG", "value": 24.0}],
        },
        "scenarios": [{"id": "A"}, {"id": "B"}, {"id": "C"}],
        "data_notes": {"is_stale": False, "levels": {}},
    }

    result = build_signal_v2(
        algorithm,
        symbol="sh.600519",
        quote={"price": 20.2},
        position_context={},
        disclaimer="仅供参考，不构成投资建议",
    )

    assert result["state"] == "success"
    assert result["primary"]["code"] == "d1_zs_above_bs3_strong"
    assert result["primary"]["label_plain"] == "日线级别回踩支撑位不破，信号较强"
    assert result["context"]["signal_code"] == result["primary"]["code"]
    assert result["context"]["stop_loss_price"] == 18.0
    assert result["context"]["risk_reward_ratio"] == 2.0
    assert result["context"]["deterministic_scenarios"] == algorithm["scenarios"]
    assert [item["level"] for item in result["resonance"]] == ["m30", "m5"]


def test_build_signal_v2_degrades_when_algorithm_has_no_atoms():
    result = build_signal_v2({}, symbol="sh.600519")

    assert result["state"] == "empty"
    assert result["primary"]["label_plain"] == "结构未给出优势信号，继续观察边界"


def test_build_signal_v2_with_kronos_envelope_does_not_validate_stop_loss_as_buy_point():
    algorithm = {
        "path": "PULLBACK_IN_UPTREND",
        "atoms": {"L0": atom()},
        "boundaries": {
            "confirm": [{"level": "day", "field": "ZG", "value": 20.0}],
            "invalidate": [{"level": "day", "field": "ZD", "value": 18.0}],
            "pressure": [{"level": "day", "field": "GG", "value": 24.0}],
        },
        "scenarios": [],
        "data_notes": {},
    }
    kronos = {
        "recursive_constraints": [
            {
                "parent_level": "day",
                "child_level": "30",
                "alignment": "ALIGNED",
                "envelope": [{"high": 21.0, "low": 19.0, "direction": "UP"}],
            }
        ]
    }

    result = build_signal_v2(
        algorithm,
        symbol="sh.600519",
        quote={"price": 20.2},
        kronos_forecast=kronos,
    )

    envelope = result["context"]["kronos_envelope"]
    assert envelope["envelope_high"] == 21.0
    assert envelope["envelope_low"] == 19.0
    assert envelope["ai_buy_point"] is None
    assert envelope["validation"] == ""


def test_build_signal_v2_with_kronos_extractor_error_still_returns_main_signal(monkeypatch):
    import server.engines.signal.context_builder as context_builder

    def boom(*_args, **_kwargs):
        raise RuntimeError("extractor exploded")

    monkeypatch.setattr(context_builder, "extract_timeline", boom)
    monkeypatch.setattr(context_builder, "extract_envelope", boom)

    algorithm = {
        "path": "PULLBACK_IN_UPTREND",
        "atoms": {"L0": atom()},
        "boundaries": {
            "confirm": [{"level": "day", "field": "ZG", "value": 20.0}],
            "invalidate": [{"level": "day", "field": "ZD", "value": 18.0}],
            "pressure": [{"level": "day", "field": "GG", "value": 24.0}],
        },
        "scenarios": [],
        "data_notes": {},
    }

    result = build_signal_v2(
        algorithm,
        symbol="sh.600519",
        quote={"price": 20.2},
        kronos_forecast={"level_forecasts": {"day": {"predicted_chan_structure": {"fenxings": []}}}},
    )

    assert result["state"] == "success"
    assert result["primary"]["code"] == "d1_zs_above_bs3_strong"
    assert "error" not in result
    assert "kronos_timeline" not in result["context"]
    assert "kronos_envelope" not in result["context"]
