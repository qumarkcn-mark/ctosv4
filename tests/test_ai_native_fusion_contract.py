import pytest

from server.engines.ai_native.fusion_schemas import (
    AIFusionInference,
    ChanAnalysisResult,
    ChanKeyLevel,
    ChanPathCandidate,
    FusionPathInference,
    KronosForecastResult,
    KronosRecursiveConstraint,
)


def test_fusion_contract_accepts_chan_and_kronos_inputs():
    key_level = ChanKeyLevel(
        label="30分钟中枢上沿",
        price=12.8,
        level="30",
        role="center_upper",
    )
    chan = ChanAnalysisResult(
        symbol="sh.600519",
        current_position="30分钟中枢离开后回抽",
        structure_state="等待3买确认",
        complete_paths=[
            ChanPathCandidate(
                id="A",
                name="回踩中枢上沿形成3买",
                level="30",
                status="WAITING",
                structure_logic="价格回踩中枢上沿后不跌回中枢，形成三买候选。",
                trigger_condition="重新站上12.80并出现5分钟向上笔。",
                invalidation_condition="跌回30分钟中枢内部。",
                key_levels=[key_level],
            )
        ],
        key_levels=[key_level],
        discipline_rules=["结构未确认前不追高。"],
    )
    kronos = KronosForecastResult(
        symbol="sh.600519",
        model_name="local-a-share-kronos",
        lookback=400,
        horizon=10,
        recursive_constraints=[
            KronosRecursiveConstraint(
                parent_level="day",
                child_level="30",
                parent_direction="UP",
                child_direction="SIDEWAYS",
                alignment="DIVERGENT",
                parent_expected_change_pct=2.4,
                child_expected_change_pct=0.2,
                parent_horizon=10,
                child_horizon=80,
                constraint_summary="日线向上但30分钟震荡，递归关系 DIVERGENT。",
                fusion_instruction="高低周期方向冲突，Fusion 必须降级为等待或说明分歧，不得输出强动作。",
            )
        ],
        volatility_state="收敛",
    )

    assert not hasattr(kronos, "path_probabilities")
    assert kronos.recursive_constraints[0].alignment == "DIVERGENT"


def test_fusion_output_requires_risk_disclaimer_in_user_visible_text():
    path = FusionPathInference(
        id="path-A",
        chan_path_id="A",
        rank=1,
        name="回踩中枢上沿形成3买",
        probability=0.48,
        confidence="MEDIUM",
        chan_basis="缠论结构仍在等待三买确认。",
        kronos_basis="Kronos 采样支持回踩止跌，但置信度中等。",
        wait_condition="等待重新站上12.80。",
        trigger_condition="5分钟向上笔确认。",
        invalidation_condition="跌回中枢内部。",
        position_discipline="未确认前不加仓。",
        risk_note="结构未触发前只观察。",
    )

    with pytest.raises(ValueError):
        AIFusionInference(
            symbol="sh.600519",
            current_judgement="结构等待确认，Kronos 概率中性偏多。",
            primary_path_id="path-A",
            path_inferences=[path],
            coach_message="等待确认，不追高。",
            defense_line="跌回中枢内部则结构失效。",
        )

    result = AIFusionInference(
        symbol="sh.600519",
        current_judgement="结构等待确认，Kronos 概率中性偏多。",
        primary_path_id="path-A",
        path_inferences=[path],
        coach_message="等待确认，不追高。仅供参考，不构成投资建议。",
        defense_line="跌回中枢内部则结构失效。",
    )

    assert "仅供参考" in result.coach_message
    assert "仅供参考" in result.disclaimer
