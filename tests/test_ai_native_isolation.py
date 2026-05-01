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
    }


class BrokenLLM:
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


class GateBypassLLM(UserAwareLLM):
    async def infer_ai_native_markdown(self, *_args, **kwargs):
        await super().infer_ai_native_markdown(*_args, **kwargs)
        return "突破 13.27 就买入，A路径继续。仅供参考，不构成投资建议"


class RewriteLLM(UserAwareLLM):
    async def infer_ai_native_markdown(self, *_args, **kwargs):
        await super().infer_ai_native_markdown(*_args, **kwargs)
        return """**1. 【全局语境定性】**
结构处在确认后的观察段。

**2. 【防守看门狗】**
只在 12.80、11.90、10.80 这些结构价内有效。

**3. 【推演与应对沙盘】**
这里出现做空表述，门禁应提示但不能触发二次 LLM 调用。仅供参考，不构成投资建议。"""


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

    assert response.gate_status == "FALLBACK"

    conn = sqlite3.connect(database.DB_PATH)
    ai_count = conn.execute("SELECT COUNT(*) FROM ai_reasoning_runs").fetchone()[0]
    old_count = conn.execute("SELECT COUNT(*) FROM radar_deductions").fetchone()[0]
    conn.close()
    assert ai_count == 1
    assert old_count == 0


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

    assert response.gate_status == "FALLBACK"
    assert "近端分钟结构缺失" in response.coach_filtered_md
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
    payload = llm.contexts[0]["evidence_pack"]
    assert "levels" in payload
    assert "operative_context" in payload
    assert "semantic_assertions" in payload
    assert "basic_anchors" in payload
    assert "divergence_context" not in payload
    assert "agent_observations" not in payload
    assert "structure_snapshot" not in payload
    assert "rule_engine_observations" not in payload
    assert "state" not in payload["levels"]["30"]
    assert response.model_route.tier in {"simple", "hard", "calibration"}
    assert response.model_route.model_name


def test_rewrite_gate_keeps_ai_reasoning_visible():
    transcript = compile_structure_transcript(make_radar_contract())
    output = {
        "raw_reasoning_md": "内部文本",
        "coach_filtered_md": """**1. 【全局语境定性】**
结构处在确认后的观察段。

**2. 【防守看门狗】**
只在 12.80、11.90、10.80 这些结构价内有效。

**3. 【推演与应对沙盘】**
当前做空会破坏 A 股普通股票纪律。仅供参考，不构成投资建议。""",
    }
    gate_output, gate = verify_ai_reasoning(output, transcript)

    assert gate.status == "REWRITE"
    response = reasoning_orchestrator._response_from_output(
        gate_output,
        transcript,
        gate,
        ModelRoute(model_name="deepseek-test"),
    )

    assert response.gate_status == "REWRITE"
    assert "做空" in response.coach_filtered_md
    assert response.fallback_data is None
    assert response.fallback_reason.startswith("门禁提示")


def test_rewrite_gate_does_not_call_llm_twice(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()

    async def fake_get_radar(symbol, user_id=1, include_structure=False):
        assert include_structure is True
        return {"status": "success", "data": make_radar_contract()}

    llm = RewriteLLM()
    monkeypatch.setattr(reasoning_orchestrator.radar_api, "get_radar", fake_get_radar)
    monkeypatch.setattr(reasoning_orchestrator.config, "AI_NATIVE_RADAR_WRITE_SNAPSHOTS", False)

    response = asyncio.run(
        reasoning_orchestrator.build_ai_native_reasoning(
            symbol="sh600519",
            user_id=1,
            llm_service=llm,
        )
    )

    assert response.gate_status == "REWRITE"
    assert llm.calls == 1
    assert "做空" in response.coach_filtered_md


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
    assert response["data"]["gate_status"] == "FALLBACK"
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
