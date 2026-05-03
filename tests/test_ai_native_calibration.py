import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.ai_native.calibration import (
    render_calibration_markdown,
    summarize_reasoning_response,
)
from server.engines.ai_native.schemas import (
    AIReasoningResponse,
    AgentObservation,
    AllowedPrice,
    ReasoningBoundaries,
)


def make_response():
    return AIReasoningResponse(
        gate_status="PASS",
        gate_score=92,
        generated_at="2026-04-30T13:40:00+08:00",
        coach_filtered_md="等待市场确认。仅供参考，不构成投资建议。",
        semantic_filter_status="PASS",
        agent_observations=[
            AgentObservation(
                agent_id="divergence_agent",
                verdict="LOWER_ONLY · FIRST_CANDIDATE · SIGNAL_ONLY",
                confidence=0.55,
                evidence=["5分钟有底背驰线索", "尚未回到30分钟中枢"],
                next_focus="只当低级别线索观察，等待枢纽边界确认，防止背了又背",
            ),
            AgentObservation(
                agent_id="key_level_agent",
                verdict="KEY_LEVEL_READY",
                confidence=0.8,
                next_focus="确认 12.8，失效 11.9",
            ),
            AgentObservation(
                agent_id="coach_agent",
                verdict="WAITING_MARKET",
                confidence=0.7,
                next_focus="当前只是低级别背驰线索，等待重新站回枢纽边界",
            ),
        ],
        key_boundaries=ReasoningBoundaries(
            confirm=[AllowedPrice(label="30分钟中枢上沿", value=12.8, source="center", level="30")],
            support=[AllowedPrice(label="30分钟中枢下沿", value=11.9, source="center", level="30")],
            invalidate=[AllowedPrice(label="日线防线", value=10.8, source="center", level="day")],
        ),
        coach_talk="等待市场确认。仅供参考，不构成投资建议。",
    )


def test_summarize_reasoning_response_keeps_calibration_fields():
    row = summarize_reasoning_response("sh600519", make_response())

    assert row["symbol"] == "sh600519"
    assert row["gate"]["status"] == "PASS"
    assert row["semantic_filter_status"] == "PASS"
    assert row["divergence"]["verdict"] == "LOWER_ONLY · FIRST_CANDIDATE · SIGNAL_ONLY"
    assert row["coach_next_focus"] == "当前只是低级别背驰线索，等待重新站回枢纽边界"
    assert row["boundaries"]["confirm"] == ["30 30分钟中枢上沿 12.8"]
    assert row["boundaries"]["observe"] == ["30 30分钟中枢下沿 11.9"]


def test_render_calibration_markdown_is_scan_friendly():
    row = summarize_reasoning_response("sh600519", make_response())
    report = render_calibration_markdown([row])

    assert "| 股票 | RUN | 门禁 | 语义过滤 | 背驰联动 | 下一步 | 关键价位 |" in report
    assert "sh600519" in report
    assert "LOWER_ONLY · FIRST_CANDIDATE · SIGNAL_ONLY" in report
    assert "确认: 30 30分钟中枢上沿 12.8" in report


def test_summarize_reasoning_response_deduplicates_price_labels():
    response = make_response()
    response.key_boundaries.invalidate = [
        AllowedPrice(label="30分ZD 1433", value=1433, source="center", level="30")
    ]

    row = summarize_reasoning_response("sh600519", response)

    assert row["boundaries"]["invalidate"] == ["30 30分ZD 1433"]
