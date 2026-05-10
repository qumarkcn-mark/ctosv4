"""Contracts for CT-OS AI Native portfolio rebalance intents."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from server.engines.ai_native.schemas import DISCLAIMER


RebalanceIntentType = Literal[
    "REDUCE_OR_EXIT",
    "HOLD_WITH_DEFENSE",
    "WATCH_REPLACEMENT",
    "TEST_ENTRY",
    "ADD_ON_CONFIRMATION",
    "NO_ACTION",
]

RebalanceUrgency = Literal[
    "IMMEDIATE",
    "NEXT_SESSION",
    "CONDITIONAL_WAIT",
    "WATCH_ONLY",
]

RebalanceAction = Literal[
    "EXIT",
    "REDUCE",
    "HOLD",
    "OBSERVE",
    "TEST",
    "ADD",
    "NO_ACTION",
]

RefreshTrigger = Literal[
    "NEXT_5M_CLOSE",
    "NEXT_30M_CLOSE",
    "NEXT_DAILY_CLOSE",
    "PRICE_TOUCH",
    "MANUAL_REFRESH",
    "POSITION_CHANGE",
]

RiskPosture = Literal["DEFENSIVE", "BALANCED", "OFFENSIVE", "UNKNOWN"]
IntentStatus = Literal["PENDING", "ACKNOWLEDGED", "EXECUTED", "IGNORED", "EXPIRED", "INVALIDATED"]


class PortfolioState(BaseModel):
    total_value: Optional[float] = None
    cash_available: Optional[float] = None
    position_count: int = 0
    max_position_weight_pct: Optional[float] = None
    risk_posture: RiskPosture = "UNKNOWN"
    summary: str = ""


class RebalanceSymbolRef(BaseModel):
    symbol: str
    name: str = ""
    is_holding: bool = False
    quantity: Optional[float] = None
    weight_pct: Optional[float] = None
    avg_cost: Optional[float] = None
    current_price: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None


class RecommendedAction(BaseModel):
    action: RebalanceAction
    action_label: str
    position_delta: str = ""
    max_after_weight_pct: Optional[float] = None
    reason: str
    disclaimer: str = DISCLAIMER

    @field_validator("reason", "disclaimer")
    @classmethod
    def require_risk_disclaimer(cls, value: str) -> str:
        if "仅供参考" not in value and "投资建议" not in value:
            raise ValueError("rebalance action must include risk disclaimer language")
        return value


class RebalanceConditions(BaseModel):
    execute_if: list[str] = Field(default_factory=list)
    delay_if: list[str] = Field(default_factory=list)
    invalidate_if: list[str] = Field(default_factory=list)
    recheck_at: RefreshTrigger = "NEXT_30M_CLOSE"

    @model_validator(mode="after")
    def require_conditioned_action(self) -> "RebalanceConditions":
        # 调仓必须是条件化意图；没有任何条件时容易退化成交易指令。
        if not (self.execute_if or self.delay_if or self.invalidate_if):
            raise ValueError("rebalance intent must include conditional boundaries")
        return self


class RebalanceRisk(BaseModel):
    defense_line: Optional[float] = None
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "UNKNOWN"] = "UNKNOWN"
    failure_mode: str = ""
    disclaimer: str = DISCLAIMER

    @field_validator("failure_mode", "disclaimer")
    @classmethod
    def require_risk_disclaimer(cls, value: str) -> str:
        if "仅供参考" not in value and "投资建议" not in value:
            raise ValueError("rebalance risk must include risk disclaimer language")
        return value


class RebalanceEvidence(BaseModel):
    radar: dict = Field(default_factory=dict)
    kronos: dict = Field(default_factory=dict)
    ai_fusion: dict = Field(default_factory=dict)
    fusion_status: dict = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class RebalanceMemory(BaseModel):
    previous_intent_count: int = 0
    first_seen_at: str = ""
    last_user_response: Optional[str] = None
    urgency_escalated: bool = False


class RebalanceIntent(BaseModel):
    intent_id: str
    intent_type: RebalanceIntentType
    urgency: RebalanceUrgency
    status: IntentStatus = "PENDING"
    source: RebalanceSymbolRef
    target: Optional[RebalanceSymbolRef] = None
    recommended_action: RecommendedAction
    conditions: RebalanceConditions
    risk: RebalanceRisk
    evidence: RebalanceEvidence = Field(default_factory=RebalanceEvidence)
    memory: RebalanceMemory = Field(default_factory=RebalanceMemory)


class RebalanceSummary(BaseModel):
    immediate_count: int = 0
    next_session_count: int = 0
    conditional_wait_count: int = 0
    watch_only_count: int = 0
    capital_policy: str = "释放资金先等待目标确认，不自动迁移到候选。仅供参考，不构成投资建议。"
    coach_message: str = "仅供参考，不构成投资建议。"

    @field_validator("capital_policy", "coach_message")
    @classmethod
    def require_risk_disclaimer(cls, value: str) -> str:
        if "仅供参考" not in value and "投资建议" not in value:
            raise ValueError("rebalance summary must include risk disclaimer language")
        return value


class RebalanceContract(BaseModel):
    contract_version: str = "ai_native.rebalance.v1"
    run_id: str
    user_id: int
    generated_at: str
    valid_until: str
    refresh_trigger: RefreshTrigger
    portfolio_state: PortfolioState
    intents: list[RebalanceIntent] = Field(default_factory=list)
    summary: RebalanceSummary
    disclaimer: str = DISCLAIMER

    @field_validator("generated_at", "valid_until")
    @classmethod
    def require_timestamp(cls, value: str) -> str:
        if not value:
            raise ValueError("rebalance contract requires generated_at and valid_until")
        return value

    @field_validator("disclaimer")
    @classmethod
    def require_risk_disclaimer(cls, value: str) -> str:
        if "仅供参考" not in value and "投资建议" not in value:
            raise ValueError("rebalance contract must include risk disclaimer language")
        return value
