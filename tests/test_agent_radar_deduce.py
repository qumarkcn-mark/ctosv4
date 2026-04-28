"""Agent Radar deduction should consume the Radar contract, not legacy matrix."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import agent


def make_radar_contract() -> dict:
    level = {
        "level": "day",
        "price": 10.0,
        "zg": 9.8,
        "zd": 9.2,
        "state": "UPWARD_LEAVING",
        "patterns": ["底背驰"],
        "zoushi_type": {"type": "盘整", "zs_count": 1},
        "classifications": [],
        "active_zhongshu": {"zg": 9.8, "zd": 9.2},
    }
    deduction = {
        "status": "WAITING_TRIGGER",
        "summary": "日线和30分支持观察，等待5分买点形成",
        "path_thesis": {
            "title": "等待5分触发",
            "boundaries": [
                {"label": "30分ZG", "price": 9.8, "meaning": "跌破后推演失效"}
            ],
        },
        "complete_classification": [
            {
                "id": "A_CONFIRM",
                "code": "A",
                "label": "确认",
                "title": "回落止住，路径转强",
                "summary": "短级别出现确认事件",
                "trigger_if": ["5分出现新的买点确认事件"],
                "state": "WAITING",
            },
            {
                "id": "B_EXTEND",
                "code": "B",
                "label": "延长",
                "title": "没有确认，也没有破坏",
                "summary": "走势继续震荡或等待",
                "trigger_if": ["价格仍守住30分ZG"],
                "state": "CURRENT",
            },
            {
                "id": "C_INVALID",
                "code": "C",
                "label": "失效",
                "title": "关键边界被破坏",
                "summary": "跌破结构边界或出现反向风险",
                "trigger_if": ["跌破30分ZG"],
                "state": "WAITING",
            },
        ],
    }
    return {
        "api_version": "radar.v1",
        "symbol": "sh.600519",
        "mode": "EMPTY",
        "structure": {
            "levels": {
                "week": {**level, "level": "week"},
                "day": level,
                "60": {**level, "level": "60"},
                "30": {**level, "level": "30"},
                "15": {**level, "level": "15"},
                "5": {**level, "level": "5"},
            },
            "systems": {
                "short_term": {"interval_nesting": None},
                "swing": {"interval_nesting": None},
            },
        },
        "strategy": {"strategy_type": "观察中", "name": "观察中"},
        "entry_plan": {"title": "空仓入场观察", "conditions": [], "targets": []},
        "holding_plan": None,
        "deduction": deduction,
        "freshness": {"is_stale": False},
        "structure_config": {"preset": "live_tolerant"},
    }


def test_radar_deduce_uses_radar_contract_and_algorithmic_fallback(monkeypatch):
    async def fake_get_radar(symbol, **kwargs):
        return {"status": "success", "data": make_radar_contract()}

    async def fake_market_context(symbol):
        return {"symbol": symbol}

    class FakeLLM:
        async def infer_radar_deduction(self, system_prompt, context_json):
            assert '"radar_contract"' in context_json
            return {
                "diagnosis": "推演引擎异常: offline",
                "account_status": {"is_holding": False},
                "pre_plans": [{"plan_name": "系统故障", "trigger": "后台报错", "deduction": "-", "machine_action": "-", "color": "🟡"}],
                "core_defense": None,
                "market_context_verdict": None,
            }

    class FakeConn:
        def execute(self, *args, **kwargs):
            return None

        def commit(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(agent.radar_api, "get_radar", fake_get_radar)
    monkeypatch.setattr(agent, "get_market_context", fake_market_context)
    monkeypatch.setattr(agent, "_llm_service", FakeLLM())
    monkeypatch.setattr(agent, "get_connection", lambda: FakeConn())

    response = asyncio.run(agent.radar_deduce(agent.InferenceRequest(symbol="sh600519")))

    assert response["status"] == "success"
    data = response["data"]
    assert data["diagnosis"] == "等待5分触发"
    assert [plan["color"] for plan in data["pre_plans"]] == ["🟢", "🟡", "🔴"]
    assert "30分ZG 9.80" in data["core_defense"]


def test_portfolio_structure_summary_uses_radar_contract_shape():
    summary = agent._portfolio_structure_summary_from_radar(make_radar_contract())

    assert summary["source"] == "radar.v1"
    assert summary["day_state"] == "UPWARD_LEAVING"
    assert summary["m30_state"] == "UPWARD_LEAVING"
    assert summary["patterns"] == ["底背驰"]
    assert summary["status"] == "WAITING_TRIGGER"
