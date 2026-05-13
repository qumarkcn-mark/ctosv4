import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.ai_native.ai_chan_reasoner import build_ai_chan_inference
from server.prompts.ai_chan_reasoning_prompt import AI_CHAN_REASONING_PROMPT_E1_VERSION
from server.engines.ai_native.fusion_chan_adapter import build_chan_analysis_from_radar_contract
from server.engines.ai_native.fusion_kronos_adapter import build_kronos_forecast_from_service_result


class FakeChanLLM:
    def __init__(self, payload):
        self.payload = payload
        self.system_prompt = ""
        self.context_json = ""

    async def infer_ai_native_radar(self, system_prompt, context_json, **kwargs):
        self.system_prompt = system_prompt
        self.context_json = context_json
        return self.payload


class SlowChanLLM:
    async def infer_ai_native_radar(self, system_prompt, context_json, **kwargs):
        await asyncio.sleep(1)
        return {}


def chan_contract():
    return {
        "symbol": "sh.600519",
        "mode": "EMPTY",
        "structure": {
            "levels": {
                "30": {"level": "30", "price": 12.3, "state": "WAITING", "zg": 11.8, "zd": 11.2},
                "5": {"level": "5", "price": 12.3, "state": "REPAIR", "zg": 12.8, "zd": 11.9},
            }
        },
        "algorithm_v2": {
            "path": "UPWARD_MAJOR_WAVE",
            "phase": "BREAKOUT_EXTENSION",
            "boundaries": {
                "confirm": [{"label": "确认", "value": 12.8, "level": "5"}],
                "invalidate": [
                    {"label": "风险事件", "value": 13.5, "level": "30", "trigger": "risk_event"},
                    {"label": "失效", "value": 11.2, "level": "30", "trigger": "break_below"},
                ],
            },
        },
    }


def kronos_result():
    return {"symbol": "sh.600519", "levels": {}}


def inputs():
    chan = build_chan_analysis_from_radar_contract(chan_contract())
    kronos = build_kronos_forecast_from_service_result(kronos_result(), chan_analysis=chan)
    return chan, kronos


