"""PPE 到 T0 的确定性策略投影。

这个模块不调用 LLM，只把统一推演已经产出的 watch_state_machine /
PositionPathState 压缩成 T0 状态机可执行的日内许可。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


LONG_ONLY = "LONG_ONLY"
SHORT_ONLY = "SHORT_ONLY"
BOTH = "BOTH"
OBSERVE_ONLY = "OBSERVE_ONLY"
_VALID_DIRECTIONS = {LONG_ONLY, SHORT_ONLY, BOTH, OBSERVE_ONLY}
_VALID_MULTIPLIERS = {0.0, 0.1, 0.5, 1.0}


@dataclass(frozen=True)
class PPET0Policy:
    allowed_t0_direction: str = OBSERVE_ONLY
    size_multiplier: float = 0.0
    ppe_stage: int = 5
    policy_reason: str = "缺少大周期路径许可"
    policy_source_run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_t0_direction": self.allowed_t0_direction,
            "size_multiplier": self.size_multiplier,
            "ppe_stage": self.ppe_stage,
            "policy_reason": self.policy_reason,
            "policy_source_run_id": self.policy_source_run_id,
        }


def derive_t0_policy_from_ppe(
    *,
    summary: dict[str, Any] | None = None,
    position_path: dict[str, Any] | None = None,
    source_run_id: str = "",
) -> PPET0Policy:
    """从 PPE / 统一推演摘要生成 T0 当日许可。

    只接受 AI Native 已抽取的结构化 T0 字段；缺失或非法时一律观察。
    """
    summary = summary or {}
    direction = str(summary.get("t0_allowed_direction") or "").strip().upper()
    multiplier = _normalize_multiplier(summary.get("t0_size_multiplier"))
    reason = str(summary.get("t0_reason") or "").strip()
    if direction not in _VALID_DIRECTIONS or multiplier not in _VALID_MULTIPLIERS:
        return PPET0Policy(policy_source_run_id=source_run_id)
    if direction == OBSERVE_ONLY or multiplier <= 0:
        return PPET0Policy(
            allowed_t0_direction=OBSERVE_ONLY,
            size_multiplier=0.0,
            ppe_stage=5,
            policy_reason=reason or "结构化 T0 许可为观察",
            policy_source_run_id=source_run_id,
        )
    return PPET0Policy(
        allowed_t0_direction=direction,
        size_multiplier=multiplier,
        ppe_stage=_stage_from_multiplier(direction, multiplier),
        policy_reason=reason or "AI Native 结构化 T0 许可",
        policy_source_run_id=source_run_id,
    )


def _normalize_multiplier(value: Any) -> float:
    try:
        return round(float(value), 4)
    except Exception:
        return -1.0


def _stage_from_multiplier(direction: str, multiplier: float) -> int:
    if multiplier == 0.1:
        return 0
    if multiplier == 0.5:
        return 3
    if multiplier == 1.0:
        return 2 if direction in {LONG_ONLY, BOTH} else 3
    return 5
