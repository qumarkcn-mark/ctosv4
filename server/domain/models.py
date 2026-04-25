"""Shared lightweight domain models.

These dataclasses describe stable CT-OS concepts. They intentionally avoid DB
or API framework dependencies so engines can use them without creating cycles.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from server.domain.enums import PlanStatus, PlanType, RadarMode


@dataclass(frozen=True)
class Kline:
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class Freshness:
    source: str
    last_bar_at: str = ""
    is_stale: bool = False
    stale_reason: str = ""
    adjustflag: str = ""


@dataclass(frozen=True)
class LevelStructure:
    level: str
    price: float = 0.0
    state: str = "UNKNOWN"
    zg: float = 0.0
    zd: float = 0.0
    patterns: List[str] = field(default_factory=list)
    zoushi_type: Dict[str, Any] = field(default_factory=dict)
    active_zhongshu: Dict[str, Any] = field(default_factory=dict)
    bis: List[Dict[str, Any]] = field(default_factory=list)
    segs: List[Dict[str, Any]] = field(default_factory=list)
    bi_zhongshus: List[Dict[str, Any]] = field(default_factory=list)
    bsps: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LevelStructure":
        return cls(
            level=str(data.get("level", "")),
            price=float(data.get("price", 0) or 0),
            state=str(data.get("state", "UNKNOWN")),
            zg=float(data.get("zg", 0) or 0),
            zd=float(data.get("zd", 0) or 0),
            patterns=list(data.get("patterns") or []),
            zoushi_type=dict(data.get("zoushi_type") or {}),
            active_zhongshu=dict(data.get("active_zhongshu") or {}),
            bis=list(data.get("bis") or data.get("detail_bis") or []),
            segs=list(data.get("segs") or []),
            bi_zhongshus=list(data.get("bi_zhongshus") or data.get("zhongshus") or []),
            bsps=list(data.get("bsps") or []),
        )


@dataclass(frozen=True)
class RiskPlan:
    invalid_if: str
    stop_reference: Optional[Dict[str, Any]] = None
    trailing_stop: Optional[float] = None
    stop_check: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class BasePlan:
    plan_id: str
    plan_type: PlanType
    status: PlanStatus
    risk: RiskPlan
    conditions: List[Dict[str, Any]] = field(default_factory=list)
    disclaimer: str = ""


@dataclass(frozen=True)
class EntryPlan(BasePlan):
    targets: List[Dict[str, Any]] = field(default_factory=list)
    position_sizing: Optional[Dict[str, Any]] = None
    reward_ratio: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class HoldingPlan(BasePlan):
    stage: Any = "UNKNOWN"
    reduce_plan: Optional[Dict[str, Any]] = None
    exit_plan: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class RadarContract:
    api_version: str
    symbol: str
    mode: RadarMode
    structure: Dict[str, Any]
    strategy: Dict[str, Any]
    plans: List[Dict[str, Any]]
    freshness: Dict[str, Any]
    entry_plan: Optional[Dict[str, Any]] = None
    holding_plan: Optional[Dict[str, Any]] = None
    disclaimer: str = ""
