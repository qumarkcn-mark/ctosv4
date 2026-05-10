import pytest
from pydantic import ValidationError

from server.engines.ai_native.rebalance_engine import build_rebalance_contract
from server.engines.ai_native.rebalance_schemas import (
    PortfolioState,
    RebalanceConditions,
    RebalanceContract,
    RebalanceIntent,
    RebalanceRisk,
    RebalanceSymbolRef,
    RebalanceSummary,
    RecommendedAction,
)


def test_rebalance_contract_requires_time_boundary_and_disclaimer():
    contract = RebalanceContract(
        run_id="rebalance_test",
        user_id=1,
        generated_at="2026-05-05T15:00:00+08:00",
        valid_until="2026-05-06T09:30:00+08:00",
        refresh_trigger="NEXT_30M_CLOSE",
        portfolio_state=PortfolioState(
            position_count=1,
            risk_posture="DEFENSIVE",
            summary="当前先防守。仅供参考，不构成投资建议",
        ),
        intents=[],
        summary=RebalanceSummary(
            coach_message="先处理高风险仓位。仅供参考，不构成投资建议",
        ),
    )

    assert contract.contract_version == "ai_native.rebalance.v1"
    assert contract.valid_until == "2026-05-06T09:30:00+08:00"


def test_rebalance_contract_rejects_missing_visible_risk_disclaimer():
    with pytest.raises(ValidationError, match="risk disclaimer"):
        RecommendedAction(
            action="REDUCE",
            action_label="降低风险暴露",
            reason="结构偏弱，建议减仓",
        )


def test_rebalance_intent_must_be_conditioned():
    with pytest.raises(ValidationError, match="conditional boundaries"):
        RebalanceIntent(
            intent_id="rb_sz002138",
            intent_type="REDUCE_OR_EXIT",
            urgency="IMMEDIATE",
            source=RebalanceSymbolRef(symbol="sz002138", is_holding=True),
            recommended_action=RecommendedAction(
                action="EXIT",
                action_label="退出",
                reason="结构失效。仅供参考，不构成投资建议",
            ),
            conditions=RebalanceConditions(),
            risk=RebalanceRisk(
                risk_level="HIGH",
                failure_mode="继续持有可能扩大回撤。仅供参考，不构成投资建议",
            ),
        )


def test_rebalance_engine_escalates_c_path_holding_reduce_to_immediate():
    contract = build_rebalance_contract(
        [
            {
                "symbol": "sz002138",
                "name": "顺络电子",
                "is_holding": True,
                "quantity": 3000,
                "weight_pct": 2.62,
                "avg_cost": 35.164,
                "current_price": 33.95,
                "radar": {"risk_level": "HIGH", "defense_line": 34.94},
                "ai_fusion": {
                    "primary_path": "C",
                    "action": "HOLD",
                    "action_playbook": {
                        "action": "REDUCE",
                        "action_label": "降低风险暴露",
                        "primary_reason": "结构修复失败，优先降低风险暴露。仅供参考，不构成投资建议",
                        "reduce_conditions": ["不能重新站回 34.94"],
                        "exit_conditions": ["继续跌破 33.50"],
                        "max_position_weight_pct": 1.31,
                        "recheck_trigger": "NEXT_5M_CLOSE",
                        "risk_note": "继续持有可能扩大回撤。仅供参考，不构成投资建议",
                    },
                    "current_judgement": "结构修复失败，优先降低风险暴露",
                    "wait_for": ["不能重新站回 34.94"],
                    "invalidation": ["重新站回 35.47 并修复 30 分钟结构"],
                },
                "memory": {"previous_intent_count": 2, "last_user_response": "IGNORED"},
            }
        ],
        user_id=1,
        generated_at="2026-05-05T15:00:00+08:00",
        valid_until="2026-05-06T09:30:00+08:00",
        run_id="rebalance_test",
    )

    intent = contract.intents[0]
    assert intent.intent_type == "REDUCE_OR_EXIT"
    assert intent.urgency == "IMMEDIATE"
    assert intent.recommended_action.action == "REDUCE"
    assert intent.recommended_action.max_after_weight_pct == 1.31
    assert intent.conditions.execute_if == ["不能重新站回 34.94"]
    assert intent.conditions.invalidate_if == ["重新站回 35.47 并修复 30 分钟结构"]
    assert intent.conditions.recheck_at == "NEXT_5M_CLOSE"
    assert intent.memory.previous_intent_count == 2
    assert intent.evidence.fusion_status["state"] == "AI_READY"
    assert contract.summary.immediate_count == 1


