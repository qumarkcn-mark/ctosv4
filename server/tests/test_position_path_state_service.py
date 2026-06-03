from server.engines.ai_native.position_path_state_service import derive_position_path_state


def test_position_path_state_missing_when_no_watch_state_machine():
    result = derive_position_path_state(summary={}, current_price=10.0, position=None)

    assert result["data_status"] == "missing"
    assert result["current_phase"] == "暂无路径状态"
    assert result["draft_action"] == "WAIT"


def test_position_path_state_uses_watch_state_machine_current_state():
    summary = {
        "watch_state_machine": {
            "version": "watch_state_machine.v1",
            "current_state": {
                "name": "5m三买尝试",
                "display": "回踩确认中",
                "range": [9.8, 10.6],
            },
            "transitions": [
                {
                    "id": "up_break",
                    "trigger": {"type": "price_above", "level": 10.6},
                    "next_state": "离开确认",
                    "observe": "回踩不破上沿",
                    "success": "三买确认",
                    "failure": "跌回中枢",
                }
            ],
        }
    }

    result = derive_position_path_state(summary=summary, current_price=10.2, position={"shares": 1000})

    assert result["data_status"] == "ready"
    assert result["lifecycle_stage"] == "entry_validation"
    assert result["major_task"] == "5m三买尝试"
    assert result["current_phase"] == "回踩确认中"
    assert result["next_focus"] == "回踩不破上沿"
    assert result["range"] == {"low": 9.8, "high": 10.6}


def test_position_path_state_current_price_triggers_transition_without_llm():
    summary = {
        "watch_state_machine": {
            "version": "watch_state_machine.v1",
            "current_state": {"name": "中枢震荡", "display": "等待方向", "range": [9.8, 10.6]},
            "transitions": [
                {
                    "id": "down_break",
                    "trigger": {"type": "price_below", "level": 9.8},
                    "next_state": "跌回下沿",
                    "observe": "反抽能否回中枢",
                    "success": "拉回中枢",
                    "failure": "三卖风险",
                }
            ],
        }
    }

    result = derive_position_path_state(summary=summary, current_price=9.7, position={"shares": 1000})

    assert result["data_status"] == "ready"
    assert result["current_phase"] == "跌回下沿"
    assert result["draft_action"] == "REVIEW_TRIGGER"
    assert result["failure_path"] == "三卖风险"
