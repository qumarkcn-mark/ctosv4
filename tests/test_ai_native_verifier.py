import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.ai_native.schemas import AllowedPrice, StructureTranscript
from server.engines.ai_native.verifier import verify_ai_reasoning


def transcript():
    return StructureTranscript(
        symbol="sh.600519",
        mode="EMPTY",
        generated_at="2026-04-29T10:00:00+08:00",
        fingerprint_version="fingerprint.v1",
        structure_fingerprint="EMPTY|UPWARD_MAJOR_WAVE",
        allowed_prices=[
            AllowedPrice(label="confirm", value=12.8, source="test"),
            AllowedPrice(label="support", value=11.9, source="test"),
            AllowedPrice(label="defense", value=10.8, source="test"),
        ],
    )


def good_output():
    return {
        "diagnosis": "日线离开后，30分回踩验证中",
        "current_hypothesis": "B",
        "reasoning_boundary": "11.90 到 12.80 内先按观察处理",
        "hypotheses": [
            {
                "id": "A",
                "name": "向上确认",
                "current_applicability": "WAITING",
                "evidence": ["日线离开"],
                "trigger": "站稳 12.80 后观察回踩",
                "invalidation": "跌回 11.90 后拉不回",
                "next_focus": "只盯 12.80 是否站稳",
                "empty_position_view": "空仓等确认，不提前下结论",
                "holding_position_view": "持仓看回踩是否守住",
            },
            {
                "id": "B",
                "name": "区间观察",
                "current_applicability": "CURRENT",
                "evidence": ["区间未离开"],
                "trigger": "继续在 11.90 到 12.80 内震荡",
                "invalidation": "离开区间后重新分类",
                "next_focus": "只盯区间边界",
                "empty_position_view": "空仓等待分类",
                "holding_position_view": "持仓看防线",
            },
            {
                "id": "C",
                "name": "向下失效",
                "current_applicability": "WAITING",
                "evidence": ["失效边界明确"],
                "trigger": "跌破 11.90 后拉不回",
                "invalidation": "重新站回 11.90 后再观察",
                "next_focus": "只盯 11.90",
                "empty_position_view": "空仓等待新结构",
                "holding_position_view": "持仓收紧风险参考",
            },
            {
                "id": "D",
                "name": "数据不足",
                "current_applicability": "WAITING",
                "evidence": ["数据可能过期"],
                "trigger": "结构数据过期",
                "invalidation": "数据恢复后重新推理",
                "next_focus": "只盯数据新鲜度",
                "empty_position_view": "空仓等待数据恢复",
                "holding_position_view": "持仓先看规则雷达防线",
            },
        ],
        "operator_mistake": "最容易犯的错是在区间内提前替市场下结论",
        "coach_talk": "当前不是预测涨跌，而是等待分类完成。仅供参考，不构成投资建议",
        "disclaimer": "仅供参考，不构成投资建议",
    }


def test_verifier_passes_complete_output():
    output, gate = verify_ai_reasoning(good_output(), transcript())

    assert output is not None
    assert gate.status == "PASS"
    assert gate.score == 100


def test_verifier_rewrites_missing_c_path():
    raw = good_output()
    raw["hypotheses"] = [item for item in raw["hypotheses"] if item["id"] != "C"]

    _, gate = verify_ai_reasoning(raw, transcript())

    assert gate.status == "REWRITE"
    assert any(item.code == "MISSING_HYPOTHESES" for item in gate.violations)


def test_verifier_fallbacks_unknown_price_and_trading_command():
    raw = good_output()
    raw["coach_talk"] = "突破 13.27 就买入。仅供参考，不构成投资建议"

    _, gate = verify_ai_reasoning(raw, transcript())

    assert gate.status == "FALLBACK"
    codes = {item.code for item in gate.violations}
    assert "UNKNOWN_PRICE" in codes
    assert "FORBIDDEN_TRADING_COMMAND" in codes

