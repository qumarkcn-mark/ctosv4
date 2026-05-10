import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.ai_native.transcript_compiler import compile_structure_transcript


def radar_contract():
    return {
        "symbol": "sh.600519",
        "mode": "EMPTY",
        "freshness": {"is_stale": False},
        "structure_kernel": {
            "version": "structure_kernel.v1",
            "profile": "fast",
            "structure_fingerprint": "kernel:sh.600519:fast:abc123",
            "facts_digest": {"path": "UPWARD_MAJOR_WAVE"},
            "data_quality": {"is_stale": False},
        },
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
                "week": {
                    "level": "week",
                    "price": 12.3,
                    "state": "RANGE",
                    "zg": 13.5,
                    "zd": 8.8,
                },
                "30": {
                    "level": "30",
                    "price": 12.3,
                    "state": "WAITING_FOR_PULLBACK",
                    "zg": 11.8,
                    "zd": 11.2,
                    "bi_count": 2,
                    "bis": [{"is_up": False}, {"is_up": True}],
                    "bi_zhongshus": [{"zg": 11.8, "zd": 11.2}],
                },
                "5": {
                    "level": "5",
                    "price": 12.3,
                    "state": "IN_CENTER_OSC",
                    "zg": 12.8,
                    "zd": 11.9,
                    "bsps": [{"type": "2买", "price": 11.95}],
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


def test_compile_structure_transcript_collects_allowed_prices_and_fingerprint():
    transcript = compile_structure_transcript(radar_contract())

    assert transcript.symbol == "sh.600519"
    assert transcript.mode == "EMPTY"
    assert transcript.fingerprint_version == "fingerprint.v2"
    assert "UPWARD_MAJOR_WAVE" in transcript.structure_fingerprint
    assert {level.role for level in transcript.levels} == {"L0", "L1", "L2"}

    prices = {round(item.value, 2) for item in transcript.allowed_prices}
    assert {12.3, 12.8, 11.95, 11.9, 10.8}.issubset(prices)
    assert transcript.reasoning_boundaries.confirm[0].value == 12.8
    assert transcript.divergence_context.pivot_level == "30"
    assert transcript.divergence_context.alignment == "NO_DIVERGENCE"
    assert "NO_DIVERGENCE" in transcript.structure_fingerprint
    assert "CHART_ALIGNED" in transcript.structure_fingerprint
    assert "CHART_WARN_NONE" in transcript.structure_fingerprint
    assert transcript.structure_snapshot.version == "structure_snapshot.v1"
    assert transcript.structure_snapshot.available_levels == ["week", "day", "30", "5"]
    assert transcript.structure_snapshot.levels[0].role == "macro"
    assert transcript.structure_snapshot.levels[2].center.zd == 11.2
    assert transcript.structure_snapshot.key_boundaries.confirm[0].value == 12.8
    assert transcript.structure_snapshot.chart_alignment.status == "ALIGNED"
    chart_30 = next(item for item in transcript.structure_snapshot.chart_alignment.levels if item.level == "30")
    assert chart_30.counts["bi"] == 2
    assert chart_30.counts["bi_center"] == 1
    chart_5 = next(item for item in transcript.structure_snapshot.chart_alignment.levels if item.level == "5")
    assert chart_5.recent_buy_sell_points[0]["type"] == "2买"
    assert transcript.structure_snapshot.consistency_warnings == []
    assert {item.agent_id for item in transcript.agent_observations} == {
        "structure_agent",
        "divergence_agent",
        "key_level_agent",
        "path_scorer_agent",
        "coach_agent",
    }
    assert transcript.agent_observations[0].verdict == "结构事实可用于推演"
    assert transcript.reasoning_evidence_pack["version"] == "reasoning_evidence_pack.v1"
    assert transcript.reasoning_evidence_pack["levels"]["30"]["price_vs_center"]["position"] == "above_zg"
    assert transcript.reasoning_evidence_pack["structure_kernel"]["structure_fingerprint"] == "kernel:sh.600519:fast:abc123"
    assert transcript.reasoning_evidence_pack["structure_kernel"]["facts_digest"]["path"] == "UPWARD_MAJOR_WAVE"


def test_reasoning_evidence_pack_marks_high_extension_not_standard_third_sell():
    contract = radar_contract()
    contract["symbol"] = "sz.002176"
    contract["quote"] = {"price": 16.05, "low": 15.8, "high": 16.97}
    contract["structure"]["levels"]["30"].update(
        {
            "price": 16.05,
            "state": "UPWARD_LEAVING",
            "zd": 10.68,
            "zg": 11.06,
            "dd": 10.03,
            "gg": 11.42,
            "active_zhongshu": {
                "zd": 10.68,
                "zg": 11.06,
                "dd": 10.03,
                "gg": 11.42,
                "begin_date": "2026-03-30 10:00:00",
                "end_date": "2026-04-17 10:00:00",
            },
            "bis": [
                {"x0": "2026-04-29 10:00:00", "y0": 13.77, "x1": "2026-04-30 10:00:00", "y1": 16.95, "is_up": True},
                {"x0": "2026-04-30 10:00:00", "y0": 16.95, "x1": "2026-04-30 14:00:00", "y1": 15.88, "is_up": False},
            ],
            "patterns": ["1卖", "二买"],
            "bsps": [
                {"type": "1p", "is_buy": False, "time": "2026-04-30 10:00:00", "price": 16.95},
                {"type": "2", "is_buy": True, "time": "2026-04-30 14:00:00", "price": 15.88},
            ],
        }
    )

    transcript = compile_structure_transcript(contract)
    pack = transcript.reasoning_evidence_pack
    level30 = pack["levels"]["30"]
    assertions = {item["claim"]: item for item in pack["semantic_assertions"]}

    assert level30["price_vs_center"]["position"] == "above_zg"
    assert level30["recent_bsp_events"][-2]["type"] == "1p"
    assert "not_standard_30_third_sell" in assertions
    operative = pack["operative_context"]
    assert operative["current_zone"] == "between_nearest_support_and_resistance"
    assert any(item["price"] == 15.88 for item in operative["immediate_supports"])
    assert any(item["price"] == 16.95 for item in operative["immediate_resistances"])
    assert any(item["price"] == 11.06 for item in operative["deep_references"])
    commander = pack["commander_context"]
    assert commander["primary_context"]["code"] == "EXTREME_ABOVE_ALL_STRUCTURES"
    assert commander["must_use_levels"]["support"]["price"] == 15.8
    assert commander["must_use_levels"]["support"]["source"] == "intraday_low"
    assert commander["must_use_levels"]["deep_support"]["price"] in {12.8, 11.9, 11.06}
    assert any(item["claim"] == "STRUCTURE_GAP_DYNAMIC_DEFENSE_REQUIRED" for item in commander["semantic_assertions"])
    allowed = {round(item.value, 2) for item in transcript.allowed_prices}
    assert {15.88, 16.95}.issubset(allowed)
    assert transcript.divergence_context.buy_sell_candidate.kind != "THIRD_SELL_CONFIRM"


def test_above_center_third_sell_pattern_is_risk_not_confirmed_structure():
    contract = radar_contract()
    contract["structure"]["levels"]["30"].update(
        {
            "price": 16.05,
            "state": "UPWARD_LEAVING",
            "zd": 10.68,
            "zg": 11.06,
            "dd": 10.03,
            "gg": 11.42,
            "patterns": ["三卖"],
        }
    )

    transcript = compile_structure_transcript(contract)
    candidate = transcript.divergence_context.buy_sell_candidate

    assert transcript.divergence_context.pivot_position == "ABOVE_GG"
    assert candidate.side == "SELL"
    assert candidate.kind == "FIRST_SELL_RISK"
    assert candidate.status == "WAITING_CONFIRM"


def test_compile_structure_transcript_handles_stale_and_missing_structure():
    contract = {
        "symbol": "sz.000001",
        "mode": "HOLDING",
        "freshness": {"is_stale": True},
        "structure": {"levels": {}},
        "position_context": {"is_holding": True, "cost": 10.0, "pnl_percentage": -12.0},
    }

    transcript = compile_structure_transcript(contract)

    assert transcript.stale is True
    assert transcript.mode == "HOLDING"
    assert transcript.position_context
    assert transcript.position_context.cost == 10.0
    assert all(level.raw_state == "UNKNOWN" for level in transcript.levels)
    assert transcript.divergence_context.alignment == "NO_DIVERGENCE"
    assert "missing_week_level" in transcript.structure_snapshot.consistency_warnings
    assert "missing_pivot_center" in transcript.structure_snapshot.consistency_warnings
    assert transcript.structure_snapshot.chart_alignment.status == "MISSING"
    assert "missing_chart_level:30" in transcript.structure_snapshot.chart_alignment.warnings
    assert "CHART_MISSING" in transcript.structure_fingerprint
    assert "missing_chart_level:30" in transcript.structure_fingerprint
    structure_agent = next(item for item in transcript.agent_observations if item.agent_id == "structure_agent")
    assert structure_agent.verdict == "结构事实不完整"
    assert "missing_pivot_center" in structure_agent.blocks


def test_compile_structure_transcript_carries_holding_risk_lines_as_facts():
    contract = radar_contract()
    contract["mode"] = "HOLDING"
    contract["position_context"] = {
        "is_holding": True,
        "label": "持仓观察",
        "quantity": 1000,
        "avg_cost": 10.0,
        "current_price": 9.85,
        "pnl_pct": -1.5,
        "risk_flags": ["STRUCTURE_AGAINST_POSITION"],
    }
    contract["coach_action"] = {
        "summary": "贴近防守线",
        "focus": "只看防守线是否被收回",
        "reason": "结构风险升高",
        "risk_lines": [
            {"type": "stop_loss", "label": "风控边界", "price": 9.7, "distance_pct": -1.52},
        ],
        "nearest_risk_line": {"type": "stop_loss", "label": "风控边界", "price": 9.7, "distance_pct": -1.52},
    }

    transcript = compile_structure_transcript(contract)

    assert transcript.position_context
    assert transcript.position_context.is_holding is True
    assert transcript.position_context.avg_cost == 10.0
    assert transcript.position_context.current_price == 9.85
    assert transcript.position_context.pnl_percentage == -1.5
    assert "STRUCTURE_AGAINST_POSITION" in transcript.position_context.risk_flags
    assert transcript.position_context.nearest_risk_line["price"] == 9.7
    assert any(item.label == "holding.风控边界" and item.value == 9.7 for item in transcript.allowed_prices)
    assert any(item.label == "holding.nearest.风控边界" and item.value == 9.7 for item in transcript.allowed_prices)


def test_compile_structure_transcript_marks_low_level_divergence_as_signal_only():
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

    assert transcript.divergence_context.pivot_position == "BELOW_DD"
    assert transcript.divergence_context.alignment == "LOW_LEVEL_ONLY"
    assert transcript.divergence_context.chain_direction == "BOTTOM"
    assert transcript.divergence_context.chain_status == "LOWER_ONLY"
    assert transcript.divergence_context.buy_sell_candidate.side == "BUY"
    assert transcript.divergence_context.buy_sell_candidate.kind == "FIRST_CANDIDATE"
    assert transcript.divergence_context.buy_sell_candidate.status == "SIGNAL_ONLY"
    assert any(step.role == "trigger" and step.status == "SUPPORTS" for step in transcript.divergence_context.chain)
    assert any(step.role == "confirmation" and step.status == "WAITING" for step in transcript.divergence_context.chain)
    assert "LOWER_ONLY" in transcript.structure_fingerprint
    divergence_agent = next(item for item in transcript.agent_observations if item.agent_id == "divergence_agent")
    coach_agent = next(item for item in transcript.agent_observations if item.agent_id == "coach_agent")
    assert "FIRST_CANDIDATE" in divergence_agent.verdict
    assert "低级别线索" in divergence_agent.next_focus
    assert "重新站回枢纽边界" in coach_agent.next_focus
    assert any(signal.level == "5" for signal in transcript.divergence_context.lower_level_signals)
    assert "11.20" in transcript.divergence_context.upgrade_condition


def test_commander_context_promotes_macro_breakout_edge():
    contract = radar_contract()
    contract["quote"] = {"price": 182.9, "low": 179.52, "high": 185.0}
    for level in contract["structure"]["levels"].values():
        level["price"] = 182.9
    contract["structure"]["levels"]["day"].update(
        {
            "zd": 177.19,
            "zg": 180.37,
            "dd": 175.9,
            "gg": 187.58,
            "active_zhongshu": {"zd": 177.19, "zg": 180.37, "dd": 175.9, "gg": 187.58},
        }
    )

    transcript = compile_structure_transcript(contract)
    commander = transcript.reasoning_evidence_pack["commander_context"]

    assert commander["primary_context"]["code"] == "MACRO_BREAKOUT_EDGE"
    assert commander["must_use_levels"]["support"]["price"] == 180.37
    assert commander["must_use_levels"]["resistance"]["price"] == 187.58
    assert any(item["claim"] == "ATTEMPTING_MACRO_BREAKOUT" for item in commander["semantic_assertions"])


def test_ai_native_evidence_prefers_tactical_day_30_5_structure():
    contract = radar_contract()
    contract["symbol"] = "sz.300124"
    contract["quote"] = {"price": 68.6, "low": 67.42, "high": 70.41}
    for level in contract["structure"]["levels"].values():
        level["price"] = 68.6
    contract["structure"]["levels"]["30"].update(
        {
            "zd": 69.96,
            "zg": 71.4,
            "active_zhongshu": {"zd": 69.96, "zg": 71.4, "dd": 69.8, "gg": 71.69},
        }
    )
    contract["tactical_structure"] = {
        "levels": {
            "day": {
                "level": "day",
                "price": 68.6,
                "zd": 69.98,
                "zg": 75.48,
                "active_zhongshu": {"zd": 69.98, "zg": 75.48, "dd": 67.62, "gg": 82.59},
            },
            "30": {
                "level": "30",
                "price": 68.6,
                "zd": 66.98,
                "zg": 68.63,
                "active_zhongshu": {"zd": 66.98, "zg": 68.63, "dd": 64.55, "gg": 69.9},
            },
            "5": {
                "level": "5",
                "price": 68.6,
                "zd": 65.53,
                "zg": 66.38,
                "active_zhongshu": {"zd": 65.53, "zg": 66.38, "dd": 65.3, "gg": 66.54},
                "bi_zhongshus": [{"zd": 65.53, "zg": 66.38, "dd": 65.3, "gg": 66.54}],
            },
        }
    }

    transcript = compile_structure_transcript(contract)
    pack = transcript.reasoning_evidence_pack
    commander = pack["commander_context"]

    assert pack["structure_scope"] == "tactical_day_30_5"
    assert commander["primary_context"]["code"] == "REBOUND_INTO_30M_ZHONGSHU"
    assert commander["must_use_levels"]["support"]["price"] == 66.38
    assert commander["must_use_levels"]["resistance"]["price"] == 68.63
    assert transcript.levels[1].center_zd == 66.98


def test_commander_context_uses_two_recent_5m_centers_for_neckline_rebound():
    contract = radar_contract()
    contract["symbol"] = "sh.688008"
    contract["quote"] = {"price": 173.3, "low": 169.53, "high": 175.14}
    for level in contract["structure"]["levels"].values():
        level["price"] = 173.3
    contract["tactical_structure"] = {
        "levels": {
            "day": {
                "level": "day",
                "price": 173.3,
                "zd": 77.33,
                "zg": 80.15,
                "active_zhongshu": {"zd": 77.33, "zg": 80.15},
            },
            "30": {
                "level": "30",
                "price": 173.3,
                "zd": 144.46,
                "zg": 145.5,
                "active_zhongshu": {"zd": 144.46, "zg": 145.5, "gg": 160.0},
            },
            "5": {
                "level": "5",
                "price": 173.3,
                "zd": 168.5,
                "zg": 170.35,
                "active_zhongshu": {"zd": 168.5, "zg": 170.35, "dd": 165.42, "gg": 173.82},
                "bi_zhongshus": [
                    {"zd": 175.51, "zg": 180.0, "dd": 170.08, "gg": 180.48},
                    {"zd": 168.5, "zg": 170.35, "dd": 165.42, "gg": 173.82},
                ],
            },
        }
    }

    transcript = compile_structure_transcript(contract)
    commander = transcript.reasoning_evidence_pack["commander_context"]

    assert commander["primary_context"]["code"] == "REBOUND_BETWEEN_TWO_5M_ZHONGSHUS"
    assert commander["must_use_levels"]["support"]["price"] == 170.35
    assert commander["must_use_levels"]["resistance"]["price"] == 175.51
    assert commander["must_use_levels"]["deep_support"]["price"] == 165.42


def test_commander_context_promotes_low_level_retrace_testing_third_buy():
    contract = radar_contract()
    contract["quote"] = {"price": 312.99, "low": 306.48, "high": 321.0}
    for level in contract["structure"]["levels"].values():
        level["price"] = 312.99
    contract["structure"]["levels"]["day"].update(
        {
            "zd": 186.41,
            "zg": 234.63,
            "gg": 331.16,
            "active_zhongshu": {"zd": 186.41, "zg": 234.63, "gg": 331.16},
        }
    )
    contract["structure"]["levels"]["30"].update(
        {
            "zd": 287.13,
            "zg": 287.48,
            "gg": 326.01,
            "active_zhongshu": {"zd": 287.13, "zg": 287.48, "gg": 326.01},
        }
    )
    contract["structure"]["levels"]["5"].update(
        {
            "zd": 302.57,
            "zg": 307.62,
            "gg": 311.5,
            "active_zhongshu": {"zd": 302.57, "zg": 307.62, "gg": 311.5},
            "bis": [
                {"x0": "2026-04-22 10:55:00", "y0": 311.5, "x1": "2026-04-23 14:05:00", "y1": 309.75, "is_up": False},
                {"x0": "2026-04-23 14:05:00", "y0": 309.75, "x1": "2026-04-23 14:30:00", "y1": 312.99, "is_up": True},
            ],
        }
    )
    contract["algorithm_v2"]["boundaries"]["confirm"] = [
        {"level": "5", "field": "BSP", "label": "突破最近5分钟卖点压力，震荡后转强", "value": 309.75}
    ]
    contract["algorithm_v2"]["boundaries"]["pressure"] = [
        {"level": "15", "field": "SELL", "label": "15分钟一卖压力", "value": 313.65}
    ]

    transcript = compile_structure_transcript(contract)
    commander = transcript.reasoning_evidence_pack["commander_context"]

    assert commander["primary_context"]["code"] == "RETRACE_TESTING_3RD_BUY"
    assert commander["must_use_levels"]["support"]["price"] == 309.75
    claims = {item["claim"] for item in commander["semantic_assertions"]}
    assert {"BREAKOUT_PULLBACK", "POTENTIAL_THIRD_BUY_FORMING"}.issubset(claims)


def test_compile_structure_transcript_uses_explicit_lower_bsp_as_signal_only():
    contract = radar_contract()
    contract["structure"]["levels"]["30"].update(
        {
            "price": 10.6,
            "zg": 11.8,
            "zd": 11.2,
            "dd": 10.8,
        }
    )
    contract["structure"]["levels"]["5"]["bsps"] = [
        {"type": "1", "is_buy": True, "price": 10.45},
        {"type": "2", "is_buy": True, "price": 10.62},
    ]

    transcript = compile_structure_transcript(contract)

    context = transcript.divergence_context
    assert context.alignment == "LOW_LEVEL_ONLY"
    assert context.chain_status == "LOWER_ONLY"
    assert any(signal.type == "BOTTOM" and "买点候选" in " ".join(signal.evidence) for signal in context.lower_level_signals)
    assert context.buy_sell_candidate.side == "BUY"
    assert context.buy_sell_candidate.kind == "SECOND_WAIT"
    assert context.buy_sell_candidate.status == "WAITING_CONFIRM"
    assert "BSP_BUY_SECOND_WAIT_WAITING_CONFIRM" in transcript.structure_fingerprint


def test_reasoning_evidence_pack_exports_divergence_signal_assertions():
    contract = radar_contract()
    contract["structure"]["levels"]["30"].update(
        {
            "price": 10.6,
            "zg": 11.8,
            "zd": 11.2,
            "dd": 10.8,
        }
    )
    contract["structure"]["levels"]["5"]["bsps"] = [
        {"type": "1", "is_buy": True, "price": 10.45},
        {"type": "2", "is_buy": True, "price": 10.62},
    ]

    transcript = compile_structure_transcript(contract)

    assertions = transcript.reasoning_evidence_pack["semantic_assertions"]
    bottom_assertion = next(item for item in assertions if item["claim"] == "5m_BOTTOM_DIVERGENCE")
    assert "买点候选" in " ".join(bottom_assertion["evidence"])


def test_compile_structure_transcript_does_not_confirm_third_buy_below_pivot():
    contract = radar_contract()
    contract["structure"]["levels"]["30"].update(
        {
            "price": 10.6,
            "zg": 11.8,
            "zd": 11.2,
            "dd": 10.8,
        }
    )
    contract["structure"]["levels"]["5"].update(
        {
            "patterns": ["三买", "二买"],
            "bsps": [
                {"type": "3b", "is_buy": True, "price": 10.72},
                {"type": "2", "is_buy": True, "price": 10.62},
            ],
        }
    )

    transcript = compile_structure_transcript(contract)

    candidate = transcript.divergence_context.buy_sell_candidate
    assert transcript.divergence_context.chain_status == "LOWER_ONLY"
    assert candidate.kind == "SECOND_WAIT"
    assert candidate.status == "WAITING_CONFIRM"


def test_compile_structure_transcript_does_not_let_macro_third_buy_confirm_lower_candidate():
    contract = radar_contract()
    contract["structure"]["levels"]["day"]["patterns"] = ["三买确认"]
    contract["structure"]["levels"]["30"].update(
        {
            "price": 10.6,
            "zg": 11.8,
            "zd": 11.2,
            "dd": 10.8,
            "patterns": ["二买"],
        }
    )
    contract["structure"]["levels"]["5"]["bsps"] = [
        {"type": "2", "is_buy": True, "price": 10.62},
    ]

    transcript = compile_structure_transcript(contract)

    candidate = transcript.divergence_context.buy_sell_candidate
    assert candidate.kind == "SECOND_WAIT"
    assert candidate.status == "WAITING_CONFIRM"


def test_compile_structure_transcript_separates_partial_chart_alignment_in_fingerprint():
    contract = radar_contract()
    contract["structure"]["levels"]["30"].update(
        {
            "bi_count": 3,
            "bis": [{"is_up": True}],
        }
    )

    transcript = compile_structure_transcript(contract)

    assert transcript.structure_snapshot.chart_alignment.status == "PARTIAL"
    assert "CHART_PARTIAL" in transcript.structure_fingerprint
    assert "30:bi_count_mismatch:3!=1" in transcript.structure_fingerprint


def test_compile_structure_transcript_confirms_divergence_after_reclaiming_center():
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

    assert transcript.divergence_context.pivot_position == "IN_CENTER"
    assert transcript.divergence_context.alignment == "CONFIRMED_SUPPORT"
    assert transcript.divergence_context.chain_status == "CONFIRMED"
    assert transcript.divergence_context.buy_sell_candidate.kind == "FIRST_CANDIDATE"
    assert transcript.divergence_context.buy_sell_candidate.status == "WAITING_CONFIRM"
    assert "BSP_BUY_FIRST_CANDIDATE_WAITING_CONFIRM" in transcript.structure_fingerprint
    assert any(step.role == "confirmation" and step.status == "CONFIRMED" for step in transcript.divergence_context.chain)
    assert "11.80" in transcript.divergence_context.upgrade_condition


def test_compile_structure_transcript_marks_failed_divergence():
    contract = radar_contract()
    contract["structure"]["levels"]["5"]["patterns"] = ["底背驰失效"]

    transcript = compile_structure_transcript(contract)

    assert transcript.divergence_context.alignment == "FAILED_DIVERGENCE"
    assert transcript.divergence_context.chain_status == "FAILED"
    assert any(step.status == "FAILED" for step in transcript.divergence_context.chain)
    assert transcript.divergence_context.buy_sell_candidate.status == "INVALID"
    divergence_agent = next(item for item in transcript.agent_observations if item.agent_id == "divergence_agent")
    coach_agent = next(item for item in transcript.agent_observations if item.agent_id == "coach_agent")
    assert "INVALID" in divergence_agent.verdict
    assert "候选已失效" in divergence_agent.next_focus
    assert "候选失效" in coach_agent.next_focus
    assert any(signal.status == "FAILED" for signal in transcript.divergence_context.lower_level_signals)


def test_compile_structure_transcript_marks_second_buy_candidate_from_bsp():
    contract = radar_contract()
    contract["structure"]["levels"]["5"].update(
        {
            "patterns": ["底背驰"],
            "bsps": [{"type": "2", "is_buy": True, "price": 11.95}],
        }
    )
    contract["structure"]["levels"]["30"].update(
        {
            "price": 11.5,
            "zg": 11.8,
            "zd": 11.2,
            "dd": 10.8,
        }
    )

    transcript = compile_structure_transcript(contract)

    candidate = transcript.divergence_context.buy_sell_candidate
    assert candidate.side == "BUY"
    assert candidate.kind == "SECOND_WAIT"
    assert candidate.status == "CONFIRMED"
    assert "BSP_BUY_SECOND_WAIT_CONFIRMED" in transcript.structure_fingerprint


def test_compile_structure_transcript_marks_third_buy_candidate_from_pattern():
    contract = radar_contract()
    contract["structure"]["levels"]["30"]["patterns"] = ["三买确认"]
    contract["structure"]["levels"]["5"]["patterns"] = ["底背驰"]

    transcript = compile_structure_transcript(contract)

    candidate = transcript.divergence_context.buy_sell_candidate
    assert candidate.side == "BUY"
    assert candidate.kind == "THIRD_CONFIRM"
    assert candidate.status == "CONFIRMED"
    divergence_agent = next(item for item in transcript.agent_observations if item.agent_id == "divergence_agent")
    assert "THIRD_CONFIRM" in divergence_agent.verdict
    assert "候选已由结构事实确认" in divergence_agent.next_focus


def test_compile_structure_transcript_falls_back_to_60_when_30_is_empty():
    contract = radar_contract()
    contract["structure"]["levels"]["30"] = {}
    contract["structure"]["levels"]["60"] = {
        "level": "60",
        "price": 11.5,
        "zg": 11.8,
        "zd": 11.2,
    }

    transcript = compile_structure_transcript(contract)

    assert transcript.divergence_context.pivot_level == "60"
    assert transcript.levels[1].level == "60"
    assert transcript.levels[1].level == "60"


def test_compile_structure_transcript_falls_back_to_60_when_30_has_only_price():
    contract = radar_contract()
    contract["structure"]["levels"]["30"] = {"level": "30", "price": 12.3}
    contract["structure"]["levels"]["60"] = {
        "level": "60",
        "price": 11.5,
        "zg": 11.8,
        "zd": 11.2,
    }

    transcript = compile_structure_transcript(contract)

    assert transcript.divergence_context.pivot_level == "60"


def test_compile_structure_transcript_carries_dd_as_allowed_price():
    contract = radar_contract()
    contract["structure"]["levels"]["30"]["dd"] = 10.8
    contract["structure"]["levels"]["5"]["patterns"] = ["底背驰失效"]

    transcript = compile_structure_transcript(contract)

    assert any(item.label == "30.DD" and item.value == 10.8 for item in transcript.allowed_prices)


def test_compile_structure_transcript_marks_pressure_top_divergence_as_risk():
    contract = radar_contract()
    contract["structure"]["levels"]["30"].update(
        {
            "price": 11.7,
            "zg": 11.8,
            "zd": 11.2,
            "patterns": ["顶背驰"],
        }
    )

    transcript = compile_structure_transcript(contract)

    assert transcript.divergence_context.alignment == "COUNTER_TREND_RISK"
    assert any(signal.type == "TOP" for signal in transcript.divergence_context.pivot_signals)


def test_compile_structure_transcript_marks_near_dd_before_below_dd():
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

    assert transcript.divergence_context.pivot_position == "NEAR_DD"
    assert transcript.divergence_context.alignment == "ALIGNING"


def test_commander_breakdown_uses_tactical_5m_pressure_and_day_deep_support():
    contract = radar_contract()
    contract["symbol"] = "sz.300394"
    contract["quote"] = {"price": 307.5, "low": 305.0, "high": 314.9}
    contract["tactical_structure"] = {
        "levels": {
            "day": {
                "level": "day",
                "price": 307.5,
                "zd": 197.5,
                "zg": 228.0,
                "active_zhongshu": {"zd": 197.5, "zg": 228.0, "dd": 182.37, "gg": 242.0},
            },
            "30": {
                "level": "30",
                "price": 307.5,
                "zd": 325.68,
                "zg": 336.0,
                "active_zhongshu": {"zd": 325.68, "zg": 336.0, "dd": 314.88, "gg": 365.03},
                "bis": [
                    {"x0": "2026-04-28 10:00:00", "y0": 314.88, "x1": "2026-04-29 10:00:00", "y1": 305.0, "is_up": False},
                ],
            },
            "5": {
                "level": "5",
                "price": 307.5,
                "zd": 310.01,
                "zg": 313.99,
                "active_zhongshu": {"zd": 310.01, "zg": 313.99, "dd": 307.83, "gg": 315.48},
                "bi_zhongshus": [{"zd": 310.01, "zg": 313.99, "dd": 307.83, "gg": 315.48}],
            },
        }
    }

    transcript = compile_structure_transcript(contract)
    commander = transcript.reasoning_evidence_pack["commander_context"]

    assert commander["primary_context"]["code"] == "BREAKDOWN_BELOW_30M"
    assert commander["must_use_levels"]["resistance"]["price"] == 310.01
    assert commander["must_use_levels"]["deep_support"]["price"] == 228.0


def test_commander_breakdown_inside_day_center_uses_active_day_zd_before_old_center():
    contract = radar_contract()
    contract["symbol"] = "sz.002138"
    contract["quote"] = {"price": 33.95, "low": 33.34, "high": 34.2}
    contract["structure"]["levels"]["day"].update(
        {
            "price": 33.95,
            "zd": 32.2403,
            "zg": 35.5122,
            "dd": 32.0938,
            "gg": 36.6549,
            "active_zhongshu": {
                "zd": 32.2403,
                "zg": 35.5122,
                "dd": 32.0938,
                "gg": 36.6549,
                "begin_date": "2025-09-02",
                "end_date": "2025-10-27",
            },
            "bi_zhongshus": [
                {"zd": 29.2132, "zg": 31.8742, "dd": 22.6395, "gg": 32.5825, "end_date": "2025-04-22"},
                {"zd": 32.2403, "zg": 35.5122, "dd": 32.0938, "gg": 36.6549, "end_date": "2025-10-27"},
            ],
        }
    )
    contract["structure"]["levels"]["30"].update(
        {
            "price": 33.95,
            "zd": 34.94,
            "zg": 36.14,
            "dd": 34.85,
            "gg": 36.29,
            "active_zhongshu": {"zd": 34.94, "zg": 36.14, "dd": 34.85, "gg": 36.29},
            "bsps": [{"type": "2s", "is_buy": True, "time": "2026-04-13 10:00:00", "price": 33.86}],
        }
    )

    transcript = compile_structure_transcript(contract)
    commander = transcript.reasoning_evidence_pack["commander_context"]

    assert commander["primary_context"]["code"] == "BREAKDOWN_BELOW_30M"
    assert commander["must_use_levels"]["deep_support"]["label"] == "day.ZD"
    assert commander["must_use_levels"]["deep_support"]["price"] == 32.24
