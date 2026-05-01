import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.ai_native.scoring import primary_path, score_paths
from server.engines.ai_native.transcript_compiler import compile_structure_transcript


def radar_contract():
    return {
        "symbol": "sh.600519",
        "mode": "EMPTY",
        "freshness": {"is_stale": False},
        "structure": {
            "levels": {
                "day": {
                    "level": "day",
                    "price": 12.3,
                    "state": "UPWARD_LEAVING",
                    "zg": 11.9,
                    "zd": 10.8,
                    "patterns": ["底背驰"],
                },
                "30": {
                    "level": "30",
                    "price": 12.3,
                    "state": "WAITING_FOR_PULLBACK",
                    "zg": 11.8,
                    "zd": 11.2,
                },
                "5": {
                    "level": "5",
                    "price": 12.3,
                    "state": "IN_CENTER_OSC",
                    "zg": 12.8,
                    "zd": 11.9,
                },
            }
        },
        "algorithm_v2": {
            "path": "UPWARD_MAJOR_WAVE",
            "phase": "BREAKOUT_EXTENSION",
            "current_scenario_id": "B",
            "boundaries": {
                "confirm": [{"label": "历史前高", "value": 12.8}],
                "maintain": [{"label": "观察区间下沿", "value": 11.9}],
                "invalidate": [{"label": "短线失效", "value": 11.9}],
                "support": [{"label": "大级别防线", "value": 10.8}],
            },
        },
    }


def _score_map(transcript):
    scores = score_paths(transcript)
    return {item.id: item.score for item in scores}


def test_score_paths_does_not_promote_a_without_divergence():
    transcript = compile_structure_transcript(radar_contract())
    scores = _score_map(transcript)

    assert scores["B"] > scores["A"]
    assert scores["B"] >= scores["C"]


def test_score_paths_keeps_low_level_divergence_as_observation():
    contract = radar_contract()
    contract["structure"]["levels"]["30"].update(
        {
            "price": 10.6,
            "zg": 11.8,
            "zd": 11.2,
            "dd": 10.8,
        }
    )
    contract["structure"]["levels"]["5"]["patterns"] = ["底背驰"]
    transcript = compile_structure_transcript(contract)
    scores = _score_map(transcript)

    assert primary_path(score_paths(transcript)) == "B"
    assert scores["B"] > scores["A"]
    assert scores["C"] >= scores["A"]


def test_score_paths_signal_only_buy_candidate_explains_repeated_divergence():
    contract = radar_contract()
    contract["structure"]["levels"]["30"].update(
        {
            "price": 10.75,
            "zg": 11.8,
            "zd": 11.2,
            "dd": 10.8,
        }
    )
    contract["structure"]["levels"]["5"]["patterns"] = ["底背驰"]
    transcript = compile_structure_transcript(contract)
    scores = _score_map(transcript)

    assert transcript.divergence_context.chain_status == "ALIGNING"
    assert transcript.divergence_context.buy_sell_candidate.status == "SIGNAL_ONLY"
    assert primary_path(score_paths(transcript)) == "B"
    assert scores["B"] > scores["A"]


def test_score_paths_promotes_confirmed_support():
    contract = radar_contract()
    contract["structure"]["levels"]["30"].update(
        {
            "price": 11.5,
            "zg": 11.8,
            "zd": 11.2,
            "dd": 10.8,
        }
    )
    contract["structure"]["levels"]["5"]["patterns"] = ["底背驰"]
    transcript = compile_structure_transcript(contract)

    assert primary_path(score_paths(transcript)) == "A"
    assert any(signal.status == "CONFIRMED" for signal in transcript.divergence_context.lower_level_signals)


def test_score_paths_promotes_third_buy_candidate_from_structure_facts():
    contract = radar_contract()
    contract["structure"]["levels"]["30"]["patterns"] = ["三买确认"]
    contract["structure"]["levels"]["5"]["patterns"] = ["底背驰"]
    transcript = compile_structure_transcript(contract)
    scores = _score_map(transcript)

    assert transcript.divergence_context.buy_sell_candidate.kind == "THIRD_CONFIRM"
    assert transcript.divergence_context.buy_sell_candidate.status == "CONFIRMED"
    assert primary_path(score_paths(transcript)) == "A"
    assert scores["A"] > scores["B"]


