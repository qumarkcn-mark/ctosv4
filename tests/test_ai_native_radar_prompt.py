from server.prompts.ai_native_radar_prompt import FREE_REASONING_PROMPT
from server.prompts.ai_chan_reasoning_prompt import (
    AI_CHAN_REASONING_PROMPT_E1,
    AI_CHAN_REASONING_PROMPT_E1_VERSION,
)


def test_first_stage_chan_prompt_is_prompt_contract_not_hard_gate():
    assert "第一段 AI 缠论推演" in FREE_REASONING_PROMPT
    assert "中国 A 股普通股票" in FREE_REASONING_PROMPT
    assert "T+1 约束" in FREE_REASONING_PROMPT
    assert "AI 条件价/估算观察价" in FREE_REASONING_PROMPT
    assert "不要输出独立的“防守看门狗”板块" in FREE_REASONING_PROMPT
    assert "A 股普通股票不能按期货式做空逻辑推演" in FREE_REASONING_PROMPT
    assert "必须主动在下方的买点" not in FREE_REASONING_PROMPT
    assert "极其严格的操作铁律" not in FREE_REASONING_PROMPT


def test_e1_prompt_treats_czsc_centers_as_reference_not_answer():
    assert AI_CHAN_REASONING_PROMPT_E1_VERSION == "ai_chan_reasoning.e1_dynamic_growth"
    assert "bi_sequence 是主证据" in AI_CHAN_REASONING_PROMPT_E1
    assert "algorithm_zhongshus 是后端算法给出的中枢参考答案，不是最终结论" in AI_CHAN_REASONING_PROMPT_E1
    assert "中枢先从 bi_sequence 计算和验证" in AI_CHAN_REASONING_PROMPT_E1
    assert "CZSC 的 algorithm_zhongshus 不是最终答案" in AI_CHAN_REASONING_PROMPT_E1
    assert "如果输入没有 segs/线段，就按笔级别推演" in AI_CHAN_REASONING_PROMPT_E1
    assert "大级别关键边界 + 小级别高位中枢承接" in AI_CHAN_REASONING_PROMPT_E1
    assert "周线笔给大方向和空间，日线中枢给关键位置" in AI_CHAN_REASONING_PROMPT_E1
    assert "走势生长视角" in AI_CHAN_REASONING_PROMPT_E1
    assert "在 main_deduction 里尽量详细解释“走势如何生长”" in AI_CHAN_REASONING_PROMPT_E1
    assert "力度与背驰视角" in AI_CHAN_REASONING_PROMPT_E1
    assert "走势生长的常见分叉" in AI_CHAN_REASONING_PROMPT_E1
    assert "小级别背驰可以帮助观察大级别一笔是否接近结束" in AI_CHAN_REASONING_PROMPT_E1
    assert "不要预设操作级别一定是30分钟" in AI_CHAN_REASONING_PROMPT_E1
    assert "本轮主推演级别是什么，触发级别是什么" in AI_CHAN_REASONING_PROMPT_E1
    assert "如果只在小级别内部成立而大级别没有共振，应降低机会级别和置信度" in AI_CHAN_REASONING_PROMPT_E1
