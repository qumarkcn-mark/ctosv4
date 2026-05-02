"""Contracts for the AI Native Radar commander pipeline."""

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


class CenterSnapshot(BaseModel):
    zg: Optional[float] = None
    zd: Optional[float] = None
    gg: Optional[float] = None
    dd: Optional[float] = None
    source: str = "active_zhongshu"


class StructureSnapshotLevel(BaseModel):
    role: str
    level: str
    price: float = 0.0
    state: str = "UNKNOWN"
    position_state: str = "UNKNOWN"
    center_relation: str = "UNKNOWN"
    price_relation: str = "UNKNOWN"
    center: CenterSnapshot = Field(default_factory=CenterSnapshot)
    counts: dict = Field(default_factory=dict)
    last_bi_dir: str = "unknown"
    patterns: list[str] = Field(default_factory=list)
    buy_sell_points: list[dict] = Field(default_factory=list)
    freshness: dict = Field(default_factory=dict)
    source: dict = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)


class ChartOverlayAlignmentLevel(BaseModel):
    level: str
    display_freq: str
    source_endpoint: str = "/api/chan/detail"
    same_source_as_chart: bool = True
    active_center: CenterSnapshot = Field(default_factory=CenterSnapshot)
    counts: dict = Field(default_factory=dict)
    recent_buy_sell_points: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ChartOverlayAlignment(BaseModel):
    version: str = "chart_alignment.v1"
    status: Literal["ALIGNED", "PARTIAL", "MISSING"] = "MISSING"
    source_endpoint: str = "/api/chan/detail"
    levels: list[ChartOverlayAlignmentLevel] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DivergenceSignal(BaseModel):
    level: str
    type: Literal["BOTTOM", "TOP", "GENERIC"]
    status: Literal["SUSPECTED", "CONFIRMING", "CONFIRMED", "FAILED"] = "SUSPECTED"
    quality: float = 0.0
    evidence: list[str] = Field(default_factory=list)


class DivergenceChainStep(BaseModel):
    role: Literal["macro", "context", "pivot", "trigger", "confirmation"]
    level: str = ""
    direction: Literal["BOTTOM", "TOP", "GENERIC", "NEUTRAL", "UNKNOWN"] = "UNKNOWN"
    status: Literal["SUPPORTS", "BLOCKS", "WAITING", "CONFIRMED", "FAILED"] = "WAITING"
    evidence: list[str] = Field(default_factory=list)
    boundary: Optional[float] = None
    note: str = ""


class BuySellCandidate(BaseModel):
    side: Literal["BUY", "SELL", "NONE"] = "NONE"
    kind: Literal[
        "FIRST_CANDIDATE",
        "SECOND_WAIT",
        "THIRD_CONFIRM",
        "FIRST_SELL_RISK",
        "SECOND_SELL_WAIT",
        "THIRD_SELL_CONFIRM",
        "NONE",
    ] = "NONE"
    status: Literal["SIGNAL_ONLY", "WAITING_CONFIRM", "CONFIRMED", "INVALID", "NONE"] = "NONE"
    level: str = ""
    evidence: list[str] = Field(default_factory=list)
    trigger_boundary: Optional[float] = None
    invalidation_boundary: Optional[float] = None
    note: str = ""


class DivergenceContext(BaseModel):
    macro_bias: str = "UNKNOWN"
    pivot_level: str = "30"
    pivot_position: str = "UNKNOWN"
    chain_direction: Literal["BOTTOM", "TOP", "GENERIC", "UNKNOWN"] = "UNKNOWN"
    chain_status: Literal[
        "NO_CHAIN",
        "LOWER_ONLY",
        "ALIGNING",
        "CONFIRMED",
        "FAILED",
        "COUNTER_RISK",
    ] = "NO_CHAIN"
    alignment: Literal[
        "NO_DIVERGENCE",
        "LOW_LEVEL_ONLY",
        "ALIGNING",
        "CONFIRMED_SUPPORT",
        "FAILED_DIVERGENCE",
        "COUNTER_TREND_RISK",
    ] = "NO_DIVERGENCE"
    lower_level_signals: list[DivergenceSignal] = Field(default_factory=list)
    pivot_signals: list[DivergenceSignal] = Field(default_factory=list)
    chain: list[DivergenceChainStep] = Field(default_factory=list)
    buy_sell_candidate: BuySellCandidate = Field(default_factory=BuySellCandidate)
    upgrade_condition: str = ""
    failure_condition: str = ""


class PositionContext(BaseModel):
    is_holding: bool = False
    state: str = "EMPTY"
    label: str = "空仓"
    cost: Optional[float] = None
    avg_cost: Optional[float] = None
    quantity: Optional[int] = None
    current_price: Optional[float] = None
    pnl_percentage: Optional[float] = None
    position_value: Optional[float] = None
    weight_pct: Optional[float] = None
    risk_flags: list[str] = Field(default_factory=list)
    risk_lines: list[dict] = Field(default_factory=list)
    nearest_risk_line: Optional[dict] = None
    coach_summary: str = ""
    coach_focus: str = ""
    coach_reason: str = ""


