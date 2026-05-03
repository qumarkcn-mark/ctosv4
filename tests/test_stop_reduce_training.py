import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from server.engines.ai_native.stop_reduce_training import (
    RebalanceIntent,
    StopReduceCondition,
    StopReduceConditions,
    apply_fundamental_constraint,
    build_stop_reduce_idempotency_key,
    evaluate_stop_reduce_conditions,
    map_stop_reduce_to_paper_intent,
    render_calibration_summary,
    score_stop_reduce_outcome,
    should_store_case_memory,
    validate_rebalance_intent,
)
from server.engines.ai_native.stop_reduce_adapter import build_stop_reduce_intent_from_ai_response
from server.engines.ai_native.stop_reduce_feedback import StopReduceFeedback
from server.engines.ai_native.schemas import AIReasoningResponse, AllowedPrice, PositionContext, ReasoningBoundaries
from server.engines.execution.paper_models import PaperAccount, PaperPosition


def account():
    return PaperAccount(
        paper_account_id="paper_1",
        user_id=1,
        cash=100000.0,
        positions={
            "sh.603893": PaperPosition(
                symbol="sh.603893",
                total_qty=1000,
                available_qty=600,
                protected_base_qty=200,
                avg_cost=10.0,
                last_price=12.0,
            )
        },
    )


def intent(action="REDUCE", target=5.0, key=None):
    as_of = "2026-05-02T10:30:44+08:00"
    return RebalanceIntent(
        intent_type="STOP_REDUCE",
        intent_id="stop_reduce:1:sh.603893:2026-05-02T10:30:44+08:00",
        idempotency_key=key
        or build_stop_reduce_idempotency_key(
            user_id=1,
            symbol="sh.603893",
            as_of=as_of,
            technical_run_id=123,
            primary_condition_id="close_below_stop",
        ),
        user_id=1,
        symbol="sh.603893",
        action=action,
        current_weight_pct=12.0,
        target_weight_pct=target,
        quantity_policy="reduce_to_target",
        as_of=as_of,
        conditions=StopReduceConditions(
            activate_if=[
                StopReduceCondition(
                    condition_id="close_below_stop",
                    source="daily_close",
                    field="close",
                    op="<=",
                    value=11.0,
                    valid_on="2026-05-02",
                )
            ],
            cancel_if=[
                StopReduceCondition(
                    condition_id="close_above_repair",
                    source="daily_close",
                    field="close",
                    op=">=",
                    value=13.0,
                    valid_on="2026-05-02",
                )
            ],
        ),
    )


def test_idempotency_key_includes_minute_run_and_condition():
    key = build_stop_reduce_idempotency_key(
        user_id=1,
        symbol="sh.603893",
        as_of="2026-05-02T10:30:44+08:00",
        technical_run_id=123,
        primary_condition_id="close_below_stop",
    )

    assert key == "1:sh.603893:stop_reduce:2026-05-02T10:30:123:close_below_stop"


def test_validate_rejects_reduce_without_minute_level_key():
    bad = intent(key="1:sh.603893:stop_reduce:2026-05-02:close_below_stop")

    with pytest.raises(ValueError, match="minute-level as_of"):
        validate_rebalance_intent(bad)


def test_fundamental_avoid_downgrades_hold_to_watch_exit():
    assert apply_fundamental_constraint("HOLD", "回避") == "WATCH_EXIT"
    assert apply_fundamental_constraint("HOLD", "中性") == "HOLD"
    assert apply_fundamental_constraint("REDUCE", "支持") == "REDUCE"


def test_condition_evaluator_uses_closed_daily_close():
    conditions = intent().conditions

    assert evaluate_stop_reduce_conditions(conditions, {"close": 10.9}) == "ACTIVATED"
    assert evaluate_stop_reduce_conditions(conditions, {"close": 13.1}) == "CANCELLED"
    assert evaluate_stop_reduce_conditions(conditions, {"close": 12.0}) == "WAITING"
    assert evaluate_stop_reduce_conditions(conditions, None) == "DATA_MISSING"