def test_score_paths_promotes_failed_divergence_to_c():
    contract = radar_contract()
    contract["structure"]["levels"]["5"]["patterns"] = ["底背驰失效"]
    transcript = compile_structure_transcript(contract)
    scores = _score_map(transcript)

    assert primary_path(score_paths(transcript)) == "C"
    assert scores["C"] > scores["B"]


def test_score_paths_stale_data_stops_strong_deduction():
    contract = radar_contract()
    contract["freshness"] = {"is_stale": True}
    transcript = compile_structure_transcript(contract)

    assert primary_path(score_paths(transcript, gate_status="FALLBACK")) == "D"


def test_score_paths_structure_snapshot_gaps_reduce_directional_deduction():
    contract = radar_contract()
    contract["structure"]["levels"].pop("30")
    transcript = compile_structure_transcript(contract)
    scores = _score_map(transcript)

    assert scores["D"] > 10
    assert scores["A"] < scores["B"]


def test_score_paths_partial_chart_alignment_reduces_directional_deduction():
    contract = radar_contract()
    contract["structure"]["levels"]["30"].update(
        {
            "price": 11.5,
            "zg": 11.8,
            "zd": 11.2,
            "dd": 10.8,
            "bi_count": 3,
            "bis": [{"is_up": False}],
        }
    )
    contract["structure"]["levels"]["5"]["patterns"] = ["底背驰"]
    transcript = compile_structure_transcript(contract)
    scores = _score_map(transcript)

    assert transcript.structure_snapshot.chart_alignment.status == "PARTIAL"
    assert primary_path(score_paths(transcript)) == "B"
    assert scores["B"] > scores["A"]
    assert scores["D"] > 10


def test_score_paths_missing_chart_alignment_stops_strong_directional_deduction():
    contract = radar_contract()
    contract["structure"]["levels"] = {}
    transcript = compile_structure_transcript(contract)
    scores = _score_map(transcript)

    assert transcript.structure_snapshot.chart_alignment.status == "MISSING"
    assert primary_path(score_paths(transcript)) == "D"
    assert scores["D"] > scores["A"]
    assert scores["D"] > scores["C"]


def test_score_paths_price_structure_gap_stops_old_center_deduction():
    contract = radar_contract()
    contract["quote"] = {"price": 16.05}
    for level in contract["structure"]["levels"].values():
        level["price"] = 16.05
    transcript = compile_structure_transcript(contract)
    scores = score_paths(transcript)
    score_map = {item.id: item.score for item in scores}
    d_reason = next(item.reason for item in scores if item.id == "D")

    assert transcript.reasoning_evidence_pack["operative_context"]["structure_gap"] is True
    assert primary_path(scores) == "D"
    assert score_map["D"] > score_map["A"]
    assert "旧中枢边界" in d_reason


def test_score_paths_holding_near_risk_line_promotes_defense_paths():
    contract = radar_contract()
    contract["mode"] = "HOLDING"
    contract["position_context"] = {
        "is_holding": True,
        "avg_cost": 10.0,
        "current_price": 9.85,
        "pnl_pct": -6.0,
        "risk_flags": ["STRUCTURE_AGAINST_POSITION"],
    }
    contract["coach_action"] = {
        "risk_lines": [{"label": "风控边界", "price": 9.7, "distance_pct": -1.52}],
        "nearest_risk_line": {"label": "风控边界", "price": 9.7, "distance_pct": -1.52},
    }

    transcript = compile_structure_transcript(contract)
    scores = score_paths(transcript)
    score_map = {item.id: item.score for item in scores}
    c_reason = next(item.reason for item in scores if item.id == "C")

    assert score_map["C"] > score_map["A"]
    assert "持仓结构风险升高" in c_reason
    assert "贴近持仓风险线" in c_reason


def test_score_paths_uses_review_calibration_without_overriding_facts():
    transcript = compile_structure_transcript(radar_contract())
    scores = score_paths(
        transcript,
        calibration={
            "sample_count": 6,
            "outcome_counts": {"C": 5, "B": 1},
            "tag_counts": {"OVER_OPTIMISTIC": 3, "REPEATED_DIVERGENCE_RISK": 2},
        },
    )
    score_map = {item.id: item.score for item in scores}
    c_reason = next(item.reason for item in scores if item.id == "C")

    assert score_map["C"] > 25
    assert score_map["A"] < 25
    assert "历史复盘" in c_reason
