import asyncio
import importlib
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import agent
from server.db import database
from server.engines.ai_native import reasoning_orchestrator


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
    async def infer_ai_native_radar(self, *_args, **_kwargs):
        raise RuntimeError("offline")


def test_ai_native_failure_writes_only_new_table(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()

    async def fake_get_radar(symbol, user_id=1):
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


def test_new_route_success_does_not_call_old_radar_deduce(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    monkeypatch.setattr(agent.config, "AI_NATIVE_RADAR_ENABLED", True)
    monkeypatch.setattr(reasoning_orchestrator.config, "AI_NATIVE_RADAR_WRITE_SNAPSHOTS", False)

    async def fake_get_radar(symbol, user_id=1):
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


def test_old_radar_module_does_not_import_ai_native(monkeypatch):
    for name in list(sys.modules):
        if name.startswith("server.engines.ai_native"):
            sys.modules.pop(name)

    importlib.import_module("server.api.radar")

    assert not any(name.startswith("server.engines.ai_native") for name in sys.modules)
