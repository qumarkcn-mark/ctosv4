import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.ai_native.score_calibration import summarize_score_calibration


def test_summarize_score_calibration_counts_reviewed_outcomes_and_tags():
    summary = summarize_score_calibration([
        {
            "outcome_json": '{"actual_hypothesis":"C","tags":["OVER_OPTIMISTIC","REPEATED_DIVERGENCE_RISK"],"sample_quality":"HIGH","learning_weight":1.2}',
            "replay_score": 43.0,
        },
        {
            "outcome_json": '{"path":"C_INVALID","tags":["OVER_OPTIMISTIC"],"sample_quality":"LOW","learning_weight":0.15}',
            "replay_score": 63.0,
        },
        {
            "outcome_json": '{"actual_hypothesis":"B","tags":["MATCHED"],"sample_quality":"MEDIUM","learning_weight":0.55}',
            "replay_score": 93.0,
        },
    ])

    assert summary["sample_count"] == 3
    assert summary["effective_sample_weight"] == 1.9
    assert summary["outcome_counts"]["C"] == 1.35
    assert summary["outcome_counts"]["B"] == 0.55
    assert summary["tag_counts"]["OVER_OPTIMISTIC"] == 1.35
    assert summary["quality_counts"] == {"HIGH": 1, "LOW": 1, "MEDIUM": 1}
    assert summary["average_replay_score"] == 66.33
