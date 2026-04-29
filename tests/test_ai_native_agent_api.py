import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import agent


def test_ai_native_route_disabled_by_default(monkeypatch):
    monkeypatch.setattr(agent.config, "AI_NATIVE_RADAR_ENABLED", False)

    response = __import__("asyncio").run(
        agent.ai_native_radar(agent.AINativeRadarRequest(symbol="sh600519"))
    )

    assert response["status"] == "disabled"

