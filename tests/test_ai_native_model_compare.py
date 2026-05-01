import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.scripts.ai_native_model_compare import route_for


def test_model_compare_flash_route_is_fast_no_thinking():
    route = route_for("flash")

    assert route.model_name == "deepseek-v4-flash"
    assert route.thinking_enabled is False
    assert route.max_tokens <= 2048
    assert route.timeout_seconds <= 45


def test_model_compare_pro_route_is_no_thinking_baseline():
    route = route_for("pro")

    assert route.model_name
    assert route.thinking_enabled is False
    assert route.tier == "hard"


def test_model_compare_can_include_pro_thinking_reference():
    route = route_for("pro-thinking")

    assert route.thinking_enabled is True
    assert route.reasoning_effort == "high"
