"""Typed API contract shapes shared by API and engine layers."""

from typing import Any, Dict, List, Optional, TypedDict


class FreshnessContract(TypedDict, total=False):
    source: str
    adjustflag: str
    last_bar_at: str
    checked_at: str
    is_stale: bool
    stale_reason: str
    levels: Dict[str, Dict[str, Any]]


class LevelStructureContract(TypedDict, total=False):
    level: str
    price: float
    state: str
    data_status: str
    bi_count: int
    seg_count: int
    zhongshu_count: int
    zg: float
    zd: float
    active_zhongshu: Dict[str, Any]
    bis: List[Dict[str, Any]]
    segs: List[Dict[str, Any]]
    bi_zhongshus: List[Dict[str, Any]]
    seg_zhongshus: List[Dict[str, Any]]
    bsps: List[Dict[str, Any]]
    patterns: List[str]
    zoushi_type: Dict[str, Any]
    classifications: List[Dict[str, Any]]
    source: Dict[str, Any]


class RiskPlanContract(TypedDict, total=False):
    invalid_if: str
    stop_reference: Dict[str, Any]
    stop_check: Optional[Dict[str, Any]]
    trailing_stop: float


class EntryPlanContract(TypedDict, total=False):
    plan_id: str
    plan_type: str
    status: str
    title: str
    conditions: List[Dict[str, Any]]
    risk: RiskPlanContract
    targets: List[Dict[str, Any]]
    position_sizing: Optional[Dict[str, Any]]
    reward_ratio: Optional[Dict[str, Any]]
    disclaimer: str


class HoldingPlanContract(TypedDict, total=False):
    plan_id: str
    plan_type: str
    status: str
    stage: Any
    conditions: List[Dict[str, Any]]
    risk: RiskPlanContract
    reduce_plan: Optional[Dict[str, Any]]
    exit_plan: Optional[Dict[str, Any]]
    legacy_status: Dict[str, Any]
    legacy_stage_v2: Optional[Dict[str, Any]]
    disclaimer: str


class RadarContract(TypedDict, total=False):
    api_version: str
    symbol: str
    mode: str
    user_id: Optional[int]
    as_of: str
    data_source: Dict[str, Any]
    freshness: FreshnessContract
    structure: Dict[str, Any]
    strategy: Dict[str, Any]
    entry_plan: Optional[EntryPlanContract]
    holding_plan: Optional[HoldingPlanContract]
    plans: List[Dict[str, Any]]
    alerts: List[Dict[str, Any]]
    narrative: Optional[Dict[str, Any]]
    legacy_refs: Dict[str, Any]
    disclaimer: str
