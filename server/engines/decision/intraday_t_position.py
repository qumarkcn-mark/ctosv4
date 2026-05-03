"""CZSC-style signal / event layer for the intraday T simulator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from server.engines.decision.intraday_t_features import IntradayTFeatures
from server.engines.execution.paper_models import PaperAccount, PaperRiskConfig


FIRST_LEG_ALLOWED_PATHS = {
    "DOWNWARD_DEFENSE",
    "HIGH_VOLATILITY_OSCILLATION",
    "PULLBACK_IN_UPTREND",
    "UPWARD_MAJOR_WAVE",
}


@dataclass(frozen=True)
class IntradayTSignal:
    key: str
    matched: bool
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IntradayTEvent:
    name: str
    decision: str
    reason: str
    condition_id: str = ""
    side: str = ""
    signals_all: tuple[str, ...] = ()
    signals_any: tuple[str, ...] = ()
    signals_not: tuple[str, ...] = ()

    def is_match(self, signals: dict[str, IntradayTSignal]) -> bool:
        if self.signals_not and any(_matched(signals, key) for key in self.signals_not):
            return False
        if self.signals_all and not all(_matched(signals, key) for key in self.signals_all):
            return False
        if self.signals_any and not any(_matched(signals, key) for key in self.signals_any):
            return False
        return bool(self.signals_all or self.signals_any)


@dataclass(frozen=True)
class IntradayTPositionPlan:
    name: str
    opens: tuple[IntradayTEvent, ...]
    exits: tuple[IntradayTEvent, ...]
    timeout_bars: int
    stop_loss_bps: int
    interval_seconds: int
    max_cycles: int
    exit_plan: dict[str, Any] = field(default_factory=dict)

    @property
    def events(self) -> tuple[IntradayTEvent, ...]:
        return self.exits + self.opens

    def select_event(self, signals: dict[str, IntradayTSignal]) -> IntradayTEvent | None:
        for event in self.events:
            if event.is_match(signals):
                return event
        return None


def build_intraday_t_position_plan(
    features: IntradayTFeatures,
    config: PaperRiskConfig,
    *,
    max_cycles: int = 1,
    stop_loss_bps: int = 80,
    interval_seconds: int = 0,
) -> IntradayTPositionPlan:
    """Build a complete one-cycle T plan before any first-leg action."""
    timeout_bars = max(0, int(config.buyback_timeout_bars))
    return IntradayTPositionPlan(
        name="intraday_t_base_position",
        opens=(
            IntradayTEvent(
                name="开第一腿卖出#顶背驰",
                decision="SELL_THEN_BUY_BACK",
                reason="top_divergence_sell_first",
                condition_id="first_leg_sell_top_divergence",
                side="SELL",
                signals_all=(
                    "flat_cycle",
                    "fresh_event",
                    "sell_first_trigger",
                    "has_exit_plan",
                    "first_leg_path_allowed",
                    "parent_allows_sell_first",
                    "sell_first_position_quality",
                    "expected_edge_after_cost",
                ),
            ),
            IntradayTEvent(
                name="开第一腿买入#底背驰",
                decision="BUY_THEN_SELL_BACK",
                reason="bottom_divergence_buy_first",
                condition_id="first_leg_buy_bottom_divergence",
                side="BUY",
                signals_all=(
                    "flat_cycle",
                    "fresh_event",
                    "buy_first_trigger",
                    "has_exit_plan",
                    "first_leg_path_allowed",
                    "parent_allows_buy_first",
                    "buy_first_position_quality",
                    "expected_edge_after_cost",
                ),
            ),
        ),
        exits=(
            IntradayTEvent(
                name="第二腿买回#底背驰",
                decision="BUY_THEN_SELL_BACK",
                reason="buyback_triggered",
                condition_id="second_leg_buyback_bottom_divergence",
                side="BUY",
                signals_all=(
                    "waiting_second_leg",
                    "first_leg_sell",
                    "second_leg_interval_ok",
                    "second_leg_confirmation_ok",
                    "fresh_event",
                    "buy_first_trigger",
                ),
            ),
            IntradayTEvent(
                name="第二腿卖回#顶背驰",
                decision="SELL_THEN_BUY_BACK",
                reason="sellback_triggered",
                condition_id="second_leg_sellback_top_divergence",
                side="SELL",
                signals_all=(
                    "waiting_second_leg",
                    "first_leg_buy",
                    "second_leg_interval_ok",
                    "second_leg_confirmation_ok",
                    "fresh_event",
                    "sell_first_trigger",
                ),
            ),
            IntradayTEvent(
                name="第二腿超时强制买回",
                decision="BUYBACK_TIMEOUT",
                reason="second_leg_timeout_force_close",
                condition_id="second_leg_timeout_force_close",
                side="BUY",
                signals_all=("waiting_second_leg", "first_leg_sell", "second_leg_timeout"),
            ),
            IntradayTEvent(
                name="第二腿超时强制卖回",
                decision="BUYBACK_TIMEOUT",
                reason="second_leg_timeout_force_close",
                condition_id="second_leg_timeout_force_close",
                side="SELL",
                signals_all=("waiting_second_leg", "first_leg_buy", "second_leg_timeout"),
            ),
        ),
        timeout_bars=timeout_bars,
        stop_loss_bps=stop_loss_bps,
        interval_seconds=interval_seconds,
        max_cycles=max_cycles,
        exit_plan={
            "style": "one_cycle_intraday_t",
            "symbol": features.symbol,
            "timeout_bars": timeout_bars,
            "stop_loss_bps": stop_loss_bps,
            "force_close": True,
            "normal_sell_first_exit": "bottom_divergence_buyback",
            "normal_buy_first_exit": "top_divergence_sellback",
            "fallback_exit": "next_bar_open_after_timeout",
        },
    )


def build_intraday_t_signals(
    features: IntradayTFeatures,
    account: PaperAccount,
    config: PaperRiskConfig,
    state: Any,
    plan: IntradayTPositionPlan,
) -> dict[str, IntradayTSignal]:
    position = account.positions.get(features.symbol)
    waiting = bool(getattr(state, "waiting_second_leg", False))
    first_side = str(getattr(state, "first_leg_side", "") or "")
    bars_since_first_leg = int(getattr(state, "bars_since_first_leg", 0) or 0)
    min_second_leg_bars = max(0, int(getattr(config, "min_second_leg_bars", 0) or 0))
    second_leg_interval_ok = (not waiting) or bars_since_first_leg >= min_second_leg_bars
    second_leg_confirmation_bars = max(0, int(getattr(config, "second_leg_confirmation_bars", 0) or 0))
    second_leg_confirmation_ok = (not waiting) or features.bars_since_event >= second_leg_confirmation_bars
    first_leg_confirmation = _first_leg_confirmation(features, config)
    fresh_event = features.bars_since_event <= config.event_freshness_bars
    min_divergence_strength = float(getattr(config, "min_divergence_strength", 0.5))
    sell_position_threshold = float(getattr(config, "sell_first_min_distance_to_zg_atr", -0.25))
    buy_position_threshold = float(getattr(config, "buy_first_max_distance_to_zd_atr", 0.25))
    sell_trigger = (
        features.latest_event_side == "sell"
        and features.divergence_direction == "top"
        and features.divergence_strength >= min_divergence_strength
    )
    buy_trigger = (
        features.latest_event_side == "buy"
        and features.divergence_direction == "bottom"
        and features.divergence_strength >= min_divergence_strength
    )
    parent_allowed_side = str((features.parent_context or {}).get("allowed_first_side") or "").upper()
    main_path = str(features.paths.get("main") or "")
    distance_to_zg = _float(features.position_to_center.get("distance_to_zg_atr"))
    distance_to_zd = _float(features.position_to_center.get("distance_to_zd_atr"))
    edge = _expected_edge_after_cost(features, config)
    signals = {
        "flat_cycle": IntradayTSignal("flat_cycle", not waiting),
        "waiting_second_leg": IntradayTSignal("waiting_second_leg", waiting),
        "first_leg_sell": IntradayTSignal("first_leg_sell", first_side == "SELL"),
        "first_leg_buy": IntradayTSignal("first_leg_buy", first_side == "BUY"),
        "fresh_event": IntradayTSignal(
            "fresh_event",
            fresh_event,
            {"bars_since_event": features.bars_since_event, "max_bars": config.event_freshness_bars},
        ),
        "sell_first_trigger": IntradayTSignal(
            "sell_first_trigger",
            sell_trigger,
            {
                "side": features.latest_event_side,
                "divergence": features.divergence,
                "min_divergence_strength": min_divergence_strength,
            },
        ),
        "buy_first_trigger": IntradayTSignal(
            "buy_first_trigger",
            buy_trigger,
            {
                "side": features.latest_event_side,
                "divergence": features.divergence,
                "min_divergence_strength": min_divergence_strength,
            },
        ),
        "second_leg_timeout": IntradayTSignal(
            "second_leg_timeout",
            waiting and bars_since_first_leg >= plan.timeout_bars,
            {"bars_since_first_leg": bars_since_first_leg, "timeout_bars": plan.timeout_bars},
        ),
        "second_leg_interval_ok": IntradayTSignal(
            "second_leg_interval_ok",
            second_leg_interval_ok,
            {"bars_since_first_leg": bars_since_first_leg, "min_second_leg_bars": min_second_leg_bars},
        ),
        "second_leg_confirmation_ok": IntradayTSignal(
            "second_leg_confirmation_ok",
            second_leg_confirmation_ok,
            {
                "bars_since_event": features.bars_since_event,
                "confirmation_bars": second_leg_confirmation_bars,
                "meaning": "normal second leg waits for the divergence event to survive confirmation bars",
            },
        ),
        "has_exit_plan": IntradayTSignal(
            "has_exit_plan",
            bool(plan.exit_plan.get("force_close") and plan.timeout_bars >= 0),
            plan.exit_plan,
        ),
        "first_leg_path_allowed": IntradayTSignal(
            "first_leg_path_allowed",
            main_path in FIRST_LEG_ALLOWED_PATHS,
            {"path": main_path, "allowed_paths": sorted(FIRST_LEG_ALLOWED_PATHS)},
        ),
        "parent_allows_sell_first": IntradayTSignal(
            "parent_allows_sell_first",
            parent_allowed_side in {"", "SELL"},
            {"allowed_first_side": parent_allowed_side},
        ),
        "parent_allows_buy_first": IntradayTSignal(
            "parent_allows_buy_first",
            parent_allowed_side in {"", "BUY"},
            {"allowed_first_side": parent_allowed_side},
        ),
        "sell_first_position_quality": IntradayTSignal(
            "sell_first_position_quality",
            distance_to_zg >= sell_position_threshold,
            {
                "distance_to_zg_atr": distance_to_zg,
                "threshold": sell_position_threshold,
                "meaning": "sell-first requires price near or above structure ZG",
            },
        ),
        "buy_first_position_quality": IntradayTSignal(
            "buy_first_position_quality",
            distance_to_zd <= buy_position_threshold,
            {
                "distance_to_zd_atr": distance_to_zd,
                "threshold": buy_position_threshold,
                "meaning": "buy-first requires price near or below structure ZD",
            },
        ),
        "expected_edge_after_cost": IntradayTSignal(
            "expected_edge_after_cost",
            edge["matched"],
            edge,
        ),
        "first_leg_confirmation_ok": IntradayTSignal(
            "first_leg_confirmation_ok",
            first_leg_confirmation["matched"],
            first_leg_confirmation,
        ),
        "has_base_position": IntradayTSignal(
            "has_base_position",
            bool(position and position.protected_base_qty > 0),
            {"protected_base_qty": position.protected_base_qty if position else 0},
        ),
    }
    return signals


def signal_evidence(signals: dict[str, IntradayTSignal]) -> dict[str, Any]:
    return {key: {"matched": signal.matched, "evidence": signal.evidence} for key, signal in signals.items()}


def _matched(signals: dict[str, IntradayTSignal], key: str) -> bool:
    signal = signals.get(key)
    return bool(signal and signal.matched)


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _expected_edge_after_cost(features: IntradayTFeatures, config: PaperRiskConfig) -> dict[str, Any]:
    threshold = max(0.0, float(getattr(config, "min_expected_edge_after_cost", 0.0) or 0.0))
    current = _current_price(features)
    zg = _float(features.position_to_center.get("zg"))
    zd = _float(features.position_to_center.get("zd"))
    quantity = max(0, int(getattr(config, "default_t_qty", 0) or 0))
    side = "SELL" if features.divergence_direction == "top" else "BUY" if features.divergence_direction == "bottom" else ""
    target = zd if side == "SELL" else zg if side == "BUY" else 0.0
    structure_edge = max(0.0, abs(current - target) * quantity) if current > 0 and target > 0 else 0.0
    atr_edge = _atr_edge(features, config, quantity)
    gross_edge = min(structure_edge, atr_edge) if atr_edge > 0 else structure_edge
    estimated_cost = _round4(_estimated_round_trip_cost(current, target, quantity, side, config))
    net_edge = _round4(gross_edge - estimated_cost)
    enabled = threshold > 0
    return {
        "matched": True if not enabled else net_edge >= threshold,
        "enabled": enabled,
        "side": side,
        "current_price": _round4(current),
        "target_price": _round4(target),
        "quantity": quantity,
        "structure_edge": _round4(structure_edge),
        "atr_edge": _round4(atr_edge),
        "gross_edge": _round4(gross_edge),
        "estimated_cost": estimated_cost,
        "net_edge": net_edge,
        "threshold": threshold,
        "atr_multiple": float(getattr(config, "expected_edge_atr_multiple", 2.0) or 0.0),
        "meaning": "first leg requires conservative expected edge to cover estimated fees and slippage",
    }


def _first_leg_confirmation(features: IntradayTFeatures, config: PaperRiskConfig) -> dict[str, Any]:
    min_bars = max(0, int(getattr(config, "first_leg_confirmation_bars", 0) or 0))
    event_price = _event_price(features)
    current_price = _current_price(features)
    side = "SELL" if features.divergence_direction == "top" else "BUY" if features.divergence_direction == "bottom" else ""
    if side == "SELL":
        price_confirmed = current_price < event_price if current_price > 0 and event_price > 0 else False
    elif side == "BUY":
        price_confirmed = current_price > event_price if current_price > 0 and event_price > 0 else False
    else:
        price_confirmed = False
    enabled = min_bars > 0
    matched = True if not enabled else features.bars_since_event >= min_bars and price_confirmed
    return {
        "matched": matched,
        "enabled": enabled,
        "side": side,
        "bars_since_event": features.bars_since_event,
        "confirmation_bars": min_bars,
        "event_price": _round4(event_price),
        "current_price": _round4(current_price),
        "price_confirmed": price_confirmed,
        "meaning": "first leg waits for candidate divergence to survive and move away from the event price",
    }


def _event_price(features: IntradayTFeatures) -> float:
    return _float(features.latest_event.get("price"))


def _current_price(features: IntradayTFeatures) -> float:
    current_price = _float(getattr(features, "current_price", 0.0))
    if current_price > 0:
        return current_price
    price = _float(features.position_to_center.get("price"))
    if price > 0:
        return price
    event_price = _event_price(features)
    if event_price > 0:
        return event_price
    zg = _float(features.position_to_center.get("zg"))
    zd = _float(features.position_to_center.get("zd"))
    if zg > 0 and zd > 0:
        width = max(zg - zd, 0.0)
        distance_to_zd = _float(features.position_to_center.get("distance_to_zd_atr"))
        if width > 0:
            return zd + distance_to_zd * width
    return 0.0


def _estimated_round_trip_cost(
    current: float,
    target: float,
    quantity: int,
    first_side: str,
    config: PaperRiskConfig,
) -> float:
    if current <= 0 or target <= 0 or quantity <= 0 or first_side not in {"BUY", "SELL"}:
        return 0.0
    fees = config.fees
    buy_price = target if first_side == "SELL" else current
    sell_price = current if first_side == "SELL" else target
    buy_amount = buy_price * quantity
    sell_amount = sell_price * quantity
    commission = max(fees.min_commission, buy_amount * fees.commission_rate)
    commission += max(fees.min_commission, sell_amount * fees.commission_rate)
    stamp_tax = sell_amount * fees.stamp_tax_rate
    transfer_fee = (buy_amount + sell_amount) * fees.transfer_fee_rate
    slippage = (buy_amount + sell_amount) * fees.slippage_bps / 10000
    return commission + stamp_tax + transfer_fee + slippage


def _atr_edge(features: IntradayTFeatures, config: PaperRiskConfig, quantity: int) -> float:
    atr = _float(features.volatility.get("atr"))
    multiple = max(0.0, float(getattr(config, "expected_edge_atr_multiple", 2.0) or 0.0))
    if atr <= 0 or multiple <= 0 or quantity <= 0:
        return 0.0
    return atr * multiple * quantity


def _round4(value: float) -> float:
    return round(float(value or 0.0), 4)
