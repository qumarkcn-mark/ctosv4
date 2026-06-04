"""统一推演 WatchBoard 抽取中的 T0 许可字段测试。"""

from server.engines.ai_native.unified_reasoning_service import normalize_watchboard_payload


def test_watchboard_payload_keeps_structured_t0_policy():
    payload = normalize_watchboard_payload(
        {
            "card_summary": "5分钟三买验证",
            "t0_allowed_direction": "SHORT_ONLY",
            "t0_size_multiplier": 0.5,
            "t0_reason": "压力测试，背驰高抛",
            "watch_state_machine": {
                "current_state": {
                    "name": "压力测试",
                    "level": "5分钟",
                    "range": [10, 12],
                    "display": "压力测试",
                },
                "transitions": [],
            },
        }
    )

    assert payload["t0_allowed_direction"] == "SHORT_ONLY"
    assert payload["t0_size_multiplier"] == 0.5
    assert payload["t0_reason"] == "压力测试，背驰高抛"


def test_watchboard_payload_invalid_t0_policy_fails_closed():
    payload = normalize_watchboard_payload(
        {
            "card_summary": "结构观察",
            "t0_allowed_direction": "INVALID",
            "t0_size_multiplier": 9.9,
            "t0_reason": "非法字段",
        }
    )

    assert payload["t0_allowed_direction"] == "OBSERVE_ONLY"
    assert payload["t0_size_multiplier"] == 0.0
    assert payload["t0_reason"] == "非法字段"


def test_watchboard_payload_observe_forces_zero_multiplier():
    payload = normalize_watchboard_payload(
        {
            "card_summary": "等待方向",
            "t0_allowed_direction": "OBSERVE_ONLY",
            "t0_size_multiplier": 1.0,
            "t0_reason": "缺少明确路径",
        }
    )

    assert payload["t0_allowed_direction"] == "OBSERVE_ONLY"
    assert payload["t0_size_multiplier"] == 0.0
