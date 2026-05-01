import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import agent
from server.db import database
from server.engines.ai_native.case_memory import save_reasoning_run
from server.engines.ai_native.schemas import (
    AIReasoningOutput,
    AllowedPrice,
    ChartOverlayAlignment,
    GateResult,
    SimilarCaseSummary,
    StructureSnapshot,
    StructureTranscript,
)


COACH_MD = """**1. 【全局语境定性】**
高位震荡，观察 5 分钟边界。

**2. 【防守看门狗】**
只在 302.57 到 331.16 内有效。

**3. 【推演与应对沙盘】**
先看结构边界。仅供参考，不构成投资建议。"""


def test_ai_native_route_disabled_by_default(monkeypatch):
    monkeypatch.setattr(agent.config, "AI_NATIVE_RADAR_ENABLED", False)

    response = __import__("asyncio").run(
        agent.ai_native_radar(agent.AINativeRadarRequest(symbol="sh600519"))
    )

    assert response["status"] == "disabled"


def test_latest_ai_native_radar_run_backfills_dotless_symbol(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    run_id = save_reasoning_run(
        user_id=1,
        symbol="sh.603986",
        mode="HOLDING",
        prompt_version="ai_native_radar.v1",
        model_name="deepseek-v4-pro",
        transcript=StructureTranscript(
            symbol="sh.603986",
            generated_at="2026-04-30T15:00:00+08:00",
            fingerprint_version="fingerprint.v1",
            structure_fingerprint="TEST|603986",
            structure_snapshot=StructureSnapshot(
                chart_alignment=ChartOverlayAlignment(status="ALIGNED"),
            ),
            allowed_prices=[
                AllowedPrice(label="support", value=302.57, source="test"),
                AllowedPrice(label="pressure", value=331.16, source="test"),
            ],
        ),
        memory_context=SimilarCaseSummary(),
        ai_output=AIReasoningOutput(
            raw_reasoning_md=COACH_MD,
            coach_filtered_md=COACH_MD,
        ),
        gate_result=GateResult(status="PASS", score=100, violations=[]),
    )

    response = __import__("asyncio").run(
        agent.latest_ai_native_radar_run(user_id=1, symbol="sh603986", mode="HOLDING")
    )

    assert response["status"] == "success"
    assert response["data"]["run_id"] == run_id
    assert response["data"]["symbol"] == "sh.603986"
    assert response["data"]["gate_status"] == "PASS"
    assert "高位震荡" in response["data"]["coach_filtered_md"]
