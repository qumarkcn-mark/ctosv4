"""Rotation API resilience tests."""

import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import rotation


def test_analyze_one_returns_fallback_on_timeout(monkeypatch):
    async def slow_matrix(symbol, user_id=1):
        await asyncio.sleep(0.05)
        return {}

    monkeypatch.setattr(rotation, "ROTATION_ANALYSIS_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(rotation, "_load_rotation_matrix", slow_matrix)

    item = asyncio.run(
        rotation._analyze_one(
            {"symbol": "sh600519", "name": "贵州茅台"},
            is_holding=False,
        )
    )

    assert item["symbol"] == "sh600519"
    assert item["structure_summary"]["error"] == "analysis timeout"
    assert item["plans"][0]["disclaimer"] == rotation.RISK_DISCLAIMER


def test_rotation_compass_returns_pending_items_after_global_timeout(monkeypatch):
    monkeypatch.setattr(rotation, "ROTATION_TOTAL_TIMEOUT_SECONDS", 0.01)

    def fake_fetch_rows(user_id):
        return [], [{"symbol": "sh600519", "name": "贵州茅台"}]

    async def slow_analyze(row, is_holding, semaphore=None):
        await asyncio.sleep(0.05)
        return {}

    monkeypatch.setattr(rotation, "_fetch_rows", fake_fetch_rows)
    monkeypatch.setattr(rotation, "_analyze_one", slow_analyze)

    response = asyncio.run(rotation.rotation_compass(user_id=1))

    candidates = response["data"]["candidates"]
    assert response["status"] == "success"
    assert candidates[0]["symbol"] == "sh600519"
    assert candidates[0]["structure_summary"]["error"] == "analysis timeout"


def test_radar_contract_to_score_matrix_uses_public_level_keys():
    radar_data = {
        "api_version": "radar.v1",
        "structure": {
            "levels": {
                "day": {
                    "level": "day",
                    "price": 10,
                    "state": "UPWARD_LEAVING",
                    "patterns": ["趋势底背驰"],
                    "zoushi_type": {"type": "盘整"},
                    "active_zhongshu": {"zg": 11, "zd": 9},
                },
                "30": {
                    "level": "30",
                    "price": 10.3,
                    "state": "IN_CENTER_OSC",
                    "patterns": ["二买确认"],
                    "zoushi_type": {"type": "盘整"},
                    "active_zhongshu": {"zg": 10.8, "zd": 9.9},
                },
                "5": {"level": "5", "price": 10.1, "state": "UNKNOWN"},
            },
            "systems": {
                "short_term": {"interval_nesting": {"depth": 2, "direction": "bottom"}}
            },
        },
        "deduction": {
            "summary": "等待确认",
            "path_thesis": {"boundaries": [{"price": 9.9}]},
            "complete_classification": [
                {"code": "A", "title": "向上确认", "summary": "继续观察。"}
            ],
        },
        "strategy": {"strategy_type": "观察中"},
    }

    matrix = rotation._radar_contract_to_score_matrix(radar_data)

    assert [level["level"] for level in matrix["matrix_a"]] == ["day", "m30", "m5"]
    assert matrix["matrix_a"][1]["zg"] == 10.8
    assert matrix["interval_nesting_a"]["depth"] == 2
    assert matrix["forward_analysis_a"]["forward_classes"][0]["stop_loss"] == 9.9
