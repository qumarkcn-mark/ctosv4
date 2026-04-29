import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import agent
from server.db import database
from server.engines.ai_native.case_memory import save_reasoning_run
from server.engines.ai_native.observation import (
    list_reasoning_runs,
    review_reasoning_run,
    summarize_observation,
)
from server.engines.ai_native.schemas import (
    AIReasoningOutput,
    GateResult,
    Hypothesis,
    SimilarCaseSummary,
    StructureTranscript,
)


def make_transcript(symbol="sh.600519", fingerprint="EMPTY|OBSERVE"):
    return StructureTranscript(
        symbol=symbol,
        generated_at="2026-04-29T10:00:00+08:00",
        fingerprint_version="fingerprint.v1",
        structure_fingerprint=fingerprint,
    )


def make_output(current="B"):
    hypotheses = [
        Hypothesis(
            id=hypothesis_id,
            name=name,
            current_applicability="CURRENT" if hypothesis_id == current else "WAITING",
            evidence=["day state=UPWARD_LEAVING"],
            trigger="观察 12.80 的确认",
            invalidation="跌回 11.90 则原观察失效",
            next_focus="只盯结构边界",
            empty_position_view="空仓只等待结构确认",
            holding_position_view="持仓只按边界管理风险",
        )
        for hypothesis_id, name in (
            ("A", "向上确认"),
            ("B", "区间观察"),
            ("C", "失效路径"),
            ("D", "停止推演"),
        )
    ]
    return AIReasoningOutput(
        diagnosis="结构处在确认后的观察段",
        current_hypothesis=current,
        reasoning_boundary="只在结构价内有效",
        hypotheses=hypotheses,
        operator_mistake="把等待误判成方向",
        coach_talk="只看分类和边界。仅供参考，不构成投资建议",
        disclaimer="仅供参考，不构成投资建议",
    )


def seed_run(user_id=1, symbol="sh.600519", gate_status="PASS", gate_score=100, current="B"):
    gate = GateResult(status=gate_status, score=gate_score, violations=[])
    return save_reasoning_run(
        user_id=user_id,
        symbol=symbol,
        mode="EMPTY",
        prompt_version="ai_native_radar.v1",
        model_name="deepseek-chat",
        transcript=make_transcript(symbol=symbol),
        memory_context=SimilarCaseSummary(),
        ai_output=make_output(current=current),
        gate_result=gate,
    )


def test_observation_review_scores_and_summarizes(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    run_id = seed_run()
    seed_run(symbol="sz.300750", gate_status="FALLBACK", gate_score=40, current="UNKNOWN")

    pending = list_reasoning_runs(user_id=1, replay_status="PENDING")
    assert len(pending) == 2
    assert pending[0].replay_status == "PENDING"

    reviewed = review_reasoning_run(
        run_id=run_id,
        user_id=1,
        actual_hypothesis="B",
        quality_score=9,
        notes="分类和边界清楚",
        outcome_path="B_OSCILLATION",
    )

    assert reviewed.replay_status == "REVIEWED"
    assert reviewed.replay_score == 93.0
    assert reviewed.outcome["matched"] is True
    assert reviewed.outcome["path"] == "B_OSCILLATION"

    summary = summarize_observation(user_id=1)
    assert summary.total_runs == 2
    assert summary.reviewed_runs == 1
    assert summary.pending_runs == 1
    assert summary.fallback_runs == 1
    assert summary.ready_for_ui_beta is False
    assert "1/20" in summary.readiness_reason


def test_observation_review_penalizes_wrong_hypothesis(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    run_id = seed_run(gate_score=90, current="A")

    reviewed = review_reasoning_run(
        run_id=run_id,
        user_id=1,
        actual_hypothesis="C",
        quality_score=8,
        notes="方向分类错了",
    )

    assert reviewed.outcome["matched"] is False
    assert reviewed.replay_score == 63.0


def test_observation_api_lists_reviews_and_summarizes(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    run_id = seed_run(user_id=7)

    runs = asyncio.run(agent.list_ai_native_radar_runs(user_id=7, limit=10, symbol=None, replay_status=None))
    assert runs["status"] == "success"
    assert runs["data"][0]["id"] == run_id

    reviewed = asyncio.run(
        agent.review_ai_native_radar_run(
            run_id,
            agent.AINativeRadarReviewRequest(
                user_id=7,
                actual_hypothesis="B",
                quality_score=8,
                notes="人工复盘通过",
            ),
        )
    )
    assert reviewed["data"]["replay_status"] == "REVIEWED"

    summary = asyncio.run(agent.ai_native_radar_observation_summary(user_id=7))
    assert summary["status"] == "success"
    assert summary["data"]["reviewed_runs"] == 1
