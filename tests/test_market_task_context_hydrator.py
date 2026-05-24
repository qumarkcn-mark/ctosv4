from server.engines.ai_native.market_task_context_hydrator import hydrate_market_task_context


def test_market_task_context_builds_human_like_observation_facts():
    payload = hydrate_market_task_context(
        current_price=4.01,
        structure_geometry={
            "日线": {
                "center": {"zg": 4.09, "zd": 3.79, "maturity": "late_extension", "relevance": "active_boundary"},
                "price_position": {"position": "in_center"},
                "unfinished_bi": {"direction": "down", "is_sure": False},
            }
        },
        momentum_dynamics={
            "日线": {
                "macd_state": "below_zero",
                "macd_momentum": "weakening",
                "volume_state": "shrinking",
                "volume_ratio_5_20": 0.68,
                "atr_volatility": "expanded",
            }
        },
        intraday_observation={
            "as_of": "2026-05-22 14:30:00",
            "coverage": {"quality": "partial"},
            "levels": {
                "1m": {
                    "last_bar_status": "FORMING",
                    "macd_with_forming": {"basis": "with_forming", "macd_state": "golden_cross", "macd_momentum": "weakening"},
                },
                "5m": {
                    "last_bar_status": "FORMING",
                    "macd_with_forming": {"basis": "with_forming", "macd_state": "below_zero", "macd_momentum": "weakening"},
                },
                "30m": {
                    "last_bar_status": "CLOSED",
                    "macd_closed_only": {"basis": "closed_only", "macd_state": "below_zero", "macd_momentum": "neutral"},
                },
            },
        },
        nearby_pressure_support=[
            {
                "zone": [4.08, 4.1],
                "type": "pressure",
                "distance_pct": 2.0,
                "source_levels": ["day", "30"],
                "semantic": "日线:接近中枢上沿ZG",
            }
        ],
        reasoning_continuity_context={
            "previous_reasoning": {"card_summary": "测试4.09压力", "card_action": "持仓观察"},
            "trigger_status_since_last_run": [
                {"type": "price_above", "level": 4.09, "status": "not_touched"}
            ],
        },
    )

    assert payload["version"] == "market_task_context.v1"
    assert payload["macro_phase"]["phase"] == "mixed_transition"
    assert payload["task_candidates"][0]["task"] == "中枢内震荡后的方向选择"
    assert payload["small_to_large_turn"]["status"] == "forming_upward_chain"
    assert payload["small_to_large_turn"]["chain"][0]["state"] == "turning_up"
    assert payload["pressure_semantics"][0]["role"] == "risk_release_or_rejection_watch"
    assert "站上" in payload["pressure_semantics"][0]["after_break"]
    assert payload["volume_phase"]["state"] == "shrinking"
    assert "上一轮关键触发尚未发生" in payload["continuity_read"]["read"]


def test_market_task_context_marks_strong_trend_short_range_digesting():
    payload = hydrate_market_task_context(
        current_price=271.83,
        structure_geometry={
            "周线": {"center": {"relevance": "distant_context"}, "price_position": {"position": "above_zg"}},
            "日线": {"center": {"relevance": "distant_context"}, "price_position": {"position": "above_zg"}},
            "30分钟": {
                "center": {"zg": 252.32, "zd": 242.63, "maturity": "late_extension", "relevance": "active_boundary"},
                "price_position": {"position": "above_zg"},
            },
            "5分钟": {
                "center": {"zg": 278.88, "zd": 270.6, "maturity": "normal_extension", "relevance": "active_boundary"},
                "price_position": {"position": "in_center"},
            },
        },
        momentum_dynamics={},
    )

    macro = payload["macro_phase"]
    assert macro["phase"] == "strong_trend_short_range_digesting"
    assert "大级别强势离开" in macro["read"]
    assert "短级别" in macro["implication"]
