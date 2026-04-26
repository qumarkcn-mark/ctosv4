"""Rotation API resilience tests."""

import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import rotation


def test_analyze_one_returns_fallback_on_timeout(monkeypatch):
    async def slow_matrix(symbol):
        await asyncio.sleep(0.05)
        return {}

    monkeypatch.setattr(rotation, "ROTATION_ANALYSIS_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(rotation, "analyze_matrix_state", slow_matrix)

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