def test_ai_chan_reasoner_returns_structured_output():
    chan, kronos = inputs()
    llm = FakeChanLLM({
        "symbol": "sh.600519",
        "current_position": "30分钟中枢震荡，5分钟等待确认。",
        "structure_confidence": 0.72,
        "level_positions": [
            {"level": "30", "position": "30分钟中枢震荡。", "key_price": 12.8, "key_price_label": "确认位"},
            {"level": "5", "position": "5分钟等待向上笔确认。"},
        ],
        "synthesis": "现价在确认位下方，先等12.80站稳。",
        "classification": {
            "current_signal": "m30_zs_above_bs3_strong",
            "structure_basis": "现价在12.80确认位下方，5分钟修复未确认。",
            "paths": [
                    {
                        "path_id": 1,
                        "operation_state": "30F_RANGE_UNKNOWN",
                        "current_state": "现价在确认位12.80下方，尚未转强。",
                    "description": "站回确认位后再看修复延续",
                    "next_boundary": "5分钟确认位 12.80",
                    "trigger_condition": "现价重新站稳 12.80。",
                    "target_price": 13.2,
                    "invalidate_price": 11.2,
                    "action": "等待确认后再评估。仅供参考，不构成投资建议。",
                    "requires_confirmation": True,
                    "evidence": ["m30_zs_above_bs3_strong"],
                },
                    {
                        "path_id": 2,
                        "operation_state": "30F_RANGE_UNKNOWN",
                        "current_state": "现价未站回12.80，结构仍按震荡处理。",
                    "description": "继续中枢震荡等待",
                    "next_boundary": "30分钟失效位 11.20",
                    "trigger_condition": "现价继续在 11.20-12.80 区间内运行。",
                    "target_price": None,
                    "invalidate_price": 11.2,
                    "action": "观望。仅供参考，不构成投资建议。",
                    "requires_confirmation": True,
                    "evidence": ["m30_zs_above_bs3_strong"],
                },
            ],
        },
        "primary_path_id": "B",
        "paths": [
            {
                "id": "B",
                "name": "中枢震荡等待",
                "description": "结构未确认前继续按震荡处理。",
                "status": "CURRENT",
                "entry_condition": "站稳 12.80 后才转强。",
                "invalidation": "跌破 11.20 则结构失效。",
                "chan_basis": "30分钟仍在中枢内，5分钟只是修复。",
                "confidence": 0.72,
            }
        ],
        "defense_line": 11.2,
        "observation_line": 12.8,
        "wait_for": ["站稳 12.80"],
        "invalidation": ["跌破 11.20"],
        "discipline": "未确认前不追高。仅供参考，不构成投资建议。",
        "corrections": ["将规则输出的强延续降级为等待确认。"],
        "uncertainty": ["5分钟修复尚未闭合确认。"],
        "disclaimer": "仅供参考，不构成投资建议",
    })

    output = asyncio.run(
        build_ai_chan_inference(
            chan_analysis=chan,
            llm_service=llm,
        )
    )

    assert output.version == "ai_chan_inference.v70"
    assert output.primary_path_id == "B"
    assert output.structure_confidence == 0.72
    assert output.level_positions[0].level == "30"
    assert output.level_positions[0].key_price == 12.8
    assert output.synthesis == "现价在确认位下方，先等12.80站稳。"
    assert output.paths[0].entry_condition == "站稳 12.80 后才转强。"
    assert output.classification.current_signal == "m30_zs_above_bs3_strong"
    assert len(output.classification.paths) == 2
    assert output.classification.paths[0].current_state.startswith("现价")
    assert output.source_versions["classification_compat_mode"] is False
    assert output.source_versions["classification_validation_violations"] == []
    assert output.corrections == ["将规则输出的强延续降级为等待确认。"]
    assert output.source_versions["fallback_triggered"] is False
    assert "kronos_evidence" not in llm.context_json
    assert "forecast_mean" not in llm.context_json
    assert "\"raw_bi_context\"" in llm.context_json
    assert "\"allowed_prices\"" in llm.context_json
    assert "\"algorithm_reference\"" not in llm.context_json
    assert "\"deterministic_scenarios\"" not in llm.context_json
    assert "\"ai_classification\"" not in llm.context_json
    assert "paths(ABC)" not in llm.context_json
    assert "\"output_format\"" in llm.context_json


def test_ai_chan_reasoner_can_use_e1_prompt_with_czsc_context():
    chan, _ = inputs()
    llm = FakeChanLLM({
        "symbol": "sh.600519",
        "current_position": "30分钟中枢震荡。",
        "structure_confidence": 0.6,
        "classification": {
            "current_signal": "",
            "structure_basis": "当前焦点是 5分钟顶端中枢能否离开。",
            "paths": [
                {
                    "path_id": 1,
                    "operation_state": "30F_RANGE_UNKNOWN",
                    "current_state": "价格仍在5分钟中枢内。",
                    "description": "观察能否向上离开中枢",
                    "next_boundary": "5分钟中枢上沿 243.49",
                    "trigger_condition": "站稳 243.49 后重新推演。",
                    "action": "观察。仅供参考，不构成投资建议。",
                    "requires_confirmation": True,
                    "evidence": ["czsc_raw_bi_context"],
                },
                {
                    "path_id": 2,
                    "operation_state": "30F_RANGE_UNKNOWN",
                    "current_state": "价格仍在5分钟中枢内。",
                    "description": "跌回中枢下沿",
                    "next_boundary": "5分钟中枢下沿 236.72",
                    "trigger_condition": "跌破 236.72 后重新推演。",
                    "action": "放弃追高。仅供参考，不构成投资建议。",
                    "requires_confirmation": True,
                    "evidence": ["czsc_raw_bi_context"],
                },
            ],
        },
        "paths": [],
        "main_deduction": "当前先看5分钟顶端中枢能否有效离开。",
        "synthesis": "当前焦点是5分钟顶端中枢能否离开，先观察。",
        "discipline": "未确认前不追高。仅供参考，不构成投资建议。",
        "disclaimer": "仅供参考，不构成投资建议",
    })
    czsc_context = {
        "symbol": "sh.600519",
        "version": "czsc_raw_bi_context.v1",
        "levels": {
            "5": {
                "last_close": 240.7,
                "bi_sequence": [
                    {
                        "direction": "UP",
                        "begin_price": 231.17,
                        "end_price": 245.69,
                        "high": 245.69,
                        "low": 231.17,
                        "begin_time": "2026-05-12 09:45:00",
                        "end_time": "2026-05-12 10:55:00",
                        "bar_count": 14,
                        "is_sure": True,
                    }
                ],
                "algorithm_zhongshus": [{"zg": 243.49, "zd": 236.72}],
            }
        },
    }

    output = asyncio.run(
        build_ai_chan_inference(
            chan_analysis=chan,
            raw_bi_context=czsc_context,
            llm_service=llm,
            prompt_variant="e1",
        )
    )

    assert output.source_versions["prompt"] == AI_CHAN_REASONING_PROMPT_E1_VERSION
    assert output.source_versions["prompt_variant"] == "e1"
    assert "AI 缠论实战教练" in llm.system_prompt
    assert "czsc_raw_bi_context.v1" in llm.context_json


