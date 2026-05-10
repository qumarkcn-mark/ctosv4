import asyncio
import importlib
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import agent
from server.db import database
from server.engines.ai_native import reasoning_orchestrator
from server.engines.ai_native.model_router import ModelRoute
from server.engines.ai_native.transcript_compiler import compile_structure_transcript
from server.engines.ai_native.verifier import verify_ai_reasoning


def make_radar_contract():
    return {
        "symbol": "sh.600519",
        "mode": "EMPTY",
        "freshness": {"is_stale": False},
        "structure": {
            "levels": {
                "day": {"level": "day", "price": 12.3, "state": "UPWARD_LEAVING", "zg": 11.9, "zd": 10.8},
                "30": {"level": "30", "price": 12.3, "state": "WAITING_FOR_PULLBACK", "zg": 11.8, "zd": 11.2},
                "5": {"level": "5", "price": 12.3, "state": "IN_CENTER_OSC", "zg": 12.8, "zd": 11.9},
            }
        },
        "deduction": {
            "summary": "等待5分触发",
            "complete_classification": [],
        },
        "algorithm_v2": {
            "path": "UPWARD_MAJOR_WAVE",
            "phase": "BREAKOUT_EXTENSION",
            "summary": "旧高突破后回踩验证",
            "current_scenario_id": "B",
            "boundaries": {
                "confirm": [{"label": "历史前高", "value": 12.8}],
                "maintain": [{"label": "观察区间下沿", "value": 11.9}],
                "invalidate": [{"label": "短线失效", "value": 11.9}],
                "support": [{"label": "大级别防线", "value": 10.8}],
            },
        },
        "signals_v2": {
            "version": "semantic_signal.v2",
            "state": "success",
            "primary": {
                "code": "d1_zs_above_bs3_strong",
                "label_plain": "日线级别回踩支撑位不破，信号较强",
                "label_expert": "日线 · 中枢上 · 三买 · 强",
                "action": "建议观察确认，止损 11.90",
            },
            "context": {
                "signal_code": "d1_zs_above_bs3_strong",
                "key_price": 12.3,
                "boundary_state": "observe",
                "stop_loss_price": 11.9,
                "risk_reward_ratio": 2.0,
                "action_rule": "由于出现信号 d1_zs_above_bs3_strong，根据三买规则，等待确认。",
            },
            "classification": [],
        },
    }


class BrokenLLM:
    async def infer_ai_native_radar(self, *_args, **_kwargs):
        raise RuntimeError("offline")

    async def infer_ai_native_markdown(self, *_args, **_kwargs):
        raise RuntimeError("offline")


class UserAwareLLM:
    def __init__(self):
        self.user_ids = []
        self.model_routes = []
        self.contexts = []
        self.calls = 0

    async def infer_ai_native_markdown(self, *_args, **kwargs):
        self.user_ids.append(kwargs.get("user_id"))
        self.model_routes.append(kwargs.get("model_route"))
        if len(_args) >= 2:
            context_text = _args[1]
            if context_text.startswith("EVIDENCE PACK:\n"):
                self.contexts.append(json.loads(context_text.split("\n", 1)[1]))
        self.calls += 1
        return """**1. 【全局语境定性】**
结构处在确认后的观察段。

**2. 【防守看门狗】**
只在 12.80、11.90、10.80 这些结构价内有效。

**3. 【推演与应对沙盘】**
当前先承认分类没有结束，空仓只等待结构给出确认，持仓只按边界管理风险。仅供参考，不构成投资建议。"""

    async def infer_ai_native_radar(self, *_args, **kwargs):
        self.user_ids.append(kwargs.get("user_id"))
        self.model_routes.append(kwargs.get("model_route"))
        if len(_args) >= 2:
            self.contexts.append(json.loads(_args[1]))
        self.calls += 1
        return {
            "symbol": "sh.600519",
            "current_position": "结构处在确认后的观察段。",
            "structure_confidence": 0.72,
            "main_deduction": "结构处在确认后的观察段。",
            "primary_path_id": "B",
            "paths": [
                {
                    "id": "B",
                    "name": "震荡等待",
                    "description": "当前先承认分类没有结束。",
                    "status": "CURRENT",
                    "entry_condition": "站稳 12.80 后再确认。",
                    "invalidation": "跌破 10.80 则失效。",
                    "chan_basis": "按结构边界管理风险。",
                    "confidence": 0.72,
                }
            ],
            "discipline": "空仓只等待结构给出确认，持仓只按边界管理风险。仅供参考，不构成投资建议。",
            "disclaimer": "仅供参考，不构成投资建议",
        }


