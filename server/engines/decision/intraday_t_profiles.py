"""Parameter profiles for intraday T paper experiments."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from server.engines.execution.paper_models import PaperRiskConfig


INTRADAY_T_PROFILES: dict[str, dict[str, Any]] = {
    "strict": {
        "event_freshness_bars": 5,
        "min_divergence_strength": 0.5,
        "sell_first_min_distance_to_zg_atr": -0.25,
        "buy_first_max_distance_to_zd_atr": 0.25,
        "min_second_leg_bars": 0,
        "buyback_timeout_bars": 30,
    },
    "balanced": {
        "event_freshness_bars": 8,
        "min_divergence_strength": 0.45,
        "sell_first_min_distance_to_zg_atr": -0.5,
        "buy_first_max_distance_to_zd_atr": 0.5,
        "min_second_leg_bars": 0,
        "buyback_timeout_bars": 30,
    },
    "loose": {
        "event_freshness_bars": 12,
        "min_divergence_strength": 0.4,
        "sell_first_min_distance_to_zg_atr": -0.8,
        "buy_first_max_distance_to_zd_atr": 0.8,
        "min_second_leg_bars": 0,
        "buyback_timeout_bars": 45,
    },
    "explore": {
        "event_freshness_bars": 12,
        "min_divergence_strength": 0.4,
        "sell_first_min_distance_to_zg_atr": -0.8,
        "buy_first_max_distance_to_zd_atr": 0.8,
        "min_second_leg_bars": 0,
        "buyback_timeout_bars": 45,
        "min_expected_edge_after_cost": 5.0,
        "expected_edge_atr_multiple": 2.0,
        "first_leg_confirmation_bars": 1,
        "min_bars_before_window_end_for_first_leg": 12,
    },
    "loose_observe": {
        "event_freshness_bars": 20,
        "min_divergence_strength": 0.35,
        "sell_first_min_distance_to_zg_atr": -1.2,
        "buy_first_max_distance_to_zd_atr": 1.2,
        "min_second_leg_bars": 0,
        "buyback_timeout_bars": 60,
        "observe_only": True,
    },
}


def build_intraday_t_risk_config(
    *,
    profile: str = "strict",
    default_t_qty: int = 100,
    protected_base_qty: int = 0,
    overrides: dict[str, Any] | None = None,
) -> PaperRiskConfig:
    """Build risk config from a named experiment profile plus explicit overrides."""
    profile_key = (profile or "strict").strip().lower()
    if profile_key not in INTRADAY_T_PROFILES:
        raise ValueError(f"unknown intraday T profile: {profile}")
    values = dict(INTRADAY_T_PROFILES[profile_key])
    values.update(overrides or {})
    config = PaperRiskConfig(
        profile=profile_key,
        default_t_qty=default_t_qty,
        protected_base_qty=protected_base_qty,
        **values,
    )
    return replace(config, profile=profile_key)


def intraday_t_profile_choices() -> tuple[str, ...]:
    return tuple(INTRADAY_T_PROFILES.keys())
