"""Lightweight audit for first-stage AI Chan Markdown output.

2026-05-01 重构：
- Semantic Coach Filter（第二次 LLM 调用）已移除。
- 教练用语（买入/卖出/止损/清仓/接飞刀等）允许出现在推演中，
  因为它们都绑定了结构条件，不是裸喊单。
- 本 verifier 不再当推演裁判，只做最低限度审计：
  1. Markdown contract 完整性
  2. 免责声明
"""

from __future__ import annotations

from pydantic import ValidationError

from server.engines.ai_native.schemas import (
    AIReasoningOutput,
    GateResult,
    GateViolation,
    StructureTranscript,
)


REQUIRED_SECTION_GROUPS = (
    ("【当前定位】", ("【当前定位】", "当前定位", "【全局语境定性】", "全局语境定性")),
    ("【完全分类】", ("【完全分类】", "完全分类", "【三种剧本】", "三种剧本", "【推演与应对沙盘】", "推演与应对沙盘")),
)


def verify_ai_reasoning(raw_output: dict, transcript: StructureTranscript) -> tuple[AIReasoningOutput | None, GateResult]:
    """Audit first-stage AI Chan reasoning without rewriting valid AI output."""
    _ = transcript
    try:
        output = AIReasoningOutput(**raw_output)
    except ValidationError as exc:
        return None, GateResult(
            status="REWRITE",
            score=0,
            violations=[
                GateViolation(
                    code="SCHEMA_INVALID",
                    message="AI 输出不符合 Markdown 推演 schema",
                    severity="REWRITE",
                    evidence=[str(exc)[:500]],
                )
            ],
        )

    body = output.coach_filtered_md or ""
    violations: list[GateViolation] = []
    violations.extend(_required_section_violations(body))
    if "仅供参考" not in body and "仅供参考" not in output.disclaimer:
        violations.append(
            GateViolation(
                code="MISSING_DISCLAIMER",
                message="用户可见推演缺少风险提示",
                severity="REWRITE",
            )
        )

    status = _status(violations)
    score = _score(violations)
    output.semantic_filter_status = status
    output.semantic_filter_violations = [item.model_dump() for item in violations]
    return output, GateResult(status=status, score=score, violations=violations)


def _required_section_violations(body: str) -> list[GateViolation]:
    missing = [label for label, aliases in REQUIRED_SECTION_GROUPS if not any(alias in body for alias in aliases)]
    if not missing:
        return []
    return [
        GateViolation(
            code="MISSING_MARKDOWN_SECTIONS",
            message="用户可见推演缺少当前定位和完全分类结构",
            severity="REWRITE",
            evidence=missing,
        )
    ]


def _score(violations: list[GateViolation]) -> int:
    score = 100
    for item in violations:
        score -= 45 if item.severity == "FALLBACK" else 18
    return max(score, 0)


def _status(violations: list[GateViolation]) -> str:
    if any(item.severity == "FALLBACK" for item in violations):
        return "FALLBACK"
    if any(item.severity == "REWRITE" for item in violations):
        return "REWRITE"
    return "PASS"