class GateBypassLLM(UserAwareLLM):
    async def infer_ai_native_radar(self, *_args, **kwargs):
        await super().infer_ai_native_radar(*_args, **kwargs)
        return {
            "symbol": "sh.600519",
            "current_position": "A路径继续。",
            "structure_confidence": 0.8,
            "main_deduction": "突破 13.27 就买入，A路径继续。",
            "primary_path_id": "A",
            "paths": [
                {
                    "id": "A",
                    "name": "A路径继续",
                    "description": "突破 13.27 就买入。",
                    "status": "CURRENT",
                    "entry_condition": "突破 13.27。",
                    "invalidation": "跌破 10.80。",
                    "chan_basis": "测试门禁关闭时保留原文。",
                    "confidence": 0.8,
                }
            ],
            "discipline": "A路径继续。仅供参考，不构成投资建议。",
            "disclaimer": "仅供参考，不构成投资建议",
        }

    async def infer_ai_native_markdown(self, *_args, **kwargs):
        await super().infer_ai_native_markdown(*_args, **kwargs)
        return "突破 13.27 就买入，A路径继续。仅供参考，不构成投资建议"


class ContractOnlyLLM(UserAwareLLM):
    async def infer_ai_native_radar(self, *_args, **kwargs):
        await super().infer_ai_native_radar(*_args, **kwargs)
        return {
            "symbol": "sh.600519",
            "current_position": "结构处在确认后的观察段。",
            "structure_confidence": 0.7,
            "main_deduction": "这里引用 13.27 作为 AI 自己推演出的观察价，门禁应提示但不能触发保护模式。",
            "primary_path_id": "B",
            "paths": [
                {
                    "id": "B",
                    "name": "震荡观察",
                    "description": "这里引用 13.27 作为 AI 自己推演出的观察价。",
                    "status": "CURRENT",
                    "entry_condition": "观察 13.27。",
                    "invalidation": "跌破 10.80。",
                    "chan_basis": "测试合同输出。",
                    "confidence": 0.7,
                }
            ],
            "discipline": "仅供参考，不构成投资建议。",
            "disclaimer": "仅供参考，不构成投资建议",
        }

    async def infer_ai_native_markdown(self, *_args, **kwargs):
        await super().infer_ai_native_markdown(*_args, **kwargs)
        return """**1. 【全局语境定性】**
结构处在确认后的观察段。

**2. 【防守看门狗】**
只在 12.80、11.90、10.80 这些结构价内有效。

**3. 【推演与应对沙盘】**
这里引用 13.27 作为 AI 自己推演出的观察价，门禁应提示但不能触发保护模式。仅供参考，不构成投资建议。"""