def test_ai_chan_reasoner_marks_legacy_paths_as_classification_compat():
    chan, _ = inputs()
    llm = FakeChanLLM({
        "symbol": "sh.600519",
        "current_position": "30分钟中枢震荡。",
        "structure_confidence": 0.55,
        "paths": [
            {
                "id": "B",
                "name": "中枢震荡",
                "description": "现价在中枢内。",
                "status": "CURRENT",
                "entry_condition": "现价站稳 12.80。",
                "invalidation": "跌破 11.20。",
                "chan_basis": "30分钟中枢内。",
                "confidence": 0.5,
            },
            {
                "id": "C",
                "name": "失效下破",
                "description": "现价跌破中枢下沿。",
                "status": "CANDIDATE",
                "entry_condition": "跌破 11.20。",
                "invalidation": "收回 11.20。",
                "chan_basis": "30分钟中枢下沿。",
                "confidence": 0.3,
            },
        ],
        "discipline": "未确认前不追高。仅供参考，不构成投资建议。",
        "disclaimer": "仅供参考，不构成投资建议",
    })

    output = asyncio.run(build_ai_chan_inference(chan_analysis=chan, llm_service=llm))

    assert output.classification is not None
    assert len(output.classification.paths) == 2
    assert output.source_versions["classification_compat_mode"] is True


def test_ai_chan_reasoner_timeout_returns_service_busy(monkeypatch):
    monkeypatch.setattr(
        "server.engines.ai_native.ai_chan_reasoner.config.AI_NATIVE_FUSION_LLM_TIMEOUT",
        0.01,
    )
    monkeypatch.setattr(
        "server.engines.ai_native.ai_chan_reasoner.config.AI_NATIVE_RADAR_LLM_TIMEOUT",
        0.01,
    )
    chan, kronos = inputs()

    with pytest.raises(RuntimeError, match="AI 服务忙"):
        asyncio.run(
            build_ai_chan_inference(
                chan_analysis=chan,
                llm_service=SlowChanLLM(),
            )
        )


def test_ai_chan_path_defaults_ignore_risk_event_as_downside_invalidation():
    chan, _ = inputs()
    path_a = next(path for path in chan.complete_paths if path.id == "A")
    assert "13.50" not in path_a.invalidation_condition
    assert "11.20" in path_a.invalidation_condition


def test_ai_chan_reasoner_does_not_use_kronos_prices():
    chan, kronos = inputs()
    kronos.forecast_mean = [{"step": 1, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5}]
    llm = FakeChanLLM({
        "symbol": "sh.600519",
        "current_position": "30分钟中枢震荡。",
        "structure_confidence": 0.55,
        "paths": [],
        "discipline": "未确认前不追高。仅供参考，不构成投资建议。",
        "disclaimer": "仅供参考，不构成投资建议",
    })

    asyncio.run(
        build_ai_chan_inference(
            chan_analysis=chan,
            llm_service=llm,
        )
    )

    assert "100.5" not in llm.context_json
    assert "101.0" not in llm.context_json
