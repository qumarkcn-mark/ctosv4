"""Contracts for the AI Native Radar shadow pipeline."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


DISCLAIMER = "仅供参考，不构成投资建议"


class AllowedPrice(BaseModel):
    label: str
    value: float
    source: str
    level: Optional[str] = None


class LevelTranscript(BaseModel):
    role: Literal["L0", "L1", "L2", "L3"]
    level: str
    price: float = 0.0
    path: str = "UNKNOWN"
    phase: str = "UNKNOWN"
    raw_state: str = "UNKNOWN"
    position_state: str = "UNKNOWN"
    summary: str = ""
    center_zg: Optional[float] = None
    center_zd: Optional[float] = None
    evidence: list[str] = Field(default_factory=list)


class ReasoningBoundaries(BaseModel):
    confirm: list[AllowedPrice] = Field(default_factory=list)
    observe: list[AllowedPrice] = Field(default_factory=list)
    invalidate: list[AllowedPrice] = Field(default_factory=list)
    support: list[AllowedPrice] = Field(default_factory=list)


class PositionContext(BaseModel):
    is_holding: bool = False
    cost: Optional[float] = None
    quantity: Optional[int] = None
    pnl_percentage: Optional[float] = None


class StructureTranscript(BaseModel):
    symbol: str
    mode: Literal["EMPTY", "HOLDING"] = "EMPTY"
    generated_at: str
    fingerprint_version: str
    structure_fingerprint: str
    levels: list[LevelTranscript] = Field(default_factory=list)
    reasoning_boundaries: ReasoningBoundaries = Field(default_factory=ReasoningBoundaries)
    position_context: Optional[PositionContext] = None
    market_context: Optional[str] = None
    allowed_prices: list[AllowedPrice] = Field(default_factory=list)
    stale: bool = False
    disclaimer: str = DISCLAIMER


class SimilarCaseSummary(BaseModel):
    similar_case_count: int = 0
    common_outcomes: list[dict] = Field(default_factory=list)
    common_failure_reasons: list[str] = Field(default_factory=list)


class Hypothesis(BaseModel):
    id: Literal["A", "B", "C", "D"]
    name: str
    current_applicability: Literal["CURRENT", "WAITING", "INVALID", "UNKNOWN"] = "UNKNOWN"
    evidence: list[str] = Field(default_factory=list)
    trigger: str
    invalidation: str
    next_focus: str
    empty_position_view: str
    holding_position_view: str


class AIReasoningOutput(BaseModel):
    diagnosis: str
    current_hypothesis: Literal["A", "B", "C", "D", "UNKNOWN"] = "UNKNOWN"
    reasoning_boundary: str
    hypotheses: list[Hypothesis]
    operator_mistake: str
    coach_talk: str
    disclaimer: str = DISCLAIMER


class GateViolation(BaseModel):
    code: str
    message: str
    severity: Literal["REWRITE", "FALLBACK"]
    evidence: list[str] = Field(default_factory=list)


class GateResult(BaseModel):
    status: Literal["PASS", "REWRITE", "FALLBACK"]
    score: int
    violations: list[GateViolation] = Field(default_factory=list)


class AIReasoningResponse(BaseModel):
    gate_status: Literal["PASS", "REWRITE", "FALLBACK"]
    gate_score: int
    diagnosis: str
    current_hypothesis: str
    reasoning_boundary: str
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    coach_talk: str
    disclaimer: str = DISCLAIMER
    fallback_reason: Optional[str] = None
    fallback_data: Optional[dict] = None
    run_id: Optional[int] = None