class StructureSnapshot(BaseModel):
    version: str = "structure_snapshot.v1"
    symbol: str = ""
    mode: Literal["EMPTY", "HOLDING"] = "EMPTY"
    generated_at: str = ""
    primary_chain: list[str] = Field(default_factory=lambda: ["week", "day", "30", "5"])
    alternative_chain: list[str] = Field(default_factory=lambda: ["week", "day", "60", "15"])
    available_levels: list[str] = Field(default_factory=list)
    levels: list[StructureSnapshotLevel] = Field(default_factory=list)
    key_boundaries: ReasoningBoundaries = Field(default_factory=ReasoningBoundaries)
    chart_alignment: ChartOverlayAlignment = Field(default_factory=ChartOverlayAlignment)
    divergence_context: DivergenceContext = Field(default_factory=DivergenceContext)
    allowed_prices: list[AllowedPrice] = Field(default_factory=list)
    data_health: dict = Field(default_factory=dict)
    source: dict = Field(default_factory=dict)
    consistency_warnings: list[str] = Field(default_factory=list)


class AgentObservation(BaseModel):
    agent_id: Literal[
        "structure_agent",
        "divergence_agent",
        "key_level_agent",
        "path_scorer_agent",
        "coach_agent",
    ]
    verdict: str
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    next_focus: str = ""
    blocks: list[str] = Field(default_factory=list)


class StructureTranscript(BaseModel):
    symbol: str
    mode: Literal["EMPTY", "HOLDING"] = "EMPTY"
    generated_at: str
    fingerprint_version: str
    structure_fingerprint: str
    structure_snapshot: StructureSnapshot = Field(default_factory=StructureSnapshot)
    levels: list[LevelTranscript] = Field(default_factory=list)
    reasoning_boundaries: ReasoningBoundaries = Field(default_factory=ReasoningBoundaries)
    divergence_context: DivergenceContext = Field(default_factory=DivergenceContext)
    agent_observations: list[AgentObservation] = Field(default_factory=list)
    position_context: Optional[PositionContext] = None
    market_context: Optional[str] = None
    reasoning_evidence_pack: dict = Field(default_factory=dict)
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


class PathScore(BaseModel):
    id: Literal["A", "B", "C", "D"]
    name: str
    score: int
    reason: str = ""


class ModelRoute(BaseModel):
    tier: Literal["simple", "hard", "calibration"] = "simple"
    difficulty_score: int = 0
    model_name: str = ""
    thinking_enabled: bool = False
    reasoning_effort: Literal["high", "max"] = "high"
    max_tokens: int = 4096
    timeout_seconds: float = 90.0
    reasons: list[str] = Field(default_factory=list)


class AIReasoningOutput(BaseModel):
    raw_reasoning_md: str = ""
    coach_filtered_md: str
    semantic_filter_status: Literal["PASS", "REWRITE", "FALLBACK"] = "PASS"
    semantic_filter_violations: list[dict] = Field(default_factory=list)
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
    generated_at: str = ""
    raw_reasoning_md: str = ""
    coach_filtered_md: str
    semantic_filter_status: str = "PASS"
    semantic_filter_violations: list[GateViolation] = Field(default_factory=list)
    agent_observations: list[AgentObservation] = Field(default_factory=list)
    key_boundaries: ReasoningBoundaries = Field(default_factory=ReasoningBoundaries)
    position_context: Optional[PositionContext] = None
    model_route: ModelRoute = Field(default_factory=ModelRoute)
    coach_talk: str = ""
    disclaimer: str = DISCLAIMER
    fallback_reason: Optional[str] = None
    fallback_data: Optional[dict] = None
    run_id: Optional[int] = None


class AINativeRunSummary(BaseModel):
    id: int
    user_id: int
    symbol: str
    mode: str
    created_at: str
    prompt_version: str
    model_name: str
    structure_fingerprint: str
    gate_status: str
    gate_score: int
    replay_status: str
    replay_score: Optional[float] = None
    outcome: Optional[dict] = None
    current_hypothesis: str = "UNKNOWN"
    diagnosis: str = ""
    violation_codes: list[str] = Field(default_factory=list)
    model_route: Optional[dict] = None


class ObservationSummary(BaseModel):
    total_runs: int = 0
    reviewed_runs: int = 0
    pending_runs: int = 0
    pass_runs: int = 0
    fallback_runs: int = 0
    average_gate_score: float = 0.0
    average_replay_score: float = 0.0
    pass_rate: float = 0.0
    fallback_rate: float = 0.0
    ready_for_ui_beta: bool = False
    readiness_reason: str = "样本不足"
    target_review_count: int = 20
