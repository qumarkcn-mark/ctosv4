"""AI stop/reduce shadow-training primitives.

本模块只定义 V1 训练闭环的纯合同和可测试逻辑：

Radar/Position + fundamental tag
    -> RebalanceIntent
    -> structured condition evaluator
    -> PaperIntent mapping
    -> Outcome/Process score
    -> sparse lesson memory policy

它不调用 LLM、不读取实时行情、不直接写数据库。这样可以先把训练闭环
的边界跑稳，再接 API / worker / UI。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from server.engines.execution.paper_models import PaperAccount, PaperIntent, PaperPosition


StopReduceAction = Literal["HOLD", "WATCH_EXIT", "REDUCE", "EXIT"]
FundamentalVerdict = Literal["支持", "中性", "回避"]
ConditionStatus = Literal["ACTIVATED", "CANCELLED", "WAITING", "EXPIRED", "DATA_MISSING"]
ConditionOp = Literal["<", "<=", ">", ">=", "=="]

DISCLAIMER = "仅供参考，不构成投资建议"


@dataclass(frozen=True)
class StopReduceCondition:
    condition_id: str
    source: Literal["daily_close"]
    field: Literal["close"]
    op: ConditionOp
    value: float
    valid_on: str = ""


@dataclass(frozen=True)
class StopReduceConditions:
    activate_if: list[StopReduceCondition] = field(default_factory=list)
    cancel_if: list[StopReduceCondition] = field(default_factory=list)
    expires_on: str = ""


@dataclass(frozen=True)
class RebalanceIntent:
    intent_type: Literal["STOP_REDUCE"]
    intent_id: str
    idempotency_key: str
    user_id: int
    symbol: str
    action: StopReduceAction
    current_weight_pct: float
    target_weight_pct: float
    quantity_policy: str
    as_of: str
    conditions: StopReduceConditions = field(default_factory=StopReduceConditions)
    reason: dict[str, Any] = field(default_factory=dict)
    evidence_refs: dict[str, Any] = field(default_factory=dict)
    disclaimer: str = DISCLAIMER


@dataclass(frozen=True)
class StopReduceScore:
    score_id: str
    intent_id: str
    outcome_score: int
    process_score: int
    final_score: int
    settlement_window: str
    settlement_source: str
    settlement_prices: list[dict[str, Any]]
    tags: list[str]
    lesson_candidate: bool
    notes: str


def build_stop_reduce_idempotency_key(
    *,
    user_id: int,
    symbol: str,
    as_of: str,
    technical_run_id: int | str,
    primary_condition_id: str,
) -> str:
    """Build a key granular enough for two same-day signals on the same symbol."""
    as_of_minute = as_of[:16]
    return f"{user_id}:{symbol}:stop_reduce:{as_of_minute}:{technical_run_id}:{primary_condition_id}"


def apply_fundamental_constraint(action: StopReduceAction, verdict: FundamentalVerdict) -> StopReduceAction:
    """Apply V1 hard rules from fundamental_service verdicts."""
    if verdict == "回避" and action == "HOLD":
        return "WATCH_EXIT"
    return action


def validate_rebalance_intent(intent: RebalanceIntent) -> None:
    """Reject unsupported or unsafe V1 intent shapes early."""
    if intent.intent_type != "STOP_REDUCE":
        raise ValueError("intent_type must be STOP_REDUCE")
    if intent.action not in {"HOLD", "WATCH_EXIT", "REDUCE", "EXIT"}:
        raise ValueError("unsupported stop/reduce action")
    if intent.target_weight_pct < 0:
        raise ValueError("target_weight_pct must be non-negative")
    if intent.action in {"REDUCE", "EXIT"} and intent.target_weight_pct >= intent.current_weight_pct:
        raise ValueError("REDUCE/EXIT target must be below current weight")
    if "stop_reduce" not in intent.idempotency_key or intent.as_of[:16] not in intent.idempotency_key:
        raise ValueError("idempotency_key must include stop_reduce and minute-level as_of")


def evaluate_stop_reduce_conditions(
    conditions: StopReduceConditions,
    daily_close: dict[str, Any] | None,
    *,
    today: str = "",
) -> ConditionStatus:
    """Evaluate structured close-bar predicates.

    Cancel is checked before activation. In normal stop/reduce plans the ranges
    do not overlap, but cancel-first keeps ambiguous plans from producing fills.
    """
    if conditions.expires_on and today and today > conditions.expires_on:
        return "EXPIRED"
    if not daily_close:
        return "DATA_MISSING"
    close = _num(daily_close.get("close"))
    if close <= 0:
        return "DATA_MISSING"

    for condition in conditions.cancel_if:
        if _matches_condition(condition, close):
            return "CANCELLED"
    for condition in conditions.activate_if:
        if _matches_condition(condition, close):
            return "ACTIVATED"
    return "WAITING"


def map_stop_reduce_to_paper_intent(
    account: PaperAccount,
    intent: RebalanceIntent,
    *,
    account_value: float,
    created_at: str,
) -> PaperIntent | None:
    """Map a reduce/exit intent into the existing paper execution contract."""
    validate_rebalance_intent(intent)
    if intent.action not in {"REDUCE", "EXIT"}:
        return None
    position = account.positions.get(intent.symbol)
    if position is None:
        return None

    quantity = _sell_quantity(position, intent, account_value)
    if quantity <= 0:
        return None
    return PaperIntent(
        intent_id=f"paper_{intent.intent_id}",
        idempotency_key=intent.idempotency_key,
        user_id=intent.user_id,
        paper_account_id=account.paper_account_id,
        symbol=intent.symbol,
        side="SELL",
        quantity=quantity,
        created_at=created_at,
        strategy_id="ai_stop_reduce_shadow",
        strategy_version="0.1.0",
        price_policy={"source": "NEXT_BAR_OPEN"},
        reason={
            "rebalance_intent_id": intent.intent_id,
            "action": intent.action,
            "target_weight_pct": intent.target_weight_pct,
            "reason": intent.reason,
            "disclaimer": intent.disclaimer,
        },
    )


def score_stop_reduce_outcome(
    intent: RebalanceIntent,
    *,
    action_taken: StopReduceAction,
    entry_price: float,
    settlement_prices: list[dict[str, Any]],
    stop_broken: bool,
    process_violations: list[str] | None = None,
) -> StopReduceScore:
    """Score V1 with only Outcome + Process dimensions."""
    process_violations = process_violations or []
    last_close = _last_close(settlement_prices)
    tags: list[str] = []
    notes = ""

    if last_close <= 0:
        outcome = 50
        tags.append("SETTLEMENT_DATA_MISSING")
        notes = "缺少结算收盘价，不能强行判断对错。"
    elif action_taken in {"REDUCE", "EXIT"}:
        avoided_loss_pct = ((entry_price - last_close) / entry_price * 100) if entry_price > 0 else 0
        if avoided_loss_pct > 0:
            outcome = min(95, 70 + int(round(avoided_loss_pct * 4)))
            tags.append("REDUCE_WAS_CORRECT")
            notes = f"减仓后结算价继续下跌，避免约 {avoided_loss_pct:.2f}% 风险扩大。"
        else:
            outcome = max(35, 65 + int(round(avoided_loss_pct * 4)))
            tags.append("REDUCE_TOO_EARLY")
            notes = "减仓后价格修复，可能偏早。"
    else:
        loss_expanded_pct = ((entry_price - last_close) / entry_price * 100) if entry_price > 0 else 0
        if stop_broken and loss_expanded_pct > 0:
            outcome = max(15, 55 - int(round(loss_expanded_pct * 5)))
            tags.append("AI_HELD_AFTER_STOP_BROKEN")
            notes = f"止损/失效后继续持有，结算期内继续下跌约 {loss_expanded_pct:.2f}%。"
        else:
            outcome = 70
            tags.append("HOLD_ACCEPTABLE")
            notes = "持有未出现明确损失扩大。"

    process = max(0, 100 - 20 * len(process_violations))
    tags.extend(process_violations)
    final = round(outcome * 0.6 + process * 0.4)
    lesson_candidate = should_store_case_memory(
        final_score=final,
        tags=tags,
        loss_delta_pct=((last_close - entry_price) / entry_price * 100) if entry_price > 0 and last_close > 0 else 0,
    )
    return StopReduceScore(
        score_id=f"score:{intent.intent_id}",
        intent_id=intent.intent_id,
        outcome_score=outcome,
        process_score=process,
        final_score=final,
        settlement_window=f"T+{len(settlement_prices)}",
        settlement_source="kline_lake.day",
        settlement_prices=settlement_prices,
        tags=tags,
        lesson_candidate=lesson_candidate,
        notes=notes,
    )


def should_store_case_memory(*, final_score: int, tags: list[str], loss_delta_pct: float) -> bool:
    """Sparse memory policy: store lessons, not normal diary entries."""
    if "REDUCE_WAS_CORRECT" in tags and final_score >= 70:
        return False
    high_value_tags = {
        "AI_HELD_AFTER_STOP_BROKEN",
        "REDUCE_TOO_EARLY",
        "FUNDAMENTAL_AVOID_VIOLATED",
        "AGENT_DISAGREEMENT_HIGH_LOSS",
        "HUMAN_MARKED_LESSON",
    }
    if set(tags) & high_value_tags:
        return True
    if final_score < 60:
        return True
    return loss_delta_pct <= -2.0


def render_calibration_summary(stats: dict[str, Any], latest_case: dict[str, Any] | None = None) -> str:
    """Render a short prompt-safe calibration hint."""
    mistake_count = int(stats.get("mistake_count") or 0)
    total_count = int(stats.get("total_count") or 0)
    avg_loss = _num(stats.get("avg_loss_if_hold_pct"))
    lines = [
        "相似历史错误：",
        f"过去 {total_count} 次同类结构中，{mistake_count} 次被标记为高价值错误，继续持有平均多亏 {abs(avg_loss):.2f}%。",
    ]
    if latest_case:
        outcome = latest_case.get("outcome") or ""
        lesson = latest_case.get("lesson") or ""
        if outcome:
            lines.append(f"最近一次错误：{outcome}")
        if lesson:
            lines.append(f"教训：{lesson}")
    return "\n".join(lines)


def _matches_condition(condition: StopReduceCondition, close: float) -> bool:
    if condition.source != "daily_close" or condition.field != "close":
        return False
    if condition.op == "<":
        return close < condition.value
    if condition.op == "<=":
        return close <= condition.value
    if condition.op == ">":
        return close > condition.value
    if condition.op == ">=":
        return close >= condition.value
    if condition.op == "==":
        return close == condition.value
    return False


def _sell_quantity(position: PaperPosition, intent: RebalanceIntent, account_value: float) -> int:
    if intent.action == "EXIT":
        return position.available_t_qty
    if account_value <= 0 or position.last_price <= 0:
        return 0
    current_value = position.total_qty * position.last_price
    target_value = account_value * intent.target_weight_pct / 100.0
    reduce_value = max(0.0, current_value - target_value)
    raw_quantity = int(reduce_value // position.last_price)
    lot_quantity = (raw_quantity // 100) * 100
    return min(position.available_t_qty, lot_quantity)


def _last_close(settlement_prices: list[dict[str, Any]]) -> float:
    if not settlement_prices:
        return 0.0
    return _num(settlement_prices[-1].get("close"))


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
