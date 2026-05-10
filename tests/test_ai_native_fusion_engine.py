import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.ai_native.ai_fusion_engine import build_ai_fusion_inference, build_data_alignment_snapshot, _fusion_context
from server.engines.ai_native.fusion_schemas import AIChanInference, AIChanPath
from server.engines.ai_native.fusion_chan_adapter import build_chan_analysis_from_radar_contract
from server.engines.ai_native.fusion_kronos_adapter import build_kronos_forecast_from_service_result
from server.prompts.ai_native_fusion_prompt import AI_NATIVE_FUSION_PROMPT


class FakeFusionLLM:
    def __init__(self, payload):
        self.payload = payload
        self.context_json = ""
        self.kwargs = {}

    async def infer_ai_native_radar(self, system_prompt, context_json, **kwargs):
        self.context_json = context_json
        self.kwargs = kwargs
        return self.payload


class SlowFusionLLM:
    async def infer_ai_native_radar(self, system_prompt, context_json, **kwargs):
        await asyncio.sleep(1)
        return fusion_payload()


def chan_contract():
    return {
        "symbol": "sh.600519",
        "mode": "EMPTY",
        "structure": {
            "levels": {
                "day": {"level": "day", "price": 12.3, "state": "UPWARD", "zg": 11.9, "zd": 10.8},
                "30": {"level": "30", "price": 12.3, "state": "WAITING", "zg": 11.8, "zd": 11.2},
                "5": {"level": "5", "price": 12.3, "state": "REPAIR", "zg": 12.8, "zd": 11.9},
            }
        },
        "algorithm_v2": {
            "path": "UPWARD_MAJOR_WAVE",
            "phase": "BREAKOUT_EXTENSION",
            "boundaries": {
                "confirm": [{"label": "确认", "value": 12.8, "level": "5"}],
                "maintain": [{"label": "观察", "value": 11.9, "level": "5"}],
                "invalidate": [{"label": "失效", "value": 11.2, "level": "30"}],
            },
        },
    }


def kronos_result():
    return {
        "symbol": "sh.600519",
        "levels": {
            "30": {
                "symbol": "sh.600519",
                "interval": "30",
                "change_pct": 1.4,
                "force_score": 18.0,
                "verdict": "Positive Force",
                "last_date": "2026-05-05 14:30:00",
                "forecast_data": [
                    {"date": "2026-05-05 15:00:00", "open": 12.3, "high": 12.5, "low": 12.2, "close": 12.45},
                    {"date": "2026-05-06 10:00:00", "open": 12.45, "high": 12.8, "low": 12.4, "close": 12.7},
                ],
            }
        },
    }


def fusion_payload():
    return {
        "version": "ai_fusion_inference.v45",
        "symbol": "sh.600519",
        "generated_at": "2026-05-05T15:00:00+08:00",
        "current_judgement": "结构仍在等待确认，Kronos 偏正但不是确定性信号。",
        "primary_path_id": "path-A",
        "path_inferences": [
            {
                "id": "path-A",
                "chan_path_id": "A",
                "rank": 1,
                "name": "结构确认后延续",
                "probability": 0.48,
                "confidence": "MEDIUM",
                "chan_basis": "缠论要求重新站上确认边界，当前仍是等待确认。",
                "kronos_basis": "Kronos 预测序列偏正，但概率来自代理摘要，不能当确定性。",
                "action_bias": "OBSERVE",
                "wait_condition": "等待重新站上 12.80。",
                "trigger_condition": "站上 12.80 后再评估。",
                "invalidation_condition": "跌破 11.20 则结构失效。",
                "position_discipline": "未确认前不追高。",
                "risk_note": "只按结构边界观察。仅供参考，不构成投资建议。",
            }
        ],
        "coach_message": "等待确认，不追高。仅供参考，不构成投资建议。",
        "defense_line": "跌破 11.20 则结构失效。",
        "wait_for": ["站上 12.80"],
        "invalidation": ["跌破 11.20"],
        "action_playbook": {
            "action": "TEST",
            "action_label": "满足确认后试仓",
            "primary_reason": "结构仍在等待确认，只能条件化试仓。仅供参考，不构成投资建议。",
            "test_conditions": ["站上 12.80 且 5 分钟向上笔确认"],
            "add_conditions": ["试仓后回踩不破确认线"],
            "reduce_conditions": [],
            "exit_conditions": ["跌破 11.20"],
            "hold_conditions": [],
            "max_position_weight_pct": 3,
            "recheck_trigger": "NEXT_30M_CLOSE",
            "risk_note": "未确认前不追高。仅供参考，不构成投资建议。",
        },
        "position_sizing_note": "不输出自动交易指令。仅供参考，不构成投资建议。",
        "source_versions": {"chan": "chan_analysis.v45", "kronos": "kronos_forecast.v45"},
        "disclaimer": "仅供参考，不构成投资建议",
    }


