import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.api import agent
from server.db import database
from server.engines.ai_native.case_memory import save_reasoning_run
from server.engines.ai_native.observation import (
    list_reasoning_runs,
    pending_runs_for_auto_settlement,
    review_reasoning_run,
    settle_reasoning_run_with_radar,
    summarize_observation,
)
from server.engines.ai_native.schemas import (
    AIReasoningOutput,
    GateResult,
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
    md = f"""**1. 【全局语境定性】**
结构处在确认后的观察段，复盘标签 {current}。

**2. 【防守看门狗】**
只在 12.80、11.90 这些结构价内有效。

**3. 【推演与应对沙盘】**
只看分类和边界。仅供参考，不构成投资建议。"""
    return AIReasoningOutput(
        raw_reasoning_md=md,
        coach_filtered_md=md,
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
        actual_hypothesis="UNKNOWN",
        quality_score=9,
        notes="分类和边界清楚",
        outcome_path="B_OSCILLATION",
    )

    assert reviewed.replay_status == "REVIEWED"
    assert reviewed.replay_score == 93.0
    assert reviewed.outcome["matched"] is None
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
                actual_hypothesis="UNKNOWN",
                quality_score=8,
                notes="人工复盘通过",
            ),
        )
    )
    assert reviewed["data"]["replay_status"] == "REVIEWED"

    summary = asyncio.run(agent.ai_native_radar_observation_summary(user_id=7))
    assert summary["status"] == "success"
    assert summary["data"]["reviewed_runs"] == 1


def test_auto_settlement_reviews_pending_run_from_current_radar(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    run_id = seed_run(user_id=1, current="B")

    pending = pending_runs_for_auto_settlement(user_id=1, limit=10, today="2026-04-30", force=True)
    assert pending[0]["id"] == run_id

    reviewed = settle_reasoning_run_with_radar(
        run_row=pending[0],
        current_radar_data={
            "freshness": {"is_stale": False},
            "algorithm_v2": {"current_scenario_id": "B"},
        },
    )

    assert reviewed.replay_status == "REVIEWED"
    assert reviewed.outcome["reviewer"] == "auto"
    assert reviewed.outcome["actual_hypothesis"] == "B"
    assert reviewed.outcome["matched"] is None
    assert "UNMATCHED" not in reviewed.outcome["tags"]
    assert reviewed.outcome["sample_quality"] == "LOW"
    assert reviewed.outcome["learning_weight"] == 0.15


def test_auto_settlement_marks_complete_structure_sample_as_high_quality(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    transcript = make_transcript()
    transcript.structure_snapshot.chart_alignment.status = "ALIGNED"
    run_id = save_reasoning_run(
        user_id=1,
        symbol="sh.600519",
        mode="EMPTY",
        prompt_version="ai_native_radar.v1",
        model_name="deepseek-chat",
        transcript=transcript,
        memory_context=SimilarCaseSummary(),
        ai_output=make_output(current="A"),
        gate_result=GateResult(status="PASS", score=100, violations=[]),
    )
    pending = pending_runs_for_auto_settlement(user_id=1, limit=10, today="2026-04-30", force=True)
    run = next(item for item in pending if item["id"] == run_id)

    reviewed = settle_reasoning_run_with_radar(
        run_row=run,
        current_radar_data={
            "freshness": {"is_stale": False},
            "algorithm_v2": {"current_scenario_id": "C"},
        },
    )

    assert reviewed.outcome["sample_quality"] == "HIGH"
    assert reviewed.outcome["learning_weight"] == 1.0


def test_auto_settlement_api_batches_pending_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    run_id = seed_run(user_id=9, symbol="sz.300394", current="A")

    async def fake_get_radar(symbol, user_id=1, include_structure=False):
        return {
            "status": "success",
            "data": {
                "symbol": symbol,
                "freshness": {"is_stale": False},
                "algorithm_v2": {"current_scenario_id": "C"},
            },
        }

    monkeypatch.setattr(agent.radar_api, "get_radar", fake_get_radar)

    result = asyncio.run(
        agent.auto_settle_ai_native_radar_runs(
            agent.AINativeRadarAutoSettleRequest(user_id=9, limit=10, force=True)
        )
    )

    assert result["data"]["settled"] == 1
    assert result["data"]["runs"][0]["id"] == run_id
    assert result["data"]["runs"][0]["outcome"]["actual_hypothesis"] == "C"
    assert result["data"]["runs"][0]["outcome"]["matched"] is None
    assert result["data"]["runs"][0]["outcome"]["sample_quality"] == "LOW"
    assert result["data"]["runs"][0]["outcome"]["learning_weight"] == 0.15