def test_condition_evaluator_expires_before_scoring():
    conditions = StopReduceConditions(expires_on="2026-05-02")

    assert evaluate_stop_reduce_conditions(conditions, {"close": 10.0}, today="2026-05-03") == "EXPIRED"


def test_map_reduce_to_existing_paper_intent_caps_sellable_quantity():
    paper_intent = map_stop_reduce_to_paper_intent(
        account(),
        intent(action="REDUCE", target=6.0),
        account_value=100000.0,
        created_at="2026-05-02T10:31:00+08:00",
    )

    assert paper_intent is not None
    assert paper_intent.side == "SELL"
    assert paper_intent.quantity == 400
    assert paper_intent.strategy_id == "ai_stop_reduce_shadow"
    assert paper_intent.idempotency_key.endswith(":123:close_below_stop")


def test_hold_intent_does_not_create_paper_intent():
    paper_intent = map_stop_reduce_to_paper_intent(
        account(),
        intent(action="HOLD", target=12.0),
        account_value=100000.0,
        created_at="2026-05-02T10:31:00+08:00",
    )

    assert paper_intent is None


def test_score_reduce_persists_settlement_prices_and_marks_lesson():
    score = score_stop_reduce_outcome(
        intent(action="HOLD", target=12.0),
        action_taken="HOLD",
        entry_price=12.0,
        settlement_prices=[
            {"date": "2026-05-04", "close": 11.6},
            {"date": "2026-05-06", "close": 11.2},
            {"date": "2026-05-11", "close": 10.9},
        ],
        stop_broken=True,
    )

    assert score.settlement_source == "kline_lake.day"
    assert score.settlement_prices[-1]["close"] == 10.9
    assert "AI_HELD_AFTER_STOP_BROKEN" in score.tags
    assert score.lesson_candidate is True


def test_case_memory_policy_skips_ordinary_correct_cases():
    assert should_store_case_memory(final_score=82, tags=["REDUCE_WAS_CORRECT"], loss_delta_pct=1.5) is False
    assert should_store_case_memory(final_score=55, tags=["HOLD_ACCEPTABLE"], loss_delta_pct=-1.0) is True
    assert should_store_case_memory(final_score=75, tags=["REDUCE_TOO_EARLY"], loss_delta_pct=1.0) is True


def test_feedback_tightens_near_stop_watch_into_reduce():
    feedback = StopReduceFeedback(
        case_key="holding:loss:structure_breakdown:near_stop",
        total_count=5,
        mistake_count=3,
        latest_mistake_type="AI_HELD_AFTER_STOP_BROKEN",
        action_bias="TIGHTEN_STOP",
        confidence=0.3,
        latest_lesson="跌破防线后不要把可能修复当成持仓理由。",
    )

    generated = build_stop_reduce_intent_from_ai_response(
        user_id=1,
        symbol="sh603893",
        response=ai_response(current_price=11.05, stop_price=11.0),
        as_of="2026-05-02T10:30:00+08:00",
        feedback=feedback,
    )

    assert generated is not None
    assert generated.action == "REDUCE"
    assert generated.reason["memory_feedback"]["action_bias"] == "TIGHTEN_STOP"
    assert generated.evidence_refs["case_memory_feedback"]["latest_mistake_type"] == "AI_HELD_AFTER_STOP_BROKEN"


def test_feedback_reduce_too_early_caps_reduce_intensity_without_overriding_stop():
    feedback = StopReduceFeedback(
        case_key="holding:loss:structure_breakdown:near_stop",
        total_count=4,
        mistake_count=2,
        latest_mistake_type="REDUCE_TOO_EARLY",
        action_bias="WAIT_FOR_CONFIRMATION",
        confidence=0.2,
        latest_lesson="同类结构需要等待日线确认。",
    )

    generated = build_stop_reduce_intent_from_ai_response(
        user_id=1,
        symbol="sh603893",
        response=ai_response(current_price=10.9, stop_price=11.0),
        as_of="2026-05-02T10:30:00+08:00",
        feedback=feedback,
    )

    assert generated is not None
    assert generated.action == "REDUCE"
    assert generated.target_weight_pct == 8.04
    assert generated.reason["memory_feedback"]["action_bias"] == "WAIT_FOR_CONFIRMATION"


