from server.prompts.ai_native_radar_prompt import FREE_REASONING_PROMPT


def test_first_stage_chan_prompt_is_prompt_contract_not_hard_gate():
    assert "第一段 AI 缠论推演" in FREE_REASONING_PROMPT
    assert "中国 A 股普通股票" in FREE_REASONING_PROMPT
    assert "T+1 约束" in FREE_REASONING_PROMPT
    assert "AI 条件价/估算观察价" in FREE_REASONING_PROMPT
    assert "不要输出独立的“防守看门狗”板块" in FREE_REASONING_PROMPT
    assert "A 股普通股票不能按期货式做空逻辑推演" in FREE_REASONING_PROMPT
    assert "必须主动在下方的买点" not in FREE_REASONING_PROMPT
    assert "极其严格的操作铁律" not in FREE_REASONING_PROMPT
