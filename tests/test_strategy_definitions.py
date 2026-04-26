import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from server.engines.decision.strategy_definitions import (
    STRATEGY_DEFINITIONS,
    build_strategy_contract,
    get_strategy_definition,
    normalize_strategy_id,
)


def test_initial_strategy_definitions_cover_contract_ids():
    expected = {
        "war1_third_buy",
        "war2_trend_step",
        "holding_stage_manager",
        "rotation_comparison",
        "intraday_t_base_position",
    }

    assert expected.issubset(STRATEGY_DEFINITIONS)
    for strategy_id in expected:
        definition = STRATEGY_DEFINITIONS[strategy_id]
        assert definition.strategy_id == strategy_id
        assert definition.strategy_version
        assert definition.name
        assert definition.freshness_required is True
        assert definition.disclaimer_required is True


def test_build_strategy_contract_preserves_version_and_conditions():
    contract = build_strategy_contract(
        "war1_third_buy",
        "WATCHING",
        [{"condition_id": "day_buy_node", "status": "PASS"}],
    )

    assert contract["strategy_id"] == "war1_third_buy"
    assert contract["strategy_version"] == "1.0.0"
    assert contract["strategy_type"] == "战法一"
    assert contract["status"] == "WATCHING"
    assert contract["conditions"][0]["condition_id"] == "day_buy_node"
    assert "plans" in contract["outputs"]
    assert "alerts" in contract["outputs"]


def test_legacy_strategy_aliases_resolve_to_canonical_ids():
    assert normalize_strategy_id("war1") == "war1_third_buy"
    assert normalize_strategy_id("war2") == "war2_trend_step"
    assert get_strategy_definition("war1").strategy_id == "war1_third_buy"


def test_strategy_definitions_do_not_expose_executable_orders():
    for definition in STRATEGY_DEFINITIONS.values():
        contract = definition.to_contract(status="WATCHING")

        assert "orders" not in contract["outputs"]
        assert "place_order" not in contract["outputs"]
        if definition.strategy_id == "intraday_t_base_position":
            assert contract["outputs"] == ["execution_intent_candidates"]


def test_unknown_strategy_definition_raises():
    with pytest.raises(ValueError):
        get_strategy_definition("missing")
