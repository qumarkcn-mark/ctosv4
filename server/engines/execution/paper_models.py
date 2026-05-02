"""Paper trading contracts used by the intraday T simulator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


PaperSide = Literal["BUY", "SELL"]
PaperIntentStatus = Literal[
    "PENDING_RISK",
    "REJECTED",
    "APPROVED",
    "FILLED",
    "PARTIALLY_FILLED",
    "NOT_FILLED",
    "DRY_RUN_RECORDED",
]
PaperFillStatus = Literal["FILLED", "PARTIALLY_FILLED", "NOT_FILLED"]


@dataclass(frozen=True)
class PaperFeesConfig:
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 5.0


@dataclass(frozen=True)
class PaperRiskConfig:
    profile: str = "strict"
    max_trades_per_day: int = 6
    max_single_order_amount: float = 100000.0
    max_daily_loss: float = 5000.0
    default_t_qty: int = 100
    protected_base_qty: int = 0
    min_second_leg_bars: int = 0
    buyback_timeout_bars: int = 30
    event_freshness_bars: int = 5
    min_divergence_strength: float = 0.5
    sell_first_min_distance_to_zg_atr: float = -0.25
    buy_first_max_distance_to_zd_atr: float = 0.25
    min_expected_edge_after_cost: float = 0.0
    expected_edge_atr_multiple: float = 2.0
    first_leg_confirmation_bars: int = 0
    second_leg_confirmation_bars: int = 0
    min_bars_before_window_end_for_first_leg: int = 0
    observe_only: bool = False
    fees: PaperFeesConfig = field(default_factory=PaperFeesConfig)


@dataclass(frozen=True)
class PaperPosition:
    symbol: str
    total_qty: int
    available_qty: int
    protected_base_qty: int
    avg_cost: float
    last_price: float = 0.0

    @property
    def available_t_qty(self) -> int:
        return max(0, self.available_qty - self.protected_base_qty)


@dataclass(frozen=True)
class PaperAccount:
    paper_account_id: str
    user_id: int
    cash: float
    positions: dict[str, PaperPosition] = field(default_factory=dict)
    realized_pnl: float = 0.0
    trade_count: int = 0
    used_idempotency_keys: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PaperKline:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    limit_up: bool = False
    limit_down: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PaperKline":
        return cls(
            time=str(data.get("time") or data.get("date") or ""),
            open=float(data.get("open", 0) or 0),
            high=float(data.get("high", 0) or 0),
            low=float(data.get("low", 0) or 0),
            close=float(data.get("close", 0) or 0),
            volume=float(data.get("volume", 0) or 0),
            limit_up=bool(data.get("limit_up", False)),
            limit_down=bool(data.get("limit_down", False)),
        )


@dataclass(frozen=True)
class PaperIntent:
    intent_id: str
    idempotency_key: str
    user_id: int
    paper_account_id: str
    symbol: str
    side: PaperSide
    quantity: int
    created_at: str
    strategy_id: str = "intraday_t_base_position"
    strategy_version: str = "0.1.0"
    status: PaperIntentStatus = "PENDING_RISK"
    dry_run: bool = True
    simulator: bool = True
    price_policy: dict[str, Any] = field(default_factory=lambda: {"source": "NEXT_BAR_OPEN"})
    reason: dict[str, Any] = field(default_factory=dict)
    risk_checks: list[dict[str, Any]] = field(default_factory=list)
    linked_intent_id: str = ""


@dataclass(frozen=True)
class PaperFill:
    fill_id: str
    intent_id: str
    symbol: str
    side: PaperSide
    quantity: int
    fill_price: float
    filled_at: str
    status: PaperFillStatus
    price_source: str = "NEXT_BAR_OPEN"
    commission: float = 0.0
    stamp_tax: float = 0.0
    transfer_fee: float = 0.0
    slippage: float = 0.0
    reason: str = ""

    @property
    def amount(self) -> float:
        return self.fill_price * self.quantity

    @property
    def total_cost(self) -> float:
        return self.commission + self.stamp_tax + self.transfer_fee


@dataclass(frozen=True)
class PaperAuditEvent:
    event_type: str
    intent_id: str
    symbol: str
    occurred_at: str
    status: str
    evidence: dict[str, Any] = field(default_factory=dict)