def ai_chan_inference():
    return AIChanInference(
        symbol="sh.600519",
        generated_at="2026-05-05T15:00:00+08:00",
        current_position="AI Chan 判断为中枢震荡等待确认。",
        structure_confidence=0.72,
        primary_path_id="B",
        paths=[
            AIChanPath(
                id="B",
                name="中枢震荡等待",
                description="当前仍按中枢震荡处理。",
                status="CURRENT",
                entry_condition="站稳 12.80 后转强。",
                invalidation="跌破 11.20 则结构失效。",
                chan_basis="AI Chan 综合多级别结构后，将延续路径降级为等待确认。",
                confidence=0.72,
            )
        ],
        defense_line=11.2,
        observation_line=12.8,
        wait_for=["站稳 12.80"],
        invalidation=["跌破 11.20"],
        discipline="未确认前不追高。仅供参考，不构成投资建议。",
        corrections=["规则路径延续过强，AI Chan 降级为等待确认。"],
        uncertainty=["5分钟修复尚未闭合确认。"],
    )


def inputs():
    chan = build_chan_analysis_from_radar_contract(chan_contract())
    kronos = build_kronos_forecast_from_service_result(kronos_result(), chan_analysis=chan)
    return chan, kronos


def test_fusion_engine_returns_llm_output():
    chan, kronos = inputs()
    fake_llm = FakeFusionLLM(fusion_payload())
    output = asyncio.run(
        build_ai_fusion_inference(
            chan_analysis=chan,
            kronos_forecast=kronos,
            ai_chan_inference=ai_chan_inference(),
            llm_service=fake_llm,
        )
    )

    assert output.primary_path_id == "path-A"
    assert output.action_playbook.action == "TEST"
    assert output.action_playbook.test_conditions == ["站上 12.80 且 5 分钟向上笔确认"]
    assert output.action_playbook.max_position_weight_pct == 3
    assert "等待确认" in output.coach_message
    context = json.loads(fake_llm.context_json)
    assert set(context).issuperset({"raw_facts", "chan_structure", "ai_chan_inference", "data_alignment", "conflict_candidates", "fusion_rules"})
    assert "kronos_evidence" not in context
    assert "kronos_evidence" not in fake_llm.context_json
    assert "forecast_mean" not in fake_llm.context_json
    assert "force_score" not in fake_llm.context_json
    assert "verdict" not in fake_llm.context_json
    assert "resonance_type" not in fake_llm.context_json
    assert context["ai_chan_inference"]["structure_confidence"] == 0.72
    assert context["ai_chan_inference"]["corrections"] == ["规则路径延续过强，AI Chan 降级为等待确认。"]
    assert context["data_alignment"]["status"] in {"ALIGNED", "STALE_KRONOS", "STALE_CHAN", "UNKNOWN"}
    assert context["fusion_rules"]["responsibilities"]["path_probability_and_final_judgement"] == "AI Fusion"
    assert fake_llm.kwargs["model_route"].timeout_seconds > 0
    assert output.diagnostics["prompt_chars"] > 0
    assert output.diagnostics["llm_ms"] >= 0
    assert output.diagnostics["fallback_triggered"] is False
    assert output.diagnostics["has_ai_chan_inference"] is True


