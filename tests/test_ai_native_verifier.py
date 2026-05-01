import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.engines.ai_native.schemas import (
    AllowedPrice,
    ChartOverlayAlignment,
    StructureSnapshot,
    StructureTranscript,
)
from server.engines.ai_native.verifier import verify_ai_reasoning


def transcript():
    return StructureTranscript(
        symbol="sh.600519",
        mode="EMPTY",
        generated_at="2026-04-29T10:00:00+08:00",
        fingerprint_version="fingerprint.v1",
        structure_fingerprint="EMPTY|UPWARD_MAJOR_WAVE",
        structure_snapshot=StructureSnapshot(
            chart_alignment=ChartOverlayAlignment(status="ALIGNED"),
        ),
        reasoning_evidence_pack={
            "current_price": 12.3,
            "commander_context": {
                "must_use_levels": {
                    "support": {"price": 15.85},
                }
            },
        },
        allowed_prices=[
            AllowedPrice(label="confirm", value=12.8, source="test"),
            AllowedPrice(label="support", value=11.9, source="test"),
            AllowedPrice(label="defense", value=10.8, source="test"),
        ],
    )


def good_markdown():
    return """**1. 【全局语境定性】**
日线离开后，30 分钟回踩验证中。

**2. 【防守看门狗】**
11.90 到 12.80 内先按观察处理。

**3. 【推演与应对沙盘】**
如果重新站回 12.80，延续路径权重提高；如果跌回 11.90，失效路径升权。
仅供参考，不构成投资建议。"""


def good_output(markdown=None):
    text = markdown or good_markdown()
    return {
        "raw_reasoning_md": text,
        "coach_filtered_md": text,
        "semantic_filter_status": "PASS",
        "semantic_filter_violations": [],
        "disclaimer": "仅供参考，不构成投资建议",
    }


def test_verifier_passes_filtered_markdown():
    output, gate = verify_ai_reasoning(good_output(), transcript())

    assert output is not None
    assert gate.status == "PASS"
    assert gate.score == 100


def test_verifier_rewrites_missing_sections():
    _, gate = verify_ai_reasoning(good_output("只看 12.80。仅供参考，不构成投资建议"), transcript())

    assert gate.status == "REWRITE"
    assert any(item.code == "MISSING_MARKDOWN_SECTIONS" for item in gate.violations)


def test_verifier_accepts_plain_section_headings():
    text = good_markdown().replace("**1. 【全局语境定性】**", "# 全局语境定性")
    text = text.replace("**2. 【防守看门狗】**", "# 防守看门狗")
    text = text.replace("**3. 【推演与应对沙盘】**", "# 推演与应对沙盘")

    _, gate = verify_ai_reasoning(good_output(text), transcript())

    assert gate.status == "PASS"


def test_verifier_allows_coach_execution_terms():
    text = good_markdown() + "\n突破 12.80 后买入，跌破 11.90 后清仓。"

    _, gate = verify_ai_reasoning(good_output(text), transcript())

    assert gate.status == "PASS"


def test_verifier_rewrites_a_share_short_terms():
    text = good_markdown() + "\n跌破 11.90 后做空，空头目标看 10.80。"

    _, gate = verify_ai_reasoning(good_output(text), transcript())

    assert gate.status == "REWRITE"
    violation = next(item for item in gate.violations if item.code == "A_SHARE_SHORT_SELLING")
    assert "做空" in violation.evidence
    assert "空头目标" in violation.evidence


def test_verifier_fallbacks_extreme_certainty_terms():
    text = good_markdown() + "\n这里必涨，稳赚。"

    _, gate = verify_ai_reasoning(good_output(text), transcript())

    assert gate.status == "FALLBACK"
    assert any(item.code == "HARD_FORBIDDEN_TERMS" for item in gate.violations)


def test_verifier_rewrites_unknown_prices():
    text = good_markdown() + "\n额外观察 13.27。"

    _, gate = verify_ai_reasoning(good_output(text), transcript())

    assert gate.status == "REWRITE"
    violation = next(item for item in gate.violations if item.code == "UNKNOWN_PRICE_REFERENCE")
    assert "13.27" in violation.evidence


def test_verifier_allows_current_price_and_allowed_prices():
    text = good_markdown() + "\n当前价 12.30，确认位 12.80，防守线 11.90，日内低点 15.85，距离 0.27%。"

    _, gate = verify_ai_reasoning(good_output(text), transcript())

    assert gate.status == "PASS"


def test_verifier_rewrites_missing_disclaimer():
    text = good_markdown().replace("仅供参考，不构成投资建议。", "")
    raw = good_output(text)
    raw["disclaimer"] = ""

    _, gate = verify_ai_reasoning(raw, transcript())

    assert gate.status == "REWRITE"
    assert any(item.code == "MISSING_DISCLAIMER" for item in gate.violations)
