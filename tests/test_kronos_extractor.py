"""Unit tests for Kronos deterministic extractor (Phase 1)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.signal.kronos_extractor import (
    extract_envelope,
    extract_timeline,
    _signal_kronos_level,
    _target_fenxing_type,
    _validate_sub_level_action,
)
from server.engines.signal.models import SignalContext


# ── Fixtures ──────────────────────────────────────────────────────────

FORECAST_WITH_FENXINGS = {
    "level_forecasts": {
        "day": {
            "predicted_chan_structure": {
                "fenxings": [
                    {"type": "BOTTOM", "step": 3, "price": 14.5},
                    {"type": "TOP", "step": 7, "price": 16.2},
                ],
                "trend_summary": "day 预测前5根整体上行约2.1%",
            }
        },
        "30": {
            "predicted_chan_structure": {
                "fenxings": [
                    {"type": "BOTTOM", "step": 1, "price": 14.7},
                    {"type": "TOP", "step": 2, "price": 15.2},
                ],
                "trend_summary": "30m 预测前5根整体上行约1.1%",
            }
        },
    }
}

FORECAST_WITH_ENVELOPE = {
    "recursive_constraints": [
        {
            "parent_level": "day",
            "child_level": "30",
            "alignment": "ALIGNED",
            "envelope": [
                {"high": 15.3, "low": 14.8, "direction": "UP"},
                {"high": 15.9, "low": 14.8, "direction": "UP"},
            ],
        }
    ],
    "forecast_mean": [],
}

FORECAST_MEAN_ONLY = {
    "forecast_mean": [
        {"open": 14.5, "high": 15.0, "low": 14.2, "close": 14.8},
        {"open": 14.8, "high": 15.5, "low": 14.6, "close": 15.3},
    ],
}


# ── _target_fenxing_type ──────────────────────────────────────────────

def test_signal_kronos_level_maps_signal_code_prefixes():
    assert _signal_kronos_level("d1_zs_above_bs3_strong") == "day"
    assert _signal_kronos_level("day_zs_above_bs3_strong") == "day"
    assert _signal_kronos_level("m30_zs_above_ss1_strong") == "30"
    assert _signal_kronos_level("m5_zs_inside_bot_div_strong") == "5"
    assert _signal_kronos_level("w_zs_above_ss3_strong") == "week"

def test_target_fenxing_type_buy_signals():
    assert _target_fenxing_type("d1_zs_above_bs3_strong") == "BOTTOM"
    assert _target_fenxing_type("m30_bi5_bs1_weak") == "BOTTOM"
    assert _target_fenxing_type("d1_zs_below_bs2_medium") == "BOTTOM"
    assert _target_fenxing_type("m5_zs_inside_bot_div_strong") == "BOTTOM"
    assert _target_fenxing_type("d1_bi3_pullback_strong") == "BOTTOM"


def test_target_fenxing_type_sell_signals():
    assert _target_fenxing_type("m30_zs_above_top_div_weak") == "TOP"
    assert _target_fenxing_type("d1_zs_above_ss1_strong") == "TOP"
    assert _target_fenxing_type("m60_bi7_ss2_medium") == "TOP"
    assert _target_fenxing_type("w_zs_above_ss3_strong") == "TOP"


def test_target_fenxing_type_no_direction():
    assert _target_fenxing_type("") == ""
    assert _target_fenxing_type("d1_zs_above_trend_up_weak") == ""
    assert _target_fenxing_type("m30_zs_inside_range_osc_weak") == ""


# ── extract_timeline ──────────────────────────────────────────────────

def test_extract_timeline_buy_signal_finds_bottom():
    tl = extract_timeline(
        FORECAST_WITH_FENXINGS,
        signal_code="d1_bi5_bs3_strong",
        current_date="2026-05-09",
    )
    assert tl is not None
    assert tl["estimated_confirmation_bars"] == 3
    assert tl["predicted_fenxing"]["type"] == "BOTTOM"
    assert tl["predicted_fenxing"]["price"] == 14.5
    assert tl["estimated_confirmation_date"] == "2026-05-13"  # 跳过周末
    assert tl["predicted_trend_summary"] == "day 预测前5根整体上行约2.1%"


def test_extract_timeline_m30_signal_uses_30_forecast():
    tl = extract_timeline(
        FORECAST_WITH_FENXINGS,
        signal_code="m30_zs_above_ss1_strong",
    )
    assert tl is not None
    assert tl["predicted_fenxing"]["type"] == "TOP"
    assert tl["estimated_confirmation_bars"] == 2
    assert tl["predicted_trend_summary"] == "30m 预测前5根整体上行约1.1%"


def test_extract_timeline_d1_signal_does_not_use_30m_structure():
    """d1 信号不能用 30m predicted_chan_structure 计算交易日确认。"""
    forecast = {
        "level_forecasts": {
            "30": {
                "predicted_chan_structure": {
                    "fenxings": [{"type": "BOTTOM", "step": 1, "price": 10.1}],
                    "trend_summary": "30m structure only",
                }
            }
        },
        "predicted_chan_structure": {
            "fenxings": [{"type": "BOTTOM", "step": 1, "price": 10.1}],
            "trend_summary": "primary 30m structure",
        },
    }

    assert extract_timeline(forecast, signal_code="d1_zs_above_bs3_strong") is None


def test_extract_timeline_empty_forecast():
    assert extract_timeline({}) is None
    assert extract_timeline({"predicted_chan_structure": {}}) is None
    assert extract_timeline({"predicted_chan_structure": {"fenxings": []}}) is None


def test_extract_timeline_directional_signal_requires_matching_fenxing():
    """明确买卖方向的信号不能用反向分型生成时间线。"""
    forecast = {
        "level_forecasts": {
            "day": {
                "predicted_chan_structure": {
                    "fenxings": [{"type": "TOP", "step": 5, "price": 16.0}],
                }
            }
        }
    }

    assert extract_timeline(forecast, signal_code="d1_bi5_bs3_strong") is None


def test_extract_timeline_no_direction_signal_falls_back():
    """无方向信号可以取第一个分型作为通用时间估计。"""
    forecast = {
        "level_forecasts": {
            "30": {
                "predicted_chan_structure": {
                    "fenxings": [{"type": "TOP", "step": 5, "price": 16.0}],
                }
            }
        }
    }
    tl = extract_timeline(forecast, signal_code="m30_zs_inside_range_osc_weak")
    assert tl is not None
    assert tl["predicted_fenxing"]["type"] == "TOP"
    assert tl["estimated_confirmation_bars"] == 5


# ── extract_envelope ──────────────────────────────────────────────────

def test_extract_envelope_from_recursive_constraints():
    env = extract_envelope(
        FORECAST_WITH_ENVELOPE,
        signal_code="bs3",
        ai_buy_point=14.9,
    )
    assert env is not None
    assert env["envelope_high"] == 15.3
    assert env["envelope_low"] == 14.8
    assert env["target_day"] == "Day1"
    assert env["parent_level"] == "day"
    assert env["child_level"] == "30"
    assert "ALIGNED" in env["validation"]


def test_extract_envelope_day2():
    env = extract_envelope(
        FORECAST_WITH_ENVELOPE,
        target_day=2,
        ai_buy_point=14.9,
    )
    assert env is not None
    assert env["envelope_high"] == 15.9
    assert env["target_day"] == "Day2"


def test_extract_envelope_fallback_to_forecast_mean():
    env = extract_envelope(
        FORECAST_MEAN_ONLY,
        ai_buy_point=14.3,
    )
    assert env is not None
    assert env["envelope_high"] == 15.0
    assert env["envelope_low"] == 14.2
    assert "forecast_mean" in env.get("confidence_note", "")


def test_extract_envelope_without_ai_buy_point_has_bounds_without_validation():
    env = extract_envelope(FORECAST_WITH_ENVELOPE)

    assert env is not None
    assert env["envelope_high"] == 15.3
    assert env["envelope_low"] == 14.8
    assert env["ai_buy_point"] is None
    assert env["validation"] == ""


def test_extract_envelope_empty():
    assert extract_envelope({}) is None
    assert extract_envelope({"recursive_constraints": []}) is None


# ── _validate_sub_level_action ────────────────────────────────────────

def test_validate_below_envelope():
    assert "CONFLICT" in _validate_sub_level_action(14.0, 14.5, 16.0)


def test_validate_above_envelope():
    assert "CONFLICT" in _validate_sub_level_action(17.0, 14.5, 16.0)


def test_validate_near_bottom():
    assert "ALIGNED" in _validate_sub_level_action(14.6, 14.5, 16.0)


def test_validate_near_top():
    assert "WARNING" in _validate_sub_level_action(15.8, 14.5, 16.0)


def test_validate_middle():
    assert "NEUTRAL" in _validate_sub_level_action(15.2, 14.5, 16.0)


def test_validate_zero_envelope():
    assert _validate_sub_level_action(15.0, 0.0, 16.0) == ""
    assert _validate_sub_level_action(15.0, 14.0, 0.0) == ""


# ── SignalContext Kronos fields ───────────────────────────────────────

def test_signal_context_to_dict_omits_none_kronos():
    ctx = SignalContext(
        signal_code="test", signal_id="x", timestamp="now",
        symbol="sh.600519", level="d1",
    )
    d = ctx.to_dict()
    assert "kronos_timeline" not in d
    assert "kronos_envelope" not in d


def test_signal_context_to_dict_preserves_kronos_data():
    ctx = SignalContext(
        signal_code="test", signal_id="x", timestamp="now",
        symbol="sh.600519", level="d1",
        kronos_timeline={"estimated_confirmation_bars": 3},
        kronos_envelope={"envelope_high": 15.0, "envelope_low": 14.2},
    )
    d = ctx.to_dict()
    assert d["kronos_timeline"]["estimated_confirmation_bars"] == 3
    assert d["kronos_envelope"]["envelope_high"] == 15.0


# ── Integration: build_signal_v2 + Kronos ─────────────────────────────

def test_build_signal_v2_with_kronos_forecast():
    from server.engines.signal import build_signal_v2

    algorithm = {
        "atoms": {
            "L0": {
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
                "divergence": {},
                "momentum_compare": {"area_ratio": 0.35},
            }
        },
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
        kronos_forecast=FORECAST_WITH_FENXINGS,
    )

    assert result["state"] == "success"
    ctx = result["context"]
    assert "kronos_timeline" in ctx
    assert ctx["kronos_timeline"]["estimated_confirmation_bars"] == 3


def test_build_signal_v2_without_kronos_no_fields():
    from server.engines.signal import build_signal_v2

    algorithm = {
        "atoms": {
            "L0": {
                "public_level": "day",
                "price": 20.2,
                "position_state": "UP_RETEST",
                "center": {"zd": 18.0, "zg": 20.0, "dd": 17.6, "gg": 20.5},
                "event_sequence": [
                    {"code": "B3A", "is_buy": True, "is_current": True}
                ],
                "buy_events": [],
                "sell_events": [],
                "divergence": {},
                "momentum_compare": {"area_ratio": 0.35},
            }
        },
        "boundaries": {"confirm": [], "invalidate": [], "pressure": []},
        "scenarios": [],
        "data_notes": {},
    }

    result = build_signal_v2(algorithm, symbol="sh.600519")
    ctx = result["context"]
    assert "kronos_timeline" not in ctx
    assert "kronos_envelope" not in ctx