def test_fusion_context_prefers_first_stage_reasoning_when_available():
    chan, kronos = inputs()
    context = _fusion_context(
        chan,
        kronos,
        None,
        first_stage_reasoning={
            "run_id": 99,
            "symbol": "sh.600519",
            "mode": "HOLDING",
            "generated_at": "2026-05-05T15:00:00+08:00",
            "gate_status": "PASS",
            "coach_filtered_md": "### 【当前定位】\n中枢震荡。\n\n### 【三种剧本】\n剧本A：向上确认。\n剧本B：震荡等待。\n剧本C：结构失效。",
        },
    )

    assert context["first_stage_reasoning"]["run_id"] == 99
    assert "三种剧本" in context["first_stage_reasoning"]["current_position_and_scripts_md"]
    assert context["fusion_rules"]["responsibilities"]["complete_classification"].startswith("first_stage_reasoning")


def test_fusion_context_compacts_long_first_stage_reasoning():
    chan, kronos = inputs()
    long_tail = "冗余解释。" * 3000
    context = _fusion_context(
        chan,
        kronos,
        None,
        first_stage_reasoning={
            "run_id": 100,
            "symbol": "sh.600519",
            "mode": "HOLDING",
            "generated_at": "2026-05-05T15:00:00+08:00",
            "gate_status": "PASS",
            "coach_filtered_md": f"### 【当前定位】\n中枢震荡。\n\n### 【三种剧本】\n剧本A：向下突破。\n\n### 【废话】\n{long_tail}",
        },
    )

    text = context["first_stage_reasoning"]["current_position_and_scripts_md"]
    assert "当前定位" in text
    assert "三种剧本" in text
    assert "废话" not in text
    assert len(text) < 1000


def test_fusion_context_marks_conflict_candidates_without_final_judgement():
    chan, kronos = inputs()
    chan.signal_v2 = {
        "primary": {"code": "m30_zs_above_breakout_medium", "action": "建议观察确认"},
        "context": {
            "kronos_timeline": {"estimated_confirmation_bars": 4},
            "kronos_envelope": {"envelope_low": 14.2, "envelope_high": 14.8},
        },
    }
    context = _fusion_context(chan, kronos, None)

    assert context["raw_facts"]["symbol"] == "sh.600519"
    assert context["chan_structure"]["complete_paths"]
    assert context["chan_structure"]["signal_context"]["kronos_timeline"]["estimated_confirmation_bars"] == 4
    assert context["chan_structure"]["signal_context"]["kronos_envelope"]["envelope_low"] == 14.2
    assert "kronos_evidence" not in context
    assert context["conflict_candidates"]
    assert context["conflict_candidates"][0]["status"] in {"CONSISTENT", "POTENTIAL_CONFLICT", "NEEDS_AI_JUDGEMENT"}
    assert context["ai_chan_inference"] is None
    assert context["data_alignment"]["primary_data_time"] == "2026-05-05 14:30:00"


def test_data_alignment_marks_stale_kronos_when_timestamps_diverge():
    chan, kronos = inputs()
    chan.generated_at = "2026-05-05T15:00:00+08:00"
    kronos.generated_at = "2026-05-05 10:00:00"

    alignment = build_data_alignment_snapshot(chan, kronos, ai_chan_inference())

    assert alignment.status == "STALE_KRONOS"
    assert alignment.max_delta_minutes is not None
    assert alignment.max_delta_minutes > 35


def test_data_alignment_uses_first_stage_time_when_present():
    chan, kronos = inputs()
    chan.generated_at = "2026-05-05T10:00:00+08:00"
    kronos.generated_at = "2026-05-05 14:30:00"

    alignment = build_data_alignment_snapshot(
        chan,
        kronos,
        first_stage_generated_at="2026-05-05T14:30:00+08:00",
    )

    assert alignment.status == "ALIGNED"
    assert alignment.ai_chan_generated_at == "2026-05-05T14:30:00+08:00"
    assert alignment.analysis_data_time.endswith("14:30:00+08:00")


