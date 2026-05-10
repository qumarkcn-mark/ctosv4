"""Semantic Signal V2 data models.

信号层只投影 algorithm_v2 的确定性结构事实，不重新计算缠论结构。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


DISCLAIMER = "仅供参考，不构成投资建议"


@dataclass(frozen=True)
class SignalCode:
    """语义短码的四段式结构。"""

    level: str = "unknown"
    position: str = "unknown"
    pattern: str = "unknown"
    strength: str = "weak"

    @property
    def code(self) -> str:
        return f"{self.level}_{self.position}_{self.pattern}_{self.strength}"


@dataclass(frozen=True)
class SignalContext:
    """一个可展示、可推理、可回溯的信号上下文包。"""

    signal_code: str
    signal_id: str
    timestamp: str
    symbol: str
    level: str
    zhongshu: dict[str, Any] = field(default_factory=dict)
    key_price: float = 0.0
    boundary_state: str = "observe"
    macd_area_ratio: float = 0.0
    volume_ratio: float = 0.0
    atr_stop_distance: float = 0.0
    deterministic_scenarios: list[dict[str, Any]] = field(default_factory=list)
    classification: list[dict[str, Any]] = field(default_factory=list)
    action_rule: str = ""
    risk_reward_ratio: float = 0.0
    stop_loss_price: float = 0.0
    historical_win_rate: float = 0.0
    similar_signals_count: int = 0
    resonance: list[str] = field(default_factory=list)

    # Kronos 扩展字段（Phase 1: 时间线 + 信封）
    kronos_timeline: Optional[dict[str, Any]] = None
    kronos_envelope: Optional[dict[str, Any]] = None

    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        # 清理 None 的 Kronos 字段，避免前端收到无用 null
        if result.get("kronos_timeline") is None:
            del result["kronos_timeline"]
        if result.get("kronos_envelope") is None:
            del result["kronos_envelope"]
        return result
