import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.ai_native.fusion_chan_adapter import (
    build_chan_analysis_from_radar_contract,
)


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
                    "bsps": [{"type": "2买", "is_buy": True, "price": 11.95}],
                },
            }
        },
        "algorithm_v2": {
            "path": "UPWARD_MAJOR_WAVE",
            "phase": "BREAKOUT_EXTENSION",
            "current_scenario_id": "B",
            "boundaries": {
                "confirm": [{"label": "历史前高", "value": 12.8, "level": "5"}],
                "maintain": [{"label": "观察区间下沿", "value": 11.9, "level": "5"}],
                "invalidate": [{"label": "短线失效", "value": 11.2, "level": "30"}],
                "support": [{"label": "大级别防线", "value": 10.8, "level": "day"}],
            },
        },
    }


def test_chan_adapter_builds_v45_structure_input_without_probabilities():
    result = build_chan_analysis_from_radar_contract(radar_contract())

    assert result.version == "chan_analysis.v45"
    assert result.symbol == "sh.600519"
    assert result.primary_level == "30"
    assert result.structure_state == "WAITING_FOR_PULLBACK"
    assert "UPWARD_MAJOR_WAVE" in result.trend_context
    assert "30 center" in result.center_state

    path_ids = {path.id for path in result.complete_paths}
    assert {"A", "B", "C"}.issubset(path_ids)
    path_a = next(path for path in result.complete_paths if path.id == "A")
    assert path_a.status == "WAITING"
    assert "12.80" in path_a.trigger_condition
    assert "11.20" in path_a.invalidation_condition

    levels = {(round(level.price, 2), level.role) for level in result.key_levels}
    assert (12.8, "trigger") in levels
    assert (11.2, "invalidation") in levels
    assert (11.8, "center_upper") in levels
    assert all(not hasattr(path, "probability") for path in result.complete_paths)
    assert all("Kronos 概率" not in rule for rule in result.discipline_rules)
    assert any("时间窗口和价格区间参考" in rule for rule in result.discipline_rules)


def test_chan_adapter_marks_stale_structure_as_warning_and_discipline():
    contract = radar_contract()
    contract["freshness"] = {"is_stale": True}

    result = build_chan_analysis_from_radar_contract(contract)

    assert "structure_data_stale" in result.warnings
    assert "结构数据过期时，不做强推演。" in result.discipline_rules