def test_data_alignment_maps_after_close_first_stage_to_daily_close():
    chan, kronos = inputs()
    chan.generated_at = "2026-05-06T15:00:00+08:00"
    kronos.generated_at = "2026-05-06 15:00:00"

    alignment = build_data_alignment_snapshot(
        chan,
        kronos,
        first_stage_generated_at="2026-05-06T19:40:07+08:00",
    )

    assert alignment.status == "ALIGNED"
    assert alignment.analysis_data_time.endswith("15:00:00+08:00")
    assert alignment.max_delta_minutes == 0
    assert "A 股交易时段" in alignment.note


def test_fusion_prompt_declares_key_fields():
    assert "first_stage_reasoning" in AI_NATIVE_FUSION_PROMPT
    assert "ai_chan_inference" in AI_NATIVE_FUSION_PROMPT
    assert "中国 A 股普通股票" in AI_NATIVE_FUSION_PROMPT
    assert "T+1 约束" in AI_NATIVE_FUSION_PROMPT
    assert "逐根阅读" not in AI_NATIVE_FUSION_PROMPT
    assert "forecast_mean" not in AI_NATIVE_FUSION_PROMPT
    assert "force_score" not in AI_NATIVE_FUSION_PROMPT
    assert "recursive_constraints" not in AI_NATIVE_FUSION_PROMPT


def test_fusion_engine_does_not_apply_trading_gate_to_llm_output():
    chan, kronos = inputs()
    payload = fusion_payload()
    payload["coach_message"] = "这里必涨，满仓。仅供参考，不构成投资建议。"

    output = asyncio.run(
        build_ai_fusion_inference(
            chan_analysis=chan,
            kronos_forecast=kronos,
            llm_service=FakeFusionLLM(payload),
        )
    )

    assert output.primary_path_id == "path-A"
    assert "必涨" in output.coach_message


def test_fusion_engine_coerces_partial_ai_output_without_fallback():
    chan, kronos = inputs()
    payload = {
        "symbol": "sh.600519",
        "judgement": "AI 认为结构未失效，但需要等触发。",
        "paths": [
            {
                "chanPathId": "A",
                "title": "等待确认",
                "probability": "52%",
                "actionBias": "等待",
            }
        ],
        "advice": "继续观察",
    }

    output = asyncio.run(
        build_ai_fusion_inference(
            chan_analysis=chan,
            kronos_forecast=kronos,
            llm_service=FakeFusionLLM(payload),
        )
    )

    assert output.primary_path_id == "path-A"
    assert output.path_inferences[0].probability == 0.52
    assert output.path_inferences[0].action_bias == "WAIT"
    assert output.action_playbook.action == "OBSERVE"
    assert output.action_playbook.recheck_trigger == "NEXT_30M_CLOSE"
    assert "仅供参考" in output.coach_message


def test_fusion_engine_moves_misplaced_reduce_condition_out_of_test_bucket():
    chan, kronos = inputs()
    payload = {
        "symbol": "sh.600519",
        "path_inferences": [
            {
                "chanPathId": "A",
                "title": "向下突破",
                "probability": "60%",
                "actionBias": "REDUCE_RISK",
            }
        ],
        "action_playbook": {
            "action": "REDUCE",
            "primary_reason": "结构风险优先。仅供参考，不构成投资建议。",
            "test_conditions": ["若价格反弹至180.91成本线附近受阻，可考虑减仓。"],
            "reduce_conditions": ["跌破176.87应减仓。"],
        },
        "advice": "继续观察",
        "disclaimer": "仅供参考，不构成投资建议",
    }

    output = asyncio.run(
        build_ai_fusion_inference(
            chan_analysis=chan,
            kronos_forecast=kronos,
            llm_service=FakeFusionLLM(payload),
        )
    )

    assert output.action_playbook.action == "REDUCE"
    assert output.action_playbook.test_conditions == []
    assert "若价格反弹至180.91成本线附近受阻，可考虑减仓。" in output.action_playbook.reduce_conditions
    assert "跌破176.87应减仓。" in output.action_playbook.reduce_conditions


