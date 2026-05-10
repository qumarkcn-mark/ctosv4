"""Contracts for CT-OS V4.5 Chan + Kronos AI Fusion inference."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from server.engines.ai_native.schemas import DISCLAIMER, PositionContext


LevelName = Literal["week", "day", "60", "30", "15", "5"]
PathStatus = Literal["CURRENT", "WAITING", "INVALID", "UNKNOWN"]
ActionBias = Literal["WAIT", "OBSERVE", "DEFEND", "REDUCE_RISK", "PLAN_ENTRY", "PLAN_EXIT"]
FusionAction = Literal["EXIT", "REDUCE", "HOLD", "OBSERVE", "TEST", "ADD", "NO_ACTION"]
FusionRecheckTrigger = Literal["NEXT_5M_CLOSE", "NEXT_30M_CLOSE", "NEXT_DAILY_CLOSE", "PRICE_TOUCH", "MANUAL_REFRESH"]
AIChanPathStatus = Literal["CURRENT", "CANDIDATE", "INVALIDATED", "UNKNOWN"]
DataAlignmentStatus = Literal["ALIGNED", "STALE_KRONOS", "STALE_CHAN", "UNKNOWN"]


class ChanKeyLevel(BaseModel):
    label: str
    price: float
    level: LevelName
    role: Literal["support", "resistance", "center_upper", "center_lower", "invalidation", "trigger", "risk"]
    source: str = "chan_structure"


class ChanPathCandidate(BaseModel):
    """缠论只定义结构路径，不在这里决定最终交易动作。"""

    id: str
    name: str
    level: LevelName
    status: PathStatus = "UNKNOWN"
    structure_logic: str
    trigger_condition: str
    invalidation_condition: str
    key_levels: list[ChanKeyLevel] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class ChanAnalysisResult(BaseModel):
    """缠论侧标准输入：定位、结构、完全分类、纪律边界。"""

    version: str = "chan_analysis.v45"
    symbol: str
    generated_at: str = ""
    primary_level: LevelName = "30"
    current_position: str = "UNKNOWN"
    structure_state: str = "UNKNOWN"
    trend_context: str = "UNKNOWN"
    center_state: str = "UNKNOWN"
    buy_sell_candidates: list[dict] = Field(default_factory=list)
    signal_v2: dict = Field(default_factory=dict)
    complete_paths: list[ChanPathCandidate] = Field(default_factory=list)
    key_levels: list[ChanKeyLevel] = Field(default_factory=list)
    discipline_rules: list[str] = Field(default_factory=list)
    fallback_logic: str = "Kronos 或 AI Fusion 不可用时，只保留缠论结构事实与风控边界。"
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


class AIChanPathAction(BaseModel):
    """单个剧本下某种持仓状态的操作指引。"""

    action: str = Field(description="操作动作：试仓/加仓/持有/减仓/止损出局/不动/等待")
    trigger: Optional[str] = Field(default=None, description="触发条件描述")
    price_zone: Optional[list[float]] = Field(default=None, description="操作价格区间 [low, high]")
    stop_price: Optional[float] = Field(default=None, description="止损价")
    stop_basis: Optional[str] = Field(default=None, description="止损依据")
    target_price: Optional[float] = Field(default=None, description="目标价")
    risk_reward: Optional[str] = Field(default=None, description="盈亏比描述，如 1:2.5")


class AIChanPath(BaseModel):
    """第一段 AI 缠论推演中的结构路径——含空仓/持仓两条操作线。"""

    id: str
    name: str
    description: str
    status: AIChanPathStatus = "UNKNOWN"
    entry_condition: str
    invalidation: str
    chan_basis: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    empty_position: Optional[AIChanPathAction] = Field(default=None, description="空仓操作指引")
    holding_position: Optional[AIChanPathAction] = Field(default=None, description="已持仓操作指引")


class AIPathScenario(BaseModel):
    """实时完全分类中的单条路径，从当前价格和最近边界出发。"""

    path_id: int
    current_state: str = Field(description="当前已经发生的结构状态")
    description: str = Field(description="路径描述")
    next_boundary: str = Field(description="下一步最先观察的结构边界")
    trigger_condition: str = Field(description="触发条件")
    target_price: Optional[float] = Field(default=None, description="目标价，震荡/观察路径可为空")
    invalidate_price: Optional[float] = Field(default=None, description="失效价格")
    action: str = Field(description="操作指令")
    requires_confirmation: bool = True
    evidence: list[str] = Field(default_factory=list)


class AIClassificationOutput(BaseModel):
    """AI 自主推导的实时完全分类，路径数量不限。"""

    paths: list[AIPathScenario] = Field(default_factory=list)
    structure_basis: str = ""
    current_signal: str = ""


class TacticalGuide(BaseModel):
    """P4 三段式第二段：实战指引——空间距离 + 即时策略。已弃用，保留向后兼容。"""

    current_price: Optional[float] = None
    defense_price: Optional[float] = None
    space_to_defense_pct: Optional[float] = Field(default=None, description="现价距防守位的百分比")
    immediate_action: str = "观察不动"
    test_zone: Optional[list[float]] = Field(default=None, description="试仓触发区间 [low, high]")
    test_basis: Optional[str] = None
    add_zone: Optional[list[float]] = Field(default=None, description="加仓确认区间 [low, high]")
    add_basis: Optional[str] = None
    stop_anchor: Optional[float] = Field(default=None, description="唯一止损价位")
    stop_basis: Optional[str] = None
    risk_reward_note: Optional[str] = None


class LevelPosition(BaseModel):
    """单个级别的结构定位摘要。"""

    level: str = Field(description="级别名称：day / 30 / 5")
    position: str = Field(description="该级别结构定位描述")
    key_price: Optional[float] = Field(default=None, description="该级别关键价位")
    key_price_label: Optional[str] = Field(default=None, description="关键价位含义，如'中枢下沿'")


class AIChanInference(BaseModel):
    """第一段 AI 缠论推演：结构定位、完全分类、实战指引、纪律和修正。"""

    version: str = "ai_chan_inference.v70"
    symbol: str
    generated_at: str = ""
    current_position: str
    structure_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    level_positions: list[LevelPosition] = Field(default_factory=list, description="多级别定位：日线/30分/5分各一条")
    main_deduction: Optional[str] = Field(default=None, description="主推演（含三级别定位 + 综合判断）")
    synthesis: Optional[str] = Field(default=None, description="多级别综合判断（一句话）")
    tactical_guide: Optional[TacticalGuide] = Field(default=None, description="已弃用，保留向后兼容")
    classification: Optional[AIClassificationOutput] = Field(default=None, description="实时完全分类，路径数量由结构决定")
    primary_path_id: Optional[str] = None
    paths: list[AIChanPath] = Field(default_factory=list, description="基于当前结构的完全分类路径，数量和内容由结构决定，通常2-4条")
    defense_line: Optional[float] = Field(default=None, description="当前最近的结构防守价，随走势动态调整")
    defense_update_rule: Optional[str] = Field(default=None, description="防守位动态更新条件")
    observation_line: Optional[float] = None
    wait_for: list[str] = Field(default_factory=list)
    invalidation: list[str] = Field(default_factory=list)
    discipline: str = "结构不清晰时只观察，不做强动作。"
    corrections: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    source_versions: dict = Field(default_factory=dict)
    fallback_reason: Optional[str] = None
    disclaimer: str = DISCLAIMER

    @field_validator("discipline", "disclaimer")
    @classmethod
    def require_risk_disclaimer(cls, value: str) -> str:
        if "仅供参考" not in value and "投资建议" not in value:
            raise ValueError("AI Chan user-visible output must include risk disclaimer language")
        return value


class KronosForecastPoint(BaseModel):
    step: int
    timestamp: str = ""
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: Optional[float] = None


class EnvelopeBar(BaseModel):
    """父级别单根预测 K 线定义的子级别价格信封。"""
    step: int = Field(description="父级别预测序列中的位置（1-based）")
    timestamp: str = ""
    high: float = Field(description="该周期内子级别价格上限")
    low: float = Field(description="该周期内子级别价格下限")
    open: Optional[float] = None
    close: Optional[float] = None
    direction: str = Field(default="UNKNOWN", description="该单根 K 线方向：UP/DOWN/DOJI")


class KronosRecursiveConstraint(BaseModel):
    """高周期 Kronos 预测对低周期子路径的约束，不等同于原生路径概率。"""

    parent_level: LevelName
    child_level: LevelName
    parent_direction: Literal["UP", "DOWN", "SIDEWAYS", "UNKNOWN"] = "UNKNOWN"
    child_direction: Literal["UP", "DOWN", "SIDEWAYS", "UNKNOWN"] = "UNKNOWN"
    alignment: Literal["ALIGNED", "DIVERGENT", "INSUFFICIENT_DATA"] = "INSUFFICIENT_DATA"
    parent_expected_change_pct: Optional[float] = None
    child_expected_change_pct: Optional[float] = None
    parent_horizon: int = 0
    child_horizon: int = 0
    envelope: list[EnvelopeBar] = Field(
        default_factory=list,
        description="父级别每根预测 K 线的 High/Low 信封——子级别走势不应突破这些边界。",
    )
    constraint_summary: str
    fusion_instruction: str
    evidence: list[str] = Field(default_factory=list)


class KronosForecastResult(BaseModel):
    """Kronos 侧标准输入：预测序列、递归约束和结构时间/价格参考。"""

    version: str = "kronos_forecast.v45"
    symbol: str
    model_name: str = ""
    model_scope: str = "a_share_finetuned"
    generated_at: str = ""
    levels: list[LevelName] = Field(default_factory=lambda: ["day", "30"])
    lookback: int = 0
    horizon: int = 0
    sample_count: int = 0
    forecast_mean: list[KronosForecastPoint] = Field(default_factory=list)
    level_forecasts: dict[str, dict] = Field(
        default_factory=dict,
        description="按 Kronos level 保留的预测序列和派生结构，供 Signal 按信号级别确定性提取。",
    )
    recursive_constraints: list[KronosRecursiveConstraint] = Field(default_factory=list)
    turning_windows: list[dict] = Field(default_factory=list)
    predicted_chan_structure: Optional[dict] = Field(
        default=None,
        description="对预测序列跑缠论分析的结构结果（分型/笔/中枢候选），由 predicted_structure_analyzer 生成。",
    )
    volatility_state: str = "UNKNOWN"
    regime_shift_score: float = Field(default=0.0, ge=0.0, le=1.0)
    signal_validation: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


class DataAlignmentSnapshot(BaseModel):
    """Timestamp contract proving Chan and Kronos consumed comparable slices."""

    status: DataAlignmentStatus = "UNKNOWN"
    chan_generated_at: str = ""
    ai_chan_generated_at: str = ""
    analysis_data_time: str = ""
    kronos_generated_at: str = ""
    primary_data_time: str = ""
    max_delta_minutes: Optional[float] = None
    note: str = ""


class MarketRawFacts(BaseModel):
    """第一层输入：尽量少解释，只陈列市场事实。"""

    symbol: str
    generated_at: str = ""
    current_price: Optional[float] = None
    recent_change_pct: Optional[float] = None
    atr: Optional[float] = None
    volume_status: str = "UNKNOWN"
    distance_to_high: Optional[float] = None
    distance_to_low: Optional[float] = None
    key_price_facts: list[dict] = Field(default_factory=list)
    source: str = "chan_kronos_derived_snapshot"


class ConflictCandidate(BaseModel):
    """第四层输入：只标记可能一致/冲突，不替 AI Fusion 裁决。"""

    code: str
    status: Literal["CONSISTENT", "POTENTIAL_CONFLICT", "NEEDS_AI_JUDGEMENT"]
    chan_fact: str
    kronos_fact: str
    fusion_instruction: str


class FusionInputBundle(BaseModel):
    """给 AI Fusion 的分层输入包。"""

    version: str = "ai_fusion_input.v45"
    raw_facts: MarketRawFacts
    chan_structure: ChanAnalysisResult
    ai_chan_inference: Optional[AIChanInference] = None
    kronos_evidence: KronosForecastResult
    data_alignment: Optional[DataAlignmentSnapshot] = None
    conflict_candidates: list[ConflictCandidate] = Field(default_factory=list)
    position_context: Optional[PositionContext] = None
    fusion_rules: dict = Field(default_factory=dict)
    disclaimer: str = DISCLAIMER


class FusionPathInference(BaseModel):
    id: str
    chan_path_id: str
    rank: int = Field(ge=1)
    name: str
    probability: float = Field(ge=0.0, le=1.0)
    confidence: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    chan_basis: str
    kronos_basis: str
    action_bias: ActionBias = "OBSERVE"
    wait_condition: str
    trigger_condition: str
    invalidation_condition: str
    position_discipline: str
    risk_note: str


class FusionActionPlaybook(BaseModel):
    """单票 Fusion 的条件化动作手册，供 Rebalance/Playbook/CLI 共同消费。"""

    action: FusionAction = "OBSERVE"
    action_label: str = "观察等待确认"
    primary_reason: str = f"只输出条件化动作，不自动交易。{DISCLAIMER}"
    test_conditions: list[str] = Field(default_factory=list)
    add_conditions: list[str] = Field(default_factory=list)
    reduce_conditions: list[str] = Field(default_factory=list)
    exit_conditions: list[str] = Field(default_factory=list)
    hold_conditions: list[str] = Field(default_factory=list)
    max_position_weight_pct: Optional[float] = None
    recheck_trigger: FusionRecheckTrigger = "NEXT_30M_CLOSE"
    risk_note: str = DISCLAIMER

    @field_validator("primary_reason", "risk_note")
    @classmethod
    def require_risk_disclaimer(cls, value: str) -> str:
        if "仅供参考" not in value and "投资建议" not in value:
            raise ValueError("Fusion action playbook must include risk disclaimer language")
        return value


class AIFusionInference(BaseModel):
    """AI Fusion 最终输出：统一推演，不允许绕过结构和概率输入。"""

    version: str = "ai_fusion_inference.v45"
    symbol: str
    generated_at: str = ""
    current_judgement: str
    primary_path_id: Optional[str] = None
    path_inferences: list[FusionPathInference] = Field(default_factory=list)
    coach_message: str
    defense_line: str
    wait_for: list[str] = Field(default_factory=list)
    invalidation: list[str] = Field(default_factory=list)
    action_playbook: FusionActionPlaybook = Field(default_factory=FusionActionPlaybook)
    position_sizing_note: str = "不输出自动交易指令；仓位仅按风险暴露做教练式建议。仅供参考，不构成投资建议。"
    position_context: Optional[PositionContext] = None
    source_versions: dict = Field(default_factory=dict)
    diagnostics: dict = Field(default_factory=dict)
    fallback_reason: Optional[str] = None
    disclaimer: str = DISCLAIMER

    @field_validator("coach_message", "position_sizing_note", "disclaimer")
    @classmethod
    def require_risk_disclaimer(cls, value: str) -> str:
        if "仅供参考" not in value and "投资建议" not in value:
            raise ValueError("AI Fusion user-visible output must include risk disclaimer language")
        return value
