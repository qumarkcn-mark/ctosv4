import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.db import database
from server.engines.ai_native.case_memory import find_similar_cases, save_reasoning_run
from server.engines.ai_native.schemas import GateResult, SimilarCaseSummary, StructureTranscript


def test_case_memory_returns_empty_when_no_table(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "missing.db"))
    transcript = StructureTranscript(
        symbol="sh.600519",
        generated_at="2026-04-29T10:00:00+08:00",
        fingerprint_version="fingerprint.v1",
        structure_fingerprint="EMPTY|NO_CASE",
    )

    summary = find_similar_cases(transcript)

    assert summary == SimilarCaseSummary()


def test_save_and_find_similar_cases(monkeypatch, tmp_path):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ctos.db"))
    database.init_db()
    transcript = StructureTranscript(
        symbol="sh.600519",
        generated_at="2026-04-29T10:00:00+08:00",
        fingerprint_version="fingerprint.v1",
        structure_fingerprint="EMPTY|UPWARD_MAJOR_WAVE",
    )
    gate = GateResult(status="PASS", score=100, violations=[])

    run_id = save_reasoning_run(
        user_id=1,
        symbol="sh.600519",
        mode="EMPTY",
        prompt_version="ai_native_radar.v1",
        model_name="deepseek-chat",
        transcript=transcript,
        memory_context=SimilarCaseSummary(),
        ai_output=None,
        gate_result=gate,
    )
    assert run_id

    conn = sqlite3.connect(database.DB_PATH)
    conn.execute(
        "UPDATE ai_reasoning_runs SET ai_output_json = ?, outcome_json = ? WHERE id = ?",
        ('{"current_hypothesis":"B"}', '{"path":"B_OSCILLATION"}', run_id),
    )
    conn.commit()
    conn.close()

    summary = find_similar_cases(transcript)

    assert summary.similar_case_count == 1
    assert summary.common_outcomes[0]["path"] in {"B", "B_OSCILLATION"}