def test_fusion_engine_fallbacks_do_not_consume_kronos_deprecated_summary_fields():
    chan, kronos = inputs()
    kronos.warnings = ["danger"]
    payload = {
        "symbol": "sh.600519",
        "path_inferences": [
            {
                "chan_path_id": "A",
                "name": "等待确认",
                "action_bias": "OBSERVE",
            }
        ],
        "action_playbook": {"action": "OBSERVE"},
        "disclaimer": "仅供参考，不构成投资建议",
    }

    output = asyncio.run(
        build_ai_fusion_inference(
            chan_analysis=chan,
            kronos_forecast=kronos,
            ai_chan_inference=ai_chan_inference(),
            llm_service=FakeFusionLLM(payload),
        )
    )

    path = output.path_inferences[0]
    dumped = json.dumps(output.model_dump(), ensure_ascii=False)
    assert path.probability == 0.72
    assert path.kronos_basis == "Fusion 未消费 Kronos 原始预测；本条按结构推演和可选时间/价格参考生成。"
    assert "force_score" not in dumped
    assert "Strong Bullish" not in dumped
    assert "Kronos 概率" not in dumped
    assert "Kronos 动力学" not in dumped


def test_fusion_engine_coerces_nonstandard_rank_and_source_versions():
    chan, kronos = inputs()
    payload = fusion_payload()
    payload["source_versions"] = "LLM 写成了字符串"
    payload["path_inferences"][0]["rank"] = "第一"

    output = asyncio.run(
        build_ai_fusion_inference(
            chan_analysis=chan,
            kronos_forecast=kronos,
            llm_service=FakeFusionLLM(payload),
        )
    )

    assert output.primary_path_id == "path-A"
    assert output.path_inferences[0].rank == 1
    assert output.source_versions["chan"] == "chan_analysis.v45"


def test_fusion_engine_returns_service_busy_when_ai_output_is_not_object():
    chan, kronos = inputs()

    with pytest.raises(RuntimeError, match="AI 服务忙"):
        asyncio.run(
            build_ai_fusion_inference(
                chan_analysis=chan,
                kronos_forecast=kronos,
                llm_service=FakeFusionLLM("not-json-object"),
            )
        )


def test_fusion_engine_timeout_returns_service_busy(monkeypatch):
    monkeypatch.setattr(
        "server.engines.ai_native.ai_fusion_engine.config.AI_NATIVE_FUSION_LLM_TIMEOUT",
        0.01,
    )
    chan, kronos = inputs()

    with pytest.raises(RuntimeError, match="AI 服务忙"):
        asyncio.run(
            build_ai_fusion_inference(
                chan_analysis=chan,
                kronos_forecast=kronos,
                llm_service=SlowFusionLLM(),
            )
        )


def test_fusion_route_uses_deepseek_v4_pro_by_default():
    from server.engines.ai_native.ai_fusion_engine import _default_fusion_model_route

    assert _default_fusion_model_route().model_name == "deepseek-v4-pro"


def test_fusion_timeout_does_not_build_structural_fallback(monkeypatch):
    monkeypatch.setattr(
        "server.engines.ai_native.ai_fusion_engine.config.AI_NATIVE_FUSION_LLM_TIMEOUT",
        0.01,
    )
    chan, kronos = inputs()

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(
            build_ai_fusion_inference(
                chan_analysis=chan,
                kronos_forecast=kronos,
                llm_service=SlowFusionLLM(),
            )
        )

    assert "AI 服务忙" in str(exc_info.value)
    assert "结构事实兜底" not in str(exc_info.value)
