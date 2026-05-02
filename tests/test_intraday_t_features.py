import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.decision.intraday_t_features import extract_intraday_t_features


def test_extract_intraday_t_features_from_radar_output():
    radar = {
        "path": "PULLBACK_IN_UPTREND",
        "level_chain": {"L0": "30", "L1": "5", "L2": "1"},
        "patterns": [{"pattern": "MICRO_CONVERSION_BREAKOUT"}],
        "freshness": {"is_stale": False},
        "atoms": {
            "L1": {
                "price": 10.5,
                "position_state": "UP_RETEST",
                "center": {"zg": 10.0, "zd": 9.0},
            },
            "L0": {
                "public_level": "30",
                "last_bi_dir": "down",
                "last_bi": {
                    "x0": "2026-04-29 09:30:00",
                    "x1": "2026-04-29 10:00:00",
                    "is_up": False,
                },
            },
            "L2": {
                "position_state": "UP_LEAVING",
                "event_sequence": [
                    {"side": "sell", "code": "S1", "time": "2026-04-29 10:02:00", "price": 10.8}
                ],
                "momentum_compare": {
                    "direction": "up",
                    "is_weaker": True,
                    "combined_score": 0.72,
                    "area_ratio": 0.42,
                },
            },
        },
    }
    klines = [
        {"date": "2026-04-29 10:00:00", "high": 10.2, "low": 10.0, "close": 10.1},
        {"date": "2026-04-29 10:01:00", "high": 10.4, "low": 10.1, "close": 10.3},
        {"date": "2026-04-29 10:02:00", "high": 10.8, "low": 10.3, "close": 10.5},
        {"date": "2026-04-29 10:03:00", "high": 10.7, "low": 10.4, "close": 10.6},
    ]

    features = extract_intraday_t_features(
        radar,
        symbol="sh.603893",
        as_of="2026-04-29 10:03:00",
        trigger_klines=klines,
    )

    assert features.paths["main"] == "PULLBACK_IN_UPTREND"
    assert features.paths["L2"] == "UP_LEAVING"
    assert features.pattern_tags == ["MICRO_CONVERSION_BREAKOUT"]
    assert features.latest_event_side == "sell"
    assert features.bars_since_event == 1
    assert features.divergence_direction == "top"
    assert features.divergence_strength == 0.72
    assert features.position_to_center["distance_to_zg_atr"] == 0.5
    assert features.current_price == 10.6
    assert features.volatility["atr"] > 0
    assert features.parent_context["parent_task"] == "DOWN_LEG"
    assert features.parent_context["allowed_first_side"] == "SELL"
    assert features.parent_context["parent_leg_id"] == "sh.603893:30:down:2026-04-29 09:30:00:2026-04-29 10:00:00"


def test_changing_future_bars_does_not_change_signal_features():
    radar = {
        "path": "PULLBACK_IN_UPTREND",
        "atoms": {
            "L2": {
                "event_sequence": [{"side": "buy", "code": "B1", "time": "2026-04-29 10:02:00"}],
                "divergence": {"direction": "bottom", "strength": 0.8, "is_valid": True},
            }
        },
    }
    visible_bars = [
        {"date": "2026-04-29 10:01:00", "high": 10.2, "low": 10.0},
        {"date": "2026-04-29 10:02:00", "high": 10.1, "low": 9.8},
    ]
    future_bars = visible_bars + [
        {"date": "2026-04-29 10:03:00", "high": 99.0, "low": 1.0},
    ]

    at_signal = extract_intraday_t_features(
        radar,
        symbol="sh.603893",
        as_of="2026-04-29 10:02:00",
        trigger_klines=visible_bars,
    )
    with_future = extract_intraday_t_features(
        radar,
        symbol="sh.603893",
        as_of="2026-04-29 10:02:00",
        trigger_klines=visible_bars,
    )

    assert at_signal.latest_event == with_future.latest_event
    assert at_signal.divergence == with_future.divergence
    assert future_bars[-1]["high"] == 99.0


def test_latest_event_prefers_side_matching_divergence_direction():
    radar = {
        "path": "PULLBACK_IN_UPTREND",
        "atoms": {
            "L2": {
                "event_sequence": [
                    {"side": "buy", "code": "B1", "time": "2026-04-29 10:02:00", "price": 9.8},
                    {"side": "sell", "code": "S1", "time": "2026-04-29 10:03:00", "price": 10.6},
                ],
                "divergence": {"direction": "bottom", "strength": 0.8, "is_valid": True},
            }
        },
    }

    features = extract_intraday_t_features(
        radar,
        symbol="sh.603893",
        as_of="2026-04-29 10:04:00",
        trigger_klines=[
            {"date": "2026-04-29 10:02:00", "high": 10.0, "low": 9.8},
            {"date": "2026-04-29 10:03:00", "high": 10.6, "low": 10.1},
            {"date": "2026-04-29 10:04:00", "high": 10.4, "low": 10.0},
        ],
    )

    assert features.divergence_direction == "bottom"
    assert features.latest_event_side == "buy"
    assert features.latest_event["code"] == "B1"