def test_render_calibration_summary_is_short_and_specific():
    summary = render_calibration_summary(
        {"total_count": 9, "mistake_count": 7, "avg_loss_if_hold_pct": -3.2},
        {"outcome": "5日继续下跌 6.1%", "lesson": "跌破防线后不要把可能修复当成持仓理由。"},
    )

    assert "过去 9 次" in summary
    assert "7 次" in summary
    assert "教训" in summary


def ai_response(current_price=10.8, stop_price=11.0, run_id=321):
    return AIReasoningResponse(
        gate_status="PASS",
        gate_score=91,
        generated_at="2026-05-02T10:30:00+08:00",
        coach_filtered_md="跌破结构防线后优先减仓观察。仅供参考，不构成投资建议。",
        key_boundaries=ReasoningBoundaries(
            confirm=[AllowedPrice(label="30m repair", value=12.2, source="structure", level="30")]
        ),
        position_context=PositionContext(
            is_holding=True,
            state="LOSS_HOLDING",
            label="亏损持仓",
            avg_cost=12.5,
            quantity=1000,
            current_price=current_price,
            pnl_percentage=-13.6,
            position_value=10800,
            weight_pct=12.0,
            risk_flags=["STRUCTURE_AGAINST_POSITION"],
            risk_lines=[
                {
                    "type": "structure_invalidation",
                    "label": "30m结构失效",
                    "value": stop_price,
                    "side": "below",
                    "distance_pct": 1.8,
                }
            ],
            nearest_risk_line={
                "type": "structure_invalidation",
                "label": "30m结构失效",
                "value": stop_price,
                "side": "below",
                "distance_pct": 1.8,
            },
            coach_summary="近端结构防线被击穿。",
            coach_focus="先处理风险",
        ),
        run_id=run_id,
    )


def test_adapter_builds_reduce_intent_from_ai_native_response():
    result = build_stop_reduce_intent_from_ai_response(
        user_id=1,
        symbol="sh.603893",
        response=ai_response(current_price=10.8, stop_price=11.0),
        as_of="2026-05-02T10:30:00+08:00",
        fundamental_verdict="中性",
    )

    assert result is not None
    assert result.action == "REDUCE"
    assert result.target_weight_pct == 6.0
    assert result.conditions.activate_if[0].value == 11.0
    assert result.conditions.cancel_if[0].value == 12.2
    assert result.evidence_refs["technical_run_id"] == 321
    assert result.idempotency_key.endswith(":321:close_below_structure_invalidation")


def test_adapter_builds_watch_exit_when_near_stop_but_not_broken():
    result = build_stop_reduce_intent_from_ai_response(
        user_id=1,
        symbol="sh.603893",
        response=ai_response(current_price=11.2, stop_price=11.0),
        as_of="2026-05-02T10:30:00+08:00",
        fundamental_verdict="中性",
    )

    assert result is not None
    assert result.action == "WATCH_EXIT"
    assert result.target_weight_pct == result.current_weight_pct


def test_adapter_forces_exit_when_fundamental_avoid_and_stop_broken():
    result = build_stop_reduce_intent_from_ai_response(
        user_id=1,
        symbol="sh.603893",
        response=ai_response(current_price=10.8, stop_price=11.0),
        as_of="2026-05-02T10:30:00+08:00",
        fundamental_verdict="回避",
    )

    assert result is not None
    assert result.action == "EXIT"
    assert result.target_weight_pct == 0.0


def test_adapter_returns_none_for_empty_position():
    response = ai_response()
    response.position_context.is_holding = False

    result = build_stop_reduce_intent_from_ai_response(
        user_id=1,
        symbol="sh.603893",
        response=response,
        as_of="2026-05-02T10:30:00+08:00",
    )

    assert result is None