def test_ai_native_failure_writes_only_new_table(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()

    async def fake_get_radar(symbol, user_id=1, include_structure=False):
        assert include_structure is True
        return {"status": "success", "data": make_radar_contract()}

    monkeypatch.setattr(reasoning_orchestrator.radar_api, "get_radar", fake_get_radar)
    monkeypatch.setattr(reasoning_orchestrator.config, "AI_NATIVE_RADAR_WRITE_SNAPSHOTS", False)

    response = asyncio.run(
        reasoning_orchestrator.build_ai_native_reasoning(
            symbol="sh600519",
            user_id=1,
            llm_service=BrokenLLM(),
        )
    )

    assert response.gate_status == "PASS"
    assert "本轮不展示自由推演文本" not in response.coach_filtered_md

    conn = sqlite3.connect(database.DB_PATH)
    ai_count = conn.execute("SELECT COUNT(*) FROM ai_reasoning_runs").fetchone()[0]
    old_count = conn.execute("SELECT COUNT(*) FROM radar_deductions").fetchone()[0]
    conn.close()
    assert ai_count == 1
    assert old_count == 0


def test_ai_native_reasoning_rejects_expected_signal_when_current_signal_missing(monkeypatch):
    contract = make_radar_contract()
    contract.pop("signals_v2", None)

    async def fake_get_radar(symbol, user_id=1, include_structure=False):
        assert include_structure is True
        return {"status": "success", "data": contract}

    monkeypatch.setattr(reasoning_orchestrator.radar_api, "get_radar", fake_get_radar)
    monkeypatch.setattr(reasoning_orchestrator.config, "AI_NATIVE_RADAR_WRITE_SNAPSHOTS", False)

    try:
        asyncio.run(
            reasoning_orchestrator.build_ai_native_reasoning(
                symbol="sh600519",
                user_id=1,
                llm_service=BrokenLLM(),
                expected_signal_code="d1_zs_above_bs3_strong",
            )
        )
    except ValueError as exc:
        assert "actual=NONE" in str(exc)
    else:
        raise AssertionError("missing current signal should be rejected when expected_signal_code is present")


def test_ai_native_fallback_uses_structure_gap_not_old_radar_path(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    contract = make_radar_contract()
    contract["quote"] = {"price": 16.05}
    for level in contract["structure"]["levels"].values():
        level["price"] = 16.05

    async def fake_get_radar(symbol, user_id=1, include_structure=False):
        return {"status": "success", "data": contract}

    monkeypatch.setattr(reasoning_orchestrator.radar_api, "get_radar", fake_get_radar)
    monkeypatch.setattr(reasoning_orchestrator.config, "AI_NATIVE_RADAR_WRITE_SNAPSHOTS", False)

    response = asyncio.run(
        reasoning_orchestrator.build_ai_native_reasoning(
            symbol="sh600519",
            user_id=1,
            llm_service=BrokenLLM(),
        )
    )

    assert response.gate_status == "PASS"
    assert "本轮不展示自由推演文本" not in response.coach_filtered_md
    assert "A 路径" not in response.coach_talk


def test_ai_native_gate_can_be_disabled_for_prompt_testing(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()

    async def fake_get_radar(symbol, user_id=1, include_structure=False):
        return {"status": "success", "data": make_radar_contract()}

    monkeypatch.setattr(reasoning_orchestrator.radar_api, "get_radar", fake_get_radar)
    monkeypatch.setattr(reasoning_orchestrator.config, "AI_NATIVE_RADAR_WRITE_SNAPSHOTS", False)
    monkeypatch.setattr(reasoning_orchestrator.config, "AI_NATIVE_RADAR_GATE_ENABLED", False)
    monkeypatch.setattr(agent.config, "AI_NATIVE_RADAR_GATE_ENABLED", False)

    response = asyncio.run(
        reasoning_orchestrator.build_ai_native_reasoning(
            symbol="sh600519",
            user_id=1,
            llm_service=GateBypassLLM(),
        )
    )
    latest = agent._load_latest_ai_native_radar_run(user_id=1, symbol="sh600519", mode="EMPTY")

    assert response.gate_status == "PASS"
    assert response.gate_score == 100
    assert "买入" in response.coach_filtered_md
    assert "A路径" in response.coach_talk
    assert latest["gate_status"] == "PASS"
    assert "买入" in latest["coach_filtered_md"]


def test_ai_native_passes_request_user_id_to_llm(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()

    async def fake_get_radar(symbol, user_id=1, include_structure=False):
        assert include_structure is True
        return {"status": "success", "data": make_radar_contract()}

    llm = UserAwareLLM()
    monkeypatch.setattr(reasoning_orchestrator.radar_api, "get_radar", fake_get_radar)
    monkeypatch.setattr(reasoning_orchestrator.config, "AI_NATIVE_RADAR_WRITE_SNAPSHOTS", False)

    response = asyncio.run(
        reasoning_orchestrator.build_ai_native_reasoning(
            symbol="sh600519",
            user_id=42,
            llm_service=llm,
        )
    )

    assert response.gate_status == "PASS"
    assert llm.user_ids == [42]
    assert llm.model_routes[0] is not None
    payload = llm.contexts[0]
    assert "raw_bi_context" in payload
    assert payload["semantic_signal"]["primary"]["code"] == "d1_zs_above_bs3_strong"
    assert [item["id"] for item in payload["semantic_signal"]["deterministic_scenarios"]] == ["A", "B", "C"]
    assert "algorithm_reference" in payload
    assert "rules" in payload
    assert "divergence_context" not in payload
    assert "agent_observations" not in payload
    assert "structure_snapshot" not in payload
    assert "rule_engine_observations" not in payload
    raw_levels = payload.get("raw_bi_context", {}).get("levels", {})
    if raw_levels.get("30"):
        assert "state" not in raw_levels["30"]
    assert response.model_route.tier in {"simple", "hard", "calibration"}
    assert response.model_route.model_name


def test_simple_route_ignores_user_thinking_override(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    conn = sqlite3.connect(database.DB_PATH)
    conn.execute(
        "UPDATE users SET settings_json = ? WHERE id = 1",
        (
            json.dumps(
                {
                    "ai_native_radar_provider": "deepseek",
                    "ai_native_radar_thinking_enabled": True,
                    "ai_native_radar_reasoning_effort": "high",
                }
            ),
        ),
    )
    conn.commit()
    conn.close()

    route = ModelRoute(
        tier="simple",
        difficulty_score=12,
        model_name="deepseek-v4-pro",
        thinking_enabled=False,
        reasoning_effort="high",
        max_tokens=4096,
        timeout_seconds=45,
        reasons=["结构清晰"],
    )

    reasoning_orchestrator._apply_user_model_settings(route, user_id=1)

    assert route.tier == "simple"
    assert route.thinking_enabled is False
    assert route.timeout_seconds == 45


def test_verifier_keeps_user_visible_reasoning_without_price_audit():
    transcript = compile_structure_transcript(make_radar_contract())
    output = {
        "raw_reasoning_md": "内部文本",
        "coach_filtered_md": """**1. 【全局语境定性】**
结构处在确认后的观察段。

**2. 【防守看门狗】**
只在 12.80、11.90、10.80 这些结构价内有效。

**3. 【推演与应对沙盘】**
这里引用 13.27 作为 AI 自己推演出的观察价。仅供参考，不构成投资建议。""",
    }
    gate_output, gate = verify_ai_reasoning(output, transcript)

    assert gate.status == "PASS"
    response = reasoning_orchestrator._response_from_output(
        gate_output,
        transcript,
        gate,
        ModelRoute(model_name="deepseek-test"),
    )

    assert gate_output is not None
    assert response.gate_status == "PASS"
    assert "13.27" in response.coach_filtered_md
    assert response.raw_reasoning_md == "内部文本"
    assert response.fallback_reason is None
    assert response.semantic_filter_violations == []


def test_verifier_keeps_output_without_calling_llm_twice(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()

    async def fake_get_radar(symbol, user_id=1, include_structure=False):
        assert include_structure is True
        return {"status": "success", "data": make_radar_contract()}

    llm = ContractOnlyLLM()
    monkeypatch.setattr(reasoning_orchestrator.radar_api, "get_radar", fake_get_radar)
    monkeypatch.setattr(reasoning_orchestrator.config, "AI_NATIVE_RADAR_WRITE_SNAPSHOTS", False)

    response = asyncio.run(
        reasoning_orchestrator.build_ai_native_reasoning(
            symbol="sh600519",
            user_id=1,
            llm_service=llm,
        )
    )

    assert response.gate_status == "PASS"
    assert llm.calls == 1
    assert "13.27" in response.coach_filtered_md
    assert response.raw_reasoning_md
    assert response.semantic_filter_violations == []


def test_new_route_success_does_not_call_old_radar_deduce(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    monkeypatch.setattr(agent.config, "AI_NATIVE_RADAR_ENABLED", True)
    monkeypatch.setattr(reasoning_orchestrator.config, "AI_NATIVE_RADAR_WRITE_SNAPSHOTS", False)

    async def fake_get_radar(symbol, user_id=1, include_structure=False):
        assert include_structure is True
        return {"status": "success", "data": make_radar_contract()}

    monkeypatch.setattr(reasoning_orchestrator.radar_api, "get_radar", fake_get_radar)
    monkeypatch.setattr(agent, "_llm_service", BrokenLLM())

    response = asyncio.run(
        agent.ai_native_radar(
            agent.AINativeRadarRequest(symbol="sh600519", user_id=1)
        )
    )

    assert response["status"] == "success"
    assert response["data"]["gate_status"] == "PASS"
    assert response["data"]["generated_at"]
    assert "coach_filtered_md" in response["data"]
    assert {item["agent_id"] for item in response["data"]["agent_observations"]} == {
        "structure_agent",
        "divergence_agent",
        "key_level_agent",
        "path_scorer_agent",
        "coach_agent",
    }
    assert response["data"]["key_boundaries"]["confirm"][0]["value"] == 12.8


def test_old_radar_module_does_not_import_ai_native(monkeypatch):
    for name in list(sys.modules):
        if name.startswith("server.engines.ai_native"):
            sys.modules.pop(name)

    importlib.import_module("server.api.radar")

    assert not any(name.startswith("server.engines.ai_native") for name in sys.modules)
