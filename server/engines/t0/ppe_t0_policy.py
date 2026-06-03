"""PPE 到 T0 的确定性策略投影。

这个模块不调用 LLM，只把统一推演已经产出的 watch_state_machine /
PositionPathState 压缩成 T0 状态机可执行的日内许可。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


LONG_ONLY = "LONG_ONLY"
SHORT_ONLY = "SHORT_ONLY"
OBSERVE_ONLY = "OBSERVE_ONLY"


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

    v1 采用保守文本规则：买点确认才允许正T，背驰高抛/压力测试才允许倒T，
    破位/减仓/缺失数据一律观察。
    """
    summary = summary or {}
    position_path = position_path or {}
    machine = _extract_machine(summary)
    if not machine and position_path.get("data_status") != "ready":
        return PPET0Policy(policy_source_run_id=source_run_id)

    text = _policy_text(summary, position_path, machine)
    if not text:
        return PPET0Policy(policy_source_run_id=source_run_id)

    if _has_any(text, _LOCKDOWN_TERMS):
        return PPET0Policy(
            allowed_t0_direction=OBSERVE_ONLY,
            size_multiplier=0.0,
            ppe_stage=5,
            policy_reason="PPE 判定处于减仓/破位阶段，T0 关闭",
            policy_source_run_id=source_run_id,
        )

    if _has_any(text, _SHORT_TERMS):
        return PPET0Policy(
            allowed_t0_direction=SHORT_ONLY,
            size_multiplier=0.5,
            ppe_stage=3,
            policy_reason="PPE 判定处于压力/背驰高抛窗口，仅允许倒T",
            policy_source_run_id=source_run_id,
        )

    if _has_any(text, _LONG_CONFIRM_TERMS):
        return PPET0Policy(
            allowed_t0_direction=LONG_ONLY,
            size_multiplier=1.0,
            ppe_stage=2,
            policy_reason="PPE 判定买点确认/趋势确立，允许正T",
            policy_source_run_id=source_run_id,
        )

    if _has_any(text, _LONG_PROBE_TERMS):
        return PPET0Policy(
            allowed_t0_direction=LONG_ONLY,
            size_multiplier=0.1,
            ppe_stage=0,
            policy_reason="PPE 判定买点验证期，仅允许轻仓正T纸盘",
            policy_source_run_id=source_run_id,
        )

    return PPET0Policy(
        allowed_t0_direction=OBSERVE_ONLY,
        size_multiplier=0.0,
        ppe_stage=5,
        policy_reason="PPE 未给出明确 T0 方向许可，保持观察",
        policy_source_run_id=source_run_id,
    )


_LONG_CONFIRM_TERMS = (
    "三买确认", "第三类买点确认", "二买确认", "一买确认", "买点确认",
    "站稳", "确认增强", "趋势确立", "向上离开确认", "突破确认",
)
_LONG_PROBE_TERMS = (
    "三买尝试", "类三买", "类2买", "类二买", "回踩确认", "承接",
    "底背驰", "底分型", "支撑验证", "验证期",
)
_SHORT_TERMS = (
    "背驰高抛", "利润锁定", "压力测试", "冲高衰竭", "二卖", "三卖",
    "反抽不过", "上沿受阻", "高抛",
)
_LOCKDOWN_TERMS = (
    "清仓", "减仓", "止损", "破位", "防守失效", "锁定", "崩溃",
    "风险扩大", "下跌延续", "空头延伸", "减仓锁利",
)


def _extract_machine(summary: dict[str, Any]) -> dict[str, Any]:
    direct = summary.get("watch_state_machine") if isinstance(summary.get("watch_state_machine"), dict) else {}
    plan = summary.get("watch_plan") if isinstance(summary.get("watch_plan"), dict) else {}
    nested = plan.get("watch_state_machine") if isinstance(plan.get("watch_state_machine"), dict) else {}
    return direct if direct else nested


def _policy_text(summary: dict[str, Any], position_path: dict[str, Any], machine: dict[str, Any]) -> str:
    parts: list[str] = []
    current = machine.get("current_state") if isinstance(machine.get("current_state"), dict) else {}
    for source in (summary, position_path, current):
        if not isinstance(source, dict):
            continue
        for key in (
            "one_liner", "card_summary", "card_secondary", "card_action",
            "major_task", "current_phase", "next_focus", "draft_action",
            "name", "display",
        ):
            value = source.get(key)
            if value:
                parts.append(str(value))
    for item in machine.get("transitions") or []:
        if isinstance(item, dict):
            parts.extend(str(item.get(k) or "") for k in ("next_state", "observe", "success"))
    return " ".join(parts)


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)