def test_rebalance_engine_carries_fusion_fallback_status():
    contract = build_rebalance_contract(
        [
            {
                "symbol": "sh600406",
                "name": "国电南瑞",
                "is_holding": True,
                "radar": {"risk_level": "MEDIUM"},
                "ai_fusion": {
                    "primary_path_id": "fallback-B",
                    "fallback_reason": "AI Fusion 推演超时 45s",
                    "action_playbook": {
                        "action": "HOLD",
                        "action_label": "持有但守防线",
                        "primary_reason": "AI Fusion 不可用，回到结构事实。仅供参考，不构成投资建议",
                    },
                },
            }
        ],
        user_id=1,
        generated_at="2026-05-05T15:00:00+08:00",
        valid_until="2026-05-06T09:30:00+08:00",
        run_id="rebalance_test",
    )

    status = contract.intents[0].evidence.fusion_status
    assert status["state"] == "FALLBACK"
    assert status["fallback_reason"] == "AI Fusion 推演超时 45s"
    assert status["primary_path_id"] == "fallback-B"
    assert contract.intents[0].intent_type == "NO_ACTION"
    assert contract.intents[0].urgency == "WATCH_ONLY"
    assert contract.intents[0].recommended_action.action == "NO_ACTION"
    assert contract.intents[0].recommended_action.position_delta == "NO_POSITION_CHANGE"
    assert contract.intents[0].conditions.execute_if == []
    assert contract.intents[0].conditions.invalidate_if == [
        "重新生成 AI Fusion 并得到 AI_READY 状态后，再评估是否导入调仓动作。"
    ]
    assert contract.summary.immediate_count == 0
    assert contract.summary.watch_only_count == 1


def test_rebalance_engine_escalates_repeated_unresolved_reduce_from_memory():
    contract = build_rebalance_contract(
        [
            {
                "symbol": "sz002138",
                "name": "顺络电子",
                "is_holding": True,
                "weight_pct": 6.0,
                "radar": {"risk_level": "MEDIUM", "defense_line": 34.94},
                "ai_fusion": {
                    "primary_path": "B",
                    "action_playbook": {
                        "action": "REDUCE",
                        "action_label": "降低风险暴露",
                        "primary_reason": "中枢震荡反复走弱。仅供参考，不构成投资建议",
                        "reduce_conditions": ["不能重新站回 34.94"],
                    },
                },
                "memory": {"previous_intent_count": 2, "last_user_response": "CONTINUE_WATCHING"},
            }
        ],
        user_id=1,
        generated_at="2026-05-05T15:00:00+08:00",
        valid_until="2026-05-06T09:30:00+08:00",
        run_id="rebalance_test",
    )

    intent = contract.intents[0]
    assert intent.urgency == "IMMEDIATE"
    assert intent.memory.urgency_escalated is True
    assert "紧急度上调" in intent.evidence.notes[-1]


def test_rebalance_engine_does_not_escalate_after_executed_response():
    contract = build_rebalance_contract(
        [
            {
                "symbol": "sz002138",
                "is_holding": True,
                "radar": {"risk_level": "MEDIUM"},
                "ai_fusion": {
                    "primary_path": "B",
                    "action_playbook": {
                        "action": "REDUCE",
                        "action_label": "降低风险暴露",
                        "primary_reason": "风险暴露偏高。仅供参考，不构成投资建议",
                    },
                },
                "memory": {"previous_intent_count": 3, "last_user_response": "EXECUTED"},
            }
        ],
        user_id=1,
        generated_at="2026-05-05T15:00:00+08:00",
        valid_until="2026-05-06T09:30:00+08:00",
        run_id="rebalance_test",
    )

    intent = contract.intents[0]
    assert intent.urgency == "NEXT_SESSION"
    assert intent.memory.urgency_escalated is False


def test_rebalance_engine_keeps_empty_candidate_as_watch_replacement():
    contract = build_rebalance_contract(
        [
            {
                "symbol": "sz000988",
                "name": "华工科技",
                "is_holding": False,
                "current_price": 119.53,
                "radar": {"risk_level": "MEDIUM"},
                "ai_fusion": {
                    "primary_path": "B",
                    "action": "OBSERVE",
                    "current_judgement": "中枢震荡，等待确认",
                    "wait_for": ["站回 120.82 后再复核"],
                },
            }
        ],
        user_id=1,
        generated_at="2026-05-05T15:00:00+08:00",
        valid_until="2026-05-06T09:30:00+08:00",
        run_id="rebalance_test",
    )

    intent = contract.intents[0]
    assert intent.intent_type == "WATCH_REPLACEMENT"
    assert intent.urgency == "WATCH_ONLY"
    assert intent.recommended_action.action == "OBSERVE"
    assert intent.target is None
